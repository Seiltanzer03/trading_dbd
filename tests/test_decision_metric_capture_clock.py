from copy import deepcopy
import math

import pytest

from test_decision_metric_inputs import engine,T0
from test_decision_minute_reference import minute_source


@pytest.mark.parametrize('tick_age',[20,120])
def test_cached_tick_cannot_discard_inputs_received_before_the_actual_capture(engine,monkeypatch,tick_age):
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime,decision_metric_values,pack_decision_metrics
    engine.market.decision_minute_reference=minute_source()
    old_quote={**engine.market.price,'ts':T0-tick_age-1}
    engine.canonical_tick_payload=lambda **kwargs:{'ts':T0-tick_age,'instrument':'NAS100',
        'feeds':{'price':old_quote}}
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+1)
    before=deepcopy(engine.market.price)
    runtime=DecisionMetricRuntime(engine);runtime.refresh()
    context=runtime.context_at('NAS100',T0+1)
    values=decision_metric_values({'captured_ts':T0+1,'strategy':{'instrument':'NAS100'},
        'decision_metric_inputs':pack_decision_metrics(context,'NAS100')})
    assert values['price.ret_5m']['available'] is True
    assert values['price.ret_5m']['value']==pytest.approx(math.log(30359./30354.))
    assert values['price.ret_5m']['asof']==T0-60
    assert runtime.context_at('NAS100',T0) is None
    assert context['observation_t0']==T0+1
    assert engine.market.price==before


def test_future_received_reference_stays_excluded_by_the_actual_capture_clock(engine,monkeypatch):
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime
    source=minute_source();source['received_ts']=T0+10
    engine.market.decision_minute_reference=source
    engine.short_horizon._conn.execute('UPDATE passive_market_bars SET created_ts=?',(T0+10,))
    engine.short_horizon._conn.commit()
    engine.canonical_tick_payload=lambda **kwargs:{'ts':T0,'instrument':'NAS100',
        'feeds':{'price':deepcopy(engine.market.price)}}
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0+1)
    runtime=DecisionMetricRuntime(engine);runtime.refresh()
    context=runtime.context_at('NAS100',T0+1)
    assert context['price_volatility']['available'] is False
    assert context['price_volatility'].get('ret_5m') is None
