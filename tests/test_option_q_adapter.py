"""Unit tests for option producer -> passive Q adapter (option-q-contract-f3-v1)."""

import math
import pytest
from seiltanzer.option_q_adapter import (
    OPTION_Q_CONTRACT_VERSION,
    adapt_option_q_forecast,
    check_horizon_alignment,
    validate_option_density,
)


def test_valid_option_density_adaptation():
    spot = 100.0
    # Synthetic Gaussian density around spot=100
    strikes = [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0]
    q_vals = [0.001, 0.02, 0.05, 0.10, 0.05, 0.02, 0.001]
    density_dict = {"strikes": strikes, "q": q_vals}

    val_res = validate_option_density(density_dict, spot)
    assert val_res["valid"] is True

    option_metrics = {
        "spot": spot,
        "t_years": 15.0 / 98280.0,  # ~15 minutes TTM in US equity clock
        "density": density_dict,
        "skew": 0.02,
        "implied_move": {"move_frac": 0.015},
    }

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100")
    assert res["q_available"] is True
    assert res["probability_measure"] == "risk_neutral_Q"
    assert res["q_source_contract"] == OPTION_Q_CONTRACT_VERSION
    assert res["quantiles_log_return"]["q50"] is not None
    assert res["standardized_barriers"]["1.0"]["up"] is not None
    assert res["standardized_barriers"]["1.0"]["down"] is not None
    assert res["standardized_barriers"]["1.0"]["no_touch"] is not None

    # Verify probability sum == 1.0
    b = res["standardized_barriers"]["1.0"]
    assert abs(b["up"] + b["down"] + b["no_touch"] - 1.0) < 1e-5


def test_invalid_density_returns_unavailable():
    spot = 100.0
    # Non-monotonic strikes
    bad_density = {"strikes": [100.0, 90.0, 110.0], "q": [0.1, 0.1, 0.1]}
    val_res = validate_option_density(bad_density, spot)
    assert val_res["valid"] is False

    option_metrics = {
        "spot": spot,
        "t_years": 0.1,
        "density": bad_density,
    }

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100")
    assert res["q_available"] is False
    assert res["probability_measure"] == "unavailable"
    assert res["q_source_contract"] == "unavailable"


def test_horizon_mismatch_returns_unavailable():
    spot = 100.0
    strikes = [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0]
    q_vals = [0.001, 0.02, 0.05, 0.10, 0.05, 0.02, 0.001]
    density_dict = {"strikes": strikes, "q": q_vals}

    # 30 days TTM (~30 * 390 = 11,700 mins) evaluated against 15m target horizon
    option_metrics = {
        "spot": spot,
        "t_years": 30.0 / 252.0,  # ~30 trading days
        "density": density_dict,
    }

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100")
    assert res["q_available"] is False
    assert res["probability_measure"] == "unavailable"
    assert res["horizon_alignment_status"] == "unavailable"
    assert res["horizon_alignment_method"] == "unsupported_horizon_mismatch"
