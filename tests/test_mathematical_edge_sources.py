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
    assert len(attempts['ETHUSD']['failures']) == 2


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
