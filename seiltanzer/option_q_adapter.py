"""Option Producer -> Passive Q Adapter (option-q-contract-f31-v1).

Phase F.3.1 Measurement Integrity Closure:
- Strict return-space proxy transformation (direct / inverse) preserving probability mass.
- Strict separation of Terminal Q (S_T at expiry) from First-Passage touch probabilities.
- Option-native horizon alignment vs fixed-horizon unavailable semantics.
- ACT/365 calendar expiry clock tracking.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
import numpy as np

from .core.options import _trapezoid
from .variance_clock import get_variance_clock_spec

OPTION_Q_CONTRACT_VERSION = "option-q-contract-f31-v1"
EXPIRY_CLOCK_VERSION = "act365-calendar-v1"


def validate_and_transform_proxy_density(
    density_dict: Optional[Dict[str, Any]],
    proxy_spot: Optional[float],
    instrument_spot: Optional[float],
    proxy_transform: str = "direct",
) -> Dict[str, Any]:
    """Validates proxy density, converts strikes to return space, applies direct/inverse transformation,

    and reconstructs transformed distribution support preserving total probability mass.
    """
    if not density_dict or not isinstance(density_dict, dict):
        return {"valid": False, "reason": "missing_density_dict"}

    if proxy_spot is None or not math.isfinite(float(proxy_spot)) or float(proxy_spot) <= 0:
        return {"valid": False, "reason": "invalid_proxy_spot"}

    if instrument_spot is None or not math.isfinite(float(instrument_spot)) or float(instrument_spot) <= 0:
        return {"valid": False, "reason": "invalid_instrument_spot"}

    strikes_raw = density_dict.get("strikes")
    q_raw = density_dict.get("q")

    if not isinstance(strikes_raw, list) or not isinstance(q_raw, list):
        return {"valid": False, "reason": "non_list_density_data"}

    if len(strikes_raw) < 5 or len(strikes_raw) != len(q_raw):
        return {"valid": False, "reason": f"invalid_length_mismatch_{len(strikes_raw)}_vs_{len(q_raw)}"}

    try:
        proxy_strikes = np.asarray(strikes_raw, dtype=float)
        q_src = np.asarray(q_raw, dtype=float)

        if not np.all(np.isfinite(proxy_strikes)) or not np.all(np.isfinite(q_src)):
            return {"valid": False, "reason": "non_finite_values"}

        if not np.all(np.diff(proxy_strikes) > 0):
            return {"valid": False, "reason": "non_monotonic_strikes"}

        if np.any(q_src < 0):
            return {"valid": False, "reason": "negative_density_values"}

        area = float(_trapezoid(q_src, proxy_strikes))
        if not math.isfinite(area) or area <= 0:
            return {"valid": False, "reason": "zero_or_invalid_integrated_mass"}

        q_norm = q_src / area

        # Compute log-returns relative to proxy spot
        r_proxy = np.log(proxy_strikes / float(proxy_spot))

        # Apply transformation: direct (r_inst = r_proxy) vs inverse (r_inst = -r_proxy)
        if str(proxy_transform).lower() == "inverse":
            r_inst = -r_proxy
        else:
            r_inst = r_proxy

        # Sort returns monotonically for instrument space
        sort_idx = np.argsort(r_inst)
        r_inst_sorted = r_inst[sort_idx]

        # Convert back to instrument strikes
        inst_spot_val = float(instrument_spot)
        inst_strikes = inst_spot_val * np.exp(r_inst_sorted)

        # Compute CDF on proxy log-returns
        cdf = np.zeros_like(q_norm)
        for i in range(1, len(proxy_strikes)):
            cdf[i] = cdf[i - 1] + float(_trapezoid(q_norm[: i + 1], proxy_strikes[: i + 1])) - float(_trapezoid(q_norm[:i], proxy_strikes[:i]))

        if cdf[-1] > 0:
            cdf = cdf / cdf[-1]

        # Sort CDF according to sorted instrument returns
        cdf_sorted = cdf[sort_idx]

        # Re-derive PDF on instrument strikes preserving total mass = 1.0
        q_inst = np.zeros_like(cdf_sorted)
        q_inst[1:] = np.diff(cdf_sorted) / np.diff(inst_strikes)
        q_inst[0] = q_inst[1]

        # Verify mass conservation
        inst_area = float(_trapezoid(q_inst, inst_strikes))
        if inst_area > 0:
            q_inst = q_inst / inst_area

        return {
            "valid": True,
            "proxy_strikes": proxy_strikes,
            "proxy_spot": float(proxy_spot),
            "instrument_spot": inst_spot_val,
            "instrument_strikes": inst_strikes,
            "r_instrument": r_inst_sorted,
            "cdf": cdf_sorted,
            "q_instrument": q_inst,
            "proxy_transform": str(proxy_transform),
        }
    except Exception as exc:
        return {"valid": False, "reason": f"exception_{type(exc).__name__}"}


def adapt_option_q_forecast(
    option_metrics: Optional[Dict[str, Any]],
    horizon_minutes: int,
    sigma_h: Optional[float],
    instrument: str,
    horizon_kind: str = "fixed_trading_time",
) -> Dict[str, Any]:
    """Adapts option metrics into risk-neutral Q terminal forecast contract for option-native or fixed horizons."""
    default_quantiles = {"q10": None, "q25": None, "q50": None, "q75": None, "q90": None}
    default_barriers = {
        str(lvl): {"q_terminal_above_upper": None, "q_terminal_below_lower": None, "q_terminal_inside": None}
        for lvl in (0.5, 1.0, 1.5, 2.0)
    }

    if not option_metrics or not isinstance(option_metrics, dict):
        return {
            "q_available": False,
            "probability_measure": "unavailable",
            "q_source_contract": "unavailable",
            "q_terminal_distribution_available": False,
            "q_first_touch_available": False,
            "horizon_kind": horizon_kind,
            "horizon_alignment_status": "unavailable",
            "horizon_alignment_method": "none",
            "quantiles_log_return": default_quantiles,
            "standardized_barriers": default_barriers,
            "q_evidence_tier": "unavailable",
        }

    spot = option_metrics.get("spot")
    proxy_spot = option_metrics.get("proxy_spot", spot)
    t_years = option_metrics.get("t_years")
    density_dict = option_metrics.get("density")
    implied_move = option_metrics.get("implied_move") or {}
    proxy_transform = option_metrics.get("proxy_transform", "direct")
    proxy_experimental = bool(option_metrics.get("experimental", False))

    val_res = validate_and_transform_proxy_density(
        density_dict, proxy_spot, spot, proxy_transform=proxy_transform
    )

    if not val_res["valid"]:
        return {
            "q_available": False,
            "probability_measure": "unavailable",
            "q_source_contract": "unavailable",
            "q_terminal_distribution_available": False,
            "q_first_touch_available": False,
            "horizon_kind": horizon_kind,
            "horizon_alignment_status": "unavailable",
            "horizon_alignment_method": "invalid_density",
            "quantiles_log_return": default_quantiles,
            "standardized_barriers": default_barriers,
            "q_evidence_tier": "unavailable",
        }

    # Time clock tracking (ACT/365 calendar expiry clock)
    t_yrs_val = float(t_years) if t_years and math.isfinite(float(t_years)) and float(t_years) > 0 else 0.0
    cal_ttm_sec = round(t_yrs_val * 365.0 * 86400.0, 2)
    cal_ttm_mins = round(cal_ttm_sec / 60.0, 2)

    # Alignment checking
    if horizon_kind == "option_native_expiry":
        aligned = True
        align_status = "native_expiry"
        align_method = "native_option_expiry"
    else:
        # Fixed horizons do NOT fabricate term scaling without explicit multi-expiry model
        aligned = False
        align_status = "unavailable"
        align_method = "no_fixed_horizon_density_scaling"

    if not aligned:
        return {
            "q_available": False,
            "probability_measure": "unavailable",
            "q_source_contract": "unavailable",
            "q_terminal_distribution_available": False,
            "q_first_touch_available": False,
            "horizon_kind": horizon_kind,
            "horizon_alignment_status": align_status,
            "horizon_alignment_method": align_method,
            "calendar_ttm_seconds": cal_ttm_sec,
            "calendar_ttm_minutes": cal_ttm_mins,
            "calendar_ttm_years_act365": t_yrs_val,
            "expiry_clock_version": EXPIRY_CLOCK_VERSION,
            "quantiles_log_return": default_quantiles,
            "standardized_barriers": default_barriers,
            "q_evidence_tier": "experimental_proxy" if proxy_experimental else "direct_or_strong_proxy",
        }

    inst_strikes = val_res["instrument_strikes"]
    r_inst = val_res["r_instrument"]
    cdf = val_res["cdf"]
    inst_spot = val_res["instrument_spot"]

    # Compute quantiles in instrument log-return space
    def _interp_quantile(tau: float) -> float:
        return float(np.interp(tau, cdf, r_inst))

    quantiles = {
        "q10": round(_interp_quantile(0.10), 6),
        "q25": round(_interp_quantile(0.25), 6),
        "q50": round(_interp_quantile(0.50), 6),
        "q75": round(_interp_quantile(0.75), 6),
        "q90": round(_interp_quantile(0.90), 6),
    }

    # Compute Terminal Q probabilities (S_T at expiry)
    barriers = {}
    if sigma_h is not None and math.isfinite(float(sigma_h)) and float(sigma_h) > 0:
        sig = float(sigma_h)
        for level in (0.5, 1.0, 1.5, 2.0):
            up_k = inst_spot * math.exp(+level * sig)
            dn_k = inst_spot * math.exp(-level * sig)

            # P_Q(S_T >= up_k)
            cdf_up = float(np.interp(up_k, inst_strikes, cdf))
            q_above = max(0.0, min(1.0, 1.0 - cdf_up))

            # P_Q(S_T <= dn_k)
            q_below = max(0.0, min(1.0, float(np.interp(dn_k, inst_strikes, cdf))))

            q_inside = max(0.0, min(1.0, 1.0 - q_above - q_below))
            tot = q_above + q_below + q_inside
            if tot > 0:
                q_above /= tot
                q_below /= tot
                q_inside /= tot

            barriers[str(level)] = {
                "q_terminal_above_upper": round(q_above, 6),
                "q_terminal_below_lower": round(q_below, 6),
                "q_terminal_inside": round(q_inside, 6),
            }
    else:
        barriers = default_barriers

    mode_idx = int(np.argmax(val_res["q_instrument"]))
    mode_price = float(inst_strikes[mode_idx])

    evidence_tier = "experimental_proxy" if proxy_experimental else "direct_or_strong_proxy"

    return {
        "q_available": True,
        "probability_measure": "risk_neutral_Q_terminal",
        "q_source_contract": OPTION_Q_CONTRACT_VERSION,
        "q_terminal_distribution_available": True,
        "q_first_touch_available": False,
        "horizon_kind": horizon_kind,
        "horizon_alignment_status": align_status,
        "horizon_alignment_method": align_method,
        "calendar_ttm_seconds": cal_ttm_sec,
        "calendar_ttm_minutes": cal_ttm_mins,
        "calendar_ttm_years_act365": t_yrs_val,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
        "source_expiry": option_metrics.get("expiry"),
        "proxy_symbol": option_metrics.get("proxy"),
        "proxy_transform": val_res["proxy_transform"],
        "proxy_experimental": proxy_experimental,
        "q_evidence_tier": evidence_tier,
        "quantiles_log_return": quantiles,
        "standardized_barriers": barriers,
        "mode_price": mode_price,
        "implied_move_frac": implied_move.get("move_frac"),
        "skew": option_metrics.get("skew"),
    }
