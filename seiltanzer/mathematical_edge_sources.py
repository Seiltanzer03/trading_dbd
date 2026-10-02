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
from .mathematical_edge import fingerprint, number

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
                'proxy_for_configured_series': True, 'configured_price_series': instrument.price_label,
                'provider_quote_currency': 'USD' if is_crypto else None,
                'configured_quote_currency': 'USDT' if is_crypto else None,
                'currency_basis_mismatch': is_crypto, 'gaps_filled': False,
                'provider_documentation': PROVIDER_DOCS.get(provider)},
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
                          budget_seconds=360., codes=None, client_factory=None):
    """Independent fresh sources; failed providers retain explicitly named fallback."""
    if (number(captured) is None or captured <= 0 or captured > time.time()+1
            or not 1 <= days <= MAX_DAYS or not 1 <= budget_seconds <= 420):
        raise ValueError('invalid collection cutoff, days or time budget')
    codes = tuple(ALL_INSTRUMENTS if codes is None else codes)
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
        if code in COINBASE_PRODUCTS:
            cached = _cache_load(cache_dir, code, 'Coinbase Exchange', captured)
            kraken_cached = _cache_load(cache_dir, code, 'Kraken', captured)
            with client_factory() as client:
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
            source = {**source, 'fresh_collection_errors': failures}
            if source.get('collection_fallback') in (None, 'PARTIAL_SAME_PROVIDER_COLLECTION'):
                _cache_save(cache_dir, source)
        return code, source, failures

    sources, errors, attempts = [], {}, {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for code, source, failures in pool.map(collect, codes):
            attempts[code] = {'failures': failures, 'source_available': source is not None,
                              'fallback': source.get('collection_fallback') if source else None}
            if source is not None:
                sources.append(source)
            else:
                errors[code] = ' | '.join(f"{row['provider']}: {row['error']}" for row in failures) or 'SOURCE_BARS_UNAVAILABLE'
    return sources, errors, attempts
