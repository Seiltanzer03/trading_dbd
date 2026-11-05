from copy import deepcopy
from types import SimpleNamespace
import json
import math
import threading

import pytest

from test_decision_metric_inputs import engine,T0
from test_reference_market_series import Socket
from seiltanzer.config import INSTRUMENTS
from seiltanzer.mathematical_edge import fingerprint


def minute_source(code='NAS100',count=360):
    from seiltanzer.reference_market_series import SYMBOLS
    bars=[dict(bar_end_ts=T0-60*(count-i),open=30000+i-.5,high=30001+i,
               low=29999+i,close=30000+i) for i in range(count)]
    return dict(contract_version='configured-decision-reference-1m-v1',provider='TradingView chart',
        instrument=code,ticker=SYMBOLS[code],resolved_symbol=SYMBOLS[code],interval='1m',
        bars=bars,source_sha256=fingerprint(bars),observed_ts=T0-60,
        request_started_ts=T0-5,received_ts=T0-1,not_broker_execution_bars=True,
        canonical_execution_quote_replaced=False,
        historical_bar_availability='COMPLETED_PROVIDER_BAR_NOT_RECORDED_HISTORICAL_RECEIPT')


def test_minute_transport_uses_actual_resolution_and_excludes_incomplete_bars():
    from seiltanzer import reference_market_series as m
    socket=Socket(rows=[[T0-60,100.,102.,99.,101.,1.],[T0,101.,103.,100.,102.,1.]])
    source=m.fetch_reference_series('XAU',limit=120,interval='1m',clock=lambda:T0+10,
        connect_factory=lambda *a,**k:socket)
    assert source['contract_version']=='configured-decision-reference-1m-v1'
    assert source['interval']=='1m' and source['received_ts']==T0+10
    assert source['observed_ts']==T0
    assert len(source['bars'])==1 and source['bars'][0]['close']==101.
    assert any('create_series' in item and '"1",120' in item for item in socket.sent)
    assert m.validate_reference_series(source,'XAU',T0+10,runtime=True,interval='1m') is None
    assert m.validate_reference_series(source,'XAU',T0+10,runtime=True) is not None
    assert m.model_source_valid({'provider':m.PROVIDER,'ticker':source['ticker'],
        'resolved_symbol':source['resolved_symbol'],'interval':'1m',
        'reference_contract':source['contract_version'],'not_broker_execution_bars':True,
        'bars_sha256':source['source_sha256']},'XAU') is False


@pytest.mark.parametrize('code',tuple(INSTRUMENTS))
def test_native_minute_inputs_work_while_cash_archive_is_closed_without_mixing_bases(engine,monkeypatch,code):
    from seiltanzer import decision_metric_inputs as m
    engine.market.instrument_code=code;engine.market.instrument=INSTRUMENTS[code]
    engine.market.decision_minute_reference=minute_source(code)
    before=engine.short_horizon._conn.total_changes
    def unexpected(*args):raise AssertionError('native data waited for cash archive')
    monkeypatch.setattr(m,'_history',unexpected)
    context=m.capture_decision_context(engine,engine.market,captured_ts=T0)
    values=m.decision_metric_values({'captured_ts':T0,'strategy':{'instrument':code},
        'decision_metric_inputs':m.pack_decision_metrics(context,code)})
    assert values['price.ret_5m']['available'] is True
    assert values['price.ret_5m']['asof']==T0-60
    assert values['price.ret_5m']['value']==pytest.approx(math.log(30359./30354.))
    assert values['vol.rv_15m']['available'] is True
    assert context['input_provenance']['provider']=='TradingView chart'
    assert context['input_provenance']['broker_execution_bars'] is False
    assert engine.short_horizon._conn.total_changes==before


@pytest.mark.parametrize('damage',['ticker','receipt','interval','hash','stale','future'])
def test_invalid_minute_reference_does_not_replace_actual_legacy_metrics(engine,damage):
    from seiltanzer.decision_metric_inputs import capture_decision_context
    source=minute_source()
    if damage=='ticker':source['ticker']='OANDA:XAUUSD'
    if damage=='receipt':source['received_ts']=T0+1
    if damage=='interval':source['interval']='5m'
    if damage=='hash':source['bars'][-1]['close']=99999
    if damage=='stale':source['observed_ts']=T0-1000
    if damage=='future':source['bars'][-1]['bar_end_ts']=T0+60;source['source_sha256']=fingerprint(source['bars'])
    engine.market.decision_minute_reference=source
    context=capture_decision_context(engine,engine.market,captured_ts=T0)
    assert context['price_volatility']['ret_5m']==pytest.approx(math.log(20180./20175.))


def test_five_minute_samples_cannot_be_relabelled_as_received_minute_bars(engine):
    from seiltanzer.decision_metric_inputs import capture_decision_context
    source=minute_source();source['bars']=source['bars'][::5]
    source['observed_ts']=source['bars'][-1]['bar_end_ts']
    source['source_sha256']=fingerprint(source['bars'])
    engine.market.decision_minute_reference=source
    context=capture_decision_context(engine,engine.market,captured_ts=T0)
    assert context['price_volatility']['ret_5m']==pytest.approx(math.log(20180./20175.))


def test_feed_publishes_and_freezes_minute_reference_without_changing_quote(engine,monkeypatch):
    from seiltanzer.data.feeds import MarketData
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    from seiltanzer import reference_market_series as module
    source=minute_source();calls=[]
    def fetch(code,**kwargs):calls.append((code,kwargs));return source
    monkeypatch.setattr(module,'fetch_reference_series',fetch)
    monkeypatch.setattr('seiltanzer.data.feeds.time.time',lambda:T0)
    before=deepcopy(engine.market.price)
    MarketData.refresh_decision_minute_reference(engine.market,'NAS100')
    frozen=capture_market_inputs(engine).market
    assert calls==[('NAS100',{'limit':1800,'timeout':5.,'interval':'1m'})]
    assert frozen.decision_minute_reference==source
    source['bars'][-1]['close']+=1
    assert frozen.decision_minute_reference!=source
    assert engine.market.price==before


def test_full_thirty_hour_minute_window_survives_existing_freeze_and_metric_bounds(engine):
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    from seiltanzer.decision_metric_inputs import capture_decision_context,pack_decision_metrics
    engine.market.decision_minute_reference=minute_source(count=1800)
    market=capture_market_inputs(engine).market
    assert len(market.decision_minute_reference['bars'])==1800
    context=capture_decision_context(engine,market,captured_ts=T0)
    assert context['price_volatility']['quality']['sample_count']==1800
    assert len(pack_decision_metrics(context,'NAS100')['metrics_encoded'])<8000


def test_minute_refresh_switch_and_provider_failure_preserve_original_clocks(engine,monkeypatch):
    from seiltanzer.data.feeds import MarketData
    from seiltanzer import reference_market_series as module
    original=minute_source();engine.market.decision_minute_reference=deepcopy(original)
    def fail(*a,**k):raise TimeoutError('provider unavailable')
    monkeypatch.setattr(module,'fetch_reference_series',fail)
    MarketData.refresh_decision_minute_reference(engine.market,'NAS100')
    assert engine.market.decision_minute_reference==original
    def switch(*a,**k):engine.market.instrument_code='XAU';return minute_source()
    monkeypatch.setattr(module,'fetch_reference_series',switch)
    monkeypatch.setattr('seiltanzer.data.feeds.time.time',lambda:T0)
    MarketData.refresh_decision_minute_reference(engine.market,'NAS100')
    assert engine.market.decision_minute_reference==original


def test_busy_research_archive_cannot_stop_repeated_actual_feed_acquisition(engine):
    from seiltanzer.app import _refresh_intraday_archive
    lock=engine.passive._lock=engine.short_horizon._lock
    fetched=[];archived=[]
    engine.market.refresh_intraday=lambda:fetched.append(1)
    def record(_):
        with lock:archived.append(1)
    engine.passive.record_configured_intraday_archive=record
    thread=threading.Thread(target=lambda:(_refresh_intraday_archive(engine),_refresh_intraday_archive(engine)))
    try:
        with lock:
            thread.start();thread.join(1)
            assert not thread.is_alive()
            assert fetched==[1,1] and archived==[]
    finally:thread.join(2)
