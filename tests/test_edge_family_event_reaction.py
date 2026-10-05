"""Artificial deterministic contract fixtures, not actual release observations."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
import threading
from types import SimpleNamespace

import pytest

KEYS = ('source_id', 'provider', 'source_symbol', 'source_instrument', 'target_instrument',
        'base_currency', 'quote_currency', 'orientation', 'price_basis', 'source_verified',
        'direct_source', 'derived', 'proxy', 'broker_execution_bars', 'received_ts', 'available_at', 'samples')
CONTRACT = 'edge-family-event-reaction-v1'


def module():
    assert importlib.util.find_spec('seiltanzer.edge_family_event_reaction'), 'received event reaction producer missing'
    return importlib.import_module('seiltanzer.edge_family_event_reaction')


def seal(series):
    series['observation_sha256'] = hashlib.sha256(json.dumps({k: series[k] for k in KEYS},
        sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def source(n=6, published=1000., receipt=1400., target='BTCUSD'):
    start = 1020. if published == 1000. else 960.
    series = dict(source_id='Binance:BTCUSDT:1m', provider='Binance', source_symbol='BTCUSDT',
        source_instrument=target, target_instrument=target, base_currency='BTC', quote_currency='USDT',
        orientation='direct', price_basis='DIRECT_CONFIGURED_CRYPTO_SPOT_CONTEXT',
        authority_role='CONFIGURED_MARKET_CONTEXT_NOT_BROKER_PRICE', source_verified=True,
        direct_source=True, derived=False, proxy=False, broker_execution_bars=False,
        received_ts=receipt, available_at=receipt, hash_kind='FROZEN_CLOSE_OBSERVATIONS_SHA256',
        samples=[[start + 60*i, start + 60*(i+1), 100+i, receipt] for i in range(n)])
    seal(series)
    release = dict(source_id='official:fomc', release_id='release-1', event_type='fomc',
        source_url='https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm',
        published_at=published, received_ts=1010., available_at=1010., body_sha256='a'*64,
        source_verified=True, publication_basis='VERIFIED_PUBLICATION_TIMESTAMP',
        hash_kind='OFFICIAL_NORMALIZED_DOCUMENT_SHA256', historical_reconstruction=True,
        source_vintage_guarantee='OFFICIAL_DATED_PAGE_NOT_VERSIONED')
    return dict(source_id='reaction:release-1', source_verified=True, instrument=target,
        observed_ts=start + 60*n, received_ts=receipt, available_at=receipt,
        published_at=published, release_id='release-1', event_type='fomc',
        event_reaction_contract=CONTRACT, reaction_release=release, reaction_series=series)


def test_observed_returns_delay_conservative_windows_and_immutable_support():
    packet = source(); before = deepcopy(packet)
    result = module().event_reaction_features(packet, 1400., 'BTCUSD')
    assert packet == before and result['rejections'] == []
    assert result['features'] == pytest.approx({'event.fomc.reaction_return_1m': .009950330853167877,
        'event.fomc.reaction_return_5m': .04879016416943127,
        'event.fomc.reaction_start_delay_seconds': 20.})
    for suffix, window, interval in [('return_1m', 140, 60), ('return_5m', 380, 300), ('start_delay_seconds', 80, 0)]:
        meta = result['feature_provenance']['event.fomc.reaction_' + suffix]
        assert meta['window_seconds'] == window
        assert meta['return_interval_seconds'] == interval
        assert meta['dependency_group'] == 'release:release-1'
        assert meta['supporting_source_ids'] == ['official:fomc', 'Binance:BTCUSDT:1m']


@pytest.mark.parametrize('n', [2, 3, 4, 5])
def test_progressive_window_has_no_five_minute_return(n):
    result = module().event_reaction_features(source(n), 1400., 'BTCUSD')
    assert set(result['features']) == {'event.fomc.reaction_return_1m', 'event.fomc.reaction_start_delay_seconds'}


def test_on_grid_delay_zero_still_requires_completed_baseline_minute():
    result = module().event_reaction_features(source(published=960), 1400, 'BTCUSD')
    assert result['features']['event.fomc.reaction_start_delay_seconds'] == 0
    assert result['feature_provenance']['event.fomc.reaction_start_delay_seconds']['window_seconds'] == 60


@pytest.mark.parametrize('change', [
    lambda s: s.pop('reaction_release'), lambda s: s.update(event_reaction_contract=None),
    lambda s: s.update(event_reaction_contract='unknown'), lambda s: s.update(reaction_series=None),
    lambda s: s.update(instrument='ETHUSD', global_context=True),
    lambda s: s.update(observed_ts=1381), lambda s: s.update(received_ts=1401),
    lambda s: s['reaction_release'].update(received_ts=1401, available_at=1401),
    lambda s: s['reaction_release'].update(published_at=1001),
    lambda s: s['reaction_release'].update(publication_basis='INFERRED'),
    lambda s: s['reaction_release'].update(body_sha256='bad'),
    lambda s: s['reaction_release'].update(source_verified=False),
    lambda s: s['reaction_series'].update(provider='Coinbase'),
    lambda s: s['reaction_series'].update(source_symbol='BTCUSD', quote_currency='USD'),
    lambda s: s['reaction_series'].update(target_instrument='ETHUSD'),
    lambda s: s['reaction_series'].update(price_basis='BROKER_EXECUTION_PRICE'),
    lambda s: s['reaction_series'].update(orientation='inverse'),
    lambda s: s['reaction_series'].update(observation_sha256='b'*64),
    lambda s: s['reaction_series'].update(proxy=True),
    lambda s: s['reaction_series'].update(extra='é'*2048),
    lambda s: s['reaction_series'].update(extra=[[[[[[[[[0]]]]]]]]]),
    lambda s: s['reaction_release'].update(source_vintage_guarantee='x'*129),
    lambda s: s['reaction_release'].update(historical_reconstruction='x'*129),
    lambda s: s['reaction_series']['samples'][0].__setitem__(0, 1000),
    lambda s: s['reaction_series']['samples'][1].__setitem__(0, 1140),
    lambda s: s['reaction_series']['samples'][1].__setitem__(1, 1200),
    lambda s: s['reaction_series']['samples'][1].__setitem__(2, 0),
    lambda s: s['reaction_series']['samples'][1].__setitem__(2, True),
    lambda s: s['reaction_series']['samples'][1].__setitem__(2, float('inf')),
    lambda s: s['reaction_series']['samples'][1].__setitem__(3, 1401),
    lambda s: s['reaction_series']['samples'].append(s['reaction_series']['samples'][0]),
    lambda s: s['reaction_series'].update(context_only=True),
    lambda s: s['reaction_release'].update(synthetic=True),
    lambda s: s['reaction_series'].update(demo=True),
    lambda s: s.update(horizon_minutes=60, reaction_release={**s['reaction_release'], 'horizon_minutes': 240}),
])
def test_bad_or_explicit_partial_proof_never_emits_features(change):
    packet = source(); change(packet)
    result = module().event_reaction_features(packet, 1400, 'BTCUSD')
    assert result['features'] == {} and result['rejections']


def test_legacy_absence_is_not_an_invalid_extension():
    assert module().event_reaction_features({'source_id': 'legacy'}, 1400, 'BTCUSD') == dict(
        features={}, feature_provenance={}, rejections=[])


def test_value_aware_provenance_validates_values_support_and_child_horizon():
    packet = source(); packet['reaction_series']['horizon_minutes'] = 240
    result = module().event_reaction_features(packet, 1400, 'BTCUSD')
    name = 'event.fomc.reaction_return_1m'; value = result['features'][name]
    meta = result['feature_provenance'][name]
    validate = module().reaction_provenance_reason
    assert validate(name, value, meta, 1400, 240, 'BTCUSD') is None
    assert validate(name, value, meta, 1400, 60, 'BTCUSD')
    assert validate(name, value + .01, meta, 1400, 240, 'BTCUSD')
    assert validate(name, value, {}, 1400, 240, 'BTCUSD')
    for field, bad in [('supporting_source_ids', ['official:fomc']), ('window_seconds', 60),
                       ('reaction_contract_version', 'unknown'), ('received_ts', 1399)]:
        changed = deepcopy(meta); changed[field] = bad
        assert validate(name, value, changed, 1400, 240, 'BTCUSD')


def test_forged_observation_digest_cannot_bypass_exact_symbol_or_grid():
    for field, bad in [('provider', 'Coinbase'), ('quote_currency', 'USD'), ('source_symbol', 'ETHUSDT')]:
        packet = source(); packet['reaction_series'][field] = bad; seal(packet['reaction_series'])
        assert module().event_reaction_features(packet, 1400, 'BTCUSD')['rejections']
    packet = source(); packet['reaction_series']['samples'][1] = [1140, 1200, 101, 1400]
    seal(packet['reaction_series'])
    assert module().event_reaction_features(packet, 1400, 'BTCUSD')['rejections']


def feed(packet=None):
    packet = source() if packet is None else packet
    series = packet['reaction_series']
    from seiltanzer.edge_regime import AUTHORITY_CONTRACT
    authority = {k: deepcopy(series[k]) for k in ('source_id', 'provider', 'source_symbol',
        'source_instrument', 'target_instrument', 'source_verified', 'direct_source',
        'derived', 'proxy', 'broker_execution_bars', 'available_at')}
    authority.update(contract_version=AUTHORITY_CONTRACT, interval_sec=60,
        authority_role=series['price_basis'], observed_ts=packet['observed_ts'], quality=.75)
    class LockedFeed(SimpleNamespace):
        def refresh_intraday(self):
            raise AssertionError('request path attempted refresh')
    return LockedFeed(_intraday_lock=threading.RLock(), demo=False, intraday_is_offset=False,
        instrument_code=packet['instrument'], intraday_source_authority=authority,
        intraday_ohlcv=[(bar[0], bar[2], bar[2]+1, bar[2]-1, bar[2], 10.) for bar in series['samples']])


def test_already_received_locked_feed_producer_freezes_without_refresh_or_mutation():
    packet = source(); market = feed(packet)
    release = packet['reaction_release']; before = deepcopy(release)
    assert hasattr(module(), 'build_received_event_reaction_source'), 'locked feed producer missing'
    result = module().build_received_event_reaction_source(release, market, 1400, 'BTCUSD')
    assert result['source'] and result['rejections'] == []
    assert release == before
    assert result['source']['reaction_series']['samples'] == packet['reaction_series']['samples']
    assert result['source']['reaction_series']['quote_currency'] == 'USDT'
    features = module().event_reaction_features(result['source'], 1400, 'BTCUSD')
    assert features['feature_provenance']['event.fomc.reaction_return_1m']['quality'] == .75
    market.intraday_ohlcv[0] = (1020, 900, 901, 899, 900, 10)
    assert result['source']['reaction_series']['samples'][0][2] == 100
    assert module().reaction_observation_sha256(result['source']['reaction_series']) == result['source']['reaction_series']['observation_sha256']


@pytest.mark.parametrize('change', [
    lambda f: setattr(f, 'demo', True), lambda f: setattr(f, 'intraday_is_offset', True),
    lambda f: f.intraday_source_authority.update(available_at=1401),
    lambda f: f.intraday_source_authority.update(provider='Coinbase'),
    lambda f: f.intraday_source_authority.update(source_symbol='ETHUSDT'),
    lambda f: f.intraday_source_authority.update(source_verified=False),
    lambda f: f.intraday_source_authority.update(authority_role='PROXY_CONTEXT_ONLY'),
    lambda f: f.intraday_source_authority.update(context_only=True),
    lambda f: f.intraday_source_authority.update(quality=0),
    lambda f: f.intraday_ohlcv.pop(1),
    lambda f: f.intraday_ohlcv.append(f.intraday_ohlcv[0]),
    lambda f: f.intraday_ohlcv.__setitem__(1, (1080, 101, 100, 102, 101, 10)),
    lambda f: f.intraday_ohlcv.__setitem__(1, (1080, True, 102, 100, 101, 10)),
])
def test_producer_refuses_wrong_authority_conflicting_or_incomplete_ohlc(change):
    market = feed(); change(market)
    assert hasattr(module(), 'build_received_event_reaction_source'), 'locked feed producer missing'
    result = module().build_received_event_reaction_source(source()['reaction_release'], market, 1400, 'BTCUSD')
    assert result['source'] is None and result['rejections']


def test_producer_progresses_only_completed_bars_and_ignores_unreceived_future_path():
    packet = source(2, receipt=1140); market = feed(packet)
    market.intraday_ohlcv.append((1140, 1000, 1001, 999, 1000, 10))
    assert hasattr(module(), 'build_received_event_reaction_source'), 'locked feed producer missing'
    first = module().build_received_event_reaction_source(packet['reaction_release'], market, 1140, 'BTCUSD')
    market.intraday_ohlcv[-1] = (1140, 1, 2, .5, 1, 10)
    second = module().build_received_event_reaction_source(packet['reaction_release'], market, 1140, 'BTCUSD')
    assert first == second and first['source']
    assert len(first['source']['reaction_series']['samples']) == 2


def engine(packet=None):
    packet = source() if packet is None else packet
    class ReceivedStore:
        def latest_received(self, captured_ts, *, nonblocking=False):
            assert nonblocking is True
            assert captured_ts == packet['received_ts']
            return deepcopy(packet['reaction_release'])
        def latest_admissible(self, captured_ts):
            raise AssertionError('reaction borrowed reconstructed release availability')
    return SimpleNamespace(market=feed(packet), passive=SimpleNamespace(_macro_data_factory=
        SimpleNamespace(fomc_deterministic_store=ReceivedStore())))


def test_snapshot_attachment_is_additive_frozen_and_combined_byte_bounded():
    assert hasattr(module(), 'attach_observed_event_reaction'), 'snapshot attachment missing'
    prior = dict(source_id='old', published_at=900)
    frozen = dict(captured_ts=1400., strategy=dict(instrument='BTCUSD'), edge_family_sources={'event': [prior]})
    module().attach_observed_event_reaction(engine(), frozen)
    assert frozen['edge_family_sources']['event'][0] == prior
    assert len(frozen['edge_family_sources']['event']) == 2
    before = deepcopy(frozen['edge_family_sources'])
    module().attach_observed_event_reaction(engine(), frozen)
    assert frozen['edge_family_sources'] == before
    full = dict(captured_ts=1400., strategy=dict(instrument='BTCUSD'),
        edge_family_sources={'event': [dict(prior, padding='x'*6800)]})
    original = deepcopy(full['edge_family_sources'])
    module().attach_observed_event_reaction(engine(), full)
    assert full['edge_family_sources'] == original
    assert full['edge_family_event_reaction_audit']['reason'] == 'SELECTED_SOURCE_FACTS_EXCEED_REVIEW_BYTE_BUDGET'


def test_adapter_reaction_does_not_need_or_weaken_consensus_and_invalid_extension_blocks_fallback():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    packet = source()
    frozen = dict(captured_ts=1400., instrument='BTCUSD', edge_family_sources={'event': [packet]})
    row = build_edge_family_evidence(frozen)['families']['event']
    assert row['available'] and len(row['features']) == 3 and row['voting_weight'] == 0
    packet.update(actual=3, period='2026-04', unit='percent', consensus=dict(
        source_id='consensus', source_verified=True, instrument='BTCUSD', observed_ts=900,
        received_ts=950, release_id='release-1', period='2026-04', unit='percent', value=2))
    row = build_edge_family_evidence(frozen)['families']['event']
    assert row['features']['event.fomc.surprise'] == 1 and len(row['features']) == 5
    packet['reaction_series']['observation_sha256'] = 'b'*64
    assert build_edge_family_evidence(frozen)['families']['event']['features'] == {}
    for key in ('event_reaction_contract', 'reaction_release', 'reaction_series'):
        packet.pop(key)
    assert build_edge_family_evidence(frozen)['families']['event']['features']['event.fomc.surprise'] == 1
    packet['consensus']['received_ts'] = 1000
    assert build_edge_family_evidence(frozen)['families']['event']['features'] == {}


def shifted_source(captured):
    packet = source(); delta = captured - 1440
    for part in (packet, packet['reaction_release'], packet['reaction_series']):
        for key in ('published_at', 'received_ts', 'available_at', 'observed_ts'):
            if key in part:
                part[key] += delta
    for bar in packet['reaction_series']['samples']:
        for index in (0, 1, 3):
            bar[index] += delta
    seal(packet['reaction_series'])
    return packet


def test_runtime_reaction_validation_uses_bundle_capture_not_later_review(tmp_path):
    from test_edge_family_source_runtime import bundle, write_bundle, load, T0
    payload = bundle(); packet = shifted_source(T0)
    packet['reaction_release'].update(received_ts=T0-2, available_at=T0-2)
    packet.update(received_ts=T0-2, available_at=T0-2)
    payload['instruments']['BTCUSD']['edge_family_sources'] = {'event': [packet]}
    write_bundle(tmp_path, payload)
    assert load(tmp_path)['edge_family_sources'] == {}


def test_runtime_reaction_hash_is_validated_before_fact_selection(tmp_path):
    from test_edge_family_source_runtime import bundle, write_bundle, load, T0
    payload = bundle(); packet = shifted_source(T0)
    packet['reaction_series']['observation_sha256'] = 'b'*64
    payload['instruments']['BTCUSD']['edge_family_sources'] = {'event': [packet]}
    write_bundle(tmp_path, payload)
    assert load(tmp_path)['edge_family_sources'] == {}


def reaction_archive():
    from test_edge_family_dataset import snapshot, record, archive, T0
    value = snapshot()
    def retarget(item):
        if isinstance(item, dict):
            return {k: retarget(v) for k, v in item.items()}
        if isinstance(item, list):
            return [retarget(v) for v in item]
        return 'BTCUSD' if item == 'NAS100' else item
    value = retarget(value)
    packet = shifted_source(T0)
    value['edge_family_sources'] = {'event': [packet]}
    retained = record(value); retained['instrument'] = 'BTCUSD'
    return archive(retained)


def test_compact_frozen_producer_to_dataset_to_trainer_and_future_path_noninterference():
    from test_edge_family_dataset import build, T0
    from test_edge_family_training import train, dataset
    value = reaction_archive()
    snapshot = json.loads(value['episodes'][0]['snapshot_json'])
    packet = snapshot['edge_family_sources']['event'][0]
    assert hasattr(module(), 'build_received_event_reaction_source'), 'locked feed producer missing'
    built = module().build_received_event_reaction_source(packet['reaction_release'], feed(packet), T0, 'BTCUSD')
    assert built['source']
    snapshot['edge_family_sources']['event'] = [built['source']]
    raw = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    value['episodes'][0].update(snapshot_json=raw, snapshot_sha256=hashlib.sha256(raw.encode()).hexdigest())
    from test_edge_family_dataset import digest
    value['dataset_sha256'] = digest(value['episodes'])
    assert len(json.dumps(snapshot['edge_family_sources']).encode()) < 8000
    rows = build(value)['rows']
    assert rows and all(row['family_id'] == 'event' and len(row['features']) == 3 for row in rows)
    actionable = [row for row in rows if row['action'] != 'HOLD']
    trained = train(dataset(actionable), trained_at=T0+20000)
    assert trained['diagnostics']['accepted_row_count'] == len(actionable)
    changed = deepcopy(value)
    changed['episodes'][0]['path_points'][1].update(r=.3, price=103.)
    changed['dataset_sha256'] = digest(changed['episodes'])
    assert [(r['features'], r['feature_provenance']) for r in build(changed)['rows']] == [(r['features'], r['feature_provenance']) for r in rows]
    name = 'event.fomc.reaction_return_1m'
    for mutate in (lambda r: r['features'].__setitem__(name, .5),
        lambda r: r['feature_provenance'][name]['supporting_source_ids'].pop(),
        lambda r: r['feature_provenance'][name]['supporting_body_sha256'].update({'official:fomc': 'b'*64}),
        lambda r: r['feature_provenance'][name]['constituent_provenance'][1].update(received_ts=T0+1),
        lambda r: r['feature_provenance'][name]['constituent_provenance'][1].update(context_only=True),
        lambda r: r['feature_provenance'][name].update(window_seconds=60),
        lambda r: r['feature_provenance'][name].pop('reaction_contract_version')):
        tampered = deepcopy(actionable); mutate(tampered[0])
        refused = train(dataset(tampered), trained_at=T0+20000)
        assert refused['diagnostics']['accepted_row_count'] == len(actionable)-1


def test_reaction_child_horizons_cannot_disagree_without_parent_declaration():
    packet = source()
    packet['reaction_release']['horizon_minutes'] = 60
    packet['reaction_series']['horizon_minutes'] = 240
    assert module().event_reaction_features(packet, 1400, 'BTCUSD')['rejections']


def test_dataset_preserves_but_does_not_admit_wrong_reaction_child_horizon():
    from test_edge_family_dataset import build, digest
    value = reaction_archive(); episode = value['episodes'][0]
    snapshot = json.loads(episode['snapshot_json'])
    snapshot['edge_family_sources']['event'][0]['reaction_series']['horizon_minutes'] = 60
    raw = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    episode.update(snapshot_json=raw, snapshot_sha256=hashlib.sha256(raw.encode()).hexdigest())
    value['dataset_sha256'] = digest(value['episodes'])
    before = deepcopy(value)
    assert build(value)['rows'] == [] and value == before


@pytest.mark.parametrize('instrument,provider,symbol,base,quote,basis', [
    ('BTCUSD', 'Binance', 'BTCUSDT', 'BTC', 'USDT', 'DIRECT_CONFIGURED_CRYPTO_SPOT_CONTEXT'),
    ('ETHUSD', 'Binance', 'ETHUSDT', 'ETH', 'USDT', 'DIRECT_CONFIGURED_CRYPTO_SPOT_CONTEXT'),
    ('SOLUSD', 'Binance', 'SOLUSDT', 'SOL', 'USDT', 'DIRECT_CONFIGURED_CRYPTO_SPOT_CONTEXT'),
    ('EURUSD', 'Yahoo', 'EURUSD=X', 'EUR', 'USD', 'DIRECT_QUOTED_FX_PAIR_CONTEXT'),
    ('USDCAD', 'Yahoo', 'CAD=X', 'USD', 'CAD', 'DIRECT_QUOTED_FX_PAIR_CONTEXT'),
])
def test_real_configured_feed_authority_retains_provider_quote_and_role(tmp_path, instrument, provider, symbol, base, quote, basis):
    from seiltanzer.data.feeds import MarketData
    from seiltanzer.data.cache import DiskCache
    from seiltanzer.config import Settings
    market = MarketData(Settings(demo=False, data_dir=str(tmp_path)), DiskCache(str(tmp_path / 'feed.sqlite')))
    market.set_instrument(instrument)
    market.intraday_ohlcv = feed().intraday_ohlcv
    with market._intraday_lock:
        market._capture_intraday_source_authority(provider, symbol, 1400)
    result = module().build_received_event_reaction_source(source()['reaction_release'], market, 1400, instrument)
    assert result['source'] and not result['rejections']
    actual = result['source']['reaction_series']
    assert (actual['provider'], actual['source_symbol'], actual['base_currency'], actual['quote_currency'], actual['price_basis']) == (provider, symbol, base, quote, basis)
    assert actual['authority_role'] == 'CONFIGURED_MARKET_CONTEXT_NOT_BROKER_PRICE'


def test_trainer_cannot_use_reaction_name_or_flag_as_consensus_bypass():
    from test_edge_family_dataset import build, T0
    from test_edge_family_training import train, dataset, received_history_dataset
    valid = [row for row in build(reaction_archive())['rows'] if row['action'] != 'HOLD']
    for mutate in (lambda m: m.clear(), lambda m: m.pop('constituent_provenance'),
                   lambda m: m.update(reaction_contract_version=True),
                   lambda m: m.update(reaction_contract_version='unknown'),
                   lambda m: m.update(history_contract_version='edge-family-intermarket-history-v1')):
        rows = deepcopy(valid)
        for row in rows:
            mutate(row['feature_provenance']['event.fomc.reaction_return_1m'])
        assert train(dataset(rows), trained_at=T0+20000)['diagnostics']['accepted_row_count'] == 0
    history, captured = received_history_dataset()
    assert train(history, trained_at=captured+100000)['diagnostics']['accepted_row_count'] == 5
    for row in history['rows']:
        for meta in row['feature_provenance'].values():
            meta['reaction_contract_version'] = CONTRACT
    assert train(dataset(history['rows']), trained_at=captured+100000)['diagnostics']['accepted_row_count'] == 0


def test_hash_manifest_ids_are_not_scope_declaration_keys():
    packet = source(); packet['reaction_release']['source_id'] = 'horizon_minutes'
    result = module().event_reaction_features(packet, 1400, 'BTCUSD')
    name = 'event.fomc.reaction_return_1m'
    assert module().reaction_provenance_reason(name, result['features'][name], result['feature_provenance'][name], 1400, 240, 'BTCUSD') is None


def test_trainer_rejects_conflicting_same_release_support_across_valid_reaction_features():
    from test_edge_family_dataset import build, T0
    from test_edge_family_training import train, dataset
    rows = [row for row in build(reaction_archive())['rows'] if row['action'] != 'HOLD']
    changed = deepcopy(rows)
    packet = shifted_source(T0); packet['reaction_release']['body_sha256'] = 'b'*64
    alternative = module().event_reaction_features(packet, T0, 'BTCUSD')
    name = 'event.fomc.reaction_return_1m'
    for row in changed:
        row['feature_provenance'][name] = alternative['feature_provenance'][name]
    refused = train(dataset(changed), trained_at=T0+20000)
    assert refused['diagnostics']['accepted_row_count'] == 0


def test_permitted_3662_byte_extension_round_trips_with_4122_byte_root_packet():
    packet = source()
    packet.update(source_id='p'*128, release_id='r'*128, horizon_minutes=240)
    release = packet['reaction_release']
    prefix = 'https://www.federalreserve.gov/'
    release.update(source_id='s'*128, release_id='r'*128, horizon_minutes=240,
        source_url=prefix + 'x'*(2048-len(prefix)))
    packet['reaction_series']['horizon_minutes'] = 240
    before = deepcopy(packet)
    result = module().event_reaction_features(packet, 1400, 'BTCUSD')
    extension = {key: packet[key] for key in ('event_reaction_contract', 'reaction_release', 'reaction_series')}
    assert len(json.dumps(extension, ensure_ascii=False, separators=(',', ':')).encode()) == 3662
    assert not result['rejections'] and packet == before
    for name, value in result['features'].items():
        meta = result['feature_provenance'][name]
        proof = dict(meta['root_provenance'], **extension)
        assert len(json.dumps(proof, ensure_ascii=False, separators=(',', ':')).encode()) == 4122
        assert module().reaction_provenance_reason(name, value, meta, 1400, 240, 'BTCUSD') is None


def near_limit_packet(captured, extension_bytes):
    """Pad permitted fixture fields independently of the product bound helper."""
    packet = shifted_source(captured)
    packet.update(release_id='r'*100, event_type='e'*32)
    release = packet['reaction_release']
    prefix = 'https://www.federalreserve.gov/'
    release.update(source_id='s'*128, release_id='r'*100, event_type='e'*32,
        source_url=prefix+'x'*(2048-len(prefix)), source_vintage_guarantee='v'*128)
    scopes = dict(synthetic=False, demo=False, is_demo=False, synthetic_demo=False,
                  contract_fixture=False, context_only=False, horizon_minutes=240)
    release.update(scopes); packet['reaction_series'].update(scopes)
    for index, sample in enumerate(packet['reaction_series']['samples']):
        sample[2] = 1.1234567890123457e20 + index*1e15
    seal(packet['reaction_series'])
    extension = {key: packet[key] for key in ('event_reaction_contract', 'reaction_release', 'reaction_series')}
    current = len(json.dumps(extension, ensure_ascii=False, separators=(',', ':')).encode())
    trim = current-extension_bytes
    assert 0 <= trim < 2048-len(prefix)
    release['source_url'] = release['source_url'][:2048-trim]
    assert len(json.dumps(extension, ensure_ascii=False, separators=(',', ':')).encode()) == extension_bytes
    market = feed(packet); market.intraday_source_authority.update(scopes)
    return packet, market


@pytest.mark.parametrize('extension_bytes,admitted', [(4096, True), (4097, False)])
def test_exact_extension_byte_boundary_is_identical_for_received_producer_and_trainer(extension_bytes, admitted):
    from test_edge_family_dataset import T0
    packet, market = near_limit_packet(T0, extension_bytes)
    produced = module().build_received_event_reaction_source(packet['reaction_release'], market, T0, 'BTCUSD')
    assert bool(produced['source']) is admitted
    if admitted:
        packet = produced['source']
        result = module().event_reaction_features(packet, T0, 'BTCUSD')
    else:
        assert produced['rejections'] and not module().event_reaction_features(packet, T0, 'BTCUSD')['features']
        valid, _ = near_limit_packet(T0, 4096)
        result = module().event_reaction_features(valid, T0, 'BTCUSD')
    for name, value in result['features'].items():
        meta = deepcopy(result['feature_provenance'][name])
        if not admitted:
            meta['constituent_provenance'][0]['source_url'] += 'x'
        assert (module().reaction_provenance_reason(name, value, meta, T0, 240, 'BTCUSD') is None) is admitted


def test_near_limit_received_producer_frozen_dataset_and_full_trainer_round_trip():
    from test_edge_family_dataset import build, digest, T0
    from test_edge_family_training import train, dataset
    packet, market = near_limit_packet(T0, 4096)
    produced = module().build_received_event_reaction_source(packet['reaction_release'], market, T0, 'BTCUSD')
    assert produced['source']
    frozen = reaction_archive(); episode = frozen['episodes'][0]
    snapshot = json.loads(episode['snapshot_json'])
    snapshot['edge_family_sources']['event'] = [produced['source']]
    raw = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    episode.update(snapshot_json=raw, snapshot_sha256=hashlib.sha256(raw.encode()).hexdigest())
    frozen['dataset_sha256'] = digest(frozen['episodes'])
    assert len(json.dumps(snapshot['edge_family_sources']).encode()) < 8000
    rows = [row for row in build(frozen)['rows'] if row['action'] != 'HOLD']
    assert len(rows) == 5
    assert train(dataset(rows), trained_at=T0+20000)['diagnostics']['accepted_row_count'] == 5
    for row in rows:
        for meta in row['feature_provenance'].values():
            meta['constituent_provenance'][0]['source_url'] += 'x'
    assert train(dataset(rows), trained_at=T0+20000)['diagnostics']['accepted_row_count'] == 0


@pytest.mark.parametrize('mutate', [
    lambda m: m['root_provenance'].update(extra='x'*5000),
    lambda m: m['root_provenance'].update(extra=[[[[[[[[[0]]]]]]]]]),
    lambda m: m.update(extra='x'*8001),
    lambda m: m.update(extra=[[[[[[[[[0]]]]]]]]]),
])
def test_extension_bound_fix_keeps_root_and_normalized_metadata_closed_and_bounded(mutate):
    result = module().event_reaction_features(source(), 1400, 'BTCUSD')
    name = 'event.fomc.reaction_return_1m'; meta = result['feature_provenance'][name]
    mutate(meta)
    assert module().reaction_provenance_reason(name, result['features'][name], meta, 1400, 240, 'BTCUSD')
