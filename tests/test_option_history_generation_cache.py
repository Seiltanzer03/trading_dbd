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
    assert context['option_static']['available'] is False


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
