import struct
import time

import pytest

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.data.feeds import MarketData
from seiltanzer.data.stream import StreamHub, parse_yaticker


def _yaticker(symbol: str, price: float) -> bytes:
    # protobuf yaticker: поле 1 (id, string) + поле 2 (price, float32)
    out = bytearray()
    out += bytes([(1 << 3) | 2, len(symbol)]) + symbol.encode()
    out += bytes([(2 << 3) | 5]) + struct.pack("<f", price)
    return bytes(out)


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
        md.refresh_price()
        assert md.price["value"] == pytest.approx(20_000.0)
        assert md.price.get("derived") is not True
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
                          "ts": time.time()})
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
