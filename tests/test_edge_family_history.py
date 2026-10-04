"""Deterministic, received-only history contracts; fixtures are not source data."""
from copy import deepcopy
import importlib
import json
import math

import pytest

T0 = 1_800_000_000.


def history_module():
    # Assert the missing interface as a behavior failure before implementation.
    assert importlib.util.find_spec('seiltanzer.edge_family_history'), 'bounded historical feature producer missing'
    return importlib.import_module('seiltanzer.edge_family_history')


def position():
    identity = dict(series_id='CFTC088691', kind='cot_report', unit='contracts',
        category='NONCOMMERCIAL_LONG_MINUS_SHORT_FUTURES_ONLY', venue='CFTC', body_sha256='a' * 64)
    proof = dict(identity, source_id='previous', received_ts=T0 - 10, published_at=T0 - 20, source_verified=True)
    return dict(source_id='current', source_verified=True, instrument='CFTC088691',
        kind='cot_report', observed_ts=T0 - 100, report_ts=T0 - 100,
        net_position=-20, published_at=T0 - 20, available_at=T0 - 10,
        position_history_contract='edge-family-position-history-v1', position_series=identity,
        position_change_history=[dict(report_ts=T0 - 604900, net_position=-50, available_at=T0 - 10, provenance=proof)])


def intermarket(target='BTCUSD'):
    histories = []
    for index, asset in enumerate(('BTC', 'ETH', 'SOL')):
        histories.append(dict(source_id='actual-' + asset, provider='COINBASE', symbol=asset + '-USD',
            base_currency=asset, quote_currency='USD', orientation='direct', body_sha256=str(index + 1) * 64,
            available_at=T0, bars=[[T0 - 360 + n * 60, 100 + n * (index - 1), T0] for n in range(7)]))
    return dict(source_id='synced', source_verified=True, instrument=target,
        observed_ts=T0, available_at=T0, dependency_group='intermarket:coinbase:5min-completed',
        intermarket_history_contract='edge-family-intermarket-history-v1', historical_series=histories)


def test_signed_position_delta_previous_age_and_immutable_proof():
    source = position()
    original = deepcopy(source)
    result = history_module().position_history_features(source, T0)
    assert source == original
    assert result['features'] == {'positioning.cot_report.change': 30, 'positioning.cot_report.previous_report_age_days': 7}
    meta = result['feature_provenance']['positioning.cot_report.change']
    assert meta['window_seconds'] == 604800
    assert set(meta['supporting_source_ids']) == {'current', 'previous'}
    assert len(meta['constituent_provenance']) == 2


def test_lagged_returns_ordered_pairs_breadth_and_windows():
    source = intermarket()
    original = deepcopy(source)
    result = history_module().intermarket_history_features(source, T0)
    assert source == original
    features = result['features']
    returns = {asset: math.log((100 + 5 * (index - 1)) / 100) for index, asset in enumerate(('BTC', 'ETH', 'SOL'))}
    for asset, value in returns.items():
        name = 'intermarket.COINBASE' + asset + '-USD.return_5m_lag_1m'
        assert features[name] == pytest.approx(value)
        assert result['feature_provenance'][name]['window_seconds'] == 360
    pair = 'intermarket.COINBASEBTC-USD.COINBASEETH-USD.relative_return_5m'
    assert features[pair] == pytest.approx(returns['BTC'] - returns['ETH'])
    assert result['feature_provenance'][pair]['window_seconds'] == 300
    assert len(result['feature_provenance'][pair]['constituent_provenance']) == 2
    assert features['intermarket.related_crypto.breadth_up_fraction_5m'] == .5
    assert features['intermarket.related_crypto.observed_n'] == 2
    assert features['intermarket.related_crypto.expected_n'] == 2
    assert features['intermarket.related_crypto.coverage'] == 1


@pytest.mark.parametrize('change', [
    lambda s: s.update(position_history_contract=None),
    lambda s: s.pop('position_series'),
    lambda s: s.update(position_history_contract='unknown'),
    lambda s: s['position_series'].update(kind='fund_flow'),
    lambda s: s['position_series'].update(body_sha256='x' * 64),
    lambda s: s['position_change_history'][0]['provenance'].update(unit='USD'),
    lambda s: s['position_change_history'][0]['provenance'].update(category='OTHER'),
    lambda s: s['position_change_history'][0]['provenance'].update(venue='OTHER'),
    lambda s: s['position_change_history'][0]['provenance'].update(series_id='OTHER'),
    lambda s: s['position_change_history'][0]['provenance'].update(received_ts=T0 + 1),
    lambda s: s['position_change_history'][0]['provenance'].update(source_verified=False),
    lambda s: s['position_change_history'][0]['provenance'].update(synthetic=True),
    lambda s: s['position_change_history'][0].update(net_position=True),
    lambda s: s.update(net_position=float('nan')),
    lambda s: s['position_change_history'].append(dict(s['position_change_history'][0], net_position=1)),
    lambda s: s.update(position_change_history=s['position_change_history'] * 9),
    lambda s: s['position_series'].update(extra='é' * 2048),
])
def test_position_invalid_extension_has_no_features(change):
    source = position(); change(source)
    result = history_module().position_history_features(source, T0)
    assert result['features'] == {} and result['rejections']


@pytest.mark.parametrize('change', [
    lambda s: s.update(intermarket_history_contract=None),
    lambda s: s.pop('historical_series'),
    lambda s: s['historical_series'][0].update(provider='OTHER'),
    lambda s: s['historical_series'][0].update(symbol='BTC-USDT'),
    lambda s: s['historical_series'][0].update(quote_currency='USDT'),
    lambda s: s['historical_series'][0].update(base_currency='ETH'),
    lambda s: s['historical_series'][0].update(orientation='inverse'),
    lambda s: s['historical_series'][0]['bars'][0].__setitem__(1, 0),
    lambda s: s['historical_series'][0]['bars'][0].__setitem__(1, True),
    lambda s: s['historical_series'][0]['bars'][0].__setitem__(0, T0 - 361),
    lambda s: s['historical_series'][0]['bars'][0].__setitem__(2, T0 + 1),
    lambda s: s['historical_series'][0].update(available_at=T0 + 1),
    lambda s: s['historical_series'].append(deepcopy(s['historical_series'][0])),
    lambda s: s['historical_series'][0].update(synthetic=True),
    lambda s: s['historical_series'][0].update(extra='x' * 4096),
])
def test_intermarket_invalid_extension_fails_closed(change):
    source = intermarket(); change(source)
    result = history_module().intermarket_history_features(source, T0)
    assert result['features'] == {} and result['rejections']


def test_child_applicability_is_preserved_and_mismatch_rejected():
    source = intermarket()
    source['historical_series'][1].update(context_only=True, horizon_minutes=240)
    result = history_module().intermarket_history_features(source, T0)
    meta = result['feature_provenance']['intermarket.COINBASEETH-USD.return_5m_lag_1m']
    assert meta['applicability_provenance'][0]['context_only'] is True
    assert meta['applicability_provenance'][0]['horizon_minutes'] == 240
    source['horizon_minutes'] = 60
    assert not history_module().intermarket_history_features(source, T0)['features']


def test_missing_peer_remains_missing_and_no_zero_breadth():
    source = intermarket()
    source['historical_series'] = source['historical_series'][:2]
    result = history_module().intermarket_history_features(source, T0)
    assert 'intermarket.related_crypto.coverage' not in result['features']
    source['instrument'] = 'NAS100'
    result = history_module().intermarket_history_features(source, T0)
    assert result['features']['intermarket.related_crypto.coverage'] == pytest.approx(2 / 3)


def test_legacy_absence_and_identical_position_duplicates():
    module = history_module()
    assert module.position_history_features({}, T0) == dict(features={}, feature_provenance={}, rejections=[])
    assert module.intermarket_history_features({}, T0) == dict(features={}, feature_provenance={}, rejections=[])
    source = position(); source['position_change_history'] *= 2
    assert module.position_history_features(source, T0)['features']['positioning.cot_report.change'] == 30


def test_adapter_recomputes_preserves_legacy_and_refuses_partial_proof():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    source = intermarket('NAS100')
    source['linked_returns'] = [dict(leader='ETHUSD', start_ts=T0 - 300, end_ts=T0, start_price=100, end_price=101)]
    source['features'] = {'intermarket.COINBASEETH-USD.return_5m_lag_1m': 999}
    snapshot = dict(instrument='NAS100', captured_ts=T0, edge_family_sources={'intermarket': source})
    row = build_edge_family_evidence(snapshot)['families']['intermarket']
    assert row['features']['intermarket.COINBASEETH-USD.return_5m_lag_1m'] == 0
    assert row['features']['intermarket.ETHUSD.return'] == pytest.approx(math.log(1.01))
    assert row['feature_provenance']['intermarket.COINBASEETH-USD.return_5m_lag_1m']['dependency_group'] == source['dependency_group']
    source['intermarket_history_contract'] = None
    row = build_edge_family_evidence(snapshot)['families']['intermarket']
    assert not row['features'] and row['rejected_sources']


def test_position_adapter_mapping_still_required_and_current_legacy_retained():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    source = position()
    snapshot = dict(instrument='CFTC088691', captured_ts=T0, edge_family_sources={'positioning': source})
    row = build_edge_family_evidence(snapshot)['families']['positioning']
    assert row['features']['positioning.cot_report.change'] == 30
    assert row['features']['positioning.net'] == -20
    snapshot['instrument'] = 'XAU'
    assert not build_edge_family_evidence(snapshot)['families']['positioning']['features']


@pytest.mark.parametrize('declaration', [dict(context_only=True), dict(horizon_minutes=15), dict(synthetic=True)])
def test_adapter_cannot_erase_constituent_scope(declaration):
    from seiltanzer.edge_family_adapters import _applicable_feature, build_edge_family_evidence
    source = intermarket('NAS100')
    source['historical_series'][1].update(declaration)
    row = build_edge_family_evidence(dict(instrument='NAS100', captured_ts=T0, edge_family_sources={'intermarket': source}))['families']['intermarket']
    name = 'intermarket.COINBASEETH-USD.return_5m_lag_1m'
    if declaration.get('synthetic'):
        assert not row['features']
    else:
        assert not _applicable_feature(row['feature_provenance'][name], 240, 240)


def test_runtime_rejects_invalid_constituent_before_freezing(tmp_path):
    from test_edge_family_source_runtime import bundle, write_bundle, load
    payload = bundle()
    source = intermarket()
    # Valid legacy link must never rescue an explicit malformed extension.
    source['linked_returns'] = [dict(leader='ETHUSD', start_ts=T0 - 300, end_ts=T0, start_price=100, end_price=101)]
    payload['captured_ts'] = T0
    payload['instruments']['BTCUSD']['edge_family_sources'] = {'intermarket': [source]}
    source['historical_series'][1]['quote_currency'] = 'USDT'
    write_bundle(tmp_path, payload)
    assert load(tmp_path)['edge_family_sources'] == {}


def test_bundle_capture_is_binding_to_each_history_receipt(tmp_path):
    from test_edge_family_source_runtime import bundle, write_bundle, load
    payload = bundle()
    payload['captured_ts'] = T0 - 2
    source = intermarket()
    # Move observed endpoint and bars before capture; constituent receipt alone
    # remains later than immutable capture and earlier than review.
    source['observed_ts'] = T0 - 60
    source['available_at'] = T0 - 2
    for item in source['historical_series']:
        item['bars'] = [[bar[0] - 60, bar[1], T0 - 1] for bar in item['bars']]
        item['available_at'] = T0 - 1
    payload['instruments']['BTCUSD']['edge_family_sources'] = {'intermarket': [source]}
    write_bundle(tmp_path, payload)
    assert not load(tmp_path)['edge_family_sources']


def test_real_collector_history_passes_unchanged_consumer_budget_and_dataset(tmp_path):
    from test_edge_family_sources import build_bundle, candles, cot, calendar, book, tape, T0 as COLLECT_T0
    from test_edge_family_source_runtime import SHA, write_bundle
    from test_edge_family_dataset import snapshot, record, archive, build, T0 as DATASET_T0
    from types import SimpleNamespace
    from seiltanzer.edge_family_source_runtime import MAX_SELECTED_BYTES, PUBLICATION, load_family_source_context
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    calls = []
    def fetch(url):
        calls.append(url)
        return cot() if 'cftc.gov' in url else calendar() if 'nyse.com' in url else book() if 'book?' in url else tape() if 'trades?' in url else candles()
    payload = build_bundle(instruments=('NAS100', 'XAU', 'BTCUSD'), fetch=fetch, clock=lambda: COLLECT_T0)
    assert len(calls) == 12
    payload.update(publication_contract_version=PUBLICATION, published_for_sha=SHA)
    write_bundle(tmp_path, payload, mtime=COLLECT_T0)
    runtime_snapshot = dict(instrument='NAS100', captured_ts=COLLECT_T0)
    loaded = load_family_source_context(SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)), runtime_snapshot, SHA)
    assert loaded['edge_family_source_bundle_audit']['available']
    assert len(json.dumps(loaded['edge_family_sources'], allow_nan=False).encode()) <= MAX_SELECTED_BYTES == 8000
    row = build_edge_family_evidence(dict(runtime_snapshot, **loaded))['families']['intermarket']
    assert row['features']['intermarket.COINBASEETH-USD.return_5m_lag_1m']
    # Rebase received fixture clocks prospectively onto the dataset review.
    source = deepcopy(loaded['edge_family_sources']['intermarket'][0])
    shift = DATASET_T0 - COLLECT_T0
    def rebase(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key.endswith('_ts') or key.endswith('_at')) and isinstance(item, (int, float)):
                    value[key] = item + shift
                else: rebase(item)
        elif isinstance(value, list):
            for item in value: rebase(item)
    rebase(source)
    for series in source['historical_series']:
        for bar in series['bars']:
            bar[0] += shift; bar[2] += shift
    value = snapshot(); value['edge_family_sources'] = {'intermarket': [source]}
    original = deepcopy(value)
    rows = build(archive(record(value)))['rows']
    assert rows and value == original
    assert rows[0]['feature_windows_sec']['intermarket.COINBASEETH-USD.return_5m_lag_1m'] == 360
    changed = record(value); changed['path_points'][-1].update(r=.3, price=103.)
    later = build(archive(changed))['rows']
    assert rows[0]['features'] == later[0]['features']
    assert rows[0]['feature_provenance'] == later[0]['feature_provenance']


def test_collector_cot_has_actual_body_hash_previous_receipt_and_no_mapping_promotion():
    import hashlib
    from test_edge_family_sources import cot, T0 as RECEIPT
    from seiltanzer.edge_family_sources import parse_cot
    source = parse_cot(cot(), contract='088691', source_id='actual', receipt=RECEIPT)
    assert source['position_series']['body_sha256'] == hashlib.sha256(cot()).hexdigest()
    assert source['position_series']['unit'] == 'contracts'
    assert source['position_change_history'][0]['provenance']['published_at'] == RECEIPT
    assert source['historical_positions'][0]['available_at'] == RECEIPT


def test_collector_omits_gapped_received_series_without_reconstructing():
    from test_edge_family_sources import build_bundle, candles, calendar, book, tape, T0 as RECEIPT
    def fetch(url):
        if 'nyse.com' in url: return calendar()
        if 'book?' in url: return book()
        if 'trades?' in url: return tape()
        rows = json.loads(candles())
        if 'ETH-USD' in url: rows = [row for row in rows if row[0] != RECEIPT - 180]
        return json.dumps(rows).encode()
    payload = build_bundle(instruments=('NAS100',), fetch=fetch, clock=lambda: RECEIPT)
    source = payload['instruments']['NAS100']['edge_family_sources']['intermarket'][0]
    assert {item['symbol'] for item in source['historical_series']} == {'BTC-USD', 'SOL-USD'}
    assert any('HISTORY' in error['reason'] for error in payload['errors'])


def test_position_child_declaration_cannot_be_overridden_by_observation():
    source = position()
    source['position_change_history'][0]['context_only'] = False
    source['position_change_history'][0]['provenance']['context_only'] = True
    result = history_module().position_history_features(source, T0)
    assert not result['features'] and result['rejections']


def test_legacy_facts_cannot_erase_history_constituent_applicability():
    from seiltanzer.edge_family_adapters import _applicable_feature, build_edge_family_evidence
    source = intermarket('NAS100')
    source['linked_returns'] = [dict(leader='ETHUSD', start_ts=T0 - 300, end_ts=T0, start_price=100, end_price=101)]
    source['historical_series'][1]['context_only'] = True
    row = build_edge_family_evidence(dict(instrument='NAS100', captured_ts=T0, edge_family_sources={'intermarket': source}))['families']['intermarket']
    assert not _applicable_feature(row['feature_provenance']['intermarket.ETHUSD.return'], 240, 240)


def test_overflowing_signed_delta_fails_closed():
    source = position()
    source['net_position'] = 1e308
    source['position_change_history'][0]['net_position'] = -1e308
    assert not history_module().position_history_features(source, T0)['features']


@pytest.mark.parametrize('producer,source', [('position_history_features', position), ('intermarket_history_features', intermarket)])
def test_deep_extension_rejects_without_exception(producer, source):
    source = source()
    nested = {}
    for _ in range(9): nested = {'nested': nested}
    root = source['position_series'] if 'position_series' in source else source['historical_series'][0]
    root['extra'] = nested
    result = getattr(history_module(), producer)(source, T0)
    assert not result['features'] and result['rejections']


def test_dataset_child_scope_excludes_original_source_before_normalization():
    from test_edge_family_dataset import snapshot, record, archive, build, T0 as REVIEW_T0
    source = intermarket('NAS100')
    shift = REVIEW_T0 - T0
    source.update(observed_ts=REVIEW_T0, available_at=REVIEW_T0)
    for series in source['historical_series']:
        series['available_at'] += shift
        for bar in series['bars']:
            bar[0] += shift; bar[2] += shift
    source['historical_series'][1]['horizon_minutes'] = 15
    value = snapshot(); value['edge_family_sources'] = {'intermarket': [source]}
    result = build(archive(record(value)))
    assert not any(row['family_id'] == 'intermarket' for row in result['rows'])
    assert any(item['reason'] == 'FEATURE_SOURCE_HORIZON_MISMATCH' for item in result['exclusions'])


def test_pure_intermarket_provenance_retains_parent_scope():
    from seiltanzer.edge_family_adapters import _applicable_feature
    source = intermarket()
    source['context_only'] = True
    meta = history_module().intermarket_history_features(source, T0)['feature_provenance']['intermarket.COINBASEETH-USD.return_5m_lag_1m']
    assert not _applicable_feature(meta, 240, 240)


def test_legacy_availability_diagnostic_names_missing_history_proof():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    source = intermarket('NAS100')
    del source['intermarket_history_contract']; del source['historical_series']
    source['linked_returns'] = [dict(leader='ETHUSD', start_ts=T0 - 300, end_ts=T0, start_price=100, end_price=101)]
    row = build_edge_family_evidence(dict(instrument='NAS100', captured_ts=T0, edge_family_sources={'intermarket': source}))['families']['intermarket']
    assert row['available'] and not row['forecast_available']
    assert row['history_diagnostics'][0]['reason'] == 'INTERMARKET_HISTORY_PROOF_UNAVAILABLE'


def test_large_original_cot_history_still_refuses_selected_budget(tmp_path):
    from datetime import datetime, timezone
    from test_edge_family_source_runtime import bundle, write_bundle, SHA
    from types import SimpleNamespace
    from seiltanzer.edge_family_sources import parse_cot
    from seiltanzer.edge_family_source_runtime import load_family_source_context
    original = [dict(cftc_contract_market_code='088691', noncomm_positions_long_all='100',
        noncomm_positions_short_all='30', report_date_as_yyyy_mm_dd=datetime.fromtimestamp(T0 - index * 86400 - 60, timezone.utc).isoformat())
        for index in range(100)]
    source = parse_cot(json.dumps(original).encode(), contract='088691', receipt=T0, source_id='original-100-report-response')
    assert len(source['historical_positions']) == 99
    payload = bundle(); payload['captured_ts'] = T0
    payload['instruments'] = {'CFTC088691': {'edge_family_sources': {'positioning': [source]}}}
    write_bundle(tmp_path, payload)
    result = load_family_source_context(SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)),
        dict(instrument='CFTC088691', captured_ts=T0), SHA)
    assert result['edge_family_sources'] == {}
    assert result['edge_family_source_bundle_audit']['reason'] == 'SELECTED_SOURCE_FACTS_EXCEED_REVIEW_BYTE_BUDGET'
    assert len(source['historical_positions']) == 99


def test_pure_history_checks_parent_publication_clock():
    source = intermarket()
    source['published_at'] = T0 + 1
    assert not history_module().intermarket_history_features(source, T0)['features']


@pytest.mark.parametrize('target', ['ETHUSD', 'ETHUSDT'])
def test_mapped_review_target_is_excluded_from_pure_and_adapter_breadth(target):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    source = intermarket('BTCUSD')
    source['historical_series'][1]['bars'] = [[end, 100 + index, receipt]
        for index, (end, close, receipt) in enumerate(source['historical_series'][1]['bars'])]
    source['proxy_mapping'] = dict(validated=True, source_instrument='BTCUSD',
        target_instrument=target, mapping_id='actual-map', validated_at=T0 - 10)
    original = deepcopy(source)
    pure = history_module().intermarket_history_features(source, T0)
    row = build_edge_family_evidence(dict(instrument=target, captured_ts=T0,
        edge_family_sources={'intermarket': source}))['families']['intermarket']
    name = 'intermarket.related_crypto.breadth_up_fraction_5m'
    for result in (pure, row):
        assert result['features'][name] == .5
        meta = result['feature_provenance'][name]
        assert meta['excluded_own_asset'] == 'ETH-USD'
        assert meta['related_peer_symbols'] == ['BTC-USD', 'SOL-USD']
        assert {proof['symbol'] for proof in meta['constituent_provenance']} == {'BTC-USD', 'SOL-USD'}
        assert {proof['provider'] for proof in meta['constituent_provenance']} == {'COINBASE'}
        assert {proof['quote_currency'] for proof in meta['constituent_provenance']} == {'USD'}
    assert row['feature_provenance'][name]['source_instrument'] == 'BTCUSD'
    assert source == original
    # When admitted directly for its source instrument, adapter exclusion uses
    # that actual review target rather than an unused alternate mapping.
    direct = build_edge_family_evidence(dict(instrument='BTCUSD', captured_ts=T0,
        edge_family_sources={'intermarket': source}))['families']['intermarket']
    assert direct['features'][name] == 1
    assert direct['feature_provenance'][name]['excluded_own_asset'] == 'BTC-USD'


@pytest.mark.parametrize('location', ['root', 'identity', 'observation', 'proof'])
def test_context_only_position_fails_pure_adapter_and_capture_admission(tmp_path, location):
    from test_edge_family_source_runtime import bundle, write_bundle, SHA
    from types import SimpleNamespace
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    from seiltanzer.edge_family_source_runtime import load_family_source_context
    source = position()
    target = {'root': source, 'identity': source['position_series'],
        'observation': source['position_change_history'][0],
        'proof': source['position_change_history'][0]['provenance']}[location]
    target['context_only'] = True
    original = deepcopy(source)
    result = history_module().position_history_features(source, T0)
    assert not result['features'] and not result['feature_provenance']
    assert result['rejections'][0]['reason'] == 'CONTEXT_ONLY_POSITION_HISTORY_NOT_ADMISSIBLE'
    snapshot = dict(instrument='CFTC088691', captured_ts=T0, edge_family_sources={'positioning': [source]})
    row = build_edge_family_evidence(snapshot)['families']['positioning']
    assert not row['features'] and row['rejected_sources']
    payload = bundle(); payload['captured_ts'] = T0
    payload['instruments'] = {'CFTC088691': {'edge_family_sources': {'positioning': [source]}}}
    write_bundle(tmp_path, payload)
    loaded = load_family_source_context(SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)), snapshot, SHA)
    assert not loaded['edge_family_sources']
    assert loaded['edge_family_source_bundle_audit']['rejected_sources'][0]['reason'] == 'CONTEXT_ONLY_POSITION_HISTORY_NOT_ADMISSIBLE'
    assert source == original
