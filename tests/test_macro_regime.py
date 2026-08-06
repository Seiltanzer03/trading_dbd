import pytest
from seiltanzer.core.macro_regime import compute_macro_regime


def test_macro_regime_empty():
    res = compute_macro_regime([])
    assert res["available"] is False
    assert res["summary"]["authority"] == "strategy_context"
    assert res["summary"]["independent_vote"] is False


def test_macro_regime_trend_expansion():
    prices = [{"ts": 1000 + i * 300, "price": 100.0 * (1.0 + i * 0.005)} for i in range(50)]
    vols = {"vix": 22.0}
    corr = {"matrix_delta": [[0.1, 0.2], [0.2, 0.1]]}

    res = compute_macro_regime(prices, vols, corr)
    assert res["available"] is True
    assert "current" in res
    assert res["current"]["x_trend"] > 0
    assert res["summary"]["authority"] == "strategy_context"


def test_macro_regime_vol_shock():
    prices = [{"ts": 1000 + i * 300, "price": 100.0} for i in range(50)]
    vols = {"vix": 50.0}  # Очень высокий VIX -> VOL SHOCK

    res = compute_macro_regime(prices, vols)
    assert res["available"] is True
    assert res["current"]["regime"] == "VOL SHOCK"
