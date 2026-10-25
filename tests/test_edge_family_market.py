"""Behavioral contracts for historical market forecasts, not trade-profit labels."""
from copy import deepcopy
import importlib
import math

import numpy as np
import pytest

from seiltanzer.mathematical_edge import fingerprint


T0 = 1791504000.


@pytest.mark.parametrize('patch,failed', [
    ({},'provider'),
    ({'provider':'Yahoo','source_symbol':'WRONG'},'source_symbol'),
    ({'provider':'Yahoo','source_symbol':'^NDX','target_instrument':'NAS100',
      'source_verified':True,'observed_ts':T0,'available_at':T0+1},'received_after_cutoff'),
    ({'provider':'Yahoo','source_symbol':'^NDX','target_instrument':'NAS100',
      'source_verified':True,'observed_ts':T0-901,'available_at':T0-1},'stale_observation'),
])
def test_source_rejection_reports_actual_failed_checks_without_admitting_models(patch,failed):
    from types import SimpleNamespace
    from seiltanzer.edge_family_market import attach_market_family_context
    s={'captured_ts':T0,'strategy':{'instrument':'NAS100'}}
    engine=SimpleNamespace(market=SimpleNamespace(instrument_code='NAS100',demo=False,
        intraday_source_authority=patch,intraday_ohlcv=[]))
    attach_market_family_context(engine,s)
    c=s['edge_family_market_context']
    assert c['reason']=='MARKET_SOURCE_IDENTITY_OR_CLOCK_INVALID'
    assert failed in c['failed_source_checks']
    assert c['available'] is False and c['families']=={}


def market():
    assert importlib.util.find_spec('seiltanzer.edge_family_market') is not None, 'historical market-family trainer missing'
    return importlib.import_module('seiltanzer.edge_family_market')


def source(code='NAS100', n=1800):
    price, bars = 100., []
    for i in range(n):
        end = T0 - (n - 1 - i) * 300
        move = .0007 * math.sin(2 * math.pi * end / 86400) + .0001 * math.cos(i)
        close = price * math.exp(move)
        bars.append(dict(bar_end_ts=end, open=price, high=max(price, close)*1.00001,
                         low=min(price, close)*.99999, close=close))
        price = close
    ticker = {'NAS100': '^NDX', 'BTCUSD': 'BTC-USD', 'ETHUSD': 'ETH-USD', 'SOLUSD': 'SOL-USD'}[code]
    return dict(instrument=code, provider='Coinbase Exchange' if '-' in ticker else 'Yahoo Finance via yfinance',
                ticker=ticker, interval='5m', source_sha256=fingerprint(bars), bars=bars)


def test_real_session_training_returns_separate_forecast_not_fake_action_profit():
    report = market().train_market_families([source()], T0, horizons=(15,))
    row = report['NAS100']['session']
    assert row['status'] == 'TRAINED'
    assert row['training_n'] >= 180
    assert row['heads']['direction']['working_supported']
    assert row['heads']['direction']['test_n'] >= 10
    assert row['target_kind'] == 'OBSERVED_MARKET_DIRECTION_AND_MOVEMENT'
    assert row['net_economic_proof'] is False
    assert 'action_models' not in row
    assert row['model_sha256'] == fingerprint({k: v for k, v in row.items() if k != 'model_sha256'})


def test_exact_crypto_targets_keep_independent_coinbase_intermarket_drivers():
    related = [source(code) for code in ('BTCUSD','ETHUSD','SOLUSD')]
    targets = [source()]
    for item in related:
        targets.append({**item, 'provider':'Binance', 'ticker':item['instrument'].replace('USD','USDT')})
    report = market().train_market_families(targets, T0, horizons=(15,), related_sources=related)
    assert report['NAS100']['intermarket']['status'] == 'TRAINED'
    assert report['BTCUSD']['session']['status'] == 'TRAINED'
    assert all('COINBASE' in name for name in report['NAS100']['intermarket']['heads']['direction']['feature_names'])


def test_session_features_use_instrument_timezone_and_dst_without_fake_open_calendar():
    from datetime import datetime, timezone
    m = market()
    winter = datetime(2026, 1, 5, 15, tzinfo=timezone.utc).timestamp()
    summer = datetime(2026, 7, 6, 14, tzinfo=timezone.utc).timestamp()
    assert m.session_features('NAS100', winter) == pytest.approx(m.session_features('NAS100', summer))
    assert not any('open' in k or 'close' in k for k in m.session_features('NAS100', winter))


def test_related_features_require_same_completed_window_and_preserve_actual_identity():
    m = market()
    sources = [source(code) for code in ('BTCUSD', 'ETHUSD', 'SOLUSD')]
    rows = m.related_series(sources, T0)
    features = m.intermarket_features(rows, T0)
    assert len(features) == 3
    assert all('COINBASE' in name and name.endswith('.return_5m_aligned_lag5m') for name in features)
    expected = math.log(sources[0]['bars'][-2]['close'] / sources[0]['bars'][-3]['close'])
    assert features['intermarket.COINBASEBTC-USD.return_5m_aligned_lag5m'] == pytest.approx(expected)
    missing = deepcopy(rows)
    missing['BTC-USD'].pop(T0 - 300)
    assert m.intermarket_features(missing, T0) is None


def test_labels_do_not_cross_gaps_overlap_or_include_future():
    m = market()
    raw = source()['bars']
    rows = m.market_dataset('NAS100', 'session', raw, T0, 15)
    assert np.all(rows['ends'] - rows['times'] == 900)
    assert np.all(rows['times'][1:] >= rows['ends'][:-1])
    assert max(rows['ends']) <= T0
    gap = raw[800]['bar_end_ts']
    broken = m.market_dataset('NAS100', 'session', raw[:800] + raw[801:], T0, 15)
    assert not any(a < gap < b for a, b in zip(broken['times'], broken['ends']))


def test_training_ignores_appended_future_and_does_not_select_on_final_test():
    m = market()
    src = source()
    first = m.train_market_families([src], T0, horizons=(15, 30))['NAS100']['session']
    future = deepcopy(src)
    future['bars'].append({**src['bars'][-1], 'bar_end_ts': T0 + 300})
    future['source_sha256'] = fingerprint(future['bars'])
    second = m.train_market_families([future], T0, horizons=(15, 30))['NAS100']['session']
    assert first == second
    for head in first['heads'].values():
        assert head['fit_end_ts'] < head['test_start_ts']
        assert head['selection_uses_final_test'] is False


@pytest.mark.parametrize('flag', ['demo', 'synthetic', 'contract_fixture', 'synthetic_data_used'])
def test_marked_training_inputs_cannot_produce_models(flag):
    m = market()
    src = source()
    src['metadata'] = {flag: True}
    report = m.train_market_families([src], T0, horizons=(15,))
    assert report['NAS100']['session']['reason'] == 'SIMULATED_TRAINING_SOURCE_NOT_ADMISSIBLE'
    assert 'heads' not in report['NAS100']['session']


def test_coinbase_target_cannot_acquire_configured_binance_identity():
    report = market().train_market_families([source('BTCUSD')], T0, horizons=(15,))
    assert report['BTCUSD']['session']['reason'] == 'TARGET_CONFIGURED_SERIES_MISMATCH'
    assert 'heads' not in report['BTCUSD']['session']


def test_missing_six_histories_are_null_and_named_not_filled_from_price_bars():
    report = market().train_market_families([source()], T0, horizons=(15,))
    for family in ('macro', 'event', 'order_flow', 'positioning', 'value_carry', 'option'):
        row = report['NAS100'][family]
        assert row['status'] == 'HISTORICAL_FEATURES_UNAVAILABLE'
        assert row['forecast'] is None
        assert 'heads' not in row


def test_genuine_macro_feature_history_uses_same_trainer_without_broker_costs():
    m = market()
    history = []
    for b in source()['bars']:
        ts = b['bar_end_ts']
        history.append(dict(captured_ts=ts, features={'macro.observed_rate': math.sin(2*math.pi*ts/86400)},
            feature_provenance={'macro.observed_rate': dict(source_verified=True, source_id=f'recorded-release:{ts}',
                source_instrument='NAS100', observed_ts=ts-60, received_ts=ts-1, max_age_sec=86400.,
                quality=1., dependency_group=f'release:recorded:{ts}')}))
    report = m.train_market_families([source()], T0, horizons=(15,),
                                   feature_history={'NAS100': {'macro': history}})
    assert report['NAS100']['macro']['status'] == 'TRAINED'
    bad = deepcopy(history)
    for row in bad:
        row['feature_provenance']['macro.observed_rate']['received_ts'] = row['captured_ts'] + 1
    report = m.train_market_families([source()], T0, horizons=(15,),
                                   feature_history={'NAS100': {'macro': bad}})
    assert report['NAS100']['macro']['status'] != 'TRAINED'


def test_large_actual_bar_history_does_not_trip_small_snapshot_traversal_bound():
    report = market().train_market_families([source(n=12000)], T0, horizons=(15,))
    assert report['NAS100']['session']['status'] == 'TRAINED'


def test_negative_oos_result_is_not_supported_merely_because_admission_is_an_object():
    m = market()
    src = source()
    # Learn the seasonal relation earlier; reverse it in untouched final20%.
    for bar in src['bars'][-360:]:
        old = bar['open']
        bar['close'] = old * old / bar['close']
        bar['high'] = max(old, bar['close'])*1.00001
        bar['low'] = min(old, bar['close'])*.99999
    src['source_sha256'] = fingerprint(src['bars'])
    row = m.train_market_families([src], T0, horizons=(15,))['NAS100']['session']
    for head in row['heads'].values():
        if head.get('test_gain_mbit', 1) <= 0:
            assert head['working_supported'] is False
    assert row['heads']['direction']['working_supported'] == row['heads']['direction']['working_admission']['supported']


def runtime(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from seiltanzer import mathematical_edge, runtime_git_identity
    m = market()
    models = m.train_market_families([source()], T0, horizons=(15,))
    report = dict(contract_version=mathematical_edge.CONTRACT, created_ts=T0,
        published_for_sha='a'*40, production_authority=False, automatic_execution=False,
        instruments={}, family_market_models=models)
    path = tmp_path/'research'/'mathematical_edge_latest.json'
    path.parent.mkdir(); path.write_text(json.dumps(report))
    raw = [[b['bar_end_ts']-300+k*60,b['open'],b['high'],b['low'],b['close']]
           for b in source()['bars'][-3:] for k in range(5)]
    authority = dict(provider='Yahoo', source_symbol='^NDX', target_instrument='NAS100',
                     source_verified=True, observed_ts=T0, available_at=T0)
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), market=SimpleNamespace(
        instrument_code='NAS100', demo=False, intraday_ohlcv=raw, intraday_source_authority=authority))
    monkeypatch.setattr(runtime_git_identity, 'runtime_git_sha', lambda: 'a'*40)
    snapshot = dict(captured_ts=T0, strategy=dict(instrument='NAS100', direction='long'),
                    policy_manager={'inputs': {'horizon_minutes': 15}})
    return engine, snapshot, report, path


def test_trained_forecast_attaches_and_votes_inside_existing_pool(tmp_path, monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, snapshot, _, _ = runtime(tmp_path, monkeypatch)
    assert hasattr(market(), 'attach_market_family_context'), 'trained family runtime adapter missing'
    market().attach_market_family_context(engine, snapshot)
    evidence = build_edge_family_evidence(snapshot)
    row = evidence['families']['session']
    assert row['available'] and row['forecast_available']
    assert row['forecast_kind'] == 'TRAINED_MARKET_RESPONSE_NOT_NET_ACTION_MODEL'
    assert row['market_predictions']['direction']['probability'] is not None
    assert row['weight_pool'] == 'mathematical_edge'
    component = next(c for c in evidence['components'] if c['family_ids'] == ['session'])
    assert component['scores']['HOLD'] == 0
    assert component['scores']['EXIT'] != 0
    assert component['net_economic_proof'] is False
    short = deepcopy(snapshot); short['strategy']['direction'] = 'short'
    opposite = build_edge_family_evidence(short)['components'][0]
    assert opposite['scores']['EXIT'] == pytest.approx(-component['scores']['EXIT'])


@pytest.mark.parametrize('case', ['hash', 'future_fit', 'sha', 'feed', 'stale', 'demo', 'authority', 'horizon'])
def test_runtime_invalid_evidence_is_isolated_and_cannot_vote(tmp_path, monkeypatch, case):
    import json
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, snapshot, report, path = runtime(tmp_path, monkeypatch)
    assert hasattr(market(), 'attach_market_family_context'), 'trained family runtime adapter missing'
    if case == 'hash': report['family_market_models']['NAS100']['session']['heads']['direction']['head']['beta'][0] += 1
    if case == 'future_fit':
        row = report['family_market_models']['NAS100']['session']
        row['heads']['direction']['fit_end_ts'] = T0 + 1
        row['model_sha256'] = fingerprint({k:v for k,v in row.items() if k != 'model_sha256'})
    if case == 'sha': report['published_for_sha'] = 'b'*40
    if case == 'feed': engine.market.instrument_code = 'EURUSD'
    if case == 'stale': snapshot['captured_ts'] += 1200
    if case == 'demo': engine.market.demo = True
    if case == 'authority': engine.market.intraday_source_authority['source_symbol'] = 'QQQ'
    if case == 'horizon': snapshot['policy_manager']['inputs']['horizon_minutes'] = 5
    path.write_text(json.dumps(report))
    market().attach_market_family_context(engine, snapshot)
    evidence = build_edge_family_evidence(snapshot)
    assert not evidence['families']['session']['forecast_available']
    assert not evidence['components']


def test_public_collector_aligned_features_equal_historical_definition():
    m = market()
    assert hasattr(m, 'aligned_related_features'), 'aligned real source feature adapter missing'
    series = m.related_series([source(code) for code in PRODUCTS_FOR_TEST], T0)
    record = dict(market_return_contract=m.FEATURE_CONTRACT, market_return_origin_ts=T0,
        aligned_related_series=[dict(provider='Coinbase Exchange', symbol=product, quote_currency='USD',
            body_sha256='a'*64, source_id=product, available_at=T0,
            closes=[[T0-600, values[T0-600]], [T0-300, values[T0-300]]])
            for product, values in series.items()])
    assert m.aligned_related_features(record, T0)[0] == pytest.approx(m.intermarket_features(series, T0))
    record['aligned_related_series'][0]['closes'][1][0] += 60
    assert m.aligned_related_features(record, T0) is None


PRODUCTS_FOR_TEST = ('BTCUSD', 'ETHUSD', 'SOLUSD')


def test_existing_offhost_report_contains_actual_fitted_family_models():
    from scripts.run_mathematical_edge import build_report
    report = build_report([source()], {}, T0, horizons=(15,))
    assert report.get('family_market_models', {}).get('NAS100', {}).get('session', {}).get('status') == 'TRAINED'


def test_collector_materializes_aligned_related_returns_with_receipt_proof():
    from test_edge_family_sources import build_bundle, calendar, book, tape, cot, candles, T0 as timestamp
    def fetch(url):
        body = calendar() if 'nyse' in url else cot() if 'cftc' in url else book() if 'book' in url else tape() if 'trades' in url else candles()
        return body
    report = build_bundle(instruments=['NAS100'], fetch=fetch, clock=lambda: timestamp)
    record = report['instruments']['NAS100']['edge_family_sources']['intermarket'][0]
    assert record.get('market_return_contract') == market().FEATURE_CONTRACT
    assert market().aligned_related_features(record, timestamp) is not None


def test_actual_family_weight_and_ablation_are_exposed_without_changing_economics(tmp_path, monkeypatch):
    from test_unified_edge_ensemble import snapshot as management_snapshot
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from seiltanzer.unified_edge_audit import compact_unified_ensemble
    engine, value, _, _ = runtime(tmp_path, monkeypatch)
    value['policy_manager'].update(management_snapshot()['policy_manager'])
    market().attach_market_family_context(engine, value)
    before = deepcopy(value)
    audit = build_unified_ensemble(value)
    family = audit['edge_families']['session']
    assert family['model_pool_effective_weight'] > 0
    assert 'model_marginal_score_delta' in family
    excluded = next(row for row in audit['market_family_counterfactuals'] if row['excluded_family_id'] == 'session')
    assert excluded['ablation_scope'] == 'TRAINED_MARKET_RESPONSE_ONLY'
    assert excluded['risk_and_cost_evaluation_preserved']
    assert sum(row['effective_weight'] for row in audit['components']) == pytest.approx(1.)
    assert value == before
    assert any(row['model_family_contributions'] for row in audit['candidates'])
    compact = compact_unified_ensemble(audit)
    row = next(row for row in compact['edge_families'] if row['family_id'] == 'session')
    assert row['market_predictions']['direction']['probability'] is not None
    assert row['model_pool_effective_weight'] > 0


def test_market_context_is_retained_by_frozen_snapshot_compaction(tmp_path, monkeypatch):
    from seiltanzer.operational_edge_compaction import compact_operational_edges
    engine, value, _, _ = runtime(tmp_path, monkeypatch)
    market().attach_market_family_context(engine, value)
    compact_operational_edges(value)
    assert 'edge_family_market_context' in __import__('seiltanzer.operational_edge_compaction', fromlist=['FAMILY_ROOTS']).FAMILY_ROOTS
    assert value['edge_family_market_context']['families']['session']['model']['heads']['direction']['head']


def test_recorded_feature_history_need_not_have_exact_five_minute_capture_clock():
    m = market()
    history = []
    for bar in source()['bars']:
        captured = bar['bar_end_ts'] - 17
        history.append({'captured_ts': captured, 'features': {'macro.recorded_rate': math.sin(2*math.pi*captured/86400)},
            'feature_provenance': {'macro.recorded_rate': {'source_verified':True, 'source_id':f'release:{captured}',
                'source_instrument':'NAS100','observed_ts':captured-60,'received_ts':captured-1,
                'max_age_sec':86400.,'quality':1.,'dependency_group':f'release:{captured}'}}})
    report = m.train_market_families([source()], T0, horizons=(15,), feature_history={'NAS100':{'macro':history}})
    assert report['NAS100']['macro']['status'] == 'TRAINED'


def test_private_archive_extraction_retains_original_receipts_and_excludes_demo():
    import json
    from test_edge_family_archive import episode, canonical, assemble, export
    from test_edge_family_adapters import family_fixture, T0 as captured
    m = market()
    assert hasattr(m, 'features_from_archive'), 'private actual feature history reader missing'
    packet = episode(captured=captured)
    frozen = json.loads(packet['snapshot_json'])
    frozen['edge_family_sources'] = {'macro': family_fixture('macro')[0]}
    packet['snapshot_json'] = canonical(frozen).decode()
    import hashlib
    packet['snapshot_sha256'] = hashlib.sha256(packet['snapshot_json'].encode()).hexdigest()
    archive = assemble([export(packet, exported=captured+100)])
    history = m.features_from_archive(archive)
    row = history['NAS100']['macro'][0]
    assert row['captured_ts'] == captured
    assert row['feature_provenance']['macro.expected_rate_change']['observed_ts'] < captured
    assert 'trade_id' not in row and 'path_points' not in row
    raw = deepcopy(archive)
    raw['episodes'][0]['snapshot_json'] = raw['episodes'][0]['snapshot_json'].replace('"captured_ts":', '"demo":true,"captured_ts":')
    assert m.features_from_archive(raw) == {}


def test_private_history_export_is_read_only_and_missing_storage_does_not_fake_data(tmp_path):
    from test_edge_family_private_archive import MemoryStore
    name = 'scripts.export_family_market_history'
    assert importlib.util.find_spec(name) is not None, 'actual private history export missing'
    module = importlib.import_module(name)
    client = MemoryStore(); before = deepcopy(client.objects)
    path = tmp_path/'private-history.json'
    result = module.export_history(client, path)
    assert result['reason'] == 'PRIVATE_ARCHIVE_INITIAL_EMPTY'
    assert result['feature_observation_count'] == 0
    assert path.read_text() == '{}'
    assert client.objects == before
    assert 'archive' not in result and 'episodes' not in result


def test_private_history_failed_restore_is_not_reported_as_initial(tmp_path):
    name = 'scripts.export_family_market_history'
    assert importlib.util.find_spec(name) is not None, 'actual private history export missing'
    class Broken:
        def get_object(self, **kwargs):
            raise RuntimeError('private account / secret exception')
    result = importlib.import_module(name).export_history(Broken(), tmp_path/'private-history.json')
    assert result['reason'] == 'PRIVATE_ARCHIVE_RESTORE_FAILED'
    assert 'secret' not in repr(result) and 'account' not in repr(result)


def test_simulated_history_container_cannot_promote_genuine_looking_children():
    from seiltanzer.edge_family_adapters import FAMILIES
    m = market()
    for value in ({'demo':True, 'NAS100':{'macro':[]}}, {'NAS100':{'demo':True,'macro':[]}}):
        # Nonempty accepted-looking series would otherwise train from this root.
        records = []
        for bar in source()['bars']:
            ts = bar['bar_end_ts']
            records.append({'captured_ts':ts,'features':{'macro.rate':math.sin(ts/86400)},
                'feature_provenance':{'macro.rate':{'source_verified':True,'source_id':f'r:{ts}',
                    'source_instrument':'NAS100','observed_ts':ts,'received_ts':ts,'max_age_sec':86400.,
                    'quality':1.,'dependency_group':f'r:{ts}'}}})
        value['NAS100']['macro'] = records
        result = m.train_market_families([source()],T0,horizons=(15,),feature_history=value)
        assert result['NAS100']['macro']['status'] != 'TRAINED'
        assert result['NAS100']['session']['status'] == 'TRAINED'


def test_intermarket_runtime_proof_must_match_trained_lag_and_window():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    m = market()
    all_sources = [source()] + [source(code) for code in PRODUCTS_FOR_TEST]
    model = m.train_market_families(all_sources, T0, horizons=(15,))['NAS100']['intermarket']
    features = m.intermarket_features(m.related_series(all_sources,T0),T0)
    proof = {name: {'source_verified':True,'source_id':name,'source_instrument':'NAS100',
        'observed_ts':T0-300,'received_ts':T0,'max_age_sec':900.,'quality':1.,
        'dependency_group':'intermarket:coinbase','forecast_origin_ts':T0,'window_seconds':600.,'lag_seconds':300.}
        for name in features}
    snapshot = {'captured_ts':T0, 'strategy':{'instrument':'NAS100','direction':'long'},
        'policy_manager':{'inputs':{'horizon_minutes':15}},
        'edge_family_market_context':{'contract_version':m.CONTRACT,'families':{'intermarket':{
            'model':model,'features':features,'feature_provenance':proof,'forecast_origin_ts':T0}}}}
    evidence = build_edge_family_evidence(snapshot)
    assert not evidence['families']['intermarket']['forecast_available']
    assert not evidence['components']


@pytest.mark.parametrize('case', ['head_none', 'source_list', 'row_list'])
def test_hash_valid_malformed_optional_model_is_rejected_without_aborting(tmp_path, monkeypatch, case):
    import json
    engine, snapshot, report, path = runtime(tmp_path, monkeypatch)
    model = report['family_market_models']['NAS100']['session']
    if case == 'head_none': model['heads']['direction']['head'] = None
    if case == 'source_list': model['source'] = ['invalid']
    if case == 'row_list': model['heads']['direction'] = ['head']
    model['model_sha256'] = fingerprint({k:v for k,v in model.items() if k != 'model_sha256'})
    path.write_text(json.dumps(report))
    market().attach_market_family_context(engine, snapshot)
    assert snapshot['edge_family_market_context']['rejected_models']['session']
    assert not snapshot['edge_family_market_context']['families']


def test_combined_session_intermarket_survives_collector_and_snapshot_budget(tmp_path, monkeypatch):
    import json
    from test_edge_family_sources import build_bundle, calendar, book, tape, cot, candles, T0 as captured
    from seiltanzer.operational_edge_compaction import compact_operational_edges
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, snapshot, report, path = runtime(tmp_path, monkeypatch)
    m = market()
    report['family_market_models'] = m.train_market_families(
        [source()] + [source(code) for code in PRODUCTS_FOR_TEST], T0, horizons=(15,))
    path.write_text(json.dumps(report))
    def fetch(url):
        return calendar() if 'nyse' in url else cot() if 'cftc' in url else book() if 'book' in url else tape() if 'trades' in url else candles()
    bundle = build_bundle(instruments=['NAS100'], fetch=fetch, clock=lambda:captured)
    record = bundle['instruments']['NAS100']['edge_family_sources']['intermarket'][0]
    # Preserve actual collector shape, shift this contract fixture's clocks only.
    delta = T0-captured
    def shift(value):
        if isinstance(value, dict):
            return {k:shift(v) for k,v in value.items()}
        if isinstance(value,list): return [shift(v) for v in value]
        return value+delta if isinstance(value,(int,float)) and captured-86400 < value <= captured else value
    snapshot['edge_family_sources'] = {'intermarket':[shift(record)]}
    m.attach_market_family_context(engine,snapshot)
    assert set(snapshot['edge_family_market_context']['families']) == {'session','intermarket'}
    compact_operational_edges(snapshot)
    assert set(snapshot['edge_family_market_context']['families']) == {'session','intermarket'}
    evidence = build_edge_family_evidence(snapshot)
    assert evidence['families']['session']['forecast_available']
    assert evidence['families']['intermarket']['forecast_available']
    assert len(json.dumps(snapshot).encode()) < 60000


def test_history_without_trainable_heads_is_not_reported_as_trained_model():
    m = market()
    src = source()
    observed = src['bars'][0]['bar_end_ts']
    history = [{'captured_ts':bar['bar_end_ts'], 'features':{'macro.recorded_rate':1.},
        'feature_provenance':{'macro.recorded_rate':{'source_verified':True,'source_id':'same-recorded-release',
            'source_instrument':'NAS100','observed_ts':observed,'received_ts':observed,
            'max_age_sec':30*86400.,'quality':1.,'dependency_group':'same-release'}}} for bar in src['bars']]
    report = m.train_market_families([src],T0,horizons=(15,),feature_history={'NAS100':{'macro':history}})
    row = report['NAS100']['macro']
    assert row['status'] == 'HEAD_TRAINING_UNAVAILABLE'
    assert row['forecast'] is None
    assert not any('head' in head for head in row['heads'].values())
    from scripts.run_mathematical_edge import build_report
    actual = build_report([src],{},T0,horizons=(15,),family_history={'NAS100':{'macro':history}})
    assert actual['family_market_model_summary']['trained_models'] == 1


def test_hash_valid_empty_heads_cannot_become_runtime_trained_model(tmp_path, monkeypatch):
    import json
    engine,snapshot,report,path = runtime(tmp_path,monkeypatch)
    model = report['family_market_models']['NAS100']['session']
    model['heads'] = {key:{'working_supported':False,'forecast':None,'reason':'HEAD_CLASSES_OR_VALIDATION_UNAVAILABLE'}
                      for key in ('direction','movement')}
    model['model_sha256'] = fingerprint({k:v for k,v in model.items() if k != 'model_sha256'})
    path.write_text(json.dumps(report))
    market().attach_market_family_context(engine,snapshot)
    assert not snapshot['edge_family_market_context']['families']
    assert snapshot['edge_family_market_context']['rejected_models']['session'] == 'MARKET_MODEL_HEADS_UNTRAINED'


def option_runtime(tmp_path,monkeypatch):
    import json
    from seiltanzer.edge_family_native_history import option_observation
    engine,snapshot,report,path = runtime(tmp_path,monkeypatch)
    history=[]
    for b in source()['bars']:
        ts=b['bar_end_ts']
        metrics={'proxy':'QQQ','demo':False,'implied_move':{'sigma_annual':.3+.1*math.sin(2*math.pi*ts/86400)}}
        history.append(option_observation(code='NAS100',metrics=metrics,source_id='actual-recorded-option',
            observed_ts=ts-60,received_ts=ts-17,quality=.72,source_sha256=fingerprint(metrics)))
    model=market().train_market_families([source()],T0,horizons=(15,),
        feature_history={'NAS100':{'option':history}})['NAS100']['option']
    assert model['status']=='TRAINED'
    report['family_market_models']['NAS100']['option']=model
    path.write_text(json.dumps(report))
    engine.market.chain={'metrics':{'proxy':'QQQ','demo':False,'implied_move':{'sigma_annual':.28}},
                         'ts':T0-60,'status':'delayed','source':'yfinance QQQ options'}
    return engine,snapshot,report,path


def test_native_trained_option_forecast_uses_same_actual_live_feature_and_preserves_session(tmp_path,monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot,_,_=option_runtime(tmp_path,monkeypatch)
    market().attach_market_family_context(engine,snapshot)
    context=snapshot['edge_family_market_context']['families']
    assert set(context)=={'option','session'}
    assert context['option']['features']=={'option.iv':.28}
    proof=context['option']['feature_provenance']['option.iv']
    assert proof['observed_ts']==T0-60
    assert proof['target_quote_authority'] is False
    families=build_edge_family_evidence(snapshot)['families']
    assert families['option']['forecast_available']
    assert all(0<p['probability']<1 for p in families['option']['market_predictions'].values())


@pytest.mark.parametrize('case',['demo','wrong_proxy','stale','after_origin','malformed','missing_ts'])
def test_bad_live_option_input_preserves_other_genuine_experts(tmp_path,monkeypatch,case):
    engine,snapshot,_,_=option_runtime(tmp_path,monkeypatch)
    chain=engine.market.chain
    if case=='demo':chain['metrics']['demo']=True
    elif case=='wrong_proxy':chain['metrics']['proxy']='SPY'
    elif case=='stale':chain['ts']=T0-1801
    elif case=='malformed':chain['metrics']['irrelevant_nan']=float('nan')
    elif case=='missing_ts':chain['ts']=None
    else:
        chain['ts']=T0+15;snapshot['captured_ts']=T0+30
    market().attach_market_family_context(engine,snapshot)
    assert 'option' not in snapshot['edge_family_market_context']['families']
    assert 'session' in snapshot['edge_family_market_context']['families']


def test_session_intermarket_and_native_option_survive_same_frozen_snapshot(tmp_path,monkeypatch):
    import json
    from test_edge_family_sources import build_bundle,calendar,book,tape,cot,candles,T0 as captured
    from seiltanzer.operational_edge_compaction import compact_operational_edges
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot,report,path=option_runtime(tmp_path,monkeypatch)
    model=market().train_market_families([source()]+[source(code) for code in PRODUCTS_FOR_TEST],
        T0,horizons=(15,))['NAS100']['intermarket']
    report['family_market_models']['NAS100']['intermarket']=model
    path.write_text(json.dumps(report))
    def fetch(url):
        return calendar() if 'nyse' in url else cot() if 'cftc' in url else book() if 'book' in url else tape() if 'trades' in url else candles()
    record=build_bundle(instruments=['NAS100'],fetch=fetch,clock=lambda:captured)['instruments']['NAS100']['edge_family_sources']['intermarket'][0]
    def shift(value):
        if isinstance(value,dict):return {k:shift(v) for k,v in value.items()}
        if isinstance(value,list):return [shift(v) for v in value]
        return value+T0-captured if isinstance(value,(int,float)) and captured-86400<value<=captured else value
    snapshot['edge_family_sources']={'intermarket':[shift(record)]}
    market().attach_market_family_context(engine,snapshot)
    compact_operational_edges(snapshot)
    assert set(snapshot['edge_family_market_context']['families'])=={'option','session','intermarket'}
    evidence=build_edge_family_evidence(snapshot)
    assert all(evidence['families'][f]['forecast_available'] for f in ('option','session','intermarket'))
    assert len(json.dumps(snapshot).encode())<60000


def test_slow_build_does_not_shift_frozen_family_forecast_origin(tmp_path, monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, snapshot, _, _ = runtime(tmp_path, monkeypatch)
    snapshot['captured_ts'] = T0 + 310.
    snapshot['policy_manager']['input_audit'] = {'snapshot_ts': T0}
    market().attach_market_family_context(engine, snapshot)
    assert snapshot['edge_family_market_context']['available'] is True
    evidence = build_edge_family_evidence(snapshot)
    session = evidence['families']['session']
    assert session['forecast_available'] is True
    assert session['forecast_origin_ts'] == T0
    assert any(c['family_ids'] == ['session'] for c in evidence['components'])


@pytest.mark.parametrize('input_clock', [T0+1, None, True, float('nan')])
def test_invalid_declared_family_input_clock_cannot_admit_a_forecast(tmp_path, monkeypatch, input_clock):
    engine, snapshot, _, _ = runtime(tmp_path, monkeypatch)
    snapshot['policy_manager']['input_audit'] = {'snapshot_ts': input_clock}
    market().attach_market_family_context(engine, snapshot)
    assert snapshot['edge_family_market_context']['available'] is False


def test_short_family_forecast_keeps_its_horizon_inside_longer_economic_comparison(tmp_path, monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, snapshot, _, _ = runtime(tmp_path, monkeypatch)
    snapshot['policy_manager']['inputs']['horizon_minutes'] = 7200.
    market().attach_market_family_context(engine, snapshot)
    evidence = build_edge_family_evidence(snapshot)
    row = evidence['families']['session']
    assert row['forecast_available'] is True
    assert row['market_predictions']['direction']['horizon_minutes'] == 15.
    assert row['market_predictions']['direction']['economic_comparison_horizon_minutes'] == 7200.
    assert row['market_predictions']['direction']['horizon_role'] == 'SHORT_HORIZON_MANUAL_PREFERENCE'
    assert evidence['components']
    assert all(c['net_economic_proof'] is False for c in evidence['components'])


@pytest.mark.parametrize('horizon', [None, True, -1., float('nan')])
def test_invalid_economic_horizon_never_admits_family_preference(tmp_path, monkeypatch, horizon):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine, snapshot, _, _ = runtime(tmp_path, monkeypatch)
    snapshot['policy_manager']['inputs']['horizon_minutes'] = horizon
    market().attach_market_family_context(engine, snapshot)
    assert not build_edge_family_evidence(snapshot)['components']


def test_expired_short_forecast_does_not_inherit_long_source_ttl():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    m = market()
    history = [dict(captured_ts=b['bar_end_ts'],
        features={'macro.observed_rate': math.sin(2*math.pi*b['bar_end_ts']/86400)},
        feature_provenance={'macro.observed_rate': dict(source_verified=True,
            source_id=f"recorded-release:{b['bar_end_ts']}", source_instrument='NAS100',
            observed_ts=b['bar_end_ts']-60, received_ts=b['bar_end_ts']-1,
            max_age_sec=86400., quality=1., dependency_group='recorded_macro')}) for b in source()['bars']]
    model = m.train_market_families([source()], T0, horizons=(15,),
        feature_history={'NAS100': {'macro': history}})['NAS100']['macro']
    value = dict(captured_ts=T0, strategy={'instrument': 'NAS100', 'direction': 'long'},
        policy_manager={'inputs': {'horizon_minutes': 7200.}, 'input_audit': {'snapshot_ts': T0}},
        edge_family_market_context={'contract_version': m.CONTRACT, 'families': {'macro': {
            'model': model, 'features': history[-1]['features'],
            'feature_provenance': history[-1]['feature_provenance'], 'forecast_origin_ts': T0}}})
    assert build_edge_family_evidence(value)['components']
    value['captured_ts'] = T0 + 1200.
    assert not build_edge_family_evidence(value)['components']
