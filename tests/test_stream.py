import struct
import time
import asyncio
import json

import pytest

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import (MarketData, _fetch_tradingview_quote,
                                  _fetch_tradingview_ws_quote, _tv_frame)
from seiltanzer.data.stream import StreamHub, parse_yaticker


def _yaticker(symbol: str, price: float) -> bytes:
    # protobuf yaticker: поле 1 (id, string) + поле 2 (price, float32)
    out = bytearray()
    out += bytes([(1 << 3) | 2, len(symbol)]) + symbol.encode()
    out += bytes([(2 << 3) | 5]) + struct.pack("<f", price)
    return bytes(out)


def test_tradingview_anonymous_snapshot_waits_for_complete_fresh_tick(monkeypatch):
    monkeypatch.delenv("TRADINGVIEW_AUTH_TOKEN", raising=False)
    stamp = int(time.time())
    class Socket:
        def __init__(self):
            self.sent = []
            self.frames = ["session",
                           _tv_frame("qsd", ["session", {"n": "OANDA:NAS100USD",
                               "v": {"bid": 30165.0, "ask": 30167.0}}]),
                           _tv_frame("qsd", ["session", {"n": "OANDA:NAS100USD",
                               "v": {"lp": 30166.0, "lp_time": stamp,
                                     "update_mode": "streaming"}}])]
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def send(self, frame): self.sent.append(frame)
        def recv(self, **kwargs):
            return self.frames.pop(0)
    socket = Socket()
    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: socket)
    result = _fetch_tradingview_quote("OANDA:NAS100USD")
    assert result["ts"] == stamp
    assert result["provider_timestamp_verified"] is True
    assert result["bid"] == 30165 and result["ask"] == 30167
    assert result["value"] == 30166
    assert any("unauthorized_user_token" in frame for frame in socket.sent)
    assert any('quote_add_symbols' in frame and 'force_permission' not in frame
               for frame in socket.sent)


def test_tradingview_rejects_stale_broker_tick(monkeypatch):
    stamp = int(time.time()) - 3600
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def send(self, frame): pass
        def recv(self, **kwargs):
            if not hasattr(self, "handshake"):
                self.handshake = True
                return "session"
            if not hasattr(self, "quote"):
                self.quote = True
                return _tv_frame("qsd", ["session", {"n": "OANDA:NAS100USD",
                    "v": {"lp": 30166, "bid": 30165, "ask": 30167,
                          "lp_time": stamp, "update_mode": "streaming"}}])
            raise TimeoutError
    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: Socket())
    with pytest.raises(RuntimeError, match="timeout"):
        _fetch_tradingview_ws_quote("OANDA:NAS100USD")


def test_parse_yaticker_extracts_id_and_price():
    msg = _yaticker("QQQ", 512.25)
    parsed = parse_yaticker(msg)
    assert parsed["id"] == "QQQ"
    assert parsed["price"] == pytest.approx(512.25, rel=1e-5)


def test_parse_yaticker_survives_garbage():
    # битый кадр не должен ронять — возвращает частичный/пустой результат
    assert isinstance(parse_yaticker(b"\xff\xff\x01\x02"), dict)


def test_streamhub_fresh_window():
    hub = StreamHub(["QQQ"])
    assert hub.fresh("QQQ") is None
    hub.latest["QQQ"] = (500.0, time.time())
    assert hub.fresh("QQQ", max_age=8.0) == 500.0
    hub.latest["QQQ"] = (500.0, time.time() - 100)
    assert hub.fresh("QQQ", max_age=8.0) is None  # протухло


def test_binance_combined_stream_uses_exchange_trade_time(monkeypatch):
    hub = StreamHub([], ["BTCUSDT", "ETHUSDT"])
    received = []

    class Socket:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def __aiter__(self):
            yield json.dumps({"stream": "btcusdt@trade", "data": {
                "s": "BTCUSDT", "p": "80000.12",
                "T": int((time.time() - 2) * 1000)}})
            hub._stop = True

    def connect(url, **kwargs):
        received.append(url)
        return Socket()

    monkeypatch.setattr("websockets.connect", connect)
    asyncio.run(hub._run_binance())
    assert received == [
        "wss://stream.binance.com:9443/stream?streams=btcusdt@trade/ethusdt@trade"]
    assert hub.fresh("BTCUSDT") == pytest.approx(80000.12)
    assert 1 <= time.time() - hub.latest["BTCUSDT"][1] <= 8


class _StubStream:
    def __init__(self, quotes):
        self.quotes = quotes

    def fresh(self, symbol, max_age=8.0):
        return self.quotes.get(symbol)


def test_price_never_uses_proxy_as_instrument_quote(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.stream = _StubStream({"QQQ": 102.0})  # ^NDX намеренно молчит
        md.price = {"value": 20_000.0, "status": "delayed", "ts": time.time()}
        md._last_price_rest_attempt = time.time()
        md._last_broker_rest_attempt = time.time()
        md.refresh_price()
        assert md.price["value"] == pytest.approx(20_000.0)
        assert md.price.get("derived") is not True
    finally:
        cache.close()


@pytest.mark.parametrize("transport", ["snapshot", "websocket"])
def test_index_uses_exact_broker_snapshot_not_cash_index(tmp_path, monkeypatch, transport):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.stream = _StubStream({"^NDX": 28_274.2})
        monkeypatch.setattr(
            "seiltanzer.data.feeds._fetch_tradingview_quote",
            lambda symbol: {"value": 28_341.5, "bid": 28_340.5,
                            "ask": 28_342.5, "ts": time.time(),
                            "transport": transport,
                            "update_mode": "streaming",
                            "description": "US Nas 100"})
        md.refresh_price()
        assert md.price["value"] == pytest.approx(28_341.5)
        assert md.price["source"] == f"TradingView {transport} OANDA:NAS100USD"
        assert md.price["instrument_type"] == "broker_cfd"
        assert md.price["derived"] is False
        assert md._has_direct_price_scale() is True
    finally:
        cache.close()


@pytest.mark.parametrize("known_timezone", [True, False])
def test_index_fallback_cannot_rejuvenate_previous_session(tmp_path, monkeypatch, known_timezone):
    import pandas as pd
    import yfinance as yf
    from types import SimpleNamespace

    stamp = pd.Timestamp.now(tz="UTC").floor("min") - pd.Timedelta(days=1)
    if not known_timezone:
        stamp = stamp.tz_localize(None)
    history = pd.DataFrame({"Close": [20000.0]}, index=[stamp])
    monkeypatch.setattr(yf, "Ticker", lambda _: SimpleNamespace(history=lambda **_: history))
    monkeypatch.setattr(
        "seiltanzer.data.feeds._fetch_tradingview_quote",
        lambda _: (_ for _ in ()).throw(RuntimeError("broker unavailable")))
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=False, data_dir=str(tmp_path)), cache)
        for _ in range(2):
            md._last_price_rest_attempt = md._last_broker_rest_attempt = 0
            md.refresh_price()
            if known_timezone:
                assert md.price["ts"] == stamp.timestamp()
                assert md.price["status"] == "delayed"
                assert md.price["instrument_type"] == "cash_index"
                assert md._has_direct_price_scale() is False
            else:
                assert md.price["status"] == "no_data"
                assert md.price["value"] is None
    finally:
        cache.close()


def test_inverse_proxy_does_not_replace_fx_quote(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.set_instrument("USDCAD")
        md.stream = _StubStream({"FXC": 102.0})  # CAD-strength proxy +2%
        md.price = {"value": 1.40, "status": "delayed", "ts": time.time()}
        md._last_price_rest_attempt = time.time()
        md.refresh_price()
        assert md.price["value"] == pytest.approx(1.40)
    finally:
        cache.close()


def test_gold_proxy_does_not_replace_futures_quote(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.set_instrument("XAU")
        md.stream = _StubStream({"PAXG-USD": 4040.0})
        md.price = {"value": 4000.0, "status": "delayed", "ts": time.time()}
        md._last_price_rest_attempt = time.time()
        md.refresh_price()
        assert md.price["value"] == pytest.approx(4000.0)
    finally:
        cache.close()


def test_gold_uses_direct_spot_quote_not_futures_stream(tmp_path, monkeypatch):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.set_instrument("XAU")
        md.stream = _StubStream({"GC=F": 4107.0})
        monkeypatch.setattr(
            "seiltanzer.data.feeds._fetch_swissquote_quote",
            lambda pair: {"value": 4044.0, "bid": 4043.7, "ask": 4044.3,
                          "ts": time.time(), "provider_timestamp_verified": True})
        md.refresh_price()
        assert md.price["value"] == pytest.approx(4044.0)
        assert md.price["source"].startswith("Swissquote OTC XAU/USD")
        assert md.price["instrument_type"] == "spot_otc"
        assert md.price["derived"] is False
    finally:
        cache.close()


def test_spot_failure_never_falls_back_to_wrong_futures(tmp_path, monkeypatch):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.set_instrument("XAU")
        md.stream = _StubStream({"GC=F": 4107.0})
        monkeypatch.setattr(
            "seiltanzer.data.feeds._fetch_swissquote_quote",
            lambda pair: (_ for _ in ()).throw(RuntimeError("feed down")))
        md.refresh_price()
        assert md.price["value"] is None
        assert md.price["status"] == "no_data"
    finally:
        cache.close()


def test_gold_prefers_gld_driver_when_both_are_fresh(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.set_instrument("XAU")
        md.stream = _StubStream({"GLD": 370.0, "PAXG-USD": 4040.0})
        assert md._fresh_price_driver() == ("GLD", 370.0)
    finally:
        cache.close()
