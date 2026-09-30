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
CONDITIONAL_POLICIES = {"SCALE_OUT_ON_SPIKE", "TIME_STOP"}


def _conditional_outcomes(inputs, params, policy, captured, seed):
    """Shared option-driver paths; explicit piecewise-linear event assumption."""
    from .ai_policy_base import _centered_skew_noise
    from .execution_simulator import ExecutionSpec, replay_execution_path
    steps, count = 160, 600
    rng = np.random.default_rng(seed)
    shape = np.exp(np.clip(inputs.term_slope, -.8, .8) *
                   ((np.arange(steps) + .5) / steps - .5))
    variance = inputs.sigma_R ** 2 * shape ** 2 / np.sum(shape ** 2)
    noise = _centered_skew_noise(rng.standard_normal((count, steps)), inputs.skew_R)
    paths = np.column_stack((np.full(count, inputs.r0),
        inputs.r0 + np.cumsum(inputs.drift_R / steps + noise * np.sqrt(variance), axis=1)))
    base = ExecutionSpec.from_values(current_r=inputs.r0, max_r=inputs.max_r,
        take_r=inputs.T, rungs=inputs.rungs, rung_fraction_original=inputs.rung_fraction,
        be_after_r=inputs.be_after, stop_r=inputs.stop_r)
    if policy == "TIME_STOP":
        fraction = (float(params["deadline_ts"]) - captured) / (60 * inputs.horizon_minutes)
        variant = replace(base, time_stop_fraction=fraction)
    else:
        variant = replace(base, spike_r=float(params["target_r"]),
                          spike_fraction=float(params["close_fraction"]))
    return (np.array([replay_execution_path(path, base).outcome_r for path in paths]),
            np.array([replay_execution_path(path, variant).outcome_r for path in paths]))


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
    if policy not in STOP_POLICIES | TAKE_POLICIES | CONDITIONAL_POLICIES:
        # A strategy time limit or a distinct, non-duplicating scale-out
        # trigger is not present in the current policy contract.
        return _blocked("CONDITIONAL_POLICY_HAS_NO_QUANTIFIED_EXECUTION_CONTRACT")
    if action.get("status") != "READY_FOR_MANUAL_CONFIRMATION":
        return _blocked(str(action.get("reason") or "ACTION_PARAMETERS_UNAVAILABLE"))
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
    if (policy == "TRAIL_GAMMA_FLIP" or params.get("anchor") == "ZERO_GAMMA") and (
        data.get("chain_status") not in {"live", "ok"}
        or _number(data.get("chain_age_sec")) is None
        or float(data["chain_age_sec"]) > 120
    ):
        return _blocked("GEX_CONTEXT_NOT_A_VERIFIED_EXECUTION_ANCHOR")
    target = _number(params.get("stop_price" if policy in STOP_POLICIES else "take_price"))
    captured = _number(snapshot.get("captured_ts"))
    if policy in CONDITIONAL_POLICIES:
        if policy == "TIME_STOP":
            deadline = _number(params.get("deadline_ts"))
            if captured is None or deadline is None or not captured < deadline <= captured + inputs.horizon_minutes * 60:
                return _blocked("INVALID_TIME_STOP_DEADLINE")
        else:
            trigger = _number(params.get("trigger_price"))
            fraction = _number(params.get("close_fraction"))
            if trigger is None or fraction is None or not 0 < fraction < 1:
                return _blocked("INVALID_SPIKE_PARAMETERS")
            target_r = sign * (trigger - entry) / risk
            if not max(inputs.r0, inputs.max_r) < target_r < inputs.T:
                return _blocked("SPIKE_TRIGGER_ALREADY_CROSSED_OR_OUTSIDE_TAKE")
            if any(abs(target_r - rung) < .05 for rung in inputs.rungs):
                return _blocked("SPIKE_DUPLICATES_STRATEGY_RUNG")
            params = {**params, "target_r": target_r}
        target_r = None if policy == "TIME_STOP" else target_r
        alternate = inputs
    elif target is None:
        return _blocked("EXACT_TARGET_PRICE_UNAVAILABLE")
    else:
        target_r = sign * (target - entry) / risk
    if policy in STOP_POLICIES:
        if not inputs.stop_r + 1e-8 < target_r < inputs.r0 - 1e-8:
            return _blocked("STOP_MUST_TIGHTEN_BELOW_CURRENT_PRICE")
        alternate = replace(inputs, stop_r=target_r)
    elif policy in TAKE_POLICIES:
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
    seed_lower_bounds: list[float] = []
    for seed in (0xA17E, 0xB17E):
        if policy in CONDITIONAL_POLICIES:
            base_out, variant_out = _conditional_outcomes(inputs, params, policy, captured, seed)
            seed_delta = variant_out - base_out
            seed_lower_bounds.append(float(np.mean(seed_delta) - 1.96 *
                np.std(seed_delta, ddof=1) / math.sqrt(seed_delta.size)))
            deltas.extend(seed_delta.tolist())
            means.append(float(np.mean(variant_out)))
            cvars.append(_cvar(variant_out))
            continue
        base = simulate_option_paths(inputs, n_paths=1200, n_steps=160, seed=seed,
                                     paired_stable_stream=True)
        variant = simulate_option_paths(alternate, n_paths=1200, n_steps=160, seed=seed,
                                        paired_stable_stream=True)
        if base.strategy_outcome is None or variant.strategy_outcome is None:
            return _blocked("EXECUTION_OUTCOMES_UNAVAILABLE")
        base_out = np.asarray(base.strategy_outcome, dtype=float)
        variant_out = np.asarray(variant.strategy_outcome, dtype=float)
        if not np.isfinite(base_out).all() or not np.isfinite(variant_out).all():
            return _blocked("NONFINITE_EXECUTION_OUTCOMES")
        seed_delta = variant_out - base_out
        seed_lower_bounds.append(float(np.mean(seed_delta) - 1.96 *
            np.std(seed_delta, ddof=1) / math.sqrt(seed_delta.size)))
        deltas.extend(seed_delta.tolist())
        means.append(float(np.mean(variant_out)))
        cvars.append(_cvar(variant_out))
    paired = np.asarray(deltas)
    gain = float(np.mean(paired))
    standard_error = float(np.std(paired, ddof=1) / math.sqrt(paired.size))
    lower = min(gain - 1.96 * standard_error, *seed_lower_bounds)
    costs = (manager.get("execution_cost_model") or
             rule.get("execution_cost_model") or {})
    # The broker's actual commission/slippage is unknown in many positions.
    # Compare gross outcomes with the gross strategy floor instead of silently
    # treating the 0.01R reporting fallback as a measured broker cost.
    variant_cvar = min(cvars)
    band = _number(rule.get("indifference_band_r"))
    material = max(0.0, band if band is not None else .03)
    evidence = {
        "target_r": round(target_r, 5) if target_r is not None else None, "paths": len(deltas),
        "expected_delta_vs_hold_r": round(gain, 5),
        "paired_delta_ci95_lower_r": round(lower, 5),
        "expected_variant_gross_r": round(min(means), 5),
        "worst_seed_cvar10_gross_r": round(variant_cvar, 5),
        "hard_gross_floor_r": floor, "materiality_band_r": material,
        "broker_execution_cost_measured": costs.get("assumed") is False,
        "method": ("two_seed_piecewise_linear_paired_option_paths" if policy in CONDITIONAL_POLICIES
                   else "two_seed_stable_path_id_paired_counterfactual_execution_paths"),
        "statistically_validated_advantage": False,
    }
    if variant_cvar < floor - 1e-8:
        return _blocked("VARIANT_CVAR_BELOW_HARD_FLOOR", **evidence)
    if lower <= material:
        return _blocked("NO_MATERIAL_ROBUST_EXPECTED_GAIN", **evidence)
    return {"status": "eligible", "reason": "ROBUST_EXPECTED_GAIN_AND_CVAR_PASS",
            "production_authority": True, "manual_confirmation_required": True,
            "automatic_execution_allowed": False, **evidence}
