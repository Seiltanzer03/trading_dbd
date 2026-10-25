"""Collector contracts only: generated candles are not trading-edge evidence."""
from datetime import datetime
import json

import pytest

from seiltanzer import mathematical_edge_sources as source


class Response:
    status_code = 200
    headers = {'content-type': 'application/json'}
    content = b'[]'

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class Client:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        return self.callback(url, params)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def candle(start):
    return [start, 99., 102., 100., 101., 10.]


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def budget():
    return source.RequestBudget(seconds=30., min_interval=0.)


def test_exact_public_archive_is_used_without_losing_coinbase_drivers(monkeypatch):
    from seiltanzer import binance_public_archive
    cutoff = 1792886400.
    bars = [source._bar(cutoff-600, 100, 102, 99, 101)]
    exact = source._make_source('BTCUSD', 'Binance', 'BTCUSDT', bars, cutoff, {})
    related = source._make_source('BTCUSD', 'Coinbase Exchange', 'BTC-USD', bars, cutoff, {})
    calls = []
    monkeypatch.setattr(binance_public_archive, 'fetch_public_archive', lambda *a, **k: calls.append('archive') or exact)
    monkeypatch.setattr(source, 'fetch_coinbase', lambda *a, **k: calls.append('coinbase') or related)
    diagnostics = []
    rows, errors, attempts = source.collect_fresh_sources(cutoff, codes=['BTCUSD'],
        diagnostic_sources=diagnostics, client_factory=lambda: Client(lambda *a: None))
    assert calls == ['archive', 'coinbase']
    assert rows[0]['ticker'] == 'BTCUSDT'
    assert diagnostics == [related]
    assert errors == {}


def test_retained_exact_bars_preserve_original_trade_flow_archive_and_do_not_invent_missing_rows(monkeypatch):
    cutoff=1792886400.
    bars=[source._bar(cutoff-900,100,102,99,101),source._bar(cutoff-600,101,103,100,102)]
    cached=source._make_source('BTCUSD','Binance','BTCUSDT',bars,cutoff-200,{})
    cached.update(fetched_ts=cutoff-200,receipt_observed_ts=cutoff-200,
        trade_flow_history=[dict(bar_end_ts=bar['bar_end_ts'],volume=1.) for bar in bars])
    cached['trade_flow_sha256']=source.fingerprint(cached['trade_flow_history'])
    cached['collection'].update(contract_version='binance-public-archive-v1',files=[{'sha256':'a'*64}],historical_first_receipt_known=False)
    seed=source._make_source('BTCUSD','Binance','BTCUSDT',[source._bar(cutoff-300,102,104,101,103)],cutoff-1,{})
    seed.update(fetched_ts=cutoff-1,receipt_observed_ts=cutoff-1)
    retained=source.retained_crypto_source('BTCUSD',cutoff,seed,cached)
    assert retained['trade_flow_history']==cached['trade_flow_history']
    assert len(retained['bars'])==3
    assert retained['collection']['files']==cached['collection']['files']


@pytest.mark.parametrize("clock_key", ["fetched_ts", "captured_ts", "receipt_observed_ts"])
def test_exact_retained_crypto_rejects_future_archive_clocks(clock_key):
    cutoff = 90000.
    bar = source._bar(cutoff-300, 100., 101., 99., 100.)
    seed = source._make_source("BTCUSD", "Binance", "BTCUSDT", [bar], cutoff, {})
    seed["source_kind"] = "SINGLE_RETAINED_PROVIDER_COMPLETED_5M"
    seed[clock_key] = cutoff+60
    assert source.retained_crypto_source("BTCUSD", cutoff, seed, None) is None
    assert source.retained_crypto_source("BTCUSD", cutoff, None, seed) is None


def test_verified_exact_archive_is_reused_without_any_public_request(monkeypatch):
    cutoff = 90000.
    monkeypatch.setattr(source.time, 'time', lambda: cutoff)
    bar = source._bar(cutoff-300, 100., 101., 99., 100.)
    seed = source._make_source('BTCUSD', 'Binance', 'BTCUSDT', [bar], cutoff, {})
    result = source.retained_crypto_source('BTCUSD', cutoff, seed, None)
    assert result is not None
    assert result['provider'] == 'Binance'
    assert result['collection']['network_requests'] == 0
    assert result['bars'] == [bar]
    assert result['source_semantics']['currency_basis_mismatch'] is False


def test_coinbase_pagination_has_bounded_ranges_dedup_and_completed_bars():
    def reply(url, params):
        lower, upper = timestamp(params['start']), timestamp(params['end'])
        assert upper-lower <= 299*300
        # Include provider's permitted row before start and unfinished last row.
        return Response([candle(t) for t in range(int(lower)-300, int(upper)+1, 300)])
    client = Client(reply)
    result = source.fetch_coinbase('BTCUSD', 90000., client, budget(), days=1)
    assert len(client.calls) == 1
    assert len(result['bars']) == 288
    assert result['bars'][0]['bar_end_ts'] == 3900.
    assert result['bars'][-1]['bar_end_ts'] == 90000.
    assert result['source_semantics']['currency_basis_mismatch'] is True
    assert result['source_semantics']['configured_quote_currency'] == 'USDT'
    assert result['coverage']['gap_count'] == 0
    assert result['source_sha256'] == source.fingerprint(result['bars'])


def test_coinbase_collects_newest_first_and_preserves_partial_on_failure():
    def reply(url, params):
        if len(client.calls) > 1:
            raise TimeoutError('bounded network timeout')
        upper = int(timestamp(params['end']))
        return Response([candle(upper-300), candle(upper-900)])
    client = Client(reply)
    result = source.fetch_coinbase('ETHUSD', 300000., client, budget(), days=2)
    assert timestamp(client.calls[0][1]['end']) == 300000.
    assert result['collection']['history_request_completed'] is False
    assert 'TimeoutError' in result['collection']['partial_collection_error']
    assert result['coverage']['gap_count'] == 1
    assert result['coverage']['gaps_filled'] is False
    assert len(result['bars']) == 2


def test_coinbase_hash_verified_same_provider_cache_incremental_overlap(tmp_path):
    cached = source._make_source('BTCUSD', 'Coinbase Exchange', 'BTC-USD',
                                [source._bar(900., 100, 102, 99, 101)], 1200., {})
    source._cache_save(tmp_path, cached)
    cached = source._cache_load(tmp_path, 'BTCUSD', 'Coinbase Exchange', 90000.)
    client = Client(lambda url, params: Response([candle(89700.)]))
    result = source.fetch_coinbase('BTCUSD', 90000., client, budget(), days=1, cached=cached)
    assert result['collection']['cache_used'] is True
    # Out-of-window cached history is not retained or mislabeled as 60d data.
    assert len(result['bars']) == 1


def test_kraken_excludes_final_uncommitted_row_even_if_cutoff_is_later():
    client = Client(lambda url, params: Response({'error': [], 'result': {
        'XXBTZUSD': [[300, '100', '102', '99', '101', '100', '1', 1],
                    [600, '100', '102', '99', '101', '100', '1', 1]], 'last': 600}}))
    result = source.fetch_kraken('BTCUSD', 1500., client, budget())
    assert result['bars'][0]['bar_end_ts'] == 600.
    assert len(result['bars']) == 1
    assert result['collection']['uncommitted_final_row_excluded']
    assert not result['collection']['requested_60d_history_available']


@pytest.mark.parametrize('response,reason', [
    ({'status_code': 403}, 'PROVIDER_HTTP_403'),
    ({'status_code': 429}, 'PROVIDER_HTTP_429'),
    ({'headers': {'content-type': 'text/html'}}, 'PROVIDER_NON_JSON_RESPONSE'),
    ({'content': b'x'*(source.MAX_RESPONSE_BYTES+1)}, 'PROVIDER_RESPONSE_EXCEEDS_BOUND'),
])
def test_denials_html_and_oversized_response_not_retried(response, reason):
    result = Response([])
    for key, value in response.items():
        setattr(result, key, value)
    client = Client(lambda *args: result)
    with pytest.raises((ValueError, RuntimeError), match=reason):
        source.fetch_coinbase('BTCUSD', 90000., client, budget(), days=1)
    assert len(client.calls) == 1


def test_cache_integrity_bound_future_and_provider_identity(tmp_path):
    bars = [source._bar(300., 100., 102., 99., 101.)]
    cached = source._make_source('SOLUSD', 'Kraken', 'SOLUSD', bars, 600., {})
    source._cache_save(tmp_path, cached)
    assert source._cache_load(tmp_path, 'SOLUSD', 'Kraken', 600.)
    assert source._cache_load(tmp_path, 'SOLUSD', 'Kraken', 300.) is None
    path = tmp_path/'SOLUSD_kraken.json'
    data = json.loads(path.read_text())
    data['bars'][0]['close'] = 100.5
    path.write_text(json.dumps(data))
    assert source._cache_load(tmp_path, 'SOLUSD', 'Kraken', 600.) is None


def test_failed_coinbase_refresh_does_not_relabel_cache_as_new_receipt():
    cached = source._make_source('BTCUSD', 'Coinbase Exchange', 'BTC-USD',
                                [source._bar(300., 100., 102., 99., 101.)], 600., {})
    client = Client(lambda *args: (_ for _ in ()).throw(TimeoutError('blocked')))
    result = source.fetch_coinbase('BTCUSD', 90000., client, budget(), cached=cached)
    assert result['fetched_ts'] == cached['fetched_ts']
    assert result['receipt_observed_ts'] == cached['receipt_observed_ts']
    assert result['captured_ts'] == 600.
    assert result['collection']['refresh_successful_pages'] == 0


def test_conflicting_duplicate_provider_candles_rejected():
    conflicting = candle(89700.)
    conflicting[4] = 100.5
    client = Client(lambda *args: Response([candle(89700.), conflicting]))
    with pytest.raises(ValueError, match='CONFLICTING_DUPLICATE'):
        source.fetch_coinbase('BTCUSD', 90000., client, budget(), days=1)


def test_unavailable_crypto_no_fabrication_and_seed_is_unmodified():
    client = Client(lambda *args: (_ for _ in ()).throw(TimeoutError('unavailable')))
    seed = {'instrument': 'BTCUSD', 'bars': [source._bar(300., 100., 102., 99., 101.)],
            'provider': 'Exported real historical source', 'source_sha256': 'existing'}
    sources, errors, attempts = source.collect_fresh_sources(
        90000., seed_sources=[seed], codes=['BTCUSD', 'ETHUSD'], client_factory=lambda: client)
    assert sources[0]['bars'] is seed['bars']
    assert sources[0]['provider'] == seed['provider']
    assert sources[0]['collection_fallback'] == 'READ_ONLY_EXPORTED_SOURCE'
    assert 'collection_fallback' not in seed
    assert set(errors) == {'ETHUSD'}
    assert [item['provider'] for item in attempts['ETHUSD']['failures']] == [
        'Binance public archive', 'Coinbase Exchange', 'Kraken']


def test_tradfi_failure_retains_provider_cache_without_splicing(tmp_path, monkeypatch):
    cached = source._make_source('NAS100', 'Yahoo Finance via yfinance', '^NDX',
                                [source._bar(300., 100., 102., 99., 101.)], 600., {})
    source._cache_save(tmp_path, cached)
    monkeypatch.setattr(source, 'fetch_yahoo', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError('blocked')))
    sources, errors, attempts = source.collect_fresh_sources(90000., cache_dir=tmp_path, codes=['NAS100'])
    assert not errors
    assert sources[0]['source_sha256'] == cached['source_sha256']
    assert attempts['NAS100']['fallback'] == 'HASH_VERIFIED_SAME_PROVIDER_CACHE'


def test_budget_empty_codes_and_invalid_inputs():
    assert source.collect_fresh_sources(90000., codes=[]) == ([], {}, {})
    for options in ({'days': 61}, {'budget_seconds': 0}, {'codes': ['BTCUSD', 'BTCUSD']}):
        with pytest.raises(ValueError):
            source.collect_fresh_sources(90000., **options)
    with pytest.raises(ValueError):
        source.collect_fresh_sources(float('nan'))
    expired = budget()
    expired.deadline = 0
    with pytest.raises(RuntimeError, match='BUDGET_EXHAUSTED'):
        expired.acquire()


def test_refresh_cli_writes_reproducible_sources_and_full_matrix(tmp_path, monkeypatch):
    import sys
    from scripts import run_mathematical_edge as cli
    seed_path, output, bars_output = [tmp_path/name for name in ('seed.json', 'report.json', 'bars.json')]
    seed_path.write_text(json.dumps({'sources': [], 'errors': {'ETHUSD': 'OLD_ERROR'}}))
    calls = []
    def collect(captured, **kwargs):
        calls.append(kwargs)
        return [], {'BTCUSD': 'PROVIDER_DENIED'}, {'BTCUSD': {'source_available': False}}
    monkeypatch.setattr(source, 'collect_fresh_sources', collect)
    monkeypatch.setattr(sys, 'argv', ['run', '--sources', str(seed_path), '--refresh-sources',
                                    '--sources-output', str(bars_output), '--output', str(output),
                                    '--cache-dir', str(tmp_path/'cache'), '--captured-ts', '90000'])
    cli.main()
    report = json.loads(output.read_text())
    assert len(report['instrument_matrix']) == 13
    assert report['instruments']['BTCUSD']['reason'] == 'PROVIDER_DENIED'
    assert calls[0]['cache_dir'] == str(tmp_path/'cache')
    assert json.loads(bars_output.read_text())['collection_attempts'] == report['collection_attempts']


def test_report_exposes_source_coverage_without_calling_it_economic_proof():
    from scripts.run_mathematical_edge import build_report
    real_schema_fixture = source._make_source('BTCUSD', 'Coinbase Exchange', 'BTC-USD',
                                             [source._bar(300., 100., 102., 99., 101.)], 600., {})
    report = build_report([real_schema_fixture], {}, 600.)
    matrix = report['instrument_matrix']['BTCUSD']
    assert matrix['source_coverage']['bar_count'] == 1
    assert matrix['source_semantics']['currency_basis_mismatch']
    assert matrix['net_economic_proof'] is False


def test_export_does_not_aggregate_five_minutes_across_providers(tmp_path, capsys):
    import base64
    import gzip
    import math
    import sqlite3
    import time
    from scripts.export_mathematical_edge_sources import REMOTE_EXPORT
    db = tmp_path/'mixed.db'
    now = time.time()
    start = math.floor((now-1200)/300)*300
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE g1s_historical_sources (source_id TEXT, source_sha256 TEXT, bars_gzip BLOB, ticker TEXT, provider TEXT, interval TEXT, source_semantics_json TEXT, instrument TEXT, contract_version TEXT, created_ts REAL)')
        connection.execute('CREATE TABLE passive_market_bars (instrument TEXT, bar_start_ts REAL, bar_end_ts REAL, open REAL, high REAL, low REAL, close REAL, source TEXT, kind TEXT, created_ts REAL)')
        for minute in range(10):
            timestamp = start+minute*60
            provider = 'A' if minute < 3 or minute >= 5 else 'B'
            connection.execute('INSERT INTO passive_market_bars VALUES (?,?,?,?,?,?,?,?,?,?)',
                               ('NAS100', timestamp, timestamp+60, 100, 101, 99, 100, provider, 'direct', now))
    original = db.read_bytes()
    exec(REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db', str(db)), {})
    result = json.loads(gzip.decompress(base64.b64decode(capsys.readouterr().out.strip())))
    assert db.read_bytes() == original
    exported = result['sources'][0]
    assert exported['provider'] == 'A'
    assert len(exported['bars']) == 1
    assert exported['bars'][0]['bar_end_ts'] == start+600
    assert exported['source_sha256'] == source.fingerprint(exported['bars'])


def yahoo_history(captured,ends):
    return source._make_source('NAS100','Yahoo Finance via yfinance','^NDX',
        [source._bar(t-300,100.,101.,99.,100.) for t in ends],captured,{})


def test_yahoo_refresh_keeps_older_verified_seed_and_actual_fresh_clocks(monkeypatch):
    captured=1791763200.
    monkeypatch.setattr(source.time,'time',lambda:captured)
    older=yahoo_history(captured,[captured-90*86400,captured-60*86400])
    fresh=yahoo_history(captured,[captured-300,captured])
    monkeypatch.setattr(source,'fetch_yahoo',lambda *a,**k:fresh)
    rows,errors,attempts=source.collect_fresh_sources(captured,seed_sources=[older],
        codes=['NAS100'],budget_seconds=30)
    assert not errors
    row=rows[0]
    assert len(row['bars'])==4
    assert row['fetched_ts']==fresh['fetched_ts']
    assert row['receipt_observed_ts']==fresh['receipt_observed_ts']
    assert row['history_retention']['accepted_sources'][0]['source_sha256']==older['source_sha256']
    assert row['source_sha256']==source.fingerprint(row['bars'])
    assert row['coverage']['gaps_filled'] is False


@pytest.mark.parametrize('change',['hash','ticker','provider','interval','future','duplicate','conflict','synthetic','partial','clock'])
def test_yahoo_retention_excludes_invalid_or_conflicting_older_source(monkeypatch,change):
    from copy import deepcopy
    captured=1791763200.;monkeypatch.setattr(source.time,'time',lambda:captured)
    current=yahoo_history(captured,[captured-300,captured])
    older=yahoo_history(captured,[captured-86400,captured-300])
    older=deepcopy(older)
    if change=='hash':older['source_sha256']='0'*64
    elif change=='ticker':older['ticker']='QQQ'
    elif change=='provider':older['provider']='Binance'
    elif change=='interval':older['interval']='1m'
    elif change=='future':older['bars'][0]['bar_end_ts']=captured+300
    elif change=='duplicate':older['bars'].append(older['bars'][0])
    elif change=='conflict':older['bars'][-1]['close']=100.5
    elif change=='synthetic':older['source_semantics']['synthetic_price_history']=True
    elif change=='clock':older['receipt_observed_ts']=captured+300
    else:older['bars'][0]['bar_start_ts']=older['bars'][0]['bar_end_ts']-60
    if change!='hash':older['source_sha256']=source.fingerprint(older['bars'])
    result=source.retain_yahoo_history(current,[older],captured)
    assert result['bars']==current['bars']
    assert result['history_retention']['excluded_sources']
    assert not result['history_retention']['accepted_sources']


def test_yahoo_retention_deduplicates_and_enforces_fixed_history_bounds(monkeypatch):
    captured=1791763200.;monkeypatch.setattr(source.time,'time',lambda:captured)
    current=yahoo_history(captured,[captured-300*i for i in range(12000)])
    older=yahoo_history(captured,[captured-300*i for i in range(12000,24000)] + [captured-121*86400])
    result=source.retain_yahoo_history(current,[older,older],captured)
    assert len(result['bars'])==20000
    assert min(b['bar_end_ts'] for b in result['bars'])>captured-120*86400
    assert len({b['bar_end_ts'] for b in result['bars']})==20000
    assert result['history_retention']['row_cap_applied'] is True
    assert result['bars'][-1]['bar_end_ts']==captured


def snapshot_database(tmp_path,older_changes=None,snapshots=2):
    import sqlite3,gzip,math,time
    db=tmp_path/'snapshots.db';now=time.time();end=math.floor((now-600)/300)*300
    semantics={'provider':'Yahoo Finance via yfinance','ticker':'^NDX','interval':'5m',
        'bar_timestamp_semantics':'provider timestamp is interval start; T0 is bar_start+300s'}
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE g1s_historical_sources (source_id TEXT, source_sha256 TEXT, bars_gzip BLOB, ticker TEXT, provider TEXT, interval TEXT, source_semantics_json TEXT, instrument TEXT, contract_version TEXT, created_ts REAL)')
        c.execute('CREATE TABLE passive_market_bars (instrument TEXT, bar_start_ts REAL, bar_end_ts REAL, open REAL, high REAL, low REAL, close REAL, source TEXT, kind TEXT, created_ts REAL)')
        for index in range(snapshots):
            bars=[source._bar(end-300*(index+slot+1),100.,101.,99.,100.) for slot in range(3)]
            values={'ticker':'^NDX','provider':'Yahoo Finance via yfinance','interval':'5m',
                'source_semantics_json':json.dumps(semantics)}
            if index==1 and older_changes:
                if older_changes=='conflict':bars[0]['close']=100.5
                elif older_changes=='malformed':bars[0]['high']=float('inf')
                elif older_changes=='duplicate':bars.append(bars[0])
                elif older_changes=='semantics':values['source_semantics_json']='{}'
                else:values[older_changes]='other'
            raw=json.dumps(bars,sort_keys=True,separators=(',',':')).encode()
            import hashlib
            digest=hashlib.sha256(raw).hexdigest()
            if index==1 and older_changes=='hash':digest='0'*64
            c.execute('INSERT INTO g1s_historical_sources VALUES (?,?,?,?,?,?,?,?,?,?)',
                (f'snapshot-{index}',digest,gzip.compress(raw),values['ticker'],values['provider'],
                 values['interval'],values['source_semantics_json'],'NAS100','g1s-historical-wf-real-bars-v1',now-index*300))
    return db,end


def run_snapshot_export(db,capsys):
    import base64,gzip
    from scripts.export_mathematical_edge_sources import REMOTE_EXPORT
    before=db.read_bytes()
    exec(REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db',str(db)),{})
    result=json.loads(gzip.decompress(base64.b64decode(capsys.readouterr().out.strip())))
    assert db.read_bytes()==before
    return next(row for row in result['sources'] if row['instrument']=='NAS100')


def test_export_reuses_existing_same_series_snapshots_without_database_writes(tmp_path,capsys):
    db,end=snapshot_database(tmp_path)
    row=run_snapshot_export(db,capsys)
    assert len(row['bars'])==4
    proof=row['historical_snapshot_history']
    assert len(proof['accepted_sources'])==2
    assert {p['source_id'] for p in proof['accepted_sources']}=={'snapshot-0','snapshot-1'}
    assert row['source_sha256']==source.fingerprint(row['bars'])
    assert row['mixed_provider_splicing'] is False


@pytest.mark.parametrize('change',['ticker','provider','interval','semantics','hash','conflict','malformed','duplicate'])
def test_export_excludes_bad_or_discordant_older_snapshot(tmp_path,capsys,change):
    db,end=snapshot_database(tmp_path,change)
    row=run_snapshot_export(db,capsys)
    assert len(row['bars'])==3
    assert row['historical_snapshot_history']['excluded_sources']
    assert len(row['historical_snapshot_history']['accepted_sources'])==1


def test_export_limits_snapshot_reads_to_eight_without_erasing_primary(tmp_path,capsys):
    db,end=snapshot_database(tmp_path,snapshots=10)
    row=run_snapshot_export(db,capsys)
    assert row['historical_snapshot_history']['snapshots_examined']==8
    assert row['historical_snapshot_history']['history_complete'] is False
    assert row['historical_snapshot_history']['reason']=='HISTORICAL_SNAPSHOT_READ_BOUND'


def test_snapshot_history_budget_preserves_verified_primary(tmp_path,capsys,monkeypatch):
    import time
    db,end=snapshot_database(tmp_path,snapshots=3)
    # Tiny indexed DB needs no progress-handler callback: stop optional older
    # reads immediately, while leaving the original25s SQL budget intact.
    calls=iter([1.,10.])
    monkeypatch.setattr(time,'monotonic',lambda:next(calls,10.))
    row=run_snapshot_export(db,capsys)
    assert len(row['bars'])==3
    proof=row['historical_snapshot_history']
    assert proof['snapshots_examined']==1
    assert proof['history_complete'] is False
    assert proof['reason']=='HISTORICAL_SNAPSHOT_READ_BUDGET'


def test_partial_row_in_old_snapshot_is_excluded_after_original_hash_verification(tmp_path,capsys):
    import sqlite3,gzip,hashlib
    db,end=snapshot_database(tmp_path)
    with sqlite3.connect(db) as c:
        raw=json.loads(gzip.decompress(c.execute("SELECT bars_gzip FROM g1s_historical_sources WHERE source_id='snapshot-1'").fetchone()[0]))
        raw.append(source._bar(end+300,100.,101.,99.,100.))
        text=json.dumps(raw,sort_keys=True,separators=(',',':')).encode()
        c.execute("UPDATE g1s_historical_sources SET created_ts=?,bars_gzip=?,source_sha256=? WHERE source_id='snapshot-1'",
            (end+100,gzip.compress(text),hashlib.sha256(text).hexdigest()))
    row=run_snapshot_export(db,capsys)
    assert len(row['bars'])==4
    assert max(b['bar_end_ts'] for b in row['bars'])<end+300
    assert row['historical_snapshot_history']['partial_rows_excluded']==1


@pytest.mark.parametrize('field,value',[('provider','DifferentProvider'),('ticker','QQQ'),
    ('interval','1h'),('auto_adjust',True),('provider_quote_currency','EUR'),
    ('completed_bars_only_for_features',False)])
def test_yahoo_history_rejects_conflicting_semantic_declarations(monkeypatch,field,value):
    captured=1791763200.;monkeypatch.setattr(source.time,'time',lambda:captured)
    current=yahoo_history(captured,[captured-300,captured])
    older=yahoo_history(captured,[captured-86400])
    older['source_semantics'][field]=value
    result=source.retain_yahoo_history(current,[older],captured)
    assert result['bars']==current['bars']
    assert result['history_retention']['excluded_sources']


def test_yahoo_archive_bars_must_be_completed_at_original_observation(monkeypatch):
    captured=1791763200.;monkeypatch.setattr(source.time,'time',lambda:captured)
    current=yahoo_history(captured,[captured-300,captured])
    older=yahoo_history(captured,[captured-86400])
    for key in ('captured_ts','fetched_ts','receipt_observed_ts'):older[key]=captured-2*86400
    result=source.retain_yahoo_history(current,[older],captured)
    assert result['bars']==current['bars']
    assert result['history_retention']['excluded_sources'][0]['reason']=='HISTORY_BAR_AFTER_ORIGINAL_OBSERVATION'


def test_second_yahoo_refresh_preserves_bounded_original_receipt_manifest(monkeypatch):
    captured=1791763200.;monkeypatch.setattr(source.time,'time',lambda:captured)
    older=yahoo_history(captured,[captured-86400]);older['source_id']='original-seed-receipt'
    first=source.retain_yahoo_history(yahoo_history(captured,[captured-300]),[older],captured)
    second=source.retain_yahoo_history(yahoo_history(captured,[captured]),[first],captured)
    assert len(second['bars'])==3
    assert 'original-seed-receipt' in json.dumps(second)
    manifest=second['history_retention']['original_sources']
    original=next(p for p in manifest if p['source_id']=='original-seed-receipt')
    assert original['source_sha256']==older['source_sha256']
    assert original['receipt_observed_ts']==older['receipt_observed_ts']
    assert len({p['source_sha256'] for p in manifest})==len(manifest)
    assert len(manifest)<=128


@pytest.mark.parametrize('corruption',['gzip','synthetic'])
def test_optional_snapshot_rejects_deflate_corruption_and_original_synthetic_markers(tmp_path,capsys,corruption):
    import sqlite3,gzip,hashlib
    db,end=snapshot_database(tmp_path)
    with sqlite3.connect(db) as c:
        raw=json.loads(gzip.decompress(c.execute("SELECT bars_gzip FROM g1s_historical_sources WHERE source_id='snapshot-1'").fetchone()[0]))
        if corruption=='synthetic':raw[0]['synthetic']=True
        text=json.dumps(raw,sort_keys=True,separators=(',',':')).encode()
        compressed=gzip.compress(text) if corruption=='synthetic' else bytes.fromhex('1f8b0800000000000003ff')+b'\x00'*8
        c.execute("UPDATE g1s_historical_sources SET bars_gzip=?,source_sha256=? WHERE source_id='snapshot-1'",
            (compressed,hashlib.sha256(text).hexdigest()))
    row=run_snapshot_export(db,capsys)
    assert len(row['bars'])==3
    assert row['historical_snapshot_history']['excluded_sources']
    assert len(row['historical_snapshot_history']['accepted_sources'])==1


def test_known_g1s_and_current_yahoo_timestamp_contracts_remain_compatible(monkeypatch):
    captured=1791763200.;monkeypatch.setattr(source.time,'time',lambda:captured)
    current=yahoo_history(captured,[captured])
    older=yahoo_history(captured,[captured-86400])
    older['source_semantics']={'provider':older['provider'],'ticker':older['ticker'],'interval':'5m',
        'bar_timestamp_semantics':'provider timestamp is interval start; T0 is bar_start+300s',
        'completed_bars_only_for_features':True,'historical_price_is_yahoo_series':True}
    result=source.retain_yahoo_history(current,[older],captured)
    assert len(result['bars'])==2
    assert not result['history_retention']['excluded_sources']


def test_export_uses_original_fetch_cutoff_when_present(tmp_path,capsys):
    import sqlite3
    db,end=snapshot_database(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE g1s_historical_sources ADD COLUMN fetched_ts REAL')
        c.execute("UPDATE g1s_historical_sources SET fetched_ts=? WHERE source_id='snapshot-1'",(end-700,))
    row=run_snapshot_export(db,capsys)
    assert len(row['bars'])==4
    assert row['historical_snapshot_history']['partial_rows_excluded']==2
    old=next(p for p in row['historical_snapshot_history']['accepted_sources'] if p['source_id']=='snapshot-1')
    assert old['fetched_ts']==end-700


def test_fresh_yahoo_receipt_after_training_cutoff_preserves_actual_clock(monkeypatch):
    captured=1791763200.;monkeypatch.setattr(source.time,'time',lambda:captured)
    older=yahoo_history(captured,[captured-86400])
    current=yahoo_history(captured,[captured])
    current['fetched_ts']=captured+.1;current['receipt_observed_ts']=captured+.2
    monkeypatch.setattr(source.time,'time',lambda:captured+.3)
    result=source.retain_yahoo_history(current,[older],captured)
    assert len(result['bars'])==2
    assert result['receipt_observed_ts']==captured+.2
    assert result['captured_ts']==captured
