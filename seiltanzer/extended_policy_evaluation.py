"""Counterfactual risk check for a proposed extended management action.

The LLM supplies a policy name. Prices come from frozen trade geometry; this
module independently replays the *same* stochastic drivers for HOLD and the
proposed stop/take change. It never creates a broker order.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

import numpy as np

from .ai_policy_base import PolicyInputs, simulate_option_paths


STOP_POLICIES = {"MOVE_TO_BE", "TRAIL_GAMMA_FLIP", "TIGHTEN_STOP"}
TAKE_POLICIES = {"EXTEND_TAKE", "REDUCE_TAKE"}


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _blocked(reason: str, **details: Any) -> dict[str, Any]:
    return {"status": "blocked", "reason": reason, "production_authority": False,
            "automatic_execution_allowed": False, **details}


def _cvar(values: np.ndarray) -> float:
    n = max(1, math.ceil(values.size * .1))
    return float(np.mean(np.partition(values, n - 1)[:n]))


def evaluate_extended_action(snapshot: dict, action: dict) -> dict[str, Any]:
    """Require source authority, hard CVaR and material paired-path benefit."""
    manager = snapshot.get("policy_manager") or {}
    policy = str(action.get("policy") or "")
    if action.get("status") != "READY_FOR_MANUAL_CONFIRMATION":
        return _blocked("ACTION_PARAMETERS_UNAVAILABLE")
    if policy not in STOP_POLICIES | TAKE_POLICIES:
        # A strategy time limit or a distinct, non-duplicating scale-out
        # trigger is not present in the current policy contract.
        return _blocked("CONDITIONAL_POLICY_HAS_NO_QUANTIFIED_EXECUTION_CONTRACT")
    price = (((manager.get("input_audit") or {}).get("rows") or {})
             .get("instrument_price") or {})
    source = str(price.get("source") or "")
    if (price.get("available") is not True
            or price.get("production_authority") is False
            or str(price.get("status") or "").lower() not in {"live", "ok"}
            or source.startswith(("Bybit ", "yfinance "))):
        return _blocked("AUTHORITATIVE_INSTRUMENT_PRICE_UNAVAILABLE")
    reliability = ((((manager.get("evidence") or {}).get("data_quality") or {})
                    .get("reliability") or {}).get("level") or "").lower()
    if reliability in {"низкая", "low"}:
        return _blocked("LOW_DATA_RELIABILITY_FOR_EXTENDED_OVERRIDE")
    rule = manager.get("selection_rule") or {}
    if "HOLD" not in (rule.get("eligible") or []):
        return _blocked("HOLD_OUTSIDE_HARD_CVAR_FEASIBLE_SET")
    floor = _number((manager.get("risk_constraint") or {}).get("gross_cvar_floor_r"))
    if floor is None:
        return _blocked("HARD_CVAR_FLOOR_UNAVAILABLE")
    data = manager.get("inputs") or {}
    if not data.get("option_available"):
        return _blocked("OPTION_PATH_DISTRIBUTION_UNAVAILABLE")
    try:
        inputs = PolicyInputs(**{**data, "rungs": tuple(data["rungs"])})
    except (TypeError, ValueError, KeyError):
        return _blocked("POLICY_INPUTS_INCOMPLETE")
    geometry = snapshot.get("trade_geometry") or {}
    entry = _number(geometry.get("entry"))
    stop = _number(geometry.get("original_stop"))
    current = _number(geometry.get("current"))
    if None in (entry, stop, current) or entry == stop:
        return _blocked("TRADE_GEOMETRY_UNAVAILABLE")
    sign = 1.0 if stop < entry else -1.0
    risk = abs(entry - stop)
    params = action.get("parameters") or {}
    if policy in {"TRAIL_GAMMA_FLIP", "TIGHTEN_STOP"} and params.get(
        "anchor") == "ZERO_GAMMA":
        return _blocked("GEX_CONTEXT_NOT_A_VERIFIED_EXECUTION_ANCHOR")
    target = _number(params.get("stop_price" if policy in STOP_POLICIES else "take_price"))
    if target is None:
        return _blocked("EXACT_TARGET_PRICE_UNAVAILABLE")
    target_r = sign * (target - entry) / risk
    if policy in STOP_POLICIES:
        if not inputs.stop_r + 1e-8 < target_r < inputs.r0 - 1e-8:
            return _blocked("STOP_MUST_TIGHTEN_BELOW_CURRENT_PRICE")
        alternate = replace(inputs, stop_r=target_r)
    else:
        if target_r <= inputs.r0 + 1e-8:
            return _blocked("TAKE_ALREADY_PASSED")
        if policy == "EXTEND_TAKE" and target_r <= inputs.T + 1e-8:
            return _blocked("TAKE_NOT_EXTENDED")
        if policy == "REDUCE_TAKE" and target_r >= inputs.T - 1e-8:
            return _blocked("TAKE_NOT_REDUCED")
        if policy == "REDUCE_TAKE" and target_r <= inputs.max_r + 1e-8:
            return _blocked("TAKE_ALREADY_CROSSED_DURING_TRADE")
        alternate = replace(inputs, T=target_r)

    deltas: list[float] = []
    cvars: list[float] = []
    means: list[float] = []
    for seed in (0xA17E, 0xB17E):
        base = simulate_option_paths(inputs, n_paths=1200, n_steps=160, seed=seed)
        variant = simulate_option_paths(alternate, n_paths=1200, n_steps=160, seed=seed)
        if base.strategy_outcome is None or variant.strategy_outcome is None:
            return _blocked("EXECUTION_OUTCOMES_UNAVAILABLE")
        base_out = np.asarray(base.strategy_outcome, dtype=float)
        variant_out = np.asarray(variant.strategy_outcome, dtype=float)
        if not np.isfinite(base_out).all() or not np.isfinite(variant_out).all():
            return _blocked("NONFINITE_EXECUTION_OUTCOMES")
        deltas.extend((variant_out - base_out).tolist())
        means.append(float(np.mean(variant_out)))
        cvars.append(_cvar(variant_out))
    paired = np.asarray(deltas)
    gain = float(np.mean(paired))
    standard_error = float(np.std(paired, ddof=1) / math.sqrt(paired.size))
    lower = gain - 1.96 * standard_error
    costs = (manager.get("execution_cost_model") or
             rule.get("execution_cost_model") or {})
    # The broker's actual commission/slippage is unknown in many positions.
    # Compare gross outcomes with the gross strategy floor instead of silently
    # treating the 0.01R reporting fallback as a measured broker cost.
    variant_cvar = min(cvars)
    band = _number(rule.get("indifference_band_r"))
    material = max(0.0, band if band is not None else .03)
    evidence = {
        "target_r": round(target_r, 5), "paths": len(deltas),
        "expected_delta_vs_hold_r": round(gain, 5),
        "paired_delta_ci95_lower_r": round(lower, 5),
        "expected_variant_gross_r": round(min(means), 5),
        "worst_seed_cvar10_gross_r": round(variant_cvar, 5),
        "hard_gross_floor_r": floor, "materiality_band_r": material,
        "broker_execution_cost_measured": costs.get("assumed") is False,
        "method": "two_seed_paired_counterfactual_execution_paths",
    }
    if variant_cvar < floor - 1e-8:
        return _blocked("VARIANT_CVAR_BELOW_HARD_FLOOR", **evidence)
    if lower <= material:
        return _blocked("NO_MATERIAL_ROBUST_EXPECTED_GAIN", **evidence)
    return {"status": "eligible", "reason": "ROBUST_EXPECTED_GAIN_AND_CVAR_PASS",
            "production_authority": True, "manual_confirmation_required": True,
            "automatic_execution_allowed": False, **evidence}
