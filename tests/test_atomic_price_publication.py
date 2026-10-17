from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest

from seiltanzer.ai_policy_v6 import _input_audit
from seiltanzer.ai_snapshot_materializer import AISnapshotMaterializer
from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import MarketData


@pytest.fixture
def market(tmp_path):
    cache=DiskCache(str(tmp_path/'cache.db'))
    feed=MarketData(Settings(data_dir=str(tmp_path)),cache)
    feed.price={'value':100.,'status':'delayed','source':'Bybit QQQ mapped',
                'production_authority':False,'derived':True,'fallback':True,'ts':time.time()}
    feed.bybit_quote={**feed.price,'fallback':False,'symbol':'QQQUSDT'}
    feed._primary_price={'value':101.,'status':'delayed','source':'yfinance cash',
                         'ts':time.time()-15000,'derived':False}
    yield feed
    cache.close()


def materializer(feed):
    trade={'id':1,'instrument':'NAS100','entry':100.,'stop':99.,'take':103.,'direction':'long'}
    engine=SimpleNamespace(market=feed,journal=SimpleNamespace(active_trade=lambda:trade),
        canonical_tick_payload=lambda **kwargs:{'instrument':'NAS100','feeds':{'price':dict(feed.price)}})
    def build(_):
        return {'trade_id':1,'strategy':{'instrument':'NAS100'},
                'trade_geometry':{'current_r':0.},
                'policy_manager':{'input_audit':_input_audit(engine.canonical_tick_payload(),{})}}
    result=AISnapshotMaterializer(engine,build);result._build_once()
    assert result._event_reason() is None
    return result


@pytest.mark.parametrize('mutate_candidate',[False,True])
def test_pending_primary_is_invisible_to_real_ai_event_detection(market,monkeypatch,mutate_candidate):
    original=deepcopy(market.price);mat=materializer(market)
    entered=threading.Event();release=threading.Event()
    def primary():
        if mutate_candidate:market.price.update(source='pending primary',production_authority=True)
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(market,'_refresh_primary_price',primary)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(market.refresh_price)
        try:
            assert entered.wait(1)
            assert mat._event_reason() is None
            assert market.price==original
        finally:release.set()
        pending.result(timeout=1)
    assert market.price['source']==original['source']
    assert market.price['production_authority'] is False
    assert market.price['ts']==original['ts']
    assert mat._event_reason() is None


def test_failed_refresh_does_not_publish_partial_quote(market,monkeypatch):
    original=deepcopy(market.price);primary_before=deepcopy(market._primary_price)
    def fail():
        market.price.update(value=None,status='no_data',source='unfinished')
        raise RuntimeError('isolated failed refresh')
    monkeypatch.setattr(market,'_refresh_primary_price',fail)
    with pytest.raises(RuntimeError,match='isolated failed refresh'):market.refresh_price()
    assert market.price==original
    assert market._primary_price==primary_before


def test_completed_real_authority_recovery_still_invalidates(market,monkeypatch):
    mat=materializer(market)
    recovered={'value':100.,'status':'live','source':'TradingView snapshot OANDA:NAS100USD',
               'production_authority':True,'derived':False,'ts':time.time()}
    monkeypatch.setattr(market,'_refresh_primary_price',lambda:setattr(market,'price',recovered))
    market.refresh_price()
    assert market.price is recovered
    assert mat._event_reason()=='PRICE_AUTHORITY_CHANGED'


def test_instrument_switch_discards_inflight_price_and_primary(market,monkeypatch):
    entered=threading.Event();release=threading.Event()
    def primary():
        entered.set();assert release.wait(2)
        market.price={'value':100.,'status':'live','source':'old NAS100','ts':time.time()}
    monkeypatch.setattr(market,'_refresh_primary_price',primary)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(market.refresh_price)
        try:
            assert entered.wait(1);market.set_instrument('XAU')
            switched=deepcopy(market.price);primary_after_switch=deepcopy(market._primary_price)
        finally:release.set()
        pending.result(timeout=1)
    assert market.price==switched
    assert market._primary_price==primary_after_switch


def test_overlapping_refresh_does_not_start_another_network_owner(market,monkeypatch):
    entered=threading.Event();release=threading.Event();calls=[]
    def primary():
        calls.append(1);entered.set();assert release.wait(2)
    monkeypatch.setattr(market,'_refresh_primary_price',primary)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending=executor.submit(market.refresh_price)
        try:
            assert entered.wait(1)
            executor.submit(market.refresh_price).result(timeout=1)
            assert len(calls)==1
        finally:release.set()
        pending.result(timeout=1)
