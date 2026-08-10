"""Unit tests for versioned variance clock contract (variance-clock-f3-v1)."""

import math
import pytest
from seiltanzer.variance_clock import (
    VARIANCE_CLOCK_VERSION,
    compute_annual_volatility,
    compute_horizon_sigma,
    get_variance_clock_spec,
)


def test_variance_clock_spec_retrieval():
    nas_spec = get_variance_clock_spec("NAS100")
    assert nas_spec["instrument"] == "NAS100"
    assert nas_spec["variance_minutes_per_year"] == 390 * 252  # 98,280
    assert nas_spec["timezone"] == "America/New_York"
    assert nas_spec["variance_clock_version"] == VARIANCE_CLOCK_VERSION

    ger_spec = get_variance_clock_spec("GER40")
    assert ger_spec["variance_minutes_per_year"] == 510 * 252  # 128,520
    assert ger_spec["timezone"] == "Europe/Berlin"

    jpy_spec = get_variance_clock_spec("JPY100")
    assert jpy_spec["variance_minutes_per_year"] == 300 * 245  # 73,500
    assert jpy_spec["timezone"] == "Asia/Tokyo"

    gold_spec = get_variance_clock_spec("XAU")
    assert gold_spec["variance_minutes_per_year"] == 1440 * 365  # 525,600


def test_annual_volatility_calculation():
    # 20 daily closes with known constant 1% daily return volatility
    base_price = 100.0
    daily_returns = [0.01 if i % 2 == 0 else -0.01 for i in range(20)]
    closes = [base_price]
    for r in daily_returns:
        closes.append(closes[-1] * math.exp(r))

    res = compute_annual_volatility(closes, "NAS100")
    assert res["volatility_status"] == "valid"
    assert res["reference_volatility_annual"] is not None
    assert res["reference_volatility_annual"] > 0
    assert res["variance_clock_version"] == VARIANCE_CLOCK_VERSION


def test_insufficient_closes_handled_gracefully():
    closes = [100.0, 101.0, 102.0]
    res = compute_annual_volatility(closes, "NAS100")
    assert res["reference_volatility_annual"] is None
    assert res["volatility_status"] == "insufficient_data"


def test_horizon_sigma_scaling_consistency():
    annual_vol = 0.20  # 20% annual volatility
    spec = get_variance_clock_spec("NAS100")
    var_mins_yr = spec["variance_minutes_per_year"]  # 98,280

    sigma_15m = compute_horizon_sigma(annual_vol, 15, "NAS100")["sigma_h_return"]
    sigma_60m = compute_horizon_sigma(annual_vol, 60, "NAS100")["sigma_h_return"]
    sigma_390m = compute_horizon_sigma(annual_vol, 390, "NAS100")["sigma_h_return"]

    expected_15m = 0.20 * math.sqrt(15.0 / var_mins_yr)
    expected_60m = 0.20 * math.sqrt(60.0 / var_mins_yr)
    expected_390m = 0.20 * math.sqrt(390.0 / var_mins_yr)

    assert abs(sigma_15m - expected_15m) < 1e-6
    assert abs(sigma_60m - expected_60m) < 1e-6
    assert abs(sigma_390m - expected_390m) < 1e-6

    # 15m < 60m < 390m monotonic scaling
    assert sigma_15m < sigma_60m < sigma_390m
