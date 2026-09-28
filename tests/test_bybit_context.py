import time
from types import SimpleNamespace

import httpx
import pytest

from seiltanzer.data.bybit import BybitPublic, PRODUCTS, fallback_quote, fresh_quote, missing, option_context, matches_underlying
from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import MarketData


def quote(now, value=500, mapped=False):
    return {"value": value, "bid": value-0.1, "ask": value+0.1, "ts": now,
            "status": "delayed", "symbol": "QQQUSDT" if mapped else "XAUUSDT",
            "mapped_proxy": mapped, "derived": True, "source": "Bybit test",
            "production_authority": False}


def test_primary_wins_and_stale_secondary_cannot_be_used():
    primary = {"value": 4000, "ts": 1000, "status": "live"}
    assert fallback_quote(primary, quote(1000), None, 1000) is primary
    assert fallback_quote({}, quote(900), None, 1000) == {}
    assert not fresh_quote({"value": float("nan"), "ts": 1000, "status": "live"}, 1000)


def test_direct_reference_fallback_is_explicit():
    out = fallback_quote(missing("primary down"), quote(1000, 4001), None, 1000)
    assert out["value"] == 4001
    assert out["fallback"] is True
    assert out["production_authority"] is False
    assert out["status"] == "delayed"


def test_qqq_requires_fresh_anchor_and_maps_entire_bid_ask():
    secondary = quote(1000, mapped=True)
    assert fallback_quote({}, secondary, None, 1000) == {}
    anchor = {"price": 20000, "proxy": 400, "ts": 900, "symbol": "QQQUSDT"}
    out = fallback_quote({}, secondary, anchor, 1000)
    assert out["value"] == 25000
    assert out["bid"] == secondary["bid"] * 50
    assert fallback_quote({}, secondary, {**anchor, "ts": 1}, 100000) == {}
    assert fallback_quote({}, secondary, {**anchor, "symbol": "SPYUSDT"}, 1000) == {}


def test_quote_validates_listing_spread_and_preserves_server_time(monkeypatch):
    now = time.time()
    client = BybitPublic()
    row = {"symbol": "XAUUSDT", "bid1Price": "4000", "ask1Price": "4001",
           "bid1Size": "1", "ask1Size": "2", "indexPrice": "3999", "markPrice": "4000"}
    def get(path, *args):
        if path == "instruments-info":
            return {"result": {"list": [{"symbol": "XAUUSDT", "status": "Trading",
                     "settleCoin": "USDT", "contractType": "LinearPerpetual", "underlyingTicker": "XAU/USD"}]}}
        return {"time": now*1000, "result": {"list": [row]}}
    monkeypatch.setattr(client, "get", get)
    result = client.quote("XAU")
    assert result["value"] == 4000.5
    assert result["ts"] == now
    assert result["index_price"] == 3999
    row["ask1Price"] = "5000"
    with pytest.raises(ValueError):
        client.quote("XAU")


def test_cold_start_anchor_requires_matching_completed_minute(monkeypatch):
    client = BybitPublic()
    stamp = int(time.time() // 60)*60 - 3600
    primary = {"value": 20000, "ts": stamp, "timestamp_kind": "bar_start"}
    row = [stamp*1000, "399", "401", "398", "400", "1", "400"]
    monkeypatch.setattr(client, "get", lambda *args: {"result": {"list": [row]}})
    assert client.historical_anchor("NAS100", primary)["proxy"] == 400
    row[0] -= 60000
    assert client.historical_anchor("NAS100", primary) is None
    assert client.historical_anchor("NAS100", {**primary, "timestamp_kind": "unknown"}) is None


def test_spx_memecoin_or_unknown_underlying_cannot_replace_index():
    assert PRODUCTS["SP500"][0] == "SPYUSDT"
    assert PRODUCTS["SP500"][2] is True
    assert not matches_underlying("SP500", {"baseCoin": "SPX", "fullName": "SPX6900"})
    assert not matches_underlying("NAS100", {"baseCoin": "QQQ"})
    assert matches_underlying("SP500", {"underlyingTicker": "SPY"})


def fixtures(now):
    specs, ticks = [], []
    for strike in (90, 100, 110):
        for suffix, side in (("C", "Call"), ("P", "Put")):
            symbol = f"QQQ-30OCT26-{strike}-{suffix}"
            specs.append({"symbol": symbol, "status": "Trading", "baseCoin": "QQQ",
                          "settleCoin": "USDT", "deliveryTime": (now+86400)*1000,
                          "optionsType": side})
            ticks.append({"symbol": symbol, "markIv": "0.2", "underlyingPrice": "100",
                          "bid1Price": "1", "ask1Price": "1.1", "bid1Size": "1",
                          "ask1Size": "1", "gamma": "0.01", "openInterest": "10"})
    return specs, ticks


def test_gamma_is_unsigned_native_1x_and_missing_is_not_zero():
    specs, ticks = fixtures(1000)
    ticks[0].pop("gamma")
    result = option_context("QQQ", specs, ticks, 1000)
    assert len(result["surface"]["value"]) == 1
    assert len(result["gamma"]) == 5
    assert all(x["value"] == 10 for x in result["gamma"])
    assert result["zero_flip"] is None
    assert result["production_authority"] is False
    assert result["surface"]["spot_current"] == 100


def test_thin_or_unlisted_options_cannot_create_surface():
    specs, ticks = fixtures(1000)
    with pytest.raises(ValueError):
        option_context("QQQ", specs[:2], ticks, 1000)
    for tick in ticks:
        tick["ask1Size"] = "0"
    with pytest.raises(ValueError):
        option_context("QQQ", specs, ticks, 1000)


def test_missing_catalog_symbol_does_not_request_fake_chain(monkeypatch):
    client = BybitPublic()
    calls = []
    def get(path, **kwargs):
        calls.append(path)
        return {"result": {"list": [{"baseCoin": "QQQ", "hasSymbol": 0}]}}
    monkeypatch.setattr(client, "get", get)
    with pytest.raises(ValueError):
        client.options("NAS100")
    assert calls == ["option-base-coins"]


def test_market_recovers_primary_and_clears_on_instrument_switch(tmp_path, monkeypatch):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(data_dir=str(tmp_path)), cache)
        md.set_instrument("XAU")
        original_chain = md.chain
        md.bybit_quote = quote(time.time(), 4001)
        monkeypatch.setattr(md, "_refresh_primary_price", lambda: setattr(md, "price", missing("down")))
        md.refresh_price()
        assert md.price["fallback"] is True
        assert md.chain is original_chain
        primary = {"value": 4000, "ts": time.time(), "status": "live", "source": "Swissquote OTC"}
        monkeypatch.setattr(md, "_refresh_primary_price", lambda: setattr(md, "price", primary))
        md.refresh_price()
        assert md.price is primary
        md.set_instrument("SP500")
        assert md.bybit_quote["value"] is None
        assert md._bybit_anchor is None
    finally:
        cache.close()


def test_nas100_fallback_keeps_oanda_scale_across_yahoo_and_restart(tmp_path, monkeypatch):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(data_dir=str(tmp_path)), cache)
        now = time.time()
        md.bybit_quote = quote(now, 400, mapped=True)
        broker = {"value": 20000, "ts": now, "status": "live",
                  "source": "TradingView WebSocket OANDA:NAS100USD"}
        monkeypatch.setattr(md, "_refresh_primary_price", lambda: setattr(md, "price", broker))
        md.refresh_price()
        assert md.price is broker
        yahoo = {"value": 23000, "ts": now, "status": "delayed",
                 "source": "yfinance REST ^NDX (indicative)"}
        monkeypatch.setattr(md, "_refresh_primary_price", lambda: setattr(md, "price", yahoo))
        md.bybit_quote = quote(now, 404, mapped=True)
        md.refresh_price()
        assert md.price["value"] == pytest.approx(20200)
        assert md.price["fallback"] is True
        restored = MarketData(Settings(data_dir=str(tmp_path)), cache)
        restored.bybit_quote = quote(now, 404, mapped=True)
        monkeypatch.setattr(restored, "_refresh_primary_price", lambda: setattr(restored, "price", yahoo))
        restored.refresh_price()
        assert restored.price["value"] == pytest.approx(20200)
        restored.bybit_quote = missing("feed down")
        restored.refresh_price()
        assert restored.price["value"] is None
    finally:
        cache.close()


def test_nas100_yahoo_scale_continues_when_oanda_never_available(tmp_path, monkeypatch):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(data_dir=str(tmp_path)), cache)
        now = time.time()
        yahoo = {"value": 30000, "ts": now, "status": "live", "source": "stream ^NDX (broker fallback)"}
        md.bybit_quote = quote(now, 400, mapped=True)
        monkeypatch.setattr(md, "_refresh_primary_price", lambda: setattr(md, "price", yahoo))
        md.refresh_price()
        assert md.price is yahoo
        md.bybit_quote = quote(now, 401, mapped=True)
        monkeypatch.setattr(md, "_refresh_primary_price", lambda: setattr(md, "price", missing("Yahoo closed")))
        md.refresh_price()
        assert md.price["value"] == pytest.approx(30075)
        assert md.price["fallback"] is True
        restarted = MarketData(Settings(data_dir=str(tmp_path)), cache)
        restarted.bybit_quote = quote(now, 401, mapped=True)
        monkeypatch.setattr(restarted, "_refresh_primary_price",
                            lambda: setattr(restarted, "price", missing("Yahoo closed")))
        restarted.refresh_price()
        assert restarted.price["value"] == pytest.approx(30075)
    finally:
        cache.close()


def test_http_access_denied_backs_off_without_option_requests(tmp_path, monkeypatch):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(data_dir=str(tmp_path)), cache)
        response = httpx.Response(403, request=httpx.Request("GET", "https://api.bybit.com"))
        calls = []
        def denied(code):
            calls.append(code)
            response.raise_for_status()
        md.bybit_client = SimpleNamespace(quote=denied)
        md.refresh_bybit()
        md.refresh_bybit()
        assert len(calls) == 1
        assert md.bybit_quote["status"] == "no_data"
        assert md._bybit_retry_at > time.time() + 3500
    finally:
        cache.close()


def test_transient_bybit_rest_failure_preserves_only_still_fresh_quote(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(data_dir=str(tmp_path)), cache)
        now = time.time()
        md.bybit_quote = quote(now - 5, 400, mapped=True)
        md.bybit_client = SimpleNamespace(quote=lambda _: (_ for _ in ()).throw(
            TimeoutError("temporary timeout")))
        md.refresh_bybit()
        assert md.bybit_quote["value"] == 400
        assert md.bybit_quote["status"] == "delayed"
        assert md.bybit_quote["ts"] == pytest.approx(now - 5)
        assert "Bybit retry" in md.bybit_quote["error"]
        assert md._bybit_retry_at < now + 12
        md._bybit_retry_at = 0
        md.bybit_quote["ts"] = now - 46
        md.refresh_bybit()
        assert md.bybit_quote["status"] == "no_data"
    finally:
        cache.close()
