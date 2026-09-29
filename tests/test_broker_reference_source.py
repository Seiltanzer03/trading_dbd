from types import SimpleNamespace

import pytest

from seiltanzer.app import _validate_reference_source


def market(price):
    instrument = SimpleNamespace(tradingview_symbol="OANDA:NAS100USD", swissquote_pair=None)
    return SimpleNamespace(price=price, instrument=instrument)


def test_broker_basis_rejects_proxy_and_indicative_sources():
    with pytest.raises(ValueError, match="Bybit"):
        _validate_reference_source(market({"value": 30253, "status": "delayed",
                                           "source": "Bybit QQQUSDT mapped", "fallback": True}))
    with pytest.raises(ValueError, match="прямой котировки"):
        _validate_reference_source(market({"value": 30253, "status": "live",
                                           "source": "stream ^NDX"}))


def test_broker_basis_accepts_only_fresh_direct_quote():
    direct = {"value": 30166, "status": "live", "fresh": True,
              "source": "TradingView stream OANDA:NAS100USD"}
    _validate_reference_source(market(direct))
    with pytest.raises(ValueError, match="свежей"):
        _validate_reference_source(market({**direct, "fresh": False}))
