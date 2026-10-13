"""Regular immutable SQLite capture, independent of a position or AI review."""
import hashlib
import importlib
import json
import os
from types import SimpleNamespace

import pytest

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.passive_learning import PassiveLearningEngine
from seiltanzer.edge_family_source_runtime import CONTRACT, POLICY, PUBLICATION
from seiltanzer.edge_family_native_history import native_history

T0 = 1_800_000_000.
SHA = 'a' * 40


def module():
    return importlib.import_module('seiltanzer.edge_family_passive_capture')


def bundle(tmp_path, **changes):
    source = dict(source_id='book-receipt', source_verified=True, instrument='NAS100',
        observed_ts=T0-5, received_ts=T0-4, available_at=T0-4, quality=.8,
        kind='exchange_order_book', venue='test-venue',
        previous_top=dict(ts=T0-20, bid_price=99., ask_price=101., bid_size=10., ask_size=10.),
        current_top=dict(ts=T0-5, bid_price=100., ask_price=102., bid_size=12., ask_size=8.))
    value = dict(contract_version=CONTRACT, edge_policy=POLICY, production_authority=False,
        publication_contract_version=PUBLICATION, published_for_sha=SHA, captured_ts=T0-3,
        instruments={'NAS100': {'edge_family_sources': {'order_flow': [source]}, 'readiness': {}}})
    value.update(changes)
    path=tmp_path/'research'/'edge_family_sources_latest.json'
    path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(value));os.utime(path,(T0-2,T0-2))
    return path


@pytest.fixture
def owner(tmp_path):
    settings=Settings(demo=False,data_dir=str(tmp_path))
    cache=DiskCache(str(tmp_path/'cache.db'))
    passive=PassiveLearningEngine(str(tmp_path/'trades.db'),settings,cache)
    engine=SimpleNamespace(settings=settings,passive=passive)
    yield engine
    passive.close();cache.close()


def capture(owner, features=None, *, ts=T0):
    features={'source_observation_ts':ts} if features is None else features
    ids=owner.passive.capture_observation(instrument='NAS100',captured_ts=ts,market_price=100.,
        features=features,forecast={'reference_volatility_annual':.2,'forecast_created_ts':ts},
        provenance={},trigger_reason='test',evidence_eligible=True)
    assert ids
    return owner.passive._conn.execute('SELECT features_json,created_ts FROM passive_market_observations WHERE observation_id=?',(ids[0],)).fetchone()


def test_regular_capture_without_trade_enters_immutable_native_history(owner,tmp_path,monkeypatch):
    bundle(tmp_path)
    module().install_family_source_capture(owner,expected_sha=SHA)
    monkeypatch.setattr('seiltanzer.passive_learning.time.time',lambda:T0+1)
    original={'source_observation_ts':T0}
    row=capture(owner,original);raw=row['features_json'];frozen=json.loads(raw)
    assert original=={'source_observation_ts':T0}
    assert frozen['edge_family_sources']['order_flow'][0]['received_ts']==T0-4
    assert frozen['edge_family_source_bundle_audit']['models_loaded']==0
    history=native_history([dict(instrument='NAS100',captured_ts=T0,original_created_ts=row['created_ts'],
        frozen_features_json=raw,features_sha256=hashlib.sha256(raw.encode()).hexdigest(),source_verified=True)],captured_ts=T0+2)
    assert history['NAS100']['order_flow'][0]['features']['flow.depth']==20.
    assert history['NAS100']['order_flow'][0]['feature_provenance']['flow.depth']['received_ts']==T0-4
    # Later local replacement cannot amend the already persisted source bytes.
    bundle(tmp_path,published_for_sha='b'*40)
    assert owner.passive._conn.execute('SELECT features_json FROM passive_market_observations LIMIT 1').fetchone()[0]==raw


@pytest.mark.parametrize('change',[{'published_for_sha':'b'*40},{'captured_ts':T0+1},{'captured_ts':T0-8*3600-1}])
def test_unavailable_generation_or_clock_keeps_native_capture(owner,tmp_path,change):
    bundle(tmp_path,**change);module().install_family_source_capture(owner,expected_sha=SHA)
    frozen=json.loads(capture(owner)['features_json'])
    assert frozen['source_observation_ts']==T0
    assert not frozen.get('edge_family_sources')
    assert frozen['edge_family_source_bundle_audit']['available'] is False


def test_missing_optional_file_and_provider_failure_do_not_drop_native_capture(owner,monkeypatch):
    m=module();m.install_family_source_capture(owner,expected_sha=SHA)
    assert json.loads(capture(owner)['features_json'])['source_observation_ts']==T0
    def broken(*args,**kwargs):raise OSError('unavailable local file')
    monkeypatch.setattr(m,'load_family_source_context',broken)
    assert json.loads(capture(owner,ts=T0+10)['features_json'])['source_observation_ts']==T0+10


def test_existing_family_is_preserved_and_engine_install_is_idempotent(owner,tmp_path):
    bundle(tmp_path);m=module();m.install_family_source_capture(owner,expected_sha=SHA)
    method=PassiveLearningEngine.capture_observation;m.install_family_source_capture(owner,expected_sha='b'*40)
    assert PassiveLearningEngine.capture_observation is method
    assert owner.passive._family_source_capture.expected_sha==SHA
    prior={'source_observation_ts':T0,'edge_family_sources':{'order_flow':[]}}
    assert json.loads(capture(owner,prior)['features_json'])['edge_family_sources']['order_flow']==[]
    assert prior['edge_family_sources']=={'order_flow':[]}
    audit=json.loads(capture(owner,prior,ts=T0+1)['features_json'])['edge_family_source_bundle_audit']
    assert audit['available'] is False
    assert audit['attached_source_counts']=={}


def test_demo_capture_is_not_enriched_with_real_bundle(owner,tmp_path):
    bundle(tmp_path);owner.settings.demo=True
    module().install_family_source_capture(owner,expected_sha=SHA)
    assert 'edge_family_sources' not in json.loads(capture(owner)['features_json'])


def test_optional_bound_never_prunes_existing_capture(owner,tmp_path):
    bundle(tmp_path);module().install_family_source_capture(owner,expected_sha=SHA)
    prior={'source_observation_ts':T0,'edge_family_sources':{'macro':[{'private':'x'*8000}]}}
    frozen=json.loads(capture(owner,prior)['features_json'])
    assert frozen['edge_family_sources']==prior['edge_family_sources']
    assert frozen['edge_family_source_bundle_audit']['available'] is False
    assert frozen['edge_family_source_bundle_audit']['attached_source_counts']=={}


@pytest.mark.parametrize('before',[True,False])
def test_macro_capture_hook_composes_in_both_installation_orders(owner,tmp_path,monkeypatch,before):
    from seiltanzer import macro_t0_context
    m=module();bundle(tmp_path)
    monkeypatch.setattr(PassiveLearningEngine,'capture_observation',PassiveLearningEngine.capture_observation)
    monkeypatch.setattr(macro_t0_context,'_INSTALLED',False)
    monkeypatch.setattr(macro_t0_context,'build_macro_t0_context',lambda factory,cutoff:
        {'captured_ts':cutoff,'candidate_vector':{},'production_authority':False})
    if before:macro_t0_context.install_macro_t0_context(owner,object())
    m.install_family_source_capture(owner,expected_sha=SHA)
    if not before:macro_t0_context.install_macro_t0_context(owner,object())
    frozen=json.loads(capture(owner)['features_json'])
    assert frozen['macro_context_v1']['captured_ts']==T0
    assert frozen['edge_family_sources']['order_flow'][0]['received_ts']==T0-4


def test_other_engine_cannot_borrow_sources_and_raw_archives_are_not_attached(owner,tmp_path):
    path=bundle(tmp_path);payload=json.loads(path.read_text())
    payload['cftc_revision_archives']={'XAU':{'raw_body_utf8':'private-archive-marker'}}
    path.write_text(json.dumps(payload));os.utime(path,(T0-2,T0-2))
    m=module();m.install_family_source_capture(owner,expected_sha=SHA)
    assert 'private-archive-marker' not in capture(owner)['features_json']
    other=tmp_path/'other';other.mkdir();settings=Settings(demo=False,data_dir=str(other))
    cache=DiskCache(str(other/'cache.db'));passive=PassiveLearningEngine(str(other/'trades.db'),settings,cache)
    second=SimpleNamespace(settings=settings,passive=passive)
    try:
        m.install_family_source_capture(second,expected_sha=SHA)
        assert not json.loads(capture(second)['features_json']).get('edge_family_sources')
        assert json.loads(capture(owner,ts=T0+1)['features_json'])['edge_family_sources']['order_flow']
    finally:passive.close();cache.close()


def test_later_source_receipt_cannot_advance_the_original_quote_clock(owner,tmp_path):
    path=bundle(tmp_path);payload=json.loads(path.read_text())
    payload['instruments']['NAS100']['edge_family_sources']['order_flow'][0]['received_ts']=T0+1
    path.write_text(json.dumps(payload));os.utime(path,(T0-2,T0-2))
    module().install_family_source_capture(owner,expected_sha=SHA)
    frozen=json.loads(capture(owner)['features_json'])
    assert frozen['source_observation_ts']==T0
    assert not frozen.get('edge_family_sources')
    assert frozen['edge_family_source_bundle_audit']['rejected_source_n']==1


@pytest.mark.parametrize('key',['timestamp','as_of'])
def test_optional_passive_clock_incompatibility_preserves_native_capture(owner,tmp_path,key):
    from seiltanzer.edge_family_source_runtime import load_family_source_context
    path=bundle(tmp_path);payload=json.loads(path.read_text())
    payload['instruments']['NAS100']['edge_family_sources']['order_flow'][0][key]=T0+1
    path.write_text(json.dumps(payload));os.utime(path,(T0-2,T0-2))
    loaded=load_family_source_context(owner,{'instrument':'NAS100','captured_ts':T0},SHA)
    assert loaded['edge_family_source_bundle_audit']['available'] is True
    original={'source_observation_ts':T0}
    capture(owner,original)
    module().install_family_source_capture(owner,expected_sha=SHA)
    frozen=json.loads(capture(owner,original,ts=T0+.5)['features_json'])
    assert frozen['source_observation_ts']==T0
    assert not frozen.get('edge_family_sources')
    assert 'edge_family_source_bundle_audit' not in frozen
    assert original=={'source_observation_ts':T0}
    with pytest.raises(ValueError,match='post-capture feature timestamp'):
        capture(owner,{key:T0+2},ts=T0+1)


def test_real_application_binds_regular_capture_before_any_trade_or_verdict(tmp_path,monkeypatch):
    from seiltanzer.app import create_app
    bundle(tmp_path);monkeypatch.setattr(module(),'runtime_git_sha',lambda:SHA)
    app=create_app(Settings(demo=False,stream=False,data_dir=str(tmp_path)))
    owner=app.state.engine
    try:
        assert owner.passive._conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]==0
        frozen=json.loads(capture(owner)['features_json'])
        assert frozen['edge_family_sources']['order_flow'][0]['source_id']=='book-receipt'
    finally:owner.close()


def test_optional_facts_cannot_make_an_exportable_native_record_too_large(owner,tmp_path,monkeypatch):
    from seiltanzer.native_family_capture_projection import project_capture
    from seiltanzer import macro_t0_context
    bundle(tmp_path);m=module()
    monkeypatch.setattr(PassiveLearningEngine,'capture_observation',PassiveLearningEngine.capture_observation)
    monkeypatch.setattr(macro_t0_context,'_INSTALLED',False)
    monkeypatch.setattr(macro_t0_context,'build_macro_t0_context',lambda factory,cutoff:
        {'captured_ts':cutoff,'private_padding':'m'*1000,'production_authority':False})
    macro_t0_context.install_macro_t0_context(owner,object())
    probe=capture(owner)['features_json']
    original={'source_observation_ts':T0+1,'private_padding':'x'*(256000-len(probe.encode())-128)}
    raw=capture(owner,original,ts=T0+1)['features_json']
    assert project_capture(raw,hashlib.sha256(raw.encode()).hexdigest()) is not None
    m.install_family_source_capture(owner,expected_sha=SHA)
    raw=capture(owner,{**original,'source_observation_ts':T0+2},ts=T0+2)['features_json']
    assert project_capture(raw,hashlib.sha256(raw.encode()).hexdigest()) is not None
    assert not json.loads(raw).get('edge_family_sources')
