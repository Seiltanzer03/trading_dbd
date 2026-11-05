"""Native minute inputs must reach consumers through real isolated publication."""
from copy import deepcopy
import math
from types import SimpleNamespace

import pytest

from seiltanzer.config import INSTRUMENTS
from test_decision_metric_inputs import engine, T0
from test_decision_minute_reference import minute_source
from test_instrument_refresh_isolation import market, yahoo


def provider(monkeypatch, fetch):
    from seiltanzer import reference_market_series
    monkeypatch.setattr(reference_market_series, 'fetch_reference_series', fetch)
    monkeypatch.setattr('seiltanzer.data.feeds.time.time', lambda: T0)
    yahoo(monkeypatch, lambda symbol: SimpleNamespace(history=lambda **kwargs: []))


@pytest.mark.parametrize('code', tuple(INSTRUMENTS))
def test_actual_intraday_publication_supplies_native_frozen_metric_vector(engine, market, monkeypatch, code):
    from seiltanzer.decision_metric_inputs import DecisionMetricRuntime
    market.set_instrument(code)
    quote=deepcopy(engine.market.price)
    market.price=quote
    engine.market=market
    source=minute_source(code, count=1800)
    def fetch(requested, **kwargs):
        if kwargs.get('interval')!='1m':raise TimeoutError('independent 5m source unavailable')
        assert requested==code and kwargs=={'limit':1800,'timeout':5.,'interval':'1m'}
        return deepcopy(source)
    provider(monkeypatch, fetch)
    market.refresh_intraday()
    runtime=DecisionMetricRuntime(engine)
    runtime.refresh()
    context=runtime.context_at(code,T0)
    assert context['price_volatility']['ret_5m']==pytest.approx(math.log(31799./31794.))
    assert context['input_provenance']['source_sha256']==source['source_sha256']
    assert context['input_provenance']['available_at']==T0-1
    assert context['input_provenance']['broker_execution_bars'] is False
    assert market.price==quote


def test_failed_isolated_minute_fetch_publishes_error_without_restamping_last_good(market,monkeypatch):
    original=minute_source()
    market.decision_minute_reference=deepcopy(original)
    def fail(*args,**kwargs):raise TimeoutError('source unavailable')
    provider(monkeypatch,fail)
    market.refresh_intraday()
    assert market.decision_minute_reference==original
    assert market.decision_minute_reference_error=='TimeoutError'


@pytest.mark.parametrize('aba',[False,True])
def test_old_native_completion_cannot_cross_an_instrument_generation(market,monkeypatch,aba):
    def fetch(code,**kwargs):
        if kwargs.get('interval')!='1m':raise TimeoutError('5m unavailable')
        market.set_instrument('XAU')
        if aba:market.set_instrument('NAS100')
        return minute_source()
    provider(monkeypatch,fetch)
    market.refresh_intraday()
    assert market.decision_minute_reference is None
    assert market.decision_minute_reference_error is None


def test_old_minute_completion_cannot_replace_newer_same_channel_publication(market,monkeypatch):
    calls=[]
    newer=minute_source(count=1800)
    def fetch(code,**kwargs):
        if kwargs.get('interval')!='1m':raise TimeoutError('5m unavailable')
        calls.append(code)
        if len(calls)==1:
            market.refresh_intraday()
            return minute_source(count=360)
        return deepcopy(newer)
    provider(monkeypatch,fetch)
    market.refresh_intraday()
    assert market.decision_minute_reference==newer
    assert market.decision_minute_reference_error is None
