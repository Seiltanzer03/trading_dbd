"""Artificial native receipt fixtures; no live source or effect is established."""
from copy import deepcopy
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import importlib.util
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from seiltanzer.macro_fomc_deterministic_bootstrap import (
    FOMCDeterministicReleaseStore, FOMCStatementSpec, parse_release_timestamp,
)

PREVIOUS = 'The Committee decided to maintain the target range for the federal funds rate at 4.25 to 4.50 percent.'
CURRENT = 'The Committee decided to lower the target range for the federal funds rate at 4.00 to 4.25 percent.'


def html(sentence):
    return '<div>For release at 2:00 p.m. EDT</div><p>The Committee observes expanding activity.</p><p>' + sentence + '</p><p>For media inquiries, call the Board.</p>'


def spec(code):
    return FOMCStatementSpec(code, 'https://www.federalreserve.gov/newsevents/pressreleases/monetary' + code + 'a.htm')


def native_store(monkeypatch, *, current=CURRENT, previous=PREVIOUS, publication=None,
                 prior_fetch=None, prior_created=None, current_fetch=None, current_created=None):
    runtime = SimpleNamespace(_conn=sqlite3.connect(':memory:', check_same_thread=False), _lock=threading.RLock())
    store = FOMCDeterministicReleaseStore(runtime)
    ppub = parse_release_timestamp(html(previous), date_code='20260318')
    cpub = parse_release_timestamp(html(current), date_code='20260429')
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: prior_created or ppub+20)
    store.ingest(spec('20260318'), html=html(previous), fetched_at=prior_fetch or ppub+10)
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: current_created or cpub+20)
    store.ingest(spec('20260429'), html=html(current), fetched_at=current_fetch or cpub+10,
                 previous_source_url=spec('20260318').source_url)
    return store, cpub


def read(store, cutoff, **kwargs):
    assert hasattr(store, 'latest_received_text_pair'), 'bounded exact-linked native text reader missing'
    return store.latest_received_text_pair(cutoff, **kwargs)


def module():
    assert importlib.util.find_spec('seiltanzer.edge_family_event_novelty'), 'frozen policy-sentence proof missing'
    return importlib.import_module('seiltanzer.edge_family_event_novelty')


def test_pair_freezes_original_text_link_first_http_and_local_clocks(monkeypatch):
    store, pub = native_store(monkeypatch)
    pair = read(store, pub+30)
    assert pair['status'] == 'AVAILABLE'
    current, previous = pair['current'], pair['previous']
    assert current['previous_release_id'] == previous['release_id']
    assert current['previous_source_url'] == previous['source_url']
    assert current['fetched_at'] == pub+10 and current['created_ts'] == pub+20
    assert current['available_at'] == pub+20
    assert CURRENT in current['body_text']
    assert current['body_sha256'] == hashlib.sha256(current['body_text'].encode()).hexdigest()
    store.ingest(spec('20260429'), html=html(CURRENT), fetched_at=pub+25,
                 previous_source_url=spec('20260318').source_url)
    assert read(store, pub+30) == pair
    assert store.latest_admissible(pub+5)['status'] == 'VALID'
    assert store.latest_received(pub+15)['status'] == 'VALID'
    assert read(store, pub+15)['status'] == 'UNAVAILABLE'


@pytest.mark.parametrize('field', ['prior_fetch', 'prior_created', 'current_fetch', 'current_created'])
def test_each_support_must_be_http_received_and_locally_materialized_before_capture(monkeypatch, field):
    pub = parse_release_timestamp(html(CURRENT), date_code='20260429')
    store, _ = native_store(monkeypatch, **{field: pub+31})
    assert read(store, pub+30)['status'] == 'UNAVAILABLE'


def corrupt(store, release, field, value):
    # Isolated fixture DB corruption is intentionally outside the immutable API.
    conn = store._conn
    conn.execute('DROP TRIGGER macro_fomc_deterministic_releases_immutable_update')
    conn.execute('UPDATE macro_fomc_deterministic_releases SET ' + field + '=? WHERE date_code=?', (value, release))


@pytest.mark.parametrize('release,field,value', [
    ('20260429', 'body_sha256', 'a'*64), ('20260318', 'body_sha256', 'bad'),
    ('20260429', 'release_id', 'forged'), ('20260318', 'source_url', 'https://evil.test/a'),
    ('20260429', 'date_code', '20260430'), ('20260429', 'previous_release_id', 'missing'),
    ('20260429', 'previous_source_url', spec('20260430').source_url),
    ('20260429', 'contract_version', 'unknown'), ('20260429', 'body_text', ''),
    ('20260429', 'body_text', 'x'*65537), ('20260318', 'body_text', 'é'*32769),
    ('20260429', 'created_ts', -1), ('20260318', 'fetched_at', 1),
], ids=['current-hash', 'previous-hash', 'native-id', 'unofficial-url', 'date', 'missing-link',
         'link-url', 'contract', 'empty-body', 'large-body', 'large-utf8-body', 'local-clock', 'http-clock'])
def test_invalid_latest_or_exact_predecessor_never_falls_back(monkeypatch, release, field, value):
    store, pub = native_store(monkeypatch)
    corrupt(store, release, field, value)
    result = read(store, pub+30)
    assert result['status'] == 'UNAVAILABLE' and result['reason']


def test_publication_freshness_not_import_time_and_latest_vintage_no_fallback(monkeypatch):
    store, pub = native_store(monkeypatch)
    assert read(store, pub+14400)['status'] == 'AVAILABLE'
    assert read(store, pub+14400.01)['status'] == 'UNAVAILABLE'
    monkeypatch.setattr('seiltanzer.macro_fomc_deterministic_bootstrap.time.time', lambda: pub+40)
    store.ingest(spec('20260429'), html=html(CURRENT.replace('lower', 'reduce')), fetched_at=pub+40,
                 previous_source_url=spec('20260318').source_url)
    assert read(store, pub+30)['status'] == 'UNAVAILABLE'


def test_full_nonblocking_read_refuses_busy_store_and_bounds_sql_materialization(monkeypatch):
    store, pub = native_store(monkeypatch)
    held, release = threading.Event(), threading.Event()
    def lock_owner():
        with store._lock:
            held.set(); release.wait(2)
    worker = threading.Thread(target=lock_owner); worker.start(); assert held.wait(1)
    try:
        assert read(store, pub+30, nonblocking=True)['reason'] == 'NOVELTY_RELEASE_STORE_BUSY'
    finally:
        release.set(); worker.join(2)
    class GuardedConnection:
        def execute(self, sql, params=()):
            # SELECT of body_text is allowed only with a SQL byte-length guard.
            if 'SELECT' in sql and 'body_text' in sql and not sql.startswith('SELECT length('):
                assert 'length(CAST(body_text AS BLOB))<=' in sql
            return store.runtime._conn.execute(sql, params)
    original = store._conn; store._conn = GuardedConnection()
    assert read(store, pub+30)['status'] == 'AVAILABLE'
    store._conn = original


@pytest.mark.parametrize('sentence,expected', [(CURRENT, 0.23529411764705888), (PREVIOUS, 0.)], ids=['changed', 'equal'])
def test_exact_complete_decimal_sentence_unsigned_distance(monkeypatch, sentence, expected):
    store, pub = native_store(monkeypatch, current=sentence)
    pair = read(store, pub+30)
    result = module().build_received_event_novelty_source(pair, pub+30)
    assert result['source'] and result['rejections'] == []
    packet = result['source']; support = packet['novelty_current']
    assert support['sentence_text'] == sentence
    assert pair['current']['body_text'][support['sentence_start']:support['sentence_end']] == sentence
    assert support['sentence_sha256'] == hashlib.sha256(sentence.encode()).hexdigest()
    assert support['sentence_sha256'] != support['body_sha256']
    features = module().event_novelty_features(packet, pub+30, 'NAS100')
    assert features['features'] == {'event.fomc.policy_sentence_lexical_distance': expected}
    assert features['feature_provenance']['event.fomc.policy_sentence_lexical_distance']['dependency_group'] == 'release:' + pair['current']['release_id']


@pytest.mark.parametrize('sentence', [CURRENT[:-1], CURRENT + ' ' + CURRENT,
    CURRENT.replace('The Committee', 'The Board'), CURRENT.replace('percent.', 'percent ' + 'word '*150 + '.'),
    CURRENT.replace('percent.', 'percent ' + 'é'*350 + '.')], ids=['incomplete', 'ambiguous', 'missing-anchor', 'long', 'utf8-long'])
def test_absent_ambiguous_incomplete_or_oversized_sentence_is_unavailable(monkeypatch, sentence):
    store, pub = native_store(monkeypatch, current=sentence)
    result = module().build_received_event_novelty_source(read(store, pub+30), pub+30)
    assert result['source'] is None and result['rejections']


def packet(monkeypatch, **kwargs):
    capture_delay = kwargs.pop('capture_delay', 30)
    store, pub = native_store(monkeypatch, **kwargs)
    source = module().build_received_event_novelty_source(read(store, pub+capture_delay), pub+capture_delay)['source']
    assert source is not None
    return source, pub+capture_delay


@pytest.mark.parametrize('change', [
    lambda s: s.pop('novelty_previous'), lambda s: s.update(event_novelty_contract=None),
    lambda s: s.update(event_novelty_contract='unknown'), lambda s: s.update(novelty_current=None),
    lambda s: s.update(extra=1), lambda s: s['novelty_current'].update(extra=1),
    lambda s: s.update(observed_ts=s['observed_ts']+1), lambda s: s.update(quality=.9),
    lambda s: s.update(global_context=False), lambda s: s.update(authority_role='BROKER_OUTCOME'),
    lambda s: s['novelty_current'].update(received_ts=s['received_ts']+1),
    lambda s: s['novelty_previous'].update(created_ts=s['received_ts']+1),
    lambda s: s['novelty_current'].update(previous_release_id='forged'),
    lambda s: s['novelty_current'].update(sentence_sha256='a'*64),
    lambda s: s['novelty_previous'].update(sentence_text=CURRENT),
    lambda s: s['novelty_current'].update(sentence_end=1),
    lambda s: s['novelty_current'].update(historical_reconstruction=False),
    lambda s: s['novelty_current'].update(hash_kind='FROZEN_POLICY_SENTENCE_UTF8_SHA256'),
    lambda s: s['novelty_previous'].update(projection_hash_kind='OFFICIAL_NORMALIZED_DOCUMENT_SHA256'),
    lambda s: s['novelty_previous'].update(context_only=True),
    lambda s: s['novelty_current'].update(demo=True),
    lambda s: s.update(synthetic=1),
    lambda s: s['novelty_current'].update(horizon_minutes=60),
], ids=['partial', 'null', 'version', 'null-support', 'root-extra', 'support-extra', 'observed', 'quality',
    'global', 'authority', 'http', 'local', 'link', 'projection-hash', 'quote', 'offset', 'reconstruction',
    'body-basis', 'projection-basis', 'context', 'demo', 'synthetic', 'horizon'])
def test_frozen_invalid_extensions_cannot_insert_or_fall_back(monkeypatch, change):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    source, cutoff = packet(monkeypatch)
    source['novelty_previous']['horizon_minutes'] = 240
    change(source)
    result = module().event_novelty_features(source, cutoff, 'NAS100')
    assert result['features'] == {} and result['rejections']
    snapshot = dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources={'event': [source]})
    row = build_edge_family_evidence(snapshot)['families']['event']
    assert row['features'] == {} and row['rejected_sources']


def test_adapter_novelty_is_consensus_independent_zero_vote_and_legacy_absence(monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    source, cutoff = packet(monkeypatch)
    row = build_edge_family_evidence(dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources={'event': [source]}))['families']['event']
    assert row['features'] == {'event.fomc.policy_sentence_lexical_distance': 0.23529411764705888}
    assert row['available'] and row['voting_weight'] == 0 and not row['forecast_available']
    assert 'event.fomc.surprise' not in row['features']
    assert module().event_novelty_features({'source_id': 'legacy'}, cutoff, 'NAS100') == dict(features={}, feature_provenance={}, rejections=[])


def test_all_explicit_extensions_validate_before_reaction_or_surprise_insertion(monkeypatch):
    from test_edge_family_event_reaction import source as reaction_source
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    reaction = reaction_source()
    reaction.update(event_novelty_contract=None, actual=3, period='2026-04', unit='percent', consensus=dict(
        source_id='consensus', source_verified=True, instrument='BTCUSD', observed_ts=900,
        received_ts=950, release_id='release-1', period='2026-04', unit='percent', value=2))
    row = build_edge_family_evidence(dict(captured_ts=1400., instrument='BTCUSD', edge_family_sources={'event': [reaction]}))['families']['event']
    assert row['features'] == {}
    novelty, cutoff = packet(monkeypatch)
    novelty['event_reaction_contract'] = None
    row = build_edge_family_evidence(dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources={'event': [novelty]}))['families']['event']
    assert row['features'] == {}


@pytest.mark.parametrize('field,value', [('unit', 'PERCENT'), ('selector_version', 'ALL_TEXT'),
    ('received_ts', 1), ('supporting_source_ids', ['forged']), ('supporting_body_sha256', {}),
    ('supporting_projection_sha256', {}), ('novelty_contract_version', 'unknown')])
def test_normalized_proof_and_metric_recomputed_exactly(monkeypatch, field, value):
    source, cutoff = packet(monkeypatch)
    result = module().event_novelty_features(source, cutoff, 'NAS100')
    name = 'event.fomc.policy_sentence_lexical_distance'; meta = result['feature_provenance'][name]
    validate = module().novelty_provenance_reason
    assert validate(name, result['features'][name], meta, cutoff, 240, 'NAS100') is None
    assert validate(name, .5, meta, cutoff, 240, 'NAS100')
    changed = deepcopy(meta); changed[field] = value
    assert validate(name, result['features'][name], changed, cutoff, 240, 'NAS100')
    assert validate(name + '_unknown', .5, {}, cutoff, 240, 'NAS100')
    assert validate('event.fomc.renamed', result['features'][name], meta, cutoff, 240, 'NAS100')


def test_renamed_metadata_with_removed_contract_cannot_fall_back_to_consensus(monkeypatch):
    source, cutoff = packet(monkeypatch)
    result = module().event_novelty_features(source, cutoff, 'NAS100')
    meta = result['feature_provenance']['event.fomc.policy_sentence_lexical_distance']
    meta.pop('novelty_contract_version')
    assert module().novelty_provenance_reason('event.fomc.surprise', .5, meta, cutoff, 240, 'NAS100')


def test_runtime_validates_novelty_at_bundle_capture_and_preserves_unrelated(monkeypatch, tmp_path):
    from test_edge_family_source_runtime import bundle, write_bundle, load
    source, cutoff = packet(monkeypatch)
    payload = bundle(); payload['captured_ts'] = cutoff-15
    payload['instruments']['BTCUSD']['edge_family_sources'] = {'event': [source]}
    write_bundle(tmp_path, payload, mtime=cutoff-1)
    assert load(tmp_path, captured_ts=cutoff)['edge_family_sources'] == {}
    payload['captured_ts'] = cutoff
    write_bundle(tmp_path, payload, mtime=cutoff)
    assert load(tmp_path, captured_ts=cutoff)['edge_family_sources'] == {'event': [source]}
    source['novelty_current']['sentence_sha256'] = 'a'*64
    legacy = dict(source_id='legacy', source_verified=True, global_context=True,
                  observed_ts=cutoff-20, received_ts=cutoff-10, actual=1)
    payload['instruments']['BTCUSD']['edge_family_sources']['event'].append(legacy)
    write_bundle(tmp_path, payload, mtime=cutoff)
    assert load(tmp_path, captured_ts=cutoff)['edge_family_sources'] == {'event': [legacy]}


def novelty_archive(monkeypatch, **kwargs):
    from test_edge_family_dataset import snapshot, record, archive
    target = kwargs.pop('target', 'NAS100')
    source, cutoff = packet(monkeypatch, **kwargs)
    # Native date identity must remain real. Construct all independent cost,
    # position and path fixtures at the same capture instead of retiming a
    # completed proof (which would leave its embedded hashes inconsistent).
    monkeypatch.setattr('test_execution_cost_context.T0', cutoff)
    monkeypatch.setattr('test_edge_family_dataset.T0', cutoff)
    value = snapshot(); value['edge_family_sources'] = {'event': [source]}
    def retarget(item):
        if isinstance(item, dict): return {k: retarget(v) for k, v in item.items()}
        if isinstance(item, list): return [retarget(v) for v in item]
        return target if item == 'NAS100' else item
    retained = record(retarget(value)); retained['instrument'] = target
    return archive(retained), cutoff


def test_frozen_dataset_and_trainer_recompute_without_store_and_future_path_noninterference(monkeypatch):
    from test_edge_family_dataset import build, digest
    from test_edge_family_training import train, dataset
    value, cutoff = novelty_archive(monkeypatch)
    before = deepcopy(value)
    rows = build(value)['rows']
    assert rows and value == before
    assert all(r['features'] == {'event.fomc.policy_sentence_lexical_distance': 0.23529411764705888} for r in rows)
    actionable = [row for row in rows if row['action'] != 'HOLD']
    trained = train(dataset(actionable), trained_at=cutoff+20000)
    assert trained['diagnostics']['accepted_row_count'] == len(actionable)
    changed = deepcopy(value); changed['episodes'][0]['path_points'][1].update(r=.3, price=103.)
    changed['dataset_sha256'] = digest(changed['episodes'])
    assert [(r['features'], r['feature_provenance']) for r in build(changed)['rows']] == [(r['features'], r['feature_provenance']) for r in rows]
    name = 'event.fomc.policy_sentence_lexical_distance'
    for mutate in (lambda r: r['features'].__setitem__(name, .5),
                   lambda r: r['feature_provenance'][name].pop('novelty_contract_version'),
                   lambda r: r['feature_provenance'][name]['constituent_provenance'][0].update(sentence_sha256='a'*64)):
        tampered = deepcopy(actionable); mutate(tampered[0])
        assert train(dataset(tampered), trained_at=cutoff+20000)['diagnostics']['accepted_row_count'] == len(actionable)-1


def test_trainer_common_original_release_binding_allows_distinct_projection_and_local_clocks(monkeypatch):
    from test_edge_family_dataset import build
    from test_edge_family_event_reaction import source as reaction_source, seal
    from seiltanzer.edge_family_event_reaction import event_reaction_features
    from seiltanzer.edge_family_training import _row_reason
    value, cutoff = novelty_archive(monkeypatch, capture_delay=180, target='BTCUSD')
    row = next(r for r in build(value)['rows'] if r['action'] == 'EXIT')
    meta = row['feature_provenance']['event.fomc.policy_sentence_lexical_distance']
    native = meta['constituent_provenance'][0]
    reaction = reaction_source(n=2, published=960)
    delta = native['published_at']-960
    for part in (reaction, reaction['reaction_release'], reaction['reaction_series']):
        for key in ('published_at', 'received_ts', 'available_at', 'observed_ts'):
            if key in part: part[key] += delta
    for sample in reaction['reaction_series']['samples']:
        for index in (0, 1, 3): sample[index] += delta
    reaction['reaction_series'].update(received_ts=cutoff, available_at=cutoff)
    for sample in reaction['reaction_series']['samples']: sample[3] = cutoff
    seal(reaction['reaction_series'])
    reaction.update(received_ts=cutoff, available_at=cutoff, release_id=native['release_id'])
    reaction['reaction_release'].update(release_id=native['release_id'], source_id=native['source_id'],
        received_ts=native['received_ts'], available_at=native['received_ts'], body_sha256=native['body_sha256'])
    produced = event_reaction_features(reaction, cutoff, 'BTCUSD')
    assert produced['features']
    name = 'event.fomc.reaction_return_1m'
    row['features'][name] = produced['features'][name]
    row['feature_provenance'][name] = produced['feature_provenance'][name]
    row['feature_windows_sec'][name] = produced['feature_provenance'][name]['window_seconds']
    assert _row_reason(row, cutoff+20000) is None
    reaction['reaction_release']['body_sha256'] = 'b'*64
    row['feature_provenance'][name] = event_reaction_features(reaction, cutoff, 'BTCUSD')['feature_provenance'][name]
    assert _row_reason(row, cutoff+20000) == 'FEATURE_EVENT_RELEASE_IDENTITY_CONFLICT'


def native_engine(monkeypatch):
    store, pub = native_store(monkeypatch)
    return SimpleNamespace(passive=SimpleNamespace(_macro_data_factory=SimpleNamespace(fomc_deterministic_store=store)),
                           market=SimpleNamespace()), pub+30


def test_single_capture_commits_novelty_without_price_reaction_and_preserves_sentence(monkeypatch):
    from seiltanzer.edge_family_event_reaction import attach_observed_event_reaction_bounded
    engine, cutoff = native_engine(monkeypatch)
    live = dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources={'event': [dict(source_id='prior')]})
    asyncio.run(attach_observed_event_reaction_bounded(engine, live))
    assert live['edge_family_event_reaction_audit']['available'] is False
    assert live['edge_family_event_novelty_audit']['available'] is True
    assert len(live['edge_family_sources']['event']) == 2
    assert live['edge_family_sources']['event'][1]['novelty_current']['sentence_text'] == CURRENT
    before = deepcopy(live['edge_family_sources'])
    asyncio.run(attach_observed_event_reaction_bounded(engine, live))
    assert live['edge_family_sources'] == before
    assert live['edge_family_event_novelty_audit']['reason'] == 'EXISTING_NOVELTY_SOURCE_PRESERVED'


def test_single_capture_preserves_reaction_success_when_native_novelty_refuses():
    from test_edge_family_event_reaction import engine
    from seiltanzer.edge_family_event_reaction import attach_observed_event_reaction_bounded
    live = dict(captured_ts=1400., instrument='BTCUSD')
    asyncio.run(attach_observed_event_reaction_bounded(engine(), live))
    assert live['edge_family_event_reaction_audit']['available'] is True
    assert live['edge_family_event_novelty_audit']['available'] is False
    assert len(live['edge_family_sources']['event']) == 1


def test_novelty_byte_refusal_keeps_existing_facts_and_private_input_retains_long_sentence(monkeypatch):
    from seiltanzer.edge_family_event_reaction import _private_capture_input
    engine, cutoff = native_engine(monkeypatch)
    prior = {'event': [dict(source_id='prior', padding='x'*6500)]}
    live = dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources=deepcopy(prior))
    assert hasattr(module(), 'attach_observed_event_novelty'), 'native novelty attachment missing'
    module().attach_observed_event_novelty(engine, live)
    assert live['edge_family_sources'] == prior
    assert live['edge_family_event_novelty_audit']['reason'] == 'SELECTED_SOURCE_FACTS_EXCEED_REVIEW_BYTE_BUDGET'
    source, cutoff = packet(monkeypatch, current=CURRENT.replace('percent.', 'percent and ' + 'carefully '*15 + 'considered.'))
    frozen = dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources={'event': [source]})
    private = _private_capture_input(frozen)
    assert len(private['edge_family_sources']['event'][0]['novelty_current']['sentence_text']) > 128
    assert private['edge_family_sources'] == frozen['edge_family_sources']


@pytest.mark.parametrize('cancel', [False, True], ids=['timeout', 'cancel'])
def test_late_novelty_worker_never_mutates_live_review_and_overlap_reports_both(monkeypatch, cancel):
    from seiltanzer import edge_family_event_reaction as reaction
    engine, cutoff = native_engine(monkeypatch)
    assert hasattr(module(), 'attach_observed_event_novelty'), 'native novelty attachment missing'
    original = module().attach_observed_event_novelty
    began, release, ended = threading.Event(), threading.Event(), threading.Event()
    def blocked(engine, private):
        began.set(); assert release.wait(3)
        original(engine, private); ended.set()
    monkeypatch.setattr(module(), 'attach_observed_event_novelty', blocked)
    async def run():
        live = dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources={'event': [dict(source_id='prior')]})
        before = deepcopy(live['edge_family_sources'])
        task = asyncio.create_task(reaction.attach_observed_event_reaction_bounded(engine, live))
        try:
            assert await asyncio.to_thread(began.wait, 1)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
                assert live['edge_family_event_novelty_audit']['reason'] == 'NOVELTY_CAPTURE_CANCELLED'
            else:
                await task
                assert live['edge_family_event_novelty_audit']['reason'] == 'NOVELTY_CAPTURE_BUDGET_EXCEEDED'
            assert live['edge_family_sources'] == before
            overlap = dict(captured_ts=cutoff, instrument='NAS100')
            await reaction.attach_observed_event_reaction_bounded(engine, overlap)
            assert overlap['edge_family_event_reaction_audit']['reason'] == 'REACTION_CAPTURE_IN_PROGRESS'
            assert overlap['edge_family_event_novelty_audit']['reason'] == 'NOVELTY_CAPTURE_IN_PROGRESS'
            frozen = json.dumps(live, sort_keys=True)
            release.set(); assert await asyncio.to_thread(ended.wait, 1)
            await asyncio.sleep(0)
            assert json.dumps(live, sort_keys=True) == frozen
        finally:
            release.set()
    asyncio.run(run())


def test_queue_time_and_invalid_private_input_share_one_budget_and_two_audits(monkeypatch):
    from seiltanzer import edge_family_event_reaction as reaction
    engine, cutoff = native_engine(monkeypatch)
    occupied, release = threading.Event(), threading.Event()
    def occupy():
        occupied.set(); assert release.wait(3)
    async def run():
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop.set_default_executor(executor)
            holder = loop.run_in_executor(None, occupy)
            assert occupied.wait(1)
            live = dict(captured_ts=cutoff, instrument='NAS100')
            try:
                await reaction.attach_observed_event_reaction_bounded(engine, live)
                assert live['edge_family_event_novelty_audit']['reason'] == 'NOVELTY_CAPTURE_BUDGET_EXCEEDED'
                frozen = json.dumps(live, sort_keys=True)
                release.set(); await holder
                await loop.run_in_executor(None, lambda: None)
                assert json.dumps(live, sort_keys=True) == frozen
            finally:
                release.set()
            after = dict(captured_ts=cutoff, instrument='NAS100')
            await reaction.attach_observed_event_reaction_bounded(engine, after)
            assert after['edge_family_event_novelty_audit']['available'] is True
            invalid = dict(captured_ts=cutoff, instrument='NAS100', edge_family_sources={'event': [object()]})
            await reaction.attach_observed_event_reaction_bounded(engine, invalid)
            assert invalid['edge_family_event_reaction_audit']['reason'] == 'REACTION_CAPTURE_INPUT_INVALID'
            assert invalid['edge_family_event_novelty_audit']['reason'] == 'NOVELTY_CAPTURE_INPUT_INVALID'
    asyncio.run(run())


def test_unterminated_second_qualifying_region_cannot_hide_behind_one_complete_sentence(monkeypatch):
    store, pub = native_store(monkeypatch, current=CURRENT + ' ' + CURRENT[:-1])
    result = module().build_received_event_novelty_source(read(store, pub+30), pub+30)
    assert result['source'] is None and result['rejections']


@pytest.mark.parametrize('location', ['pair', 'current', 'previous'])
def test_native_builder_cannot_strip_explicit_synthetic_or_context_scope(monkeypatch, location):
    store, pub = native_store(monkeypatch)
    pair = read(store, pub+30)
    item = pair if location == 'pair' else pair[location]
    item['synthetic'] = True
    assert module().build_received_event_novelty_source(pair, pub+30)['source'] is None


def test_complete_unique_token_set_is_bounded_without_truncation(monkeypatch):
    # 117 extra distinct words plus the original 15 tokens exceed128 while
    # fitting the768-byte sentence limit. No dynamic token truncation is allowed.
    words = ['x' + chr(97+i//26) + chr(97+i%26) for i in range(117)]
    store, pub = native_store(monkeypatch, current=CURRENT[:-1] + ' ' + ' '.join(words) + '.')
    result = module().build_received_event_novelty_source(read(store, pub+30), pub+30)
    assert result['source'] is None
    assert result['rejections'][0]['reason'] == 'NOVELTY_COMPLETE_TOKEN_SET_BOUND_INVALID'


def test_full_ascii_escaped_proof_bound_can_refuse_individually_bounded_sentences(monkeypatch):
    sentence = CURRENT[:-1] + ' ' + 'é'*250 + '.'
    store, pub = native_store(monkeypatch, current=sentence, previous=sentence)
    result = module().build_received_event_novelty_source(read(store, pub+30), pub+30)
    assert result['source'] is None
    assert result['rejections'][0]['reason'] == 'NOVELTY_BYTE_BOUND_EXCEEDED'


def test_explicit_retained_novelty_metadata_and_boolean_aggregate_cannot_be_renamed(monkeypatch):
    source, cutoff = packet(monkeypatch)
    result = module().event_novelty_features(source, cutoff, 'NAS100')
    name = 'event.fomc.policy_sentence_lexical_distance'
    meta = result['feature_provenance'][name]
    stripped = {key: value for key, value in meta.items() if key not in ('novelty_contract_version',
        'selector_version', 'tokenizer_version', 'metric_version', 'supporting_projection_sha256')}
    assert module().novelty_provenance_reason('event.fomc.surprise', .5, stripped, cutoff, 240, 'NAS100')
    changed = deepcopy(meta); changed['quality'] = True
    assert module().novelty_provenance_reason(name, result['features'][name], changed, cutoff, 240, 'NAS100')


def test_missing_native_table_has_precise_unavailable_reason(monkeypatch):
    store, pub = native_store(monkeypatch)
    store._conn.execute('DROP TABLE macro_fomc_deterministic_releases')
    assert read(store, pub+30) == dict(status='UNAVAILABLE', reason='NO_FOMC_DETERMINISTIC_TABLE')


def renamed_consensus_row(monkeypatch):
    """Exact I1 mutation of a valid EXIT row, with independent valid consensus."""
    from test_edge_family_dataset import build
    archive, cutoff = novelty_archive(monkeypatch)
    row = next(r for r in build(archive)['rows'] if r['action'] == 'EXIT')
    original = 'event.fomc.policy_sentence_lexical_distance'
    value = row['features'].pop(original)
    meta = row['feature_provenance'].pop(original)
    for key in ('novelty_contract_version', 'selector_version', 'tokenizer_version',
                'metric_version', 'supporting_projection_sha256', 'unit', 'authority_role',
                'constituent_provenance', 'supporting_body_sha256'):
        meta.pop(key)
    consensus = dict(source_id='official:consensus', source_verified=True, source_instrument='NAS100',
        global_context=True, proxy_mapping=None, observed_ts=meta['published_at']-60,
        received_ts=meta['published_at']-30, published_at=meta['published_at']-60,
        max_age_sec=14400., quality=1., dependency_group='release:' + meta['release_id'],
        release_id=meta['release_id'], period='2026-04', unit='percent')
    meta.update(period='2026-04', unit='percent', consensus_source_id='official:consensus',
                consensus_received_ts=consensus['received_ts'], supporting_source_ids=['official:consensus'],
                consensus_provenance=consensus)
    row['features']['event.fomc.surprise'] = value
    row['feature_provenance']['event.fomc.surprise'] = meta
    return row, cutoff


def test_i1_exact_retained_root_rename_cannot_enter_trainer_as_legacy_surprise(monkeypatch):
    from seiltanzer.edge_family_training import _row_reason
    row, cutoff = renamed_consensus_row(monkeypatch)
    name = 'event.fomc.surprise'; meta = row['feature_provenance'][name]
    assert meta['root_provenance']['authority_role'] == 'OBSERVED_OFFICIAL_TEXT_NOT_CAUSAL_EFFECT_OR_BROKER_OUTCOME'
    assert _row_reason(row, cutoff+20000) == 'FEATURE_NOVELTY_PROVENANCE_INVALID'
    assert module().novelty_provenance_reason(name, row['features'][name], meta, cutoff, 240, 'NAS100') == 'FEATURE_NOVELTY_PROVENANCE_INVALID'


@pytest.mark.parametrize('path,proof', [
    ('root_provenance', {'authority_role': 'OBSERVED_OFFICIAL_TEXT_NOT_CAUSAL_EFFECT_OR_BROKER_OUTCOME'}),
    ('root_provenance', {'source_id': 'novelty:native-release'}),
    ('root_provenance', {'event_novelty_contract': None}),
    ('root_provenance', {'novelty_current': None}),
    ('constituent_provenance', [{'projection_hash_kind': 'FROZEN_POLICY_SENTENCE_UTF8_SHA256'}]),
    ('constituent_provenance', [{'sentence_text': CURRENT}]),
    ('constituent_provenance', [{'sentence_sha256': 'a'*64}]),
    ('constituent_provenance', [{'novelty_contract_version': 'unknown'}]),
    ('root_provenance', {'constituent_provenance': [{'sentence_sha256': 'a'*64}]}),
    ('constituent_provenance', [{'root_provenance': {'source_id': 'novelty:native-release'}}]),
], ids=['root-authority', 'root-source-id', 'root-contract', 'root-extension',
        'support-projection-basis', 'support-sentence', 'support-digest', 'support-contract',
        'root-retained-support', 'support-retained-root'])
def test_i1_nested_root_and_support_dispatch_cannot_bypass_by_clearing_top_fields(monkeypatch, path, proof):
    from seiltanzer.edge_family_training import _row_reason
    row, cutoff = renamed_consensus_row(monkeypatch)
    name = 'event.fomc.surprise'; meta = row['feature_provenance'][name]
    meta['source_id'] = 'official:actual-release'
    meta.pop('root_provenance')
    meta[path] = proof
    assert module().novelty_provenance_reason(name, row['features'][name], meta, cutoff, 240, 'NAS100') == 'FEATURE_NOVELTY_PROVENANCE_INVALID'
    assert _row_reason(row, cutoff+20000) == 'FEATURE_NOVELTY_PROVENANCE_INVALID'


def test_i1_legacy_event_consensus_without_novelty_declarations_remains_admitted(monkeypatch):
    from seiltanzer.edge_family_training import _row_reason
    row, cutoff = renamed_consensus_row(monkeypatch)
    name = 'event.fomc.surprise'; meta = row['feature_provenance'][name]
    meta['source_id'] = 'official:actual-release'
    meta.pop('root_provenance')
    assert module().novelty_provenance_reason(name, row['features'][name], meta, cutoff, 240, 'NAS100') is None
    assert _row_reason(row, cutoff+20000) is None
    meta['consensus_provenance']['received_ts'] = meta['published_at']
    assert _row_reason(row, cutoff+20000) == 'FEATURE_PREPUBLICATION_CONSENSUS_INVALID'


def test_i1_dispatch_bounds_known_proof_paths_without_scanning_arbitrary_metadata():
    class Oversized(list):
        def __iter__(self):
            raise AssertionError('oversized proof scanned')
    validate = module().novelty_provenance_reason
    assert validate('event.fomc.surprise', 1., {'constituent_provenance': Oversized([{}]*129)},
                    100., 240., 'NAS100') == 'FEATURE_NOVELTY_PROVENANCE_INVALID'
    nested = {'source_id': 'novelty:native-release'}
    for _ in range(9): nested = {'root_provenance': nested}
    assert validate('event.fomc.surprise', 1., nested, 100., 240., 'NAS100') == 'FEATURE_NOVELTY_PROVENANCE_INVALID'
    # Unknown data is not another admitted proof path. Dispatch never traverses
    # arbitrary objects/keys, while strict novelty admission retains its own
    # complete JSON byte/depth/schema checks when the extension is explicit.
    assert validate('event.fomc.surprise', 1., {'ordinary_note': object()}, 100., 240., 'NAS100') is None
