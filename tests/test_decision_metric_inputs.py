from copy import deepcopy
import json
import math
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from seiltanzer.config import INSTRUMENTS

T0=1_800_000_000.


@pytest.fixture
def engine(tmp_path):
    connection=sqlite3.connect(tmp_path/'trades.db',check_same_thread=False)
    connection.row_factory=sqlite3.Row
    connection.execute('CREATE TABLE passive_market_bars(instrument TEXT,bar_start_ts REAL,'
        'bar_end_ts REAL,open REAL,high REAL,low REAL,close REAL,source TEXT,quality REAL,kind TEXT,created_ts REAL)')
    for i in range(181):
        end=T0-60*(181-i);price=20000.+i
        connection.execute('INSERT INTO passive_market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('NAS100',end-60,end,price-.5,price+1,price-1,price,'recorded minute source',.9,'direct',T0-1))
    connection.execute('CREATE TABLE g1s_observations(instrument TEXT,captured_ts REAL,frozen_features_json TEXT)')
    connection.execute('INSERT INTO g1s_observations VALUES(?,?,?)',('NAS100',T0-3600,
        json.dumps({'g1s_evidence_v3':{'captured_ts':T0-3600}})))
    connection.commit()
    cache=SimpleNamespace(chain_snapshots=lambda *args,**kwargs:[])
    settings=SimpleNamespace(data_dir=str(tmp_path),trades_db=str(tmp_path/'trades.db'),
        demo=False,ede_context_max_age_sec=900)
    feed=SimpleNamespace(instrument_code='NAS100',instrument=INSTRUMENTS['NAS100'],demo=False,
        price={'value':20180.,'ts':T0-1,'status':'live','fresh':True,'derived':False,
               'source':'TradingView stream OANDA:NAS100USD'},
        daily={'bars':{'closes':[19000+i for i in range(32)]}},chain={'metrics':None},
        correlation={'available':False},vols={},_intraday_lock=threading.RLock(),
        intraday_ohlcv=[],intraday_source_authority={},intraday_is_offset=False)
    result=SimpleNamespace(settings=settings,market=feed,
        passive=SimpleNamespace(cache=cache,settings=settings),
        short_horizon=SimpleNamespace(_conn=connection,_lock=threading.RLock()))
    try:yield result
    finally:connection.close()


def capture(engine):
    from seiltanzer.decision_metric_inputs import capture_decision_context
    return capture_decision_context(engine,engine.market,captured_ts=T0)


def test_live_model_metrics_use_received_bars_without_waiting_for_research_anchor(engine):
    from seiltanzer.edge_discovery.ai_context import canonical_current_feature_map
    before=engine.short_horizon._conn.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0]
    context=capture(engine)
    values=canonical_current_feature_map(context,'NAS100')
    assert values['price.ret_5m']['value']==pytest.approx(math.log(20180./20175.))
    assert values['price.ret_5m']['available'] is True
    assert values['price.ret_5m']['asof']==T0-60
    assert context['observation_t0']==T0
    assert context['_context_read']['reason']=='LIVE_DECISION_METRICS'
    assert len(values)==92
    assert engine.short_horizon._conn.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0]==before


@pytest.mark.parametrize('code',tuple(INSTRUMENTS))
def test_same_metric_pipeline_supports_every_configured_noncrypto_instrument(engine,code):
    engine.short_horizon._conn.execute('UPDATE passive_market_bars SET instrument=?',(code,))
    engine.short_horizon._conn.commit()
    engine.market.instrument_code=code;engine.market.instrument=INSTRUMENTS[code]
    context=capture(engine)
    assert context['instrument']==code
    assert context['price_volatility']['ret_5m']==pytest.approx(math.log(20180./20175.))


def test_received_after_cutoff_bars_cannot_enter_the_pinned_metric_vector(engine):
    engine.short_horizon._conn.execute('UPDATE passive_market_bars SET created_ts=?',(T0+10,))
    engine.short_horizon._conn.commit()
    context=capture(engine)
    assert context['price_volatility']['available'] is False
    assert context['price_volatility'].get('ret_5m') is None


def test_daily_volatility_received_after_cutoff_is_not_used(engine):
    from seiltanzer.decision_metric_inputs import capture_decision_context
    engine.market.daily['ts']=T0+1
    context=capture_decision_context(engine,engine.market,captured_ts=T0)
    assert context['_raw_frozen']['volatility']['reference_volatility_annual'] is None


def test_missing_option_inputs_remain_missing_while_real_price_metrics_work(engine):
    context=capture(engine)
    assert context['price_volatility']['available'] is True
    assert context['option_static']['available'] is False


def test_snapshot_pins_model_metrics_against_later_provider_or_cache_changes(engine):
    from seiltanzer.decision_metric_inputs import pack_decision_metrics,decision_metric_values
    context=capture(engine)
    snapshot={'captured_ts':T0+1,'strategy':{'instrument':'NAS100'},
              'decision_metric_inputs':pack_decision_metrics(context,'NAS100')}
    context['price_volatility']['ret_5m']=99
    values=decision_metric_values(snapshot)
    assert values['price.ret_5m']['value']==pytest.approx(math.log(20180./20175.))
    assert values['price.ret_5m']['asof']==T0-60
    values['price.ret_5m']['value']=88
    assert decision_metric_values(snapshot)['price.ret_5m']['value']!=88


@pytest.mark.parametrize('damage',['instrument','future','hash','bytes'])
def test_invalid_frozen_metric_vector_fails_closed(engine,damage):
    from seiltanzer.decision_metric_inputs import pack_decision_metrics,decision_metric_values
    payload=pack_decision_metrics(capture(engine),'NAS100')
    snapshot={'captured_ts':T0+1,'strategy':{'instrument':'NAS100'},'decision_metric_inputs':payload}
    if damage=='instrument':snapshot['strategy']['instrument']='XAU'
    if damage=='future':snapshot['captured_ts']=T0-1
    if damage=='hash':payload['metrics_sha256']='0'*64
    if damage=='bytes':payload['metrics_encoded']='?'*50_000
    assert decision_metric_values(snapshot)=={}


def test_stale_metrics_keep_their_values_but_cannot_match_current_advantages(engine):
    from seiltanzer.decision_metric_inputs import pack_decision_metrics,decision_metric_values
    snapshot={'captured_ts':T0+1000,'strategy':{'instrument':'NAS100'},
              'decision_metric_inputs':pack_decision_metrics(capture(engine),'NAS100')}
    row=decision_metric_values(snapshot)['price.ret_5m']
    assert row['value']==pytest.approx(math.log(20180./20175.))
    assert row['asof']==T0-60 and row['stale'] is True and row['available'] is False


def test_active_and_historical_matchers_receive_pinned_live_metric_vector(engine):
    from seiltanzer.decision_metric_inputs import pack_decision_metrics
    from seiltanzer.active_edge_ai_integration import _current_values,_conditions_match
    snapshot={'captured_ts':T0+1,'strategy':{'instrument':'NAS100'},
              'decision_metric_inputs':pack_decision_metrics(capture(engine),'NAS100')}
    values=_current_values(engine,snapshot)
    assert values['price.ret_5m']['value']==pytest.approx(math.log(20180./20175.))
    assert _conditions_match(values,{'conditions':[{'feature_id':'price.ret_5m','kind':'sign',
        'state':'POSITIVE','lower':None,'upper':None}]}) is True


def test_independent_llm_receives_readable_metric_values_and_original_source_times(engine):
    from seiltanzer.decision_metric_inputs import pack_decision_metrics
    from seiltanzer.llm_decision_shadow import _shadow_projection
    snapshot={'captured_ts':T0+1,'strategy':{'instrument':'NAS100'},'policy_manager':{},
              'decision_metric_inputs':pack_decision_metrics(capture(engine),'NAS100')}
    projection=_shadow_projection(snapshot)
    assert projection['decision_model_metrics']['price.ret_5m']['value']==pytest.approx(math.log(20180./20175.))
    assert projection['decision_model_metrics']['price.ret_5m']['asof']==T0-60
    assert 'metrics_encoded' not in json.dumps(projection)


def test_combined_provider_transport_retains_authoritative_current_metrics(engine,monkeypatch):
    from test_ai_provider_explanation import _snapshot
    from test_ai_provider_telemetry import transport
    from seiltanzer.ai_provider_guard import compact_provider_snapshot
    from seiltanzer.ai_runtime_report_v20 import request_explanation_with_shadow
    from seiltanzer.decision_metric_inputs import pack_decision_metrics
    calls,_,_=transport(monkeypatch)
    engine.market.intraday_source_authority={'provider':'Yahoo','source_symbol':'^NDX',
        'observed_ts':T0-60,'available_at':T0-1,'proxy':True,'derived':False}
    snapshot=_snapshot()
    snapshot.update(captured_ts=T0+1,strategy={'instrument':'NAS100'},
        decision_metric_inputs=pack_decision_metrics(capture(engine),'NAS100'))
    before=deepcopy(snapshot)
    result=request_explanation_with_shadow(compact_provider_snapshot(snapshot),
        authoritative_snapshot=snapshot)
    packet=json.loads(calls[0]['messages'][1]['content'].split('\n',1)[1])
    assert len(packet['decision_model_metrics'])==92
    price=packet['decision_model_metrics']['price.ret_5m']
    assert price['available'] is True
    assert price['value']==pytest.approx(math.log(20180./20175.))
    assert price['asof']==T0-60
    provenance=packet['decision_model_metric_provenance']
    assert provenance['observed_ts']==T0-60 and provenance['available_at']==T0-1
    assert provenance['proxy'] is True and provenance['derived'] is False
    assert 'metrics_encoded' not in json.dumps(packet)
    assert 'recommendation' not in packet['policy_manager']
    full=json.loads(result['llm_shadow_decision']['input_contract']['full_input_json'])
    assert full['decision_model_metrics']['price.ret_5m']==price
    assert snapshot==before


@pytest.mark.parametrize('name',['active_ml_latest.json',*[f'active_structured_{h}m_latest.json' for h in (15,30,60,120,240)]])
def test_each_expert_publication_invalidates_existing_materialization(engine,tmp_path,name):
    from seiltanzer.ai_frozen_expert_inputs import publication_marker
    before=publication_marker(engine)
    path=tmp_path/'research'/name;path.parent.mkdir(exist_ok=True);path.write_text('{}')
    assert publication_marker(engine)!=before


def test_changed_historical_hypotheses_invalidate_but_timestamp_only_refresh_does_not(engine):
    from seiltanzer.ai_frozen_expert_inputs import publication_marker
    from seiltanzer.llm_edge_lifecycle import publish_materialized_lifecycle_cache
    payload={'status':'OK','updated_ts':T0,'research_hypotheses':[{'id':'h','status':'EARLY_MIXED'}]}
    publish_materialized_lifecycle_cache(engine.short_horizon,json.dumps(payload));before=publication_marker(engine)
    payload['updated_ts']=T0+60
    publish_materialized_lifecycle_cache(engine.short_horizon,json.dumps(payload))
    assert publication_marker(engine)==before
    payload['research_hypotheses'][0]['status']='EARLY_ADVANTAGE'
    publish_materialized_lifecycle_cache(engine.short_horizon,json.dumps(payload))
    assert publication_marker(engine)!=before


@pytest.mark.parametrize('producer',['metrics','sources'])
def test_blocked_metric_producer_does_not_block_live_prices_or_shutdown(tmp_path,monkeypatch,producer):
    import asyncio
    import time
    from seiltanzer.app import create_app
    from seiltanzer.config import Settings
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime
    from seiltanzer.edge_family_source_refresh import FamilySourceRefresher
    entered,release,finished,prices=tuple(threading.Event() for _ in range(4))
    def blocked(self):
        entered.set()
        try:assert release.wait(5)
        finally:finished.set()
    monkeypatch.setattr(DecisionMetricRuntime if producer=='metrics' else FamilySourceRefresher,'refresh',blocked)
    app=create_app(Settings(demo=True,data_dir=str(tmp_path)))
    actual=app.state.engine;initial=deepcopy(app.state.live_tick_snapshot['payload']);count=[]
    def price():
        count.append(1)
        if len(count)>=2:prices.set()
    monkeypatch.setattr(actual.market,'refresh_price',price)
    for name in ('refresh_proxy_price','refresh_vols','refresh_daily','refresh_chain',
                 'refresh_iv_surface','refresh_correlation','refresh_bybit'):
        monkeypatch.setattr(actual.market,name,lambda:None)
    monkeypatch.setattr('seiltanzer.app._refresh_intraday_archive',lambda _:None)
    monkeypatch.setattr('seiltanzer.background_disk_admission.passive_step',lambda _:None)
    monkeypatch.setattr(actual,'tick_payload',lambda:{**initial,'ts':time.time()})
    async def scenario():
        try:
            async with app.router.lifespan_context(app):
                try:
                    assert await asyncio.to_thread(entered.wait,1)
                    assert await asyncio.to_thread(prices.wait,2)
                    assert not finished.is_set()
                finally:release.set()
        finally:release.set()
    asyncio.run(scenario())
    assert finished.is_set()


@pytest.mark.parametrize('field',['daily','correlation'])
def test_invalid_optional_metric_feed_cannot_erase_valid_frozen_inputs(engine,field):
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    engine.market.intraday_ohlcv=[[T0-120,1.,2.,.5,1.5,10.]]
    engine.market.chain={'ts':T0-2,'metrics':{'atm_iv':.2}}
    setattr(engine.market,field,{'oversized':'x'*1_000_001})
    frozen=capture_market_inputs(engine).market
    assert frozen.intraday_ohlcv==engine.market.intraday_ohlcv
    assert frozen.chain==engine.market.chain
    assert getattr(frozen,field)=={}


def test_background_metrics_do_not_wait_for_research_lock_and_pin_before_builder(engine,monkeypatch):
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime,pinned_decision_context
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs,freeze_expert_inputs
    from seiltanzer.edge_discovery.ai_context import _latest_frozen_context
    from seiltanzer.active_edge_ai_integration import _current_values
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    engine.canonical_tick_payload=lambda **kwargs:{'ts':T0,'instrument':'NAS100',
        'feeds':{'price':deepcopy(engine.market.price)}}
    runtime=engine.decision_metric_runtime=DecisionMetricRuntime(engine)
    with engine.short_horizon._lock:
        worker=threading.Thread(target=runtime.refresh)
        worker.start();worker.join(2)
        assert not worker.is_alive()
    assert runtime.status()['state']=='READY'
    assert runtime.status()['available_metrics']>3
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+1)
    frozen=capture_market_inputs(engine)
    snapshot={'captured_ts':T0+1,'strategy':{'instrument':'NAS100'}}
    expected=math.log(20180./20175.)
    with pinned_decision_context(engine,frozen.market.live_metric_context):
        runtime._contexts=()
        assert _latest_frozen_context(engine,snapshot)['price_volatility']['ret_5m']==pytest.approx(expected)
    freeze_expert_inputs(engine,snapshot,market_inputs=frozen)
    assert _current_values(engine,snapshot)['price.ret_5m']['value']==pytest.approx(expected)


def test_instrument_switch_during_capture_cannot_publish_wrong_metric_vector(engine,monkeypatch):
    from seiltanzer import decision_metric_inputs as module
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    engine.canonical_tick_payload=lambda **kwargs:{'ts':T0,'instrument':'NAS100',
        'feeds':{'price':deepcopy(engine.market.price)}}
    original=module.capture_decision_context
    def switch(*args,**kwargs):
        context=original(*args,**kwargs)
        engine.market.instrument_code='XAU'
        return context
    monkeypatch.setattr(module,'capture_decision_context',switch)
    runtime=module.DecisionMetricRuntime(engine);runtime.refresh()
    assert runtime.context_at('NAS100',T0) is None


def received_feed_bars(engine):
    engine.market.intraday_ohlcv=[(T0-60*(182-i),20000+i-.5,20001+i,19999+i,20000+i,1.)
        for i in range(181)]
    engine.market.intraday_source_authority={'provider':'Yahoo','source_symbol':INSTRUMENTS['NAS100'].yahoo,
        'target_instrument':'NAS100','observed_ts':T0-60,'available_at':T0-1,
        'interval_sec':60,'source_verified':True,'derived':False,'direct_source':False,'proxy':True}


@pytest.mark.parametrize('unreadable_archive',[False,True])
def test_actual_received_feed_bars_work_when_research_has_not_written_them(engine,monkeypatch,unreadable_archive):
    from seiltanzer import decision_metric_inputs as module
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    engine.short_horizon._conn.execute('UPDATE passive_market_bars SET bar_start_ts=bar_start_ts-3600,bar_end_ts=bar_end_ts-3600')
    engine.short_horizon._conn.commit();received_feed_bars(engine)
    before=engine.short_horizon._conn.total_changes
    if unreadable_archive:
        def busy(*args):raise sqlite3.OperationalError('busy')
        monkeypatch.setattr(module,'_history',busy)
    engine.canonical_tick_payload=lambda **kwargs:{'ts':T0,'instrument':'NAS100',
        'feeds':{'price':deepcopy(engine.market.price)}}
    runtime=module.DecisionMetricRuntime(engine)
    with engine.short_horizon._lock:
        worker=threading.Thread(target=runtime.refresh);worker.start();worker.join(2)
        assert not worker.is_alive()
    context=runtime.context_at('NAS100',T0)
    values=module.decision_metric_values({'captured_ts':T0,'strategy':{'instrument':'NAS100'},
        'decision_metric_inputs':module.pack_decision_metrics(context,'NAS100')})
    assert values['price.ret_5m']['available'] is True
    assert values['price.ret_5m']['asof']==T0-60
    assert values['price.ret_5m']['value']==pytest.approx(math.log(20180./20175.))
    assert engine.short_horizon._conn.total_changes==before


def test_option_cache_contention_cannot_block_available_price_metric_publication(engine,tmp_path,monkeypatch):
    from seiltanzer.data.cache import DiskCache
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    cache=engine.passive.cache=DiskCache(str(tmp_path/'cache.db'))
    engine.market.chain={'ts':T0-1,'metrics':{'proxy':'QQQ','demo':False,'atm_iv':.2}}
    cache.add_chain_snapshot('QQQ',{'proxy':'QQQ','demo':False,'atm_iv':.2},ts=T0-2)
    engine.canonical_tick_payload=lambda **kwargs:{'ts':T0,'instrument':'NAS100',
        'feeds':{'price':deepcopy(engine.market.price)}}
    runtime=DecisionMetricRuntime(engine);worker=threading.Thread(target=runtime.refresh)
    try:
        with cache._lock:
            worker.start();worker.join(1)
            assert not worker.is_alive()
            assert runtime.status()['state']=='READY'
            assert runtime.status()['available_metrics']>3
    finally:
        worker.join(2);cache.close()


def test_llm_and_matchers_apply_the_same_configured_metric_age(engine):
    from seiltanzer.decision_metric_inputs import pack_decision_metrics
    from seiltanzer.active_edge_ai_integration import _current_values
    from seiltanzer.llm_decision_shadow import _shadow_projection
    engine.settings.ede_context_max_age_sec=60
    context=capture(engine)
    snapshot={'captured_ts':T0+1,'strategy':{'instrument':'NAS100'},'policy_manager':{},
        'decision_metric_inputs':pack_decision_metrics(context,'NAS100')}
    assert _current_values(engine,snapshot)['price.ret_5m']['available'] is False
    assert _shadow_projection(snapshot)['decision_model_metrics']['price.ret_5m']['available'] is False


@pytest.mark.parametrize('field,value',[('available_at',T0+1),('source_symbol','WRONG'),
    ('source_verified',False),('target_instrument','XAU'),('interval_sec',300),('derived',True)])
def test_feed_history_requires_its_own_received_source_identity(engine,field,value):
    from seiltanzer.decision_metric_inputs import _received_bars
    received_feed_bars(engine)
    engine.market.intraday_source_authority[field]=value
    assert _received_bars(engine.market,'NAS100',T0)==[]


def test_derived_feed_bars_keep_their_source_and_original_receipt(engine):
    from seiltanzer.decision_metric_inputs import _received_bars,pack_decision_metrics
    from seiltanzer.llm_decision_shadow import _shadow_projection
    received_feed_bars(engine)
    engine.market.intraday_is_offset=True
    authority=engine.market.intraday_source_authority
    authority.update(derived=True,broker_execution_bars=False,authority_role='PROXY_CONTEXT_ONLY')
    rows=_received_bars(engine.market,'NAS100',T0)
    assert rows[-1]['kind']=='derived' and rows[-1]['source']=='yahoo_1m_offset_adjusted'
    snapshot={'captured_ts':T0,'strategy':{'instrument':'NAS100'},'policy_manager':{},
        'decision_metric_inputs':pack_decision_metrics(capture(engine),'NAS100')}
    proof=_shadow_projection(snapshot)['decision_model_metric_provenance']
    assert proof['available_at']==T0-1 and proof['proxy'] is True
    assert proof['derived'] is True and proof['broker_execution_bars'] is False


def test_option_history_uses_actual_pre_cutoff_cache_observations(engine,tmp_path):
    from seiltanzer.data.cache import DiskCache
    from seiltanzer.decision_metric_inputs import _option_history
    cache=DiskCache(str(tmp_path/'cache.db'))
    try:
        cache.add_chain_snapshot('QQQ',{'demo':False,'atm_iv':.21},ts=T0-2)
        cache.add_chain_snapshot('QQQ',{'demo':False,'atm_iv':.99},ts=T0+1)
        rows=_option_history(engine,'QQQ',T0)
        assert len(rows)==1 and rows[0]['atm_iv']==.21 and rows[0]['ts']==T0-2
    finally:cache.close()


@pytest.mark.parametrize('max_age',[float('nan'),float('inf'),-1,0])
def test_invalid_age_policy_cannot_admit_old_features(engine,max_age):
    from seiltanzer.decision_metric_inputs import pack_decision_metrics,decision_metric_values
    snapshot={'captured_ts':T0+1,'strategy':{'instrument':'NAS100'},
        'decision_metric_inputs':pack_decision_metrics(capture(engine),'NAS100')}
    assert decision_metric_values(snapshot,max_age_sec=max_age)=={}


def test_feature_received_after_packet_cutoff_cannot_become_available_on_later_review(engine):
    import base64,hashlib,zlib
    from seiltanzer.decision_metric_inputs import pack_decision_metrics,decision_metric_values
    packet=pack_decision_metrics(capture(engine),'NAS100')
    values=json.loads(zlib.decompress(base64.b64decode(packet['metrics_encoded'])))
    values['price.ret_5m']['asof']=T0+10
    raw=json.dumps(values,separators=(',',':')).encode()
    packet['metrics_encoded']=base64.b64encode(zlib.compress(raw)).decode()
    packet['metrics_sha256']=hashlib.sha256(raw).hexdigest()
    result=decision_metric_values({'captured_ts':T0+20,'strategy':{'instrument':'NAS100'},
        'decision_metric_inputs':packet})
    assert result['price.ret_5m']['available'] is False


@pytest.mark.parametrize('tier',['v18','strict','emergency','bridge','facade'])
def test_full_metric_vector_and_decision_economics_survive_snapshot_compaction(engine,tier,monkeypatch):
    from seiltanzer import ai_verdict,ai_verdict_v18,ai_snapshot_budget_guard,ai_verdict_budget_bridge
    from seiltanzer.decision_metric_inputs import pack_decision_metrics,decision_metric_values
    from test_operational_edge_compaction import operational_snapshot,signatures
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    snapshot=operational_snapshot();snapshot['captured_ts']=T0
    snapshot['decision_metric_inputs']=pack_decision_metrics(capture(engine),'NAS100')
    packet=deepcopy(snapshot['decision_metric_inputs'])
    expected=signatures(build_unified_ensemble(snapshot))
    if tier=='v18':
        snapshot.pop('report_integrity')
        snapshot['policy_manager']['redundant_explanation']='x'*80000
        ai_verdict_v18._enforce_snapshot_budget(snapshot)
    elif tier=='strict':ai_snapshot_budget_guard._strict_authoritative_compaction(snapshot)
    elif tier=='emergency':
        ai_snapshot_budget_guard._strict_authoritative_compaction(snapshot)
        ai_snapshot_budget_guard._emergency_authoritative_compaction(snapshot,ai_verdict)
    elif tier=='bridge':ai_verdict_budget_bridge._drop_duplicate_integrity_views(snapshot)
    else:
        monkeypatch.setattr(ai_snapshot_budget_guard,'_INSTALLED',False)
        monkeypatch.setattr(ai_verdict,'_enforce_snapshot_budget_with_report_integrity',
            ai_verdict._enforce_snapshot_budget_with_report_integrity)
        monkeypatch.setattr(ai_verdict._impl,'_enforce_snapshot_budget',ai_verdict._impl._enforce_snapshot_budget)
        ai_snapshot_budget_guard.install_ai_snapshot_budget_guard()
        ai_verdict._impl._enforce_snapshot_budget(snapshot)
    assert snapshot['decision_metric_inputs']==packet
    assert len(decision_metric_values(snapshot))==92
    assert signatures(build_unified_ensemble(snapshot))==expected
