import threading

from test_decision_metric_inputs import engine,T0


def test_busy_capture_is_visible_and_retried_without_restamping_last_good(engine,monkeypatch):
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    runtime=DecisionMetricRuntime(engine)
    runtime.refresh()
    assert runtime.status()['state']=='READY'
    locked=threading.Event();release=threading.Event()
    def writer():
        with engine.market._intraday_lock:
            locked.set();release.wait(2)
    thread=threading.Thread(target=writer);thread.start()
    try:
        assert locked.wait(1)
        monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+60)
        runtime.refresh()
        status=runtime.status()
        assert status['state']=='DEFERRED'
        assert status['reason']=='MARKET_INPUT_FREEZE_BUSY'
        assert status['captured_ts']==T0
        assert runtime.refresh_interval_sec()==5.
        assert runtime.context_at('NAS100',T0)['observation_t0']==T0
    finally:
        release.set();thread.join(2)
    engine.market.price['ts']=T0+64
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+65)
    runtime.refresh()
    assert runtime.status()['state']=='READY'
    assert runtime.status()['captured_ts']==T0+65
    assert runtime.refresh_interval_sec()==30.
    assert runtime.context_at('NAS100',T0)['observation_t0']==T0


def test_unusable_quote_is_reported_instead_of_advertising_old_ready_capture(engine,monkeypatch):
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    runtime=DecisionMetricRuntime(engine);runtime.refresh()
    engine.market.price={'value':None,'status':'no_data'}
    runtime.refresh()
    assert runtime.status()['state']=='DEFERRED'
    assert runtime.status()['reason']=='CURRENT_QUOTE_OR_INPUT_UNAVAILABLE'
    assert runtime.status()['captured_ts']==T0
