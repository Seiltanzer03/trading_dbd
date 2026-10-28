"""Instrument switches must not adopt another instrument's in-flight inputs."""
from copy import deepcopy
import datetime as dt
import sys
import threading
import types
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from seiltanzer.config import Settings
from seiltanzer.core import options
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import MarketData


@pytest.fixture
def market(tmp_path):
    settings = Settings(demo=False, data_dir=str(tmp_path))
    feed = MarketData(settings, DiskCache(settings.cache_db))
    feed.price = {'value': 100., 'source': 'fixture Yahoo', 'status': 'delayed'}
    yield feed
    feed.cache._conn.close()


def yahoo(monkeypatch, ticker):
    module = types.ModuleType('yfinance')
    module.Ticker = lambda symbol: ticker(symbol)
    monkeypatch.setitem(sys.modules, 'yfinance', module)


def history(base=100.):
    close = np.arange(30) * .1 + base
    return pd.DataFrame({'High': close + 1, 'Low': close - 1, 'Close': close})


def option_rows():
    raw = options.synth_chain(100., .22, 30/365, n_strikes=61, width=.35, seed=3)
    def frame(side):
        mid = raw[side + '_mid']
        return pd.DataFrame({'strike': raw['strikes'], 'bid': mid, 'ask': mid,
            'lastPrice': mid, 'openInterest': raw[side + '_oi'],
            'impliedVolatility': raw[side + '_iv']})
    return SimpleNamespace(calls=frame('call'), puts=frame('put'))


def test_daily_completion_after_switch_cannot_publish_or_cache_under_new_symbol(market, monkeypatch):
    cleared = []
    class Ticker:
        def __init__(self, symbol):
            assert symbol == '^NDX'
        def history(self, **kwargs):
            market.set_instrument('XAU')
            cleared.append(deepcopy(market.daily))
            return history()
    yahoo(monkeypatch, Ticker)
    market.refresh_daily()
    assert market.instrument_code == 'XAU'
    assert market.daily == cleared[0], 'NAS daily bars published on XAU after switch'
    assert market.cache.get('daily:GC=F') is None, 'NAS history cached as gold'


@pytest.mark.parametrize('resilient', [False, True])
@pytest.mark.parametrize('method,field', [('refresh_chain', 'chain'), ('refresh_iv_surface', 'iv_surface')])
def test_option_completion_after_switch_keeps_new_instrument_state(market, monkeypatch, method, field, resilient):
    if resilient:
        from seiltanzer import option_feed_resilience
        from seiltanzer.data.instrument_refresh import install_instrument_refresh_isolation
        class ResilientMarket(MarketData):
            refresh_chain = option_feed_resilience.resilient_refresh_chain
            refresh_iv_surface = option_feed_resilience.resilient_refresh_iv_surface
        install_instrument_refresh_isolation(ResilientMarket)
        market.__class__ = ResilientMarket
    cleared = []
    class Ticker:
        def __init__(self, symbol):
            assert symbol == 'QQQ'
            self.fast_info = SimpleNamespace(last_price=100.)
        @property
        def options(self):
            market.set_instrument('USDCAD')
            cleared.append(deepcopy(getattr(market, field)))
            return [(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).strftime('%Y-%m-%d')]
        def option_chain(self, expiry):
            return option_rows()
    yahoo(monkeypatch, Ticker)
    getattr(market, method)()
    assert market.instrument_code == 'USDCAD'
    assert getattr(market, field) == cleared[0], 'QQQ result adopted by CAD instrument'
    assert market.proxy_price.get('value') is None
    if field == 'chain':
        assert market.cache.chain_snapshots('QQQ') == [], 'rejected generation still wrote option history'


def test_aba_switch_rejects_completion_even_when_instrument_name_matches_again(market, monkeypatch):
    cleared = []
    class Ticker:
        def __init__(self, symbol):
            pass
        def history(self, **kwargs):
            market.set_instrument('XAU')
            market.set_instrument('NAS100')
            market.price = {'value': 100., 'source': 'new generation'}
            cleared.append(deepcopy(market.daily))
            return history()
    yahoo(monkeypatch, Ticker)
    market.refresh_daily()
    assert market.daily == cleared[0], 'old NAS request survived NAS→XAU→NAS'
    assert market.cache.get('daily:^NDX') is None, 'ABA-rejected generation still changed cache'


def test_older_same_channel_completion_cannot_overwrite_newer_refresh(market, monkeypatch):
    calls = []
    class Ticker:
        def __init__(self, symbol):
            pass
        def history(self, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                market.refresh_daily()
                return history(100.)
            if len(calls) == 3:
                raise TimeoutError('test cache fallback after reversed completions')
            return history(200.)
    yahoo(monkeypatch, Ticker)
    market.refresh_daily()
    assert market.daily['bars']['closes'][-1] == pytest.approx(202.9)
    assert market.cache.get('daily:^NDX')[0]['closes'][-1] == pytest.approx(202.9)
    market.refresh_daily()
    assert market.daily['bars']['closes'][-1] == pytest.approx(202.9), 'fallback resurrected rejected old result'


def test_proxy_stream_update_during_chain_fetch_is_never_downgraded(market, monkeypatch):
    class Ticker:
        def __init__(self, symbol):
            self.fast_info = SimpleNamespace(last_price=100.)
        @property
        def options(self):
            market.proxy_price = {'value': 105., 'status': 'live', 'source': 'stream QQQ', 'ts': 999.}
            return [(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).strftime('%Y-%m-%d')]
        def option_chain(self, expiry):
            return option_rows()
    yahoo(monkeypatch, Ticker)
    market.refresh_chain()
    assert market.chain['metrics']['proxy'] == 'QQQ'
    assert market.proxy_price == {'value': 105., 'status': 'live', 'source': 'stream QQQ', 'ts': 999.}


def test_fetch_failure_after_switch_cannot_replace_new_instrument_error(market, monkeypatch):
    cleared = []
    class Ticker:
        def __init__(self, symbol):
            pass
        def history(self, **kwargs):
            market.set_instrument('EURUSD')
            cleared.append(deepcopy(market.daily))
            raise TimeoutError('old NAS source failed')
    yahoo(monkeypatch, Ticker)
    market.refresh_daily()
    assert market.daily == cleared[0]


def test_optional_treasury_work_cannot_change_original_frozen_option_chain():
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    feed = SimpleNamespace(instrument_code='NAS100', demo=False,
        intraday_ohlcv=[], intraday_source_authority={}, _intraday_lock=threading.Lock(),
        chain={'metrics': {'proxy': 'QQQ'}, 'ts': 123.})
    def context_at(captured):
        feed.instrument_code = 'USDCAD'
        feed.chain = {'metrics': {'proxy': 'FXC'}, 'ts': captured + 1}
        return {}
    engine = SimpleNamespace(market=feed, passive=SimpleNamespace(
        _treasury_live_runtime=SimpleNamespace(context_at=context_at)))
    frozen = capture_market_inputs(engine).market
    assert frozen.instrument_code == 'NAS100'
    assert frozen.chain == {'metrics': {'proxy': 'QQQ'}, 'ts': 123.}


def test_provider_work_does_not_hold_the_live_market_lock(market, monkeypatch):
    class Ticker:
        def __init__(self, symbol):
            pass
        def history(self, **kwargs):
            acquired = []
            def other_reader():
                ok = market._intraday_lock.acquire(blocking=False)
                acquired.append(ok)
                if ok:
                    market._intraday_lock.release()
            reader = threading.Thread(target=other_reader)
            reader.start(); reader.join(timeout=1)
            assert acquired == [True], 'provider call blocks live market readers'
            return history()
    yahoo(monkeypatch, Ticker)
    market.refresh_daily()
    assert market.daily['bars']['closes'][-1] == pytest.approx(102.9)
