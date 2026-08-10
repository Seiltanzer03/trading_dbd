"""Unit tests for option producer -> passive Q adapter (option-q-contract-f31-v1)."""

import math
import pytest
from seiltanzer.option_q_adapter import (
    OPTION_Q_CONTRACT_VERSION,
    adapt_option_q_forecast,
    validate_and_transform_proxy_density,
)


def test_valid_option_density_adaptation():
    spot = 100.0
    strikes = [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0]
    q_vals = [0.001, 0.02, 0.05, 0.10, 0.05, 0.02, 0.001]
    density_dict = {"strikes": strikes, "q": q_vals}

    val_res = validate_and_transform_proxy_density(density_dict, spot, spot, proxy_transform="direct")
    assert val_res["valid"] is True

    option_metrics = {
        "spot": spot,
        "t_years": 15.0 / 98280.0,
        "density": density_dict,
        "skew": 0.02,
        "implied_move": {"move_frac": 0.015},
    }

    res = adapt_option_q_forecast(option_metrics, 0, 0.01, "NAS100", horizon_kind="option_native_expiry")
    assert res["q_available"] is True
    assert res["probability_measure"] == "risk_neutral_Q_terminal"
    assert res["q_source_contract"] == OPTION_Q_CONTRACT_VERSION
    assert res["quantiles_log_return"]["q50"] is not None
    assert res["standardized_barriers"]["1.0"]["q_terminal_above_upper"] is not None


def test_invalid_density_returns_unavailable():
    spot = 100.0
    bad_density = {"strikes": [100.0, 90.0, 110.0], "q": [0.1, 0.1, 0.1]}
    val_res = validate_and_transform_proxy_density(bad_density, spot, spot)
    assert val_res["valid"] is False

    option_metrics = {
        "spot": spot,
        "t_years": 0.1,
        "density": bad_density,
    }

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100", horizon_kind="fixed_trading_time")
    assert res["q_available"] is False
    assert res["probability_measure"] == "unavailable"


def test_fixed_horizon_returns_unavailable():
    spot = 100.0
    strikes = [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0]
    q_vals = [0.001, 0.02, 0.05, 0.10, 0.05, 0.02, 0.001]
    density_dict = {"strikes": strikes, "q": q_vals}

    option_metrics = {
        "spot": spot,
        "t_years": 30.0 / 252.0,
        "density": density_dict,
    }

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100", horizon_kind="fixed_trading_time")
    assert res["q_available"] is False
    assert res["probability_measure"] == "unavailable"
    assert res["horizon_alignment_status"] == "unavailable"
