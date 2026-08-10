"""Versioned Variance Clock Contract (variance-clock-f3-v1).

Standardizes annualization and horizon de-annualization across instrument families.
Prevents clock mismatch between daily annualization and intraday scaling.
"""
from __future__ import annotations

import math
from typing import Any, Dict

VARIANCE_CLOCK_VERSION = "variance-clock-f3-v1"

# Instrument family variance clock contracts
_VARIANCE_CLOCK_SPECS: Dict[str, Dict[str, Any]] = {
    "NAS100": {
        "instrument": "NAS100",
        "asset_class": "equity_index_us",
        "timezone": "America/New_York",
        "session_type": "regular_cash",
        "trading_minutes_per_day": 390,
        "trading_days_basis": 252,
        "variance_minutes_per_year": 390 * 252,  # 98,280
        "annualization_basis_days": 252.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "SP500": {
        "instrument": "SP500",
        "asset_class": "equity_index_us",
        "timezone": "America/New_York",
        "session_type": "regular_cash",
        "trading_minutes_per_day": 390,
        "trading_days_basis": 252,
        "variance_minutes_per_year": 390 * 252,  # 98,280
        "annualization_basis_days": 252.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "US30": {
        "instrument": "US30",
        "asset_class": "equity_index_us",
        "timezone": "America/New_York",
        "session_type": "regular_cash",
        "trading_minutes_per_day": 390,
        "trading_days_basis": 252,
        "variance_minutes_per_year": 390 * 252,  # 98,280
        "annualization_basis_days": 252.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "GER40": {
        "instrument": "GER40",
        "asset_class": "equity_index_eu",
        "timezone": "Europe/Berlin",
        "session_type": "regular_cash",
        "trading_minutes_per_day": 510,  # 9:00 - 17:30
        "trading_days_basis": 252,
        "variance_minutes_per_year": 510 * 252,  # 128,520
        "annualization_basis_days": 252.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "UK100": {
        "instrument": "UK100",
        "asset_class": "equity_index_eu",
        "timezone": "Europe/London",
        "session_type": "regular_cash",
        "trading_minutes_per_day": 510,  # 8:00 - 16:30
        "trading_days_basis": 252,
        "variance_minutes_per_year": 510 * 252,  # 128,520
        "annualization_basis_days": 252.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "JPY100": {
        "instrument": "JPY100",
        "asset_class": "equity_index_asia",
        "timezone": "Asia/Tokyo",
        "session_type": "split_session",
        "trading_minutes_per_day": 300,  # 9:00-11:30 + 12:30-15:00
        "trading_days_basis": 245,
        "variance_minutes_per_year": 300 * 245,  # 73,500
        "annualization_basis_days": 245.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "XAU": {
        "instrument": "XAU",
        "asset_class": "commodity_metals",
        "timezone": "UTC",
        "session_type": "continuous_24h",
        "trading_minutes_per_day": 1440,
        "trading_days_basis": 365,
        "variance_minutes_per_year": 1440 * 365,  # 525,600
        "annualization_basis_days": 365.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "XAG": {
        "instrument": "XAG",
        "asset_class": "commodity_metals",
        "timezone": "UTC",
        "session_type": "continuous_24h",
        "trading_minutes_per_day": 1440,
        "trading_days_basis": 365,
        "variance_minutes_per_year": 1440 * 365,  # 525,600
        "annualization_basis_days": 365.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "EURUSD": {
        "instrument": "EURUSD",
        "asset_class": "forex",
        "timezone": "UTC",
        "session_type": "continuous_5d_24h",
        "trading_minutes_per_day": 1440,
        "trading_days_basis": 252,
        "variance_minutes_per_year": 1440 * 252,  # 362,880
        "annualization_basis_days": 252.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
    "USDCAD": {
        "instrument": "USDCAD",
        "asset_class": "forex",
        "timezone": "UTC",
        "session_type": "continuous_5d_24h",
        "trading_minutes_per_day": 1440,
        "trading_days_basis": 252,
        "variance_minutes_per_year": 1440 * 252,  # 362,880
        "annualization_basis_days": 252.0,
        "annualization_estimator": "sample_std_daily_log_returns",
    },
}

_DEFAULT_SPEC: Dict[str, Any] = {
    "instrument": "DEFAULT",
    "asset_class": "generic",
    "timezone": "UTC",
    "session_type": "continuous_24h",
    "trading_minutes_per_day": 1440,
    "trading_days_basis": 365,
    "variance_minutes_per_year": 1440 * 365,
    "annualization_basis_days": 365.0,
    "annualization_estimator": "sample_std_daily_log_returns",
}


def get_variance_clock_spec(instrument: str) -> Dict[str, Any]:
    """Returns the immutable variance clock specification for an instrument."""
    spec = _VARIANCE_CLOCK_SPECS.get(instrument)
    if spec is None:
        return {**_DEFAULT_SPEC, "instrument": instrument, "variance_clock_version": VARIANCE_CLOCK_VERSION}
    return {**spec, "variance_clock_version": VARIANCE_CLOCK_VERSION}


def compute_annual_volatility(
    closes: list[float] | list[int] | Any,
    instrument: str,
) -> Dict[str, Any]:
    """Computes reference annual volatility from daily closes using the instrument's variance clock."""
    spec = get_variance_clock_spec(instrument)
    try:
        clean_closes = [float(x) for x in closes if x is not None and math.isfinite(float(x)) and float(x) > 0]
        if len(clean_closes) < 10:
            return {
                "reference_volatility_annual": None,
                "volatility_status": "insufficient_data",
                "observations_count": len(clean_closes),
                "variance_clock_version": VARIANCE_CLOCK_VERSION,
                "spec": spec,
            }

        # Select up to last 40 daily closes
        recent = clean_closes[-40:]
        log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
        if len(log_returns) < 9:
            return {
                "reference_volatility_annual": None,
                "volatility_status": "insufficient_returns",
                "observations_count": len(clean_closes),
                "variance_clock_version": VARIANCE_CLOCK_VERSION,
                "spec": spec,
            }

        mean_r = sum(log_returns) / len(log_returns)
        var_r = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
        daily_std = math.sqrt(var_r)

        annual_basis_days = float(spec["annualization_basis_days"])
        annual_vol = daily_std * math.sqrt(annual_basis_days)

        if not math.isfinite(annual_vol) or annual_vol <= 0:
            return {
                "reference_volatility_annual": None,
                "volatility_status": "invalid_value",
                "observations_count": len(clean_closes),
                "variance_clock_version": VARIANCE_CLOCK_VERSION,
                "spec": spec,
            }

        return {
            "reference_volatility_annual": round(annual_vol, 6),
            "volatility_status": "valid",
            "observations_count": len(clean_closes),
            "returns_count": len(log_returns),
            "daily_std": round(daily_std, 6),
            "annualization_basis_days": annual_basis_days,
            "variance_clock_version": VARIANCE_CLOCK_VERSION,
            "spec": spec,
        }
    except Exception as exc:
        return {
            "reference_volatility_annual": None,
            "volatility_status": f"error:{type(exc).__name__}",
            "observations_count": 0,
            "variance_clock_version": VARIANCE_CLOCK_VERSION,
            "spec": spec,
        }


def compute_horizon_sigma(
    annual_vol: float | None,
    horizon_trading_minutes: float,
    instrument: str,
) -> Dict[str, Any]:
    """Computes sigma_h_return using the instrument's variance clock."""
    spec = get_variance_clock_spec(instrument)
    if annual_vol is None or not math.isfinite(float(annual_vol)) or float(annual_vol) <= 0:
        return {
            "sigma_h_return": None,
            "status": "missing_annual_volatility",
            "variance_clock_version": VARIANCE_CLOCK_VERSION,
            "horizon_trading_minutes": horizon_trading_minutes,
            "variance_minutes_per_year": spec["variance_minutes_per_year"],
        }

    ann_vol = float(annual_vol)
    var_mins_per_yr = float(spec["variance_minutes_per_year"])
    horizon_mins = float(horizon_trading_minutes)

    if var_mins_per_yr <= 0 or horizon_mins <= 0:
        return {
            "sigma_h_return": None,
            "status": "invalid_minutes",
            "variance_clock_version": VARIANCE_CLOCK_VERSION,
            "horizon_trading_minutes": horizon_mins,
            "variance_minutes_per_year": var_mins_per_yr,
        }

    sigma_h = ann_vol * math.sqrt(horizon_mins / var_mins_per_yr)
    return {
        "sigma_h_return": round(sigma_h, 8),
        "status": "valid",
        "variance_clock_version": VARIANCE_CLOCK_VERSION,
        "horizon_trading_minutes": horizon_mins,
        "variance_minutes_per_year": var_mins_per_yr,
        "spec": spec,
    }
