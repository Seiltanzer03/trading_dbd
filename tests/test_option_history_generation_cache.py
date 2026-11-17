import json
import sqlite3

import pytest

from test_decision_metric_inputs import engine,T0
from seiltanzer.decision_metric_inputs import DecisionMetricRuntime,decision_metric_values,pack_decision_metrics


@pytest.fixture
def option_runtime(engine,monkeypatch):
    connection=sqlite3.connect(engine.settings.data_dir+'/cache.db')
    connection.execute('CREATE TABLE chain_snapshots(id INTEGER PRIMARY KEY,ticker TEXT,ts REAL,data TEXT)')
    connection.execute('CREATE INDEX ix_chain_ticker_ts ON chain_snapshots(ticker,ts)')
    metrics={'proxy':'QQQ','demo':False,'spot':500.,'implied_move':{'sigma_annual':.3},
             'greek_context':{'net_delta_oi_weighted':.2},'skew':{'rr':.01}}
    for i in range(12):
        connection.execute('INSERT INTO chain_snapshots(ticker,ts,data) VALUES(?,?,?)',
                           ('QQQ',T0-5-60*(11-i),json.dumps(metrics)))
    connection.commit()
    engine.market.chain={'metrics':metrics,'ts':T0-5,'status':'delayed','value':True,'source':'yfinance QQQ options'}
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    runtime=DecisionMetricRuntime(engine);runtime.refresh()
    try:yield runtime,connection
    finally:connection.rollback();connection.close()


def current_values(runtime,cutoff):
    context=runtime.context_at('NAS100',cutoff)
    return decision_metric_values({'captured_ts':cutoff,'strategy':{'instrument':'NAS100'},
        'decision_metric_inputs':pack_decision_metrics(context,'NAS100')})


def test_same_received_option_generation_survives_a_busy_history_writer(engine,monkeypatch,option_runtime):
    runtime,writer=option_runtime
    before=current_values(runtime,T0)['option.iv']
    assert before['available'] is True and before['value']==.3
    writer.execute('BEGIN EXCLUSIVE')
    engine.market.price['ts']=T0+29
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+30)
    runtime.refresh()
    after=current_values(runtime,T0+30)['option.iv']
    assert after['available'] is True and after['value']==before['value']
    assert after['asof']==before['asof']==T0-5
    assert runtime.status()['option_history_cache_used'] is True


@pytest.mark.parametrize('change',['timestamp','metrics','source','instrument','future_cutoff'])
def test_option_history_is_never_reused_for_a_different_or_future_generation(engine,monkeypatch,option_runtime,change):
    runtime,writer=option_runtime
    writer.execute('BEGIN EXCLUSIVE')
    cutoff=T0+30
    if change=='timestamp':engine.market.chain['ts']=T0+25
    elif change=='metrics':engine.market.chain['metrics']['implied_move']['sigma_annual']=.9
    elif change=='source':engine.market.chain['source']='different provider'
    elif change=='instrument':engine.market.instrument_code='GER40'
    else:cutoff=T0-10
    engine.market.price['ts']=cutoff-1
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:cutoff)
    runtime.refresh()
    code=engine.market.instrument_code
    context=runtime.context_at(code,cutoff)
    if change in ('timestamp','metrics'):
        assert runtime.status()['state']=='DEFERRED'
        assert context['observation_t0']==T0
        assert runtime.status()['pending_option_source_ts']==engine.market.chain['ts']
    else:assert context['option_static']['available'] is False


def test_reused_history_keeps_source_expiry_instead_of_refreshing_asof(engine,monkeypatch,option_runtime):
    runtime,writer=option_runtime
    writer.execute('BEGIN EXCLUSIVE')
    engine.market.price['ts']=T0+999
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+1000)
    runtime.refresh()
    row=current_values(runtime,T0+1000)['option.iv']
    assert row['value']==.3 and row['asof']==T0-5
    assert row['available'] is False and row['stale'] is True


def test_a_report_copy_cannot_mutate_the_retained_received_history(engine,monkeypatch,option_runtime):
    runtime,writer=option_runtime
    context=runtime.context_at('NAS100',T0)
    context['v2_option_context']['greek_context']['net_delta_oi_weighted']=.99
    writer.execute('BEGIN EXCLUSIVE')
    engine.market.price['ts']=T0+29
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+30)
    runtime.refresh()
    assert runtime.context_at('NAS100',T0+30)['option_static']['delta']==.2


def test_runtime_pins_original_family_receipts_when_latest_chain_arrives_after_origin(engine,monkeypatch,option_runtime):
    runtime,_=option_runtime
    original=runtime.context_at('NAS100',T0)['option_family_inputs']['option']
    assert original['features']=={'option.iv':.3}
    original['features']['option.iv']=.99
    engine.market.chain['ts']=T0+15
    engine.market.chain['metrics']['implied_move']['sigma_annual']=.7
    engine.market.price['ts']=T0+29
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+30)
    runtime.refresh()
    actual=runtime.context_at('NAS100',T0+30)['option_family_inputs']['option']
    assert actual['features']=={'option.iv':.3}
    assert actual['feature_provenance']['option.iv']['received_ts']==T0-5


def test_busy_history_for_a_new_chain_defers_without_publishing_a_partial_generation(engine,monkeypatch,option_runtime):
    runtime,writer=option_runtime
    metrics={**engine.market.chain['metrics'],'implied_move':{'sigma_annual':.9}}
    writer.execute('INSERT INTO chain_snapshots(ticker,ts,data) VALUES(?,?,?)',
                   ('QQQ',T0+25,json.dumps(metrics)))
    writer.commit()
    writer.execute('BEGIN EXCLUSIVE')
    engine.market.chain['ts']=T0+25;engine.market.chain['metrics']=metrics
    engine.market.price['ts']=T0+29
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+30)
    runtime.refresh()
    assert runtime.status()['state']=='DEFERRED'
    assert runtime.status()['reason']=='OPTION_HISTORY_READ_BUSY'
    assert runtime.refresh_interval_sec()==5
    assert runtime.context_at('NAS100',T0+30)['observation_t0']==T0
    original=current_values(runtime,T0+30)['option.iv']
    assert original['value']==.3 and original['asof']==T0-5
    writer.rollback()
    engine.market.price['ts']=T0+34
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+35)
    runtime.refresh()
    assert runtime.status()['state']=='READY'
    assert runtime.context_at('NAS100',T0+35)['observation_t0']==T0+35
    actual=current_values(runtime,T0+35)['option.iv']
    assert actual['value']==.9 and actual['asof']==T0+25


def test_empty_and_oversized_history_are_not_misreported_as_retryable_locks(engine,option_runtime):
    from seiltanzer.decision_metric_inputs import _option_history
    _,writer=option_runtime
    status={}
    assert _option_history(engine,'MISSING',T0,read_status=status)==[]
    assert status['deferred'] is False
    writer.execute('INSERT INTO chain_snapshots(ticker,ts,data) VALUES(?,?,?)',('LARGE',T0,'x'*2000001))
    writer.commit()
    assert _option_history(engine,'LARGE',T0,read_status=status)==[]
    assert status=={'deferred':False,'reason':'OPTION_HISTORY_SIZE_BOUND'}


def test_busy_new_chain_cannot_extend_the_last_generation_expiry(engine,monkeypatch,option_runtime):
    runtime,writer=option_runtime
    writer.execute('BEGIN EXCLUSIVE')
    engine.market.chain['ts']=T0+999
    engine.market.price['ts']=T0+999
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+1000)
    runtime.refresh()
    context=runtime.context_at('NAS100',T0+1000)
    assert context['observation_t0']==T0+1000
    assert context['option_static']['available'] is False
    assert runtime.status()['option_history_reason']=='OPTION_HISTORY_READ_BUSY'


def test_busy_new_chain_respects_a_shorter_original_context_age(engine,monkeypatch,option_runtime):
    runtime,writer=option_runtime
    runtime.context_at('NAS100',T0)['max_age_sec']=60
    writer.execute('BEGIN EXCLUSIVE')
    engine.market.chain['ts']=T0+119
    engine.market.price['ts']=T0+119
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+120)
    runtime.refresh()
    context=runtime.context_at('NAS100',T0+120)
    assert context['observation_t0']==T0+120
    assert context['option_static']['available'] is False


def test_discarded_instrument_switch_cannot_authorize_an_older_source_context(engine,monkeypatch,option_runtime):
    from seiltanzer import decision_metric_inputs as inputs
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    runtime,writer=option_runtime
    engine.decision_metric_runtime=runtime
    original_read=inputs._option_history
    def switch_during_read(*args,**kwargs):
        rows=original_read(*args,**kwargs)
        engine.market.instrument_code='GER40'
        return rows
    engine.market.chain['source']='new source B'
    engine.market.chain['ts']=T0+25
    engine.market.price['ts']=T0+29
    monkeypatch.setattr(inputs,'_option_history',switch_during_read)
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+30)
    runtime.refresh()
    assert runtime.status()['reason']=='INSTRUMENT_CHANGED'
    assert runtime.context_at('NAS100',T0+30)['observation_t0']==T0
    monkeypatch.setattr(inputs,'_option_history',original_read)
    engine.market.instrument_code='NAS100'
    engine.market.chain['ts']=T0+60
    engine.market.price['ts']=T0+64
    writer.execute('BEGIN EXCLUSIVE')
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+65)
    runtime.refresh()
    copied=capture_market_inputs(engine).market.live_metric_context
    assert copied['observation_t0']==T0+65
    assert copied['option_static']['available'] is False
