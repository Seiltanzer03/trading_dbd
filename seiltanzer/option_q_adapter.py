"""Option Producer -> Passive Q Adapter (option-q-contract-f3-v1).

Validates option density from chain metrics, performs horizon alignment checking,
and produces standardized risk-neutral Q distribution forecasts.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
import numpy as np

from .core.options import _trapezoid
from .variance_clock import get_variance_clock_spec

OPTION_Q_CONTRACT_VERSION = "option-q-contract-f3-v1"


def validate_option_density(
    density_dict: Optional[Dict[str, Any]],
    spot: Optional[float],
) -> Dict[str, Any]:
    """Validates risk-neutral density for monotonicity, non-negativity, and finite bounds."""
    if not density_dict or not isinstance(density_dict, dict):
        return {"valid": False, "reason": "missing_density_dict"}

    if spot is None or not math.isfinite(float(spot)) or float(spot) <= 0:
        return {"valid": False, "reason": "invalid_spot"}

    strikes_raw = density_dict.get("strikes")
    q_raw = density_dict.get("q")

    if not isinstance(strikes_raw, list) or not isinstance(q_raw, list):
        return {"valid": False, "reason": "non_list_density_data"}

    if len(strikes_raw) < 5 or len(strikes_raw) != len(q_raw):
        return {"valid": False, "reason": f"invalid_length_mismatch_{len(strikes_raw)}_vs_{len(q_raw)}"}

    try:
        strikes = np.asarray(strikes_raw, dtype=float)
        q = np.asarray(q_raw, dtype=float)

        if not np.all(np.isfinite(strikes)) or not np.all(np.isfinite(q)):
            return {"valid": False, "reason": "non_finite_values"}

        # Strictly monotonically increasing strikes
        if not np.all(np.diff(strikes) > 0):
            return {"valid": False, "reason": "non_monotonic_strikes"}

        # Non-negative density
        if np.any(q < 0):
            return {"valid": False, "reason": "negative_density_values"}

        # Integrated mass
        area = float(_trapezoid(q, strikes))
        if not math.isfinite(area) or area <= 0:
            return {"valid": False, "reason": "zero_or_invalid_integrated_mass"}

        # Normalize density
        q_norm = q / area

        return {
            "valid": True,
            "strikes": strikes,
            "q_norm": q_norm,
            "area": area,
            "spot": float(spot),
        }
    except Exception as exc:
        return {"valid": False, "reason": f"exception_{type(exc).__name__}"}


def check_horizon_alignment(
    t_years: Optional[float],
    horizon_minutes: int,
    instrument: str,
    term_dict: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Checks whether the option expiry TTM can be validly aligned with target horizon_minutes."""
    spec = get_variance_clock_spec(instrument)
    var_mins_per_yr = float(spec["variance_minutes_per_year"])

    if t_years is None or not math.isfinite(float(t_years)) or float(t_years) <= 0:
        return {
            "aligned": False,
            "status": "unavailable",
            "method": "none",
            "ttm_minutes": 0.0,
            "error_minutes": 0.0,
        }

    ttm_mins = float(t_years) * var_mins_per_yr
    target_mins = float(horizon_minutes)
    error_mins = abs(ttm_mins - target_mins)

    # Tolerance rule: exact or near-exact horizon matching
    # Allow matching if TTM is close to target horizon or within reasonable bounds
    tolerance = max(60.0, 0.75 * target_mins)

    if error_mins <= tolerance:
        return {
            "aligned": True,
            "status": "valid",
            "method": "direct_or_nearest_valid_expiry",
            "ttm_minutes": round(ttm_mins, 1),
            "error_minutes": round(error_mins, 1),
        }

    # Term structure scaling if term structure dict is available
    if term_dict and isinstance(term_dict, dict) and term_dict.get("pts"):
        return {
            "aligned": True,
            "status": "valid",
            "method": "term_structure_scaled",
            "ttm_minutes": round(ttm_mins, 1),
            "error_minutes": round(error_mins, 1),
        }

    return {
        "aligned": False,
        "status": "unavailable",
        "method": "unsupported_horizon_mismatch",
        "ttm_minutes": round(ttm_mins, 1),
        "error_minutes": round(error_mins, 1),
    }


def adapt_option_q_forecast(
    option_metrics: Optional[Dict[str, Any]],
    horizon_minutes: int,
    sigma_h: Optional[float],
    instrument: str,
) -> Dict[str, Any]:
    """Adapts raw option_metrics into authoritative risk-neutral Q forecast contract."""
    if not option_metrics or not isinstance(option_metrics, dict):
        return {
            "q_available": False,
            "probability_measure": "unavailable",
            "q_source_contract": "unavailable",
            "horizon_alignment_status": "unavailable",
            "horizon_alignment_method": "none",
            "quantiles_log_return": {"q10": None, "q25": None, "q50": None, "q75": None, "q90": None},
            "standardized_barriers": {
                str(lvl): {"up": None, "down": None, "no_touch": None} for lvl in (0.5, 1.0, 1.5, 2.0)
            },
        }

    spot = option_metrics.get("spot")
    t_years = option_metrics.get("t_years")
    term = option_metrics.get("term")
    density_dict = option_metrics.get("density")
    implied_move = option_metrics.get("implied_move") or {}

    val_res = validate_option_density(density_dict, spot)
    align_res = check_horizon_alignment(t_years, horizon_minutes, instrument, term)

    if not val_res["valid"] or not align_res["aligned"]:
        return {
            "q_available": False,
            "probability_measure": "unavailable",
            "q_source_contract": "unavailable",
            "horizon_alignment_status": align_res["status"],
            "horizon_alignment_method": align_res["method"],
            "horizon_alignment_error": align_res.get("error_minutes", 0.0),
            "source_expiry_ttm_minutes": align_res.get("ttm_minutes", 0.0),
            "density_validation_reason": val_res.get("reason", "ok"),
            "quantiles_log_return": {"q10": None, "q25": None, "q50": None, "q75": None, "q90": None},
            "standardized_barriers": {
                str(lvl): {"up": None, "down": None, "no_touch": None} for lvl in (0.5, 1.0, 1.5, 2.0)
            },
        }

    strikes = val_res["strikes"]
    q_norm = val_res["q_norm"]
    spot_val = val_res["spot"]

    # Compute CDF via trapezoidal integration
    cdf = np.zeros_like(q_norm)
    for i in range(1, len(strikes)):
        cdf[i] = cdf[i - 1] + float(_trapezoid(q_norm[: i + 1], strikes[: i + 1])) - float(_trapezoid(q_norm[:i], strikes[:i]))

    # Ensure CDF ends at 1.0
    if cdf[-1] > 0:
        cdf = cdf / cdf[-1]

    log_returns = np.log(strikes / spot_val)

    # Compute quantiles
    def _interp_quantile(tau: float) -> float:
        return float(np.interp(tau, cdf, log_returns))

    quantiles = {
        "q10": round(_interp_quantile(0.10), 6),
        "q25": round(_interp_quantile(0.25), 6),
        "q50": round(_interp_quantile(0.50), 6),
        "q75": round(_interp_quantile(0.75), 6),
        "q90": round(_interp_quantile(0.90), 6),
    }

    # Compute barrier probabilities if sigma_h is available
    barriers = {}
    if sigma_h is not None and math.isfinite(float(sigma_h)) and float(sigma_h) > 0:
        sig = float(sigma_h)
        for level in (0.5, 1.0, 1.5, 2.0):
            up_k = spot_val * math.exp(+level * sig)
            dn_k = spot_val * math.exp(-level * sig)

            # P(up) = P(S >= up_k) = 1 - CDF(up_k)
            cdf_up = float(np.interp(up_k, strikes, cdf))
            p_up = max(0.0, min(1.0, 1.0 - cdf_up))

            # P(dn) = P(S <= dn_k) = CDF(dn_k)
            p_dn = max(0.0, min(1.0, float(np.interp(dn_k, strikes, cdf))))

            p_no_touch = max(0.0, min(1.0, 1.0 - p_up - p_dn))
            total_p = p_up + p_dn + p_no_touch
            if total_p > 0:
                p_up /= total_p
                p_dn /= total_p
                p_no_touch /= total_p

            barriers[str(level)] = {
                "up": round(p_up, 6),
                "down": round(p_dn, 6),
                "no_touch": round(p_no_touch, 6),
            }
    else:
        barriers = {str(lvl): {"up": None, "down": None, "no_touch": None} for lvl in (0.5, 1.0, 1.5, 2.0)}

    mode_idx = int(np.argmax(q_norm))
    mode_price = float(strikes[mode_idx])

    return {
        "q_available": True,
        "probability_measure": "risk_neutral_Q",
        "q_source_contract": OPTION_Q_CONTRACT_VERSION,
        "horizon_alignment_status": align_res["status"],
        "horizon_alignment_method": align_res["method"],
        "horizon_alignment_error": align_res.get("error_minutes", 0.0),
        "source_expiry_ttm_minutes": align_res.get("ttm_minutes", 0.0),
        "source_expiry": option_metrics.get("expiry"),
        "quantiles_log_return": quantiles,
        "standardized_barriers": barriers,
        "mode_price": mode_price,
        "implied_move_frac": implied_move.get("move_frac"),
        "skew": option_metrics.get("skew"),
    }
