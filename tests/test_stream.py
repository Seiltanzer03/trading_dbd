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


def test_price_uses_live_proxy_return_between_rest_anchors(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.stream = _StubStream({"QQQ": 102.0})  # ^NDX намеренно молчит
        md._set_price_anchor(20_000.0, 100.0, time.time())
        md.refresh_price()
        assert md.price["value"] == pytest.approx(20_400.0)
        assert md.price["status"] == "live"
        assert md.price["derived"] is True
        assert md.price["driver_ticker"] == "QQQ"
    finally:
        cache.close()


def test_inverse_proxy_return_changes_sign(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.set_instrument("USDCAD")
        md.stream = _StubStream({"FXC": 102.0})  # CAD-strength proxy +2%
        md._set_price_anchor(1.40, 100.0, time.time())
        md.refresh_price()
        assert md.price["value"] == pytest.approx(1.40 / 1.02)
        assert md.price["source"].endswith("(inverse derived)")
    finally:
        cache.close()


def test_gold_uses_live_paxg_when_gld_stream_is_silent(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        md = MarketData(Settings(stream=True, data_dir=str(tmp_path)), cache)
        md.set_instrument("XAU")
        md.stream = _StubStream({"PAXG-USD": 4040.0})
        md._set_price_anchor(
            4000.0, 4000.0, time.time(), driver_ticker="PAXG-USD")
        md.refresh_price()
        assert md.price["value"] == pytest.approx(4040.0)
        assert md.price["status"] == "live"
        assert md.price["driver_ticker"] == "PAXG-USD"
        assert md.price["driver_experimental"] is True
        assert md.price["anchor_ticker"] == "GC=F"
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
