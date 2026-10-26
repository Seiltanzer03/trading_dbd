import time
import pytest
from test_ai_snapshot_materializer import FakeEngine, snapshot
from seiltanzer.ai_snapshot_materializer import AISnapshotMaterializer, SnapshotNotReady


def canonical_engine(quote):
    engine = FakeEngine()
    engine.journal.active_trade = lambda: {'id':1,'instrument':'NAS100','entry':100.,'stop':90.,'take':130.,'direction':'long'}
    engine.canonical_tick_payload = lambda **_: {'instrument':'NAS100','feeds':{'price':dict(quote)}}
    return engine


def test_quote_recovery_during_build_invalidates_missing_authority_snapshot():
    quote={'value':101.,'status':'no_data','fresh':False,'source':'TradingView stream OANDA:NAS100USD'}
    engine=canonical_engine(quote)
    def builder(_):
        row=snapshot();row['strategy']={'instrument':'NAS100'}
        row['policy_manager']['input_audit']={'rows':{'instrument_price':{**quote,'available':False}}}
        quote.update(status='live',fresh=True)
        return row
    mat=AISnapshotMaterializer(engine,builder)
    mat._build_once()
    assert mat._event_reason()=='PRICE_AVAILABILITY_CHANGED'
    with pytest.raises(SnapshotNotReady):mat.cached_build_snapshot(engine)


def test_same_price_new_authoritative_source_invalidates_fallback_snapshot():
    quote={'value':101.,'status':'live','fresh':True,'source':'Bybit NAS100','production_authority':False}
    engine=canonical_engine(quote)
    def builder(_):
        row=snapshot();row['strategy']={'instrument':'NAS100'}
        row['policy_manager']['input_audit']={'rows':{'instrument_price':{**quote,'available':True}}}
        return row
    mat=AISnapshotMaterializer(engine,builder);mat._build_once()
    quote.update(source='TradingView stream OANDA:NAS100USD',production_authority=True)
    assert mat._event_reason()=='PRICE_AUTHORITY_CHANGED'
    with pytest.raises(SnapshotNotReady):mat.cached_build_snapshot(engine)


@pytest.mark.parametrize('authority',[False,True])
@pytest.mark.parametrize('compact',[False,True])
def test_real_input_audit_preserves_authority_and_unchanged_quote_does_not_rebuild(authority,compact):
    from copy import deepcopy
    from seiltanzer.ai_policy_v6 import _input_audit
    from seiltanzer.ai_policy_v7 import _enrich_input_audit
    quote={'value':101.,'status':'live','fresh':True,'source':'Bybit NAS100',
           'production_authority':authority,'derived':False,'is_proxy':True,'ts':time.time()}
    original=deepcopy(quote);engine=canonical_engine(quote)
    def builder(_):
        row=snapshot();row['strategy']={'instrument':'NAS100'}
        tick=engine.canonical_tick_payload()
        row['policy_manager']['input_audit']=_input_audit(tick,{})
        _enrich_input_audit(row['policy_manager'],tick,{})
        if compact:
            from seiltanzer.ai_snapshot_budget_guard import _strict_authoritative_compaction
            _strict_authoritative_compaction(row)
        return row
    mat=AISnapshotMaterializer(engine,builder);mat._build_once()
    assert mat._event_reason() is None
    frozen=mat.cached_build_snapshot(engine)
    price=frozen['policy_manager']['input_audit']['rows']['instrument_price']
    assert {k:price[k] for k in ('production_authority','derived','is_proxy')}=={
        'production_authority':authority,'derived':False,'is_proxy':True}
    assert quote==original
    quote['production_authority']=not authority
    assert mat._event_reason()=='PRICE_AUTHORITY_CHANGED'


def test_audit_does_not_invent_optional_price_provenance():
    from seiltanzer.ai_policy_v6 import _input_audit
    price=_input_audit({'feeds':{'price':{'value':101.,'status':'live','source':'legacy'}}},{})['rows']['instrument_price']
    assert not {'production_authority','derived','is_proxy'} & price.keys()


def test_family_inputs_freeze_with_cached_snapshot_and_request_cannot_replace_them(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from seiltanzer.app import _attach_family_source_bundle
    engine=FakeEngine();engine.settings=SimpleNamespace(data_dir=str(tmp_path))
    observed=[]
    def load(_engine, row, **kwargs):
        observed.append(row['captured_ts'])
        return {'edge_family_sources':{'macro':[{'source_id':'original','features':{'macro.value':1}}]},
                'edge_family_source_bundle_audit':{'available':True,'reason':'EXACT_SHA_LOCAL_SOURCE_FACTS'}}
    monkeypatch.setattr('seiltanzer.edge_family_source_runtime.load_family_source_context',load)
    mat=AISnapshotMaterializer(engine,lambda _:snapshot());mat._build_once()
    frozen=mat.cached_build_snapshot(engine)
    assert frozen['edge_family_sources']['macro'][0]['source_id']=='original'
    assert len(observed)==1
    def future(*args,**kwargs):raise AssertionError('cached review must not read future publication')
    monkeypatch.setattr('seiltanzer.edge_family_source_runtime.load_family_source_context',future)
    _attach_family_source_bundle(engine,frozen)
    assert frozen['edge_family_sources']['macro'][0]['features']=={'macro.value':1}


def test_new_model_publication_invalidates_an_older_cached_review(tmp_path):
    from types import SimpleNamespace
    engine=FakeEngine();engine.settings=SimpleNamespace(data_dir=str(tmp_path))
    mat=AISnapshotMaterializer(engine,lambda _:snapshot());mat._build_once()
    directory=tmp_path/'research';directory.mkdir(exist_ok=True)
    (directory/'mathematical_edge_latest.json').write_text('{}')
    assert mat._event_reason()=='EXPERT_PUBLICATION_CHANGED'
    with pytest.raises(SnapshotNotReady):mat.cached_build_snapshot(engine)


def test_publication_changed_during_build_is_not_absorbed_by_completion_baseline(tmp_path):
    from types import SimpleNamespace
    engine=FakeEngine();engine.settings=SimpleNamespace(data_dir=str(tmp_path))
    directory=tmp_path/'research';directory.mkdir()
    def builder(_):
        row=snapshot()
        (directory/'edge_family_sources_latest.json').write_text('{}')
        return row
    mat=AISnapshotMaterializer(engine,builder);mat._build_once()
    assert mat._event_reason()=='EXPERT_PUBLICATION_CHANGED'


@pytest.mark.parametrize('tier',['strict','emergency'])
def test_frozen_input_marker_and_source_failure_survive_budget(tier):
    from seiltanzer import ai_verdict, ai_snapshot_budget_guard as guard
    row=snapshot(); row['frozen_expert_inputs']={'version':'ai-frozen-expert-inputs-v1','captured_ts':row['captured_ts']}
    row['edge_family_source_bundle_audit']={'available':False,'reason':'SOURCE_BUNDLE_FILE_STALE_OR_AFTER_SNAPSHOT'}
    guard._strict_authoritative_compaction(row)
    if tier=='emergency':guard._emergency_authoritative_compaction(row,ai_verdict)
    assert row['frozen_expert_inputs']['captured_ts']==row['captured_ts']
    assert row['edge_family_source_bundle_audit']['reason']=='SOURCE_BUNDLE_FILE_STALE_OR_AFTER_SNAPSHOT'


def test_report_discloses_specific_source_failure_instead_of_only_generic_missing(monkeypatch):
    from seiltanzer import ai_runtime_report_v20 as report
    monkeypatch.setattr(report,'_BASE_QUALITY_LINES',lambda _:[],raising=False)
    row=snapshot()
    row['edge_family_source_bundle_audit']={'available':False,'reason':'SOURCE_BUNDLE_FILE_STALE_OR_AFTER_SNAPSHOT'}
    row['edge_family_market_context']={'available':False,'reason':'LATEST_COMPLETED_MARKET_BAR_UNAVAILABLE'}
    text='\n'.join(report._quality_lines(row))
    assert 'SOURCE_BUNDLE_FILE_STALE_OR_AFTER_SNAPSHOT' in text
    assert 'LATEST_COMPLETED_MARKET_BAR_UNAVAILABLE' in text


def test_actual_new_completed_bar_invalidates_missing_feature_review():
    engine=FakeEngine()
    engine.market.intraday_source_authority={'provider':'Yahoo','source_symbol':'^NDX',
        'target_instrument':'NAS100','source_verified':True,'observed_ts':1200.,'available_at':1260.}
    mat=AISnapshotMaterializer(engine,lambda _:snapshot());mat._build_once()
    engine.market.intraday_source_authority.update(observed_ts=1260.,available_at=1320.)
    assert mat._event_reason() is None  # no new completed 5m boundary
    engine.market.intraday_source_authority.update(observed_ts=1500.,available_at=1560.)
    assert mat._event_reason()=='COMPLETED_MARKET_BAR_CHANGED'


def test_background_reaction_deadline_does_not_wait_for_abandoned_worker(tmp_path,monkeypatch):
    import threading,time
    from types import SimpleNamespace
    from seiltanzer.ai_frozen_expert_inputs import freeze_expert_inputs
    engine=FakeEngine();engine.settings=SimpleNamespace(data_dir=str(tmp_path))
    release=threading.Event(); ended=threading.Event(); seen=[]
    def slow(_engine, private):
        seen.append(private)
        try:
            release.wait(2)
            private['edge_family_sources']={'event':[{'source_id':'late'}]}
        finally:ended.set()
    monkeypatch.setattr('seiltanzer.edge_family_event_reaction.attach_observed_event_reaction',slow)
    row=snapshot();start=time.monotonic()
    try:
        freeze_expert_inputs(engine,row)
        assert time.monotonic()-start<1
        assert seen and seen[0] is not row
        assert row['edge_family_event_reaction_audit']['reason']=='REACTION_CAPTURE_BUDGET_EXCEEDED'
        release.set();assert ended.wait(1)
        assert 'edge_family_sources' not in row
    finally:release.set()


def test_cached_api_uses_original_expert_inputs_without_request_time_provider_reads(tmp_path,monkeypatch):
    from copy import deepcopy
    from seiltanzer import app as app_module
    from test_ai_verdict_api import _client,_snapshot
    seen=[]
    def provider(row):
        seen.append(row)
        assert row['edge_family_market_context']['reason']=='ORIGINAL_CONTEXT'
        assert row['macro_context_v1']['reason']=='ORIGINAL_MACRO'
        return {'verdict':'frozen context','model':'isolated-fixture'}
    app,client=_client(tmp_path,monkeypatch,provider)
    row=_snapshot();row['frozen_expert_inputs']={'version':'ai-frozen-expert-inputs-v1','captured_ts':row['captured_ts']}
    row['edge_family_market_context']={'available':False,'reason':'ORIGINAL_CONTEXT'}
    row['macro_context_v1']={'available':False,'reason':'ORIGINAL_MACRO'}
    monkeypatch.setattr(app_module,'build_snapshot',lambda _:deepcopy(row))
    app.state.engine.passive._macro_data_factory=object()
    def forbidden(*args,**kwargs):raise AssertionError('cached route read newer expert input')
    for name in ('seiltanzer.edge_family_source_runtime.load_family_source_context',
                 'seiltanzer.edge_family_market.attach_market_family_context',
                 'seiltanzer.edge_family_event_reaction.attach_observed_event_reaction_bounded',
                 'seiltanzer.macro_t0_context.build_macro_t0_context'):
        monkeypatch.setattr(name,forbidden)
    try:
        response=client.post('/api/ai/verdict')
        assert response.status_code==200,response.text
        assert len(seen)==1
    finally:app.state.engine.close()


def test_transient_refresh_gap_and_same_completed_bar_do_not_rebuild():
    engine=FakeEngine()
    original={'provider':'Yahoo','source_symbol':'^NDX','target_instrument':'NAS100',
        'source_verified':True,'observed_ts':1200.,'available_at':1201.}
    engine.market.intraday_source_authority=dict(original)
    mat=AISnapshotMaterializer(engine,lambda _:snapshot());mat._build_once()
    engine.market.intraday_source_authority={}
    assert mat._event_reason() is None
    engine.market.intraday_source_authority={**original,'available_at':1240.}
    assert mat._event_reason() is None


def test_same_bucket_refresh_during_builder_preserves_coherent_causal_market_input(tmp_path,monkeypatch):
    from types import SimpleNamespace
    engine=FakeEngine();engine.settings=SimpleNamespace(data_dir=str(tmp_path))
    engine.market.intraday_source_authority={'provider':'Yahoo','source_symbol':'^NDX',
        'target_instrument':'NAS100','source_verified':True,'observed_ts':1200.,'available_at':1201.}
    engine.market.intraday_ohlcv=[(1140.,1.,2.,.5,1.5,10.)]
    def attach(view,row):
        authority=view.market.intraday_source_authority
        assert authority['available_at']<=row['captured_ts']
        assert view.market.intraday_ohlcv[0][4]==1.5
        row['edge_family_market_context']={'available':True,'reason':'ORIGINAL_CAUSAL_INPUTS'}
    monkeypatch.setattr('seiltanzer.edge_family_market.attach_market_family_context',attach)
    def builder(_):
        row=snapshot();row['captured_ts']=1230.
        engine.market.intraday_source_authority.update(observed_ts=1260.,available_at=1261.)
        engine.market.intraday_ohlcv[0]=(1200.,1.,2.,.5,9.,10.)
        return row
    mat=AISnapshotMaterializer(engine,builder);mat._build_once()
    frozen=mat.cached_build_snapshot(engine)
    assert frozen['edge_family_market_context']['reason']=='ORIGINAL_CAUSAL_INPUTS'

@pytest.mark.parametrize('legacy_ahead',[False,True])
def test_reference_lane_survives_legacy_refresh_gap_without_heavy_build(legacy_ahead):
    from test_family_reference_market import reference_source,T0
    engine=FakeEngine();engine.market.instrument_code='NAS100'
    engine.market.family_reference_series=reference_source()
    original={'provider':'Yahoo','source_symbol':'^NDX','target_instrument':'NAS100',
        'source_verified':True,'observed_ts':T0+(300 if legacy_ahead else 0)}
    engine.market.intraday_source_authority=dict(original)
    mat=AISnapshotMaterializer(engine,lambda _:snapshot());mat._build_once()
    engine.market.intraday_source_authority={}
    assert mat._event_reason() is None
    engine.market.intraday_source_authority=dict(original)
    assert mat._event_reason() is None
    engine.market.intraday_source_authority['observed_ts']+=300
    assert mat._event_reason()=='COMPLETED_MARKET_BAR_CHANGED'
