import struct
import time

import pytest

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
