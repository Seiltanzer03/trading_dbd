"""Bounded off-host real market-bar acquisition; never fill missing intervals.

Coinbase Exchange candles: 300 rows/request, bucket start timestamp, public API.
Kraken OHLC: at most720 recent rows; final row is uncommitted and is excluded.
Crypto USD providers are explicit proxies for configured Binance USDT prices.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import threading
import time

from .config import ALL_INSTRUMENTS
from .mathematical_edge import crypto_training_source_admission, fingerprint, number

BAR_SECONDS = 300
MAX_BARS = 20000
MAX_DAYS = 60
MAX_PAGES = 60
MAX_RESPONSE_BYTES = 2_000_000
COINBASE_URL = 'https://api.exchange.coinbase.com'
KRAKEN_URL = 'https://api.kraken.com'
COINBASE_PRODUCTS = {'BTCUSD': 'BTC-USD', 'ETHUSD': 'ETH-USD', 'SOLUSD': 'SOL-USD'}
KRAKEN_PAIRS = {'BTCUSD': 'XBTUSD', 'ETHUSD': 'ETHUSD', 'SOLUSD': 'SOLUSD'}
PROVIDER_DOCS = {
    'Binance': 'https://github.com/binance/binance-public-data',
    'Coinbase Exchange': 'https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles',
    'Kraken': 'https://docs.kraken.com/api-reference/market-data/get-ohlc-data',
    'Yahoo Finance via yfinance': 'https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html',
}


class RequestBudget:
    """A shared ≤3rps public-provider budget and finite collection deadline."""
    def __init__(self, seconds=360., min_interval=.35):
        self.deadline = time.monotonic() + seconds
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.next_request = 0.

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0., self.next_request-now)
            if now + delay >= self.deadline:
                raise RuntimeError('COLLECTION_TIME_BUDGET_EXHAUSTED')
            if delay:
                time.sleep(delay)
            self.next_request = time.monotonic() + self.min_interval

    def remaining(self):
        return max(0., self.deadline-time.monotonic())


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def _get_json(client, url, params, budget):
    budget.acquire()
    response = client.get(url, params=params, timeout=min(12., budget.remaining()))
    if response.status_code != 200:
        # Do not retry denial/rate limits or route around geographic restrictions.
        raise RuntimeError(f'PROVIDER_HTTP_{response.status_code}')
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError('PROVIDER_RESPONSE_EXCEEDS_BOUND')
    if 'json' not in response.headers.get('content-type', '').lower():
        raise ValueError('PROVIDER_NON_JSON_RESPONSE')
    server_ts = None
    if response.headers.get('date'):
        try:
            server_ts = parsedate_to_datetime(response.headers['date']).timestamp()
        except (ValueError, TypeError, OverflowError):
            raise ValueError('PROVIDER_DATE_HEADER_INVALID')
        if abs(server_ts-time.time()) > 300:
            raise ValueError('PROVIDER_CLOCK_DIFFERS_FROM_OBSERVED_CLOCK')
    return response.json(), server_ts


def _bar(start, open_, high, low, close):
    values = [number(value) for value in (start, open_, high, low, close)]
    if any(value is None for value in values):
        return None
    start, open_, high, low, close = values
    if start < 0 or abs(start % BAR_SECONDS) > 1 or min(open_, high, low, close) <= 0:
        return None
    if low > min(open_, close) or high < max(open_, close):
        return None
    return {'bar_end_ts': start+BAR_SECONDS, 'open': open_, 'high': high, 'low': low, 'close': close}


def source_coverage(bars):
    """Expose usable continuity, not just the newest possibly isolated bar."""
    ordered = sorted(bars, key=lambda row: row['bar_end_ts'])
    runs, current = [], 0
    previous = None
    for row in ordered:
        end = row['bar_end_ts']
        if previous is None or abs(end-previous-BAR_SECONDS) <= 1:
            current += 1
        else:
            runs.append(current)
            current = 1
        previous = end
    if current:
        runs.append(current)
    return {'bar_count': len(ordered), 'first_bar_end_ts': ordered[0]['bar_end_ts'] if ordered else None,
            'last_bar_end_ts': ordered[-1]['bar_end_ts'] if ordered else None,
            'trailing_consecutive_5m_bars': current, 'longest_consecutive_5m_bars': max(runs, default=0),
            'gap_count': max(0, len(runs)-1), 'gaps_filled': False}


def _make_source(code, provider, ticker, bars, captured, metadata):
    bars = sorted(bars, key=lambda row: row['bar_end_ts'])
    if not bars or len(bars) > MAX_BARS:
        raise ValueError('SOURCE_EMPTY_OR_EXCEEDS_BOUND')
    instrument = ALL_INSTRUMENTS[code]
    is_crypto = instrument.asset_class == 'crypto'
    exact_crypto = (is_crypto and provider == 'Binance' and ticker == instrument.binance_symbol)
    digest = fingerprint(bars)
    return {'instrument': code, 'provider': provider, 'ticker': ticker, 'interval': '5m',
            'bars': bars, 'source_sha256': digest, 'source_id': f'math-real-{code}-{digest[:24]}',
            'source_kind': 'SINGLE_PROVIDER_COMPLETED_5M', 'fetched_ts': time.time(),
            'captured_ts': captured, 'not_broker_execution_bars': True,
            'receipt_observed_ts': time.time(),
            'source_semantics': {'provider': provider, 'ticker': ticker,
                'bar_timestamp_semantics': 'provider interval start +300s',
                'completed_bars_only_for_features': True, 'exact_live_broker_series': False,
                'synthetic_price_history': False, 'synthetic_option_history': False,
                'option_history_used': False, 'returns_not_absolute_price_used_by_model': True,
                'proxy_for_configured_series': not exact_crypto, 'configured_price_series': instrument.price_label,
                'provider_quote_currency': ('USDT' if exact_crypto else 'USD') if is_crypto else None,
                'configured_quote_currency': 'USDT' if is_crypto else None,
                'currency_basis_mismatch': is_crypto and not exact_crypto, 'gaps_filled': False,
                'provider_documentation': PROVIDER_DOCS.get(provider),
                **({'auto_adjust':False,'actions':False,'repair':False}
                   if provider=='Yahoo Finance via yfinance' else {})},
            'coverage': source_coverage(bars), 'collection': metadata}


def _cache_load(directory, code, provider, captured):
    if directory is None:
        return None
    slug = provider.lower().replace(' ', '_')
    path = Path(directory) / f'{code}_{slug}.json'
    try:
        if path.stat().st_size > 8_000_000:
            return None
        source = json.loads(path.read_text())
        bars = source['bars']
        if (source.get('instrument') != code or source.get('provider') != provider
                or source.get('interval') != '5m' or not isinstance(bars, list) or len(bars) > MAX_BARS
                or not bars or len({row['bar_end_ts'] for row in bars}) != len(bars)
                or source.get('ticker') != (COINBASE_PRODUCTS.get(code) if provider == 'Coinbase Exchange'
                                          else KRAKEN_PAIRS.get(code) if provider == 'Kraken'
                                          else ALL_INSTRUMENTS[code].binance_symbol if provider == 'Binance'
                                          else ALL_INSTRUMENTS[code].yahoo)
                or source.get('source_sha256') != fingerprint(bars)):
            return None
        # Retain only valid completed cached rows; never trust a future cache.
        if any(_bar(row['bar_end_ts']-BAR_SECONDS, row['open'], row['high'], row['low'], row['close']) != row
               or row['bar_end_ts'] > captured for row in bars):
            return None
        return source
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _exact_crypto_archive(source, code, captured):
    """Verify an existing archive, never infer mislabeled legacy bars' venue."""
    if not isinstance(source, dict) or source.get('instrument') != code:
        return None
    bars = source.get('bars')
    if not isinstance(bars, list) or not 0 < len(bars) <= MAX_BARS:
        return None
    try:
        clocks = [source[key] for key in ('captured_ts', 'fetched_ts', 'receipt_observed_ts') if key in source]
        collection_clock = (source.get('collection') or {}).get('archive_receipt_observed_ts')
        if collection_clock is not None:
            clocks.append(collection_clock)
        if not clocks or any(number(clock) is None or not 0 < number(clock) <= captured for clock in clocks):
            return None
        if any(_bar(row['bar_end_ts']-BAR_SECONDS, row['open'], row['high'], row['low'], row['close']) != row
               or row['bar_end_ts'] > captured for row in bars):
            return None
        if len({row['bar_end_ts'] for row in bars}) != len(bars):
            return None
        digest = fingerprint(bars)
        if source.get('source_sha256') != digest:
            return None
        admission = crypto_training_source_admission(
            {'sources': [{**source, 'validated_bars_sha256': digest}]}, {'source_sha256': digest}, code)
        return source if admission['available'] else None
    except (KeyError, TypeError, ValueError):
        return None


def retained_crypto_source(code, captured, seed, cached, *, days=60):
    """Accumulate only verified exact-feed archived observations off-host.

    No Binance request is made. Previously denied public access is not rerouted
    to another IP; the existing normally configured collector owns acquisition.
    """
    seed = _exact_crypto_archive(seed, code, captured)
    cached = _exact_crypto_archive(cached, code, captured)
    if seed is None:
        return {**cached, 'collection_fallback': 'HASH_VERIFIED_EXACT_CONFIGURED_SOURCE_CACHE'} if cached else None
    start = captured-days*86400
    merged = {row['bar_end_ts']: row for row in (cached or {}).get('bars', []) if start < row['bar_end_ts'] <= captured}
    revised = sum(row['bar_end_ts'] in merged and merged[row['bar_end_ts']] != row for row in seed['bars'])
    merged.update({row['bar_end_ts']: row for row in seed['bars'] if start < row['bar_end_ts'] <= captured})
    bars = [merged[end] for end in sorted(merged)]
    if not bars:
        return None
    result = _make_source(code, 'Binance', ALL_INSTRUMENTS[code].binance_symbol, bars, captured,
                          {'acquisition': 'READ_ONLY_EXACT_CONFIGURED_RETAINED_ARCHIVE',
                           'network_requests': 0, 'archive_receipt_observed_ts': seed.get('receipt_observed_ts'),
                           'cache_used': cached is not None, 'same_provider_revised_overlap_bars': revised,
                           'archive_source_id': seed.get('source_id'), 'requested_days': days,
                           'direct_public_api_attempted': False})
    result['source_kind'] = 'SINGLE_RETAINED_PROVIDER_COMPLETED_5M'
    result['source_semantics']['bar_timestamp_semantics'] = 'five consecutive completed direct Binance one-minute bars'
    # Keep immutable public trade aggregates only where the exact target bar
    # still agrees. Newly retained bars have no fabricated flow observations.
    if cached and isinstance(cached.get('trade_flow_history'),list):
        flow=cached['trade_flow_history'];collection=cached.get('collection') or {}
        originals={row['bar_end_ts']:row for row in cached['bars']}
        if (cached.get('trade_flow_sha256')==fingerprint(flow)
                and collection.get('contract_version')=='binance-public-archive-v1'):
            kept=[row for row in flow if row.get('bar_end_ts') in merged
                and merged[row['bar_end_ts']]==originals.get(row['bar_end_ts'])]
            if kept:
                result['trade_flow_history']=kept;result['trade_flow_sha256']=fingerprint(kept)
                result['collection'].update(contract_version='binance-public-archive-v1',
                    files=collection.get('files'),historical_first_receipt_known=False)
    return result


def retain_yahoo_history(current, archives, captured):
    """Retain existing same-series history; no new receipt or gap interpolation."""
    from .real_input_integrity import simulated_evidence
    code=current.get('instrument')
    if code not in ALL_INSTRUMENTS or ALL_INSTRUMENTS[code].asset_class=='crypto':
        return current
    def signature(item):
        semantics=item.get('source_semantics')
        if not isinstance(semantics,dict):raise ValueError('HISTORY_SEMANTICS_INVALID')
        expected={'provider':item['provider'],'ticker':item['ticker'],'interval':'5m',
            'auto_adjust':False,'actions':False,'repair':False,
            'completed_bars_only_for_features':True,'gaps_filled':False,'currency_basis_mismatch':False}
        if any(semantics.get(k,v)!=v for k,v in expected.items()):
            raise ValueError('HISTORY_SEMANTICS_MISMATCH')
        return tuple(semantics.get(k) for k in ('provider_quote_currency','configured_quote_currency'))
    def verified(item, original_cutoff=None):
        if not isinstance(item,dict) or any(item.get(k)!=v for k,v in (
                ('instrument',code),('provider','Yahoo Finance via yfinance'),
                ('ticker',ALL_INSTRUMENTS[code].yahoo),('interval','5m'))):
            raise ValueError('HISTORY_SERIES_IDENTITY_MISMATCH')
        if simulated_evidence({k:v for k,v in item.items() if k not in {'bars','event_hourly_bars','event_hourly_archive'}}):
            raise ValueError('HISTORY_SYNTHETIC_SOURCE_REJECTED')
        rows=item.get('bars')
        if not isinstance(rows,list) or not 0<len(rows)<=MAX_BARS or item.get('source_sha256')!=fingerprint(rows):
            raise ValueError('HISTORY_HASH_OR_SIZE_INVALID')
        semantics=item.get('source_semantics')
        if (not isinstance(semantics,dict) or semantics.get('synthetic_price_history') is True
                or semantics.get('bar_timestamp_semantics') not in (
                'provider interval start +300s',
                'provider timestamp is interval start; T0 is bar_start+300s')):
            raise ValueError('HISTORY_TIMESTAMP_SEMANTICS_INVALID')
        item_signature=signature(item)
        if original_cutoff is not None and item_signature!=current_signature:
            raise ValueError('HISTORY_SEMANTICS_MISMATCH')
        bars={}
        for row in rows:
            bar=_bar(row['bar_end_ts']-300,row['open'],row['high'],row['low'],row['close'])
            if (bar is None or bar['bar_end_ts']>captured or bar['bar_end_ts'] in bars
                    or ('bar_start_ts' in row and row['bar_start_ts']!=bar['bar_end_ts']-300)
                    or simulated_evidence(row)):
                raise ValueError('HISTORY_BAR_INVALID')
            if original_cutoff is not None and bar['bar_end_ts']>original_cutoff:
                raise ValueError('HISTORY_BAR_AFTER_ORIGINAL_OBSERVATION')
            bars[bar['bar_end_ts']]=bar
        return bars
    try:
        current_signature=signature(current)
        merged=verified(current)
    except (ValueError,TypeError,KeyError):
        return current  # Existing owner still rejects an invalid primary source.
    def original_manifest(item):
        keys=('source_id','source_sha256','fetched_ts','receipt_observed_ts',
              'captured_ts','created_ts','first_bar_end_ts','last_bar_end_ts')
        proof=item.get('history_retention') or {}
        inherited=proof.get('original_sources',proof.get('accepted_sources',[]))
        snapshots=(item.get('historical_snapshot_history') or {}).get('accepted_sources',[])
        if not isinstance(inherited,list) or not isinstance(snapshots,list) or len(inherited)+len(snapshots)>128:
            raise ValueError('HISTORY_ORIGINAL_MANIFEST_OVER_BOUND')
        original={k:item.get(k) for k in keys if item.get(k) is not None}
        ends=[b['bar_end_ts'] for b in item['bars']]
        original.update(first_bar_end_ts=min(ends),last_bar_end_ts=max(ends))
        return [original,*[{k:parent[k] for k in keys if k in parent and parent[k] is not None} for parent in inherited+snapshots]]
    accepted=[];excluded=[];seen=set();originals={}
    def add_originals(parents, *, fresh_primary=False):
        additions={}
        for parent in parents:
            digest=parent.get('source_sha256')
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('HISTORY_ORIGINAL_MANIFEST_INVALID')
            if not isinstance(parent.get('source_id'),str) or not parent['source_id']:
                raise ValueError('HISTORY_ORIGINAL_MANIFEST_INVALID')
            clocks=[parent[k] for k in ('fetched_ts','receipt_observed_ts','captured_ts','created_ts') if k in parent]
            limit=(max(captured,time.time())
                if fresh_primary and parent is parents[0] else captured)
            if not clocks or any(number(t) is None or not 0<number(t)<=limit for t in clocks):
                raise ValueError('HISTORY_ORIGINAL_MANIFEST_CLOCK_INVALID')
            if 'last_bar_end_ts' in parent and parent['last_bar_end_ts']>min(clocks):
                raise ValueError('HISTORY_BAR_AFTER_ORIGINAL_OBSERVATION')
            additions.setdefault(digest,parent)
        if len(originals.keys()|additions.keys())>128:raise ValueError('HISTORY_ORIGINAL_MANIFEST_OVER_BOUND')
        return additions
    try:originals.update(add_originals(original_manifest(current),fresh_primary=True))
    except (ValueError,TypeError,KeyError,AttributeError):return current
    for item in archives:
        if item is None:continue
        identity=item.get('source_sha256') if isinstance(item,dict) and isinstance(item.get('source_sha256'),str) else None
        if identity in seen:continue
        seen.add(identity)
        try:
            clocks=[item.get(key) for key in ('fetched_ts','receipt_observed_ts','captured_ts')
                    if isinstance(item,dict) and key in item]
            if not clocks or any(number(t) is None or not 0<number(t)<=captured for t in clocks):
                raise ValueError('HISTORY_RECEIPT_CLOCK_INVALID')
            older=verified(item,min(float(t) for t in clocks))
            parents=add_originals(original_manifest(item))
            if any(end in merged and merged[end]!=bar for end,bar in older.items()):
                raise ValueError('HISTORY_OVERLAP_REVISION_CONFLICT')
            added={end:bar for end,bar in older.items() if captured-120*86400<end<=captured and end not in merged}
            merged.update(added);originals.update(parents)
            accepted.append({'source_id':item.get('source_id'),'source_sha256':item['source_sha256'],
                'fetched_ts':item.get('fetched_ts'),'receipt_observed_ts':item.get('receipt_observed_ts'),
                'added_bars':len(added),'historical_snapshot_history':item.get('historical_snapshot_history')})
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            excluded.append({'source_id':item.get('source_id') if isinstance(item,dict) else None,
                'reason':str(exc) if isinstance(exc,ValueError) else 'HISTORY_BAR_INVALID'})
    ends=sorted(end for end in merged if captured-120*86400<end<=captured)
    capped=len(ends)>MAX_BARS;bars=[merged[end] for end in ends[-MAX_BARS:]]
    if not bars:return current
    digest=fingerprint(bars)
    return {**current,'bars':bars,'source_sha256':digest,
        'source_id':f'math-real-{code}-{digest[:24]}','coverage':source_coverage(bars),
        'history_retention':{'contract_version':'verified-yahoo-history-retention-v1',
            'maximum_days':120,'maximum_bars':MAX_BARS,'row_cap_applied':capped,
            'accepted_sources':accepted,'excluded_sources':excluded,'gaps_filled':False,
            'original_sources':list(originals.values()),'maximum_original_sources':128,
            'current_source_id':current.get('source_id'),'current_source_sha256':current['source_sha256'],
            'new_historical_receipts':False}}


def _cache_save(directory, source):
    if directory is None:
        return
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    slug = source['provider'].lower().replace(' ', '_')
    path = directory / f"{source['instrument']}_{slug}.json"
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(source, ensure_ascii=False, allow_nan=False))
    temporary.replace(path)


def fetch_coinbase(code, captured, client, budget, *, days=60, cached=None):
    product = COINBASE_PRODUCTS[code]
    finish = int(captured // BAR_SECONDS) * BAR_SECONDS
    start = finish-int(days*86400)
    merged = {row['bar_end_ts']: row for row in (cached or {}).get('bars', [])
              if start < row['bar_end_ts'] <= finish}
    # Newest first: a late timeout still preserves an actual recent partial series.
    # A verified cache gets a small overlap; do not silently repair older gaps.
    lower = max(start, max(merged, default=start)-12*BAR_SECONDS) if merged else start
    request_end = finish
    pages = 0
    server_times = []
    failure = None
    observed_rows = {}
    excluded_invalid_rows = 0
    while request_end > lower:
        if pages >= MAX_PAGES:
            raise RuntimeError('PROVIDER_PAGE_BOUND_EXCEEDED')
        request_start = max(lower, request_end-299*BAR_SECONDS)
        try:
            payload, server_ts = _get_json(client, f'{COINBASE_URL}/products/{product}/candles',
                                          {'granularity': BAR_SECONDS, 'start': _iso(request_start),
                                           'end': _iso(request_end)}, budget)
        except Exception as exc:
            if not merged:
                raise
            failure = f'{type(exc).__name__}: {str(exc)[:250]}'
            break
        if not isinstance(payload, list) or len(payload) > 300:
            raise ValueError('COINBASE_CANDLE_SCHEMA_INVALID')
        for row in payload:
            if not isinstance(row, list) or len(row) < 6:
                raise ValueError('COINBASE_CANDLE_SCHEMA_INVALID')
            bar = _bar(row[0], row[3], row[2], row[1], row[4])
            if bar is None:
                excluded_invalid_rows += 1
            if bar is not None and request_start < bar['bar_end_ts'] <= request_end:
                prior = observed_rows.get(bar['bar_end_ts'])
                if prior is not None and prior != bar:
                    raise ValueError('COINBASE_CONFLICTING_DUPLICATE_CANDLE')
                observed_rows[bar['bar_end_ts']] = bar
                merged[bar['bar_end_ts']] = bar
        if server_ts is not None:
            server_times.append(server_ts)
        request_end = request_start
        pages += 1
    bars = [merged[end] for end in sorted(merged)]
    if failure is not None and pages == 0 and cached:
        # A failed refresh is not a new provider receipt: preserve original clocks.
        return {**cached, 'collection': {**cached.get('collection', {}),
                'history_request_completed': False, 'partial_collection_error': failure,
                'refresh_successful_pages': 0, 'refresh_attempt_cutoff_ts': captured}}
    return _make_source(code, 'Coinbase Exchange', product, bars, captured,
                        {'requested_days': days, 'pages': pages, 'maximum_pages': MAX_PAGES,
                         'cache_used': cached is not None, 'provider_server_ts': max(server_times, default=None),
                         'history_request_completed': failure is None, 'partial_collection_error': failure,
                         'excluded_invalid_rows': excluded_invalid_rows,
                         'intrabar_order_inferred': False})


def fetch_kraken(code, captured, client, budget, *, cached=None, days=60):
    pair = KRAKEN_PAIRS[code]
    payload, server_ts = _get_json(client, f'{KRAKEN_URL}/0/public/OHLC',
                                  {'pair': pair, 'interval': 5}, budget)
    if not isinstance(payload, dict) or payload.get('error') or not isinstance(payload.get('result'), dict):
        raise ValueError('KRAKEN_ERROR_OR_SCHEMA_INVALID')
    series = [value for key, value in payload['result'].items() if key != 'last']
    if len(series) != 1 or not isinstance(series[0], list) or len(series[0]) > 720:
        raise ValueError('KRAKEN_CANDLE_SCHEMA_INVALID')
    merged = {row['bar_end_ts']: row for row in (cached or {}).get('bars', [])
              if captured-days*86400 < row['bar_end_ts'] <= captured}
    # The final returned row is documented as uncommitted regardless of since.
    for row in series[0][:-1]:
        if not isinstance(row, list) or len(row) < 8:
            raise ValueError('KRAKEN_CANDLE_SCHEMA_INVALID')
        bar = _bar(row[0], row[1], row[2], row[3], row[4])
        if bar is not None and bar['bar_end_ts'] <= captured:
            merged[bar['bar_end_ts']] = bar
    return _make_source(code, 'Kraken', pair, [merged[end] for end in sorted(merged)], captured,
                        {'pages': 1, 'historical_depth_limit_bars': 720,
                         'uncommitted_final_row_excluded': True, 'provider_server_ts': server_ts,
                         'cache_used': cached is not None, 'requested_60d_history_available': False})


def fetch_yahoo(code, captured, days=60, *, timeout=15):
    import yfinance as yf
    from .g1_short_horizon_historical_wf import _frame_to_bars
    ticker = ALL_INSTRUMENTS[code].yahoo
    frame = yf.Ticker(ticker).history(period=f'{days}d', interval='5m', auto_adjust=False,
                                      actions=False, timeout=timeout, repair=False)
    bars = [row for row in _frame_to_bars(frame) if row['bar_end_ts'] <= captured]
    # Keep the canonical OHLC schema identical across public providers.
    bars = [_bar(row['bar_end_ts']-BAR_SECONDS, row['open'], row['high'], row['low'], row['close']) for row in bars]
    return _make_source(code, 'Yahoo Finance via yfinance', ticker, [row for row in bars if row], captured,
                        {'requested_days': days, 'pages': 1, 'fresh_provider_series_replaces_mixed_retained_series': True})


def collect_fresh_sources(captured, *, seed_sources=(), cache_dir=None, days=60,
                          budget_seconds=360., codes=None, client_factory=None, diagnostic_sources=None,
                          macro_archive=None):
    """Independent fresh sources; failed providers retain explicitly named fallback."""
    if (number(captured) is None or captured <= 0 or captured > time.time()+1
            or not 1 <= days <= MAX_DAYS or not 1 <= budget_seconds <= 420):
        raise ValueError('invalid collection cutoff, days or time budget')
    codes = tuple(ALL_INSTRUMENTS if codes is None else codes)
    if macro_archive is not None:
        from .edge_family_macro_archive import OfficialMacroArchive
        if not isinstance(macro_archive,OfficialMacroArchive) or macro_archive.captured_ts!=captured:
            raise ValueError('EVENT_CALENDAR_NOT_VALIDATED_FOR_COLLECTION_CUTOFF')
    if len(set(codes)) != len(codes) or any(code not in ALL_INSTRUMENTS for code in codes):
        raise ValueError('instrument not configured')
    seeds = {}
    for source in seed_sources:
        code = source['instrument']
        if code in seeds:
            raise ValueError('duplicate seed instrument')
        seeds[code] = source
    budget = RequestBudget(budget_seconds)
    if client_factory is None:
        import httpx
        client_factory = lambda: httpx.Client(timeout=httpx.Timeout(12., connect=5.), follow_redirects=False)

    def collect(code):
        failures = []
        source = None
        related = None
        if code in COINBASE_PRODUCTS:
            exact_cached = _cache_load(cache_dir, code, 'Binance', captured)
            source = retained_crypto_source(code, captured, seeds.get(code), exact_cached, days=days)
            if source is not None and source['coverage']['bar_count'] >= min(MAX_BARS, days*288-288):
                # Old USD providers remain diagnostic caches/artifacts, never
                # splice them into an exact Binance/USDT training series.
                if not source.get('collection_fallback'):
                    _cache_save(cache_dir, source)
                related = _cache_load(cache_dir, code, 'Coinbase Exchange', captured)
                return code, source, [], related
            cached = _cache_load(cache_dir, code, 'Coinbase Exchange', captured)
            kraken_cached = _cache_load(cache_dir, code, 'Kraken', captured)
            with client_factory() as client:
                retained = source
                try:
                    from .binance_public_archive import fetch_public_archive
                    source = fetch_public_archive(code, captured, client, budget, days=days)
                except Exception as exc:
                    failures.append({'provider': 'Binance public archive', 'error': f'{type(exc).__name__}: {str(exc)[:250]}'})
                    source = retained
                if source is not None:
                    try:
                        related = fetch_coinbase(code, captured, client, budget, days=days, cached=cached)
                        _cache_save(cache_dir, related)
                    except Exception as exc:
                        failures.append({'provider':'Coinbase Exchange', 'error':f'{type(exc).__name__}: {str(exc)[:250]}'})
                        related = cached
                    source = {**source, 'fresh_collection_errors': failures}
                    _cache_save(cache_dir, source)
                    return code, source, failures, related
                for provider, fetch in [('Coinbase Exchange', lambda: fetch_coinbase(code, captured, client, budget, days=days, cached=cached)),
                                         ('Kraken', lambda: fetch_kraken(code, captured, client, budget, cached=kraken_cached, days=days))]:
                    try:
                        source = fetch()
                        partial = source.get('collection', {}).get('partial_collection_error')
                        if partial:
                            failures.append({'provider': provider, 'error': partial})
                            source['collection_fallback'] = 'PARTIAL_SAME_PROVIDER_COLLECTION'
                        break
                    except Exception as exc:
                        failures.append({'provider': provider, 'error': f'{type(exc).__name__}: {str(exc)[:250]}'})
            if source is None and (cached or kraken_cached):
                source = {**(cached or kraken_cached), 'collection_fallback': 'HASH_VERIFIED_SAME_PROVIDER_CACHE'}
        else:
            cached = _cache_load(cache_dir, code, 'Yahoo Finance via yfinance', captured)
            try:
                if budget.remaining() <= 0:
                    raise RuntimeError('COLLECTION_TIME_BUDGET_EXHAUSTED')
                source = fetch_yahoo(code, captured, days, timeout=min(15., budget.remaining()))
            except Exception as exc:
                failures.append({'provider': 'Yahoo Finance via yfinance', 'error': f'{type(exc).__name__}: {str(exc)[:250]}'})
            if source is None and cached:
                source = {**cached, 'collection_fallback': 'HASH_VERIFIED_SAME_PROVIDER_CACHE'}
        if source is None and code in seeds:
            source = {**seeds[code], 'collection_fallback': 'READ_ONLY_EXPORTED_SOURCE'}
        if source is not None:
            if code not in COINBASE_PRODUCTS:
                source=retain_yahoo_history(source,[seeds.get(code),cached],captured)
            source = {**source, 'fresh_collection_errors': failures}
            if source.get('collection_fallback') in (None, 'PARTIAL_SAME_PROVIDER_COLLECTION'):
                _cache_save(cache_dir, source)
        return code, source, failures, related

    sources, errors, attempts = [], {}, {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for code, source, failures, related in pool.map(collect, codes):
            if related is not None and diagnostic_sources is not None:
                diagnostic_sources.append(related)
            attempts[code] = {'failures': failures, 'source_available': source is not None,
                              'fallback': source.get('collection_fallback') if source else None}
            if source is not None:
                sources.append(source)
            else:
                errors[code] = ' | '.join(f"{row['provider']}: {row['error']}" for row in failures) or 'SOURCE_BARS_UNAVAILABLE'
    if macro_archive is not None and macro_archive.published_release_times():
        # A separate calendar-selected sidecar never changes recent price bars.
        # It uses the SAME collection deadline and global request rate bound.
        def enrich_events(source):
            code=source['instrument']
            try:
                from .binance_event_windows import collect_event_windows
                with client_factory() as client:
                    extra=collect_event_windows(code,captured,client,budget,
                        release_times=macro_archive.published_release_times(),
                        calendar_sha256=macro_archive.bundle_sha256)
                source.update(extra)
                attempts[code]['event_target_rows']=len(extra['event_window_bars'])
            except Exception as exc:
                attempts[code]['event_history_error']=f'{type(exc).__name__}: {str(exc)[:180]}'
            return source
        host_denied=any(failure.get('provider')=='Binance public archive'
            and any(f'HTTP_{status}' in failure.get('error','') for status in (403,451,429))
            for row in attempts.values() for failure in row['failures'])
        exact=[row for row in sources if row.get('provider')=='Binance'
            and row.get('instrument') in COINBASE_PRODUCTS
            and row.get('ticker')==ALL_INSTRUMENTS[row['instrument']].binance_symbol]
        if host_denied:
            for row in exact:
                attempts[row['instrument']]['event_history_error']='BINANCE_ARCHIVE_HOST_DENIED_DURING_PRIMARY_COLLECTION'
            exact=[]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(enrich_events,exact))
    if macro_archive is not None and macro_archive.published_release_times():
        hourly_targets=[row for row in sources if row.get('instrument') not in COINBASE_PRODUCTS
            and row.get('provider')=='Yahoo Finance via yfinance'
            and row.get('ticker')==ALL_INSTRUMENTS[row['instrument']].yahoo]
        yahoo_denied=any(failure.get('provider')=='Yahoo Finance via yfinance'
            and any(token in failure.get('error','').lower() for token in
                ('403','451','429','ratelimit','rate limit','too many requests'))
            for row in attempts.values() for failure in row['failures'])
        if yahoo_denied:
            for row in hourly_targets:
                attempts[row['instrument']]['hourly_event_error']='YAHOO_HOST_DENIED_DURING_PRIMARY_COLLECTION'
            hourly_targets=[]
        def enrich_hourly(source):
            code=source['instrument']
            try:
                from .edge_family_hourly_events import collect_hourly_events
                extra=collect_hourly_events(code,captured,budget)
                source.update(extra)
                attempts[code]['hourly_event_rows']=len(extra['event_hourly_bars'])
            except Exception as exc:
                attempts[code]['hourly_event_error']=f'{type(exc).__name__}: {str(exc)[:180]}'
            return source
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(enrich_hourly,hourly_targets))
    return sources, errors, attempts
