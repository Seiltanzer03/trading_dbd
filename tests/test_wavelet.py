import math

from seiltanzer.core.wavelet import compute_wavelet_analysis


def test_wavelet_empty():
    res = compute_wavelet_analysis([])
    assert res["available"] is False
    assert res["summary"]["authority"] == "derived_price_context"
    assert res["summary"]["independent_vote"] is False


def test_wavelet_detects_four_hour_cycle_from_sufficient_history():
    # 5m sampling: a 4h cycle has 48 observations. 600 bars give >12 cycles,
    # enough to resolve the frequency without pretending that a 5-day period
    # can be estimated from a few hours of data.
    n = 600
    prices = [
        {
            "ts": 1_000 + i * 300,
            "price": 100.0 * math.exp(0.012 * math.sin(2 * math.pi * i / 48)),
        }
        for i in range(n)
    ]
    res = compute_wavelet_analysis(prices)
    assert res["available"] is True
    assert len(res["period_grid_hours"]) >= 6
    assert len(res["spectrogram"]) == len(res["period_grid_hours"])
    assert len(res["dominant_ridge"]) <= 360
    assert abs(res["summary"]["dominant_period_hours"] - 4.0) <= 2.0
    assert res["summary"]["history_hours_trading"] >= 49.0
    total = (
        res["summary"]["micro_energy_pct"]
        + res["summary"]["intraday_energy_pct"]
        + res["summary"]["macro_energy_pct"]
    )
    assert 99.0 <= total <= 100.1
    assert 0.0 <= res["summary"]["spectral_concentration"] <= 1.0
    assert res["summary"]["authority"] == "derived_price_context"
    assert res["summary"]["independent_vote"] is False


def test_wavelet_does_not_publish_unresolvable_long_periods():
    # 120 bars = 10 trading hours; only periods <=5h have two cycles.
    prices = [
        {"ts": 1_000 + i * 300, "price": 100.0 * math.exp(0.005 * math.sin(i / 5))}
        for i in range(120)
    ]
    res = compute_wavelet_analysis(prices)
    assert res["available"] is True
    assert max(res["period_grid_hours"]) <= 4.0
    assert 24.0 not in res["period_grid_hours"]
    assert 120.0 not in res["period_grid_hours"]


def test_wavelet_constant_series_is_honest_no_data():
    prices = [{"ts": 1_000 + i * 300, "price": 100.0} for i in range(200)]
    res = compute_wavelet_analysis(prices)
    assert res["available"] is False
    assert "почти постоянна" in res["reason"]
