"""Policy manager v15: derivative-driven scenario ensemble with an OOS gate.

The ensemble is integrated as robustness and candidate attribution.  It cannot
change the production action until a separately reviewed out-of-sample
calibration promotes it; sample count alone is explicitly insufficient.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import ai_policy_v14 as _impl
from .derived_scenario_ensemble import (
    calibrated_switch_thresholds,
    execution_cost_sensitivity,
    evaluate_derived_scenarios,
)


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_ANALYZE = _impl.analyze_policies


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _metric_value(state: dict, name: str) -> float | None:
    return _number(((state.get("metrics") or {}).get(name) or {}).get("value"))


def _comparison_row(state: dict, source: dict | None = None) -> dict:
    source = source or {}
    return {
        "p_take": _number(source.get("p_take")) if source else _metric_value(state, "p_take"),
        "p_stop": _number(source.get("p_stop")) if source else _metric_value(state, "p_stop"),
        "p_no_touch": (
            _number(source.get("p_no_touch")) if source else _metric_value(state, "p_no_touch")),
        "barrier_ev_r": (
            _number(source.get("barrier_ev")) if source else _metric_value(state, "barrier_ev")),
        "q10_r": _number(source.get("q10")) if source else _metric_value(state, "q10"),
        "q50_r": _number(source.get("q50")) if source else _metric_value(state, "q50"),
        "q90_r": _number(source.get("q90")) if source else _metric_value(state, "q90"),
        "width_r": _number(source.get("width")) if source else _metric_value(state, "width"),
        "tail_log_ratio": (
            _number(source.get("tail_log_ratio")) if source
            else _metric_value(state, "tail_log_ratio")),
        "h_take": _number(source.get("h_take")) if source else _metric_value(state, "h_take"),
        "h_stop": _number(source.get("h_stop")) if source else _metric_value(state, "h_stop"),
        "hazard_log_ratio": (
            _number(source.get("hazard_log_ratio")) if source
            else _metric_value(state, "hazard_log_ratio")),
        "gex_force": _number(source.get("gex_force")) if source else _metric_value(state, "gex_force"),
        "gex_stiffness": (
            _number(source.get("gex_stiffness")) if source
            else _metric_value(state, "gex_stiffness")),
    }


def state_change_attribution(state: dict, previous_evidence: dict | None,
                             old_policy: str, candidate_policy: str) -> dict:
    comparison = state.get("comparison") or {}
    previous_state = (previous_evidence or {}).get("option_derivative_state") or {}
    rows = {
        "ENTRY": _comparison_row(state, comparison.get("entry") or {}),
        "TRADE_LIFE_AVG": _comparison_row(
            state, comparison.get("trade_life_average") or {}),
        "PREVIOUS_AI_REVIEW": (
            _comparison_row(previous_state) if previous_state.get("available") else None),
        "NOW": _comparison_row(state),
    }
    reference_name = "PREVIOUS_AI_REVIEW" if rows["PREVIOUS_AI_REVIEW"] else "ENTRY"
    reference = rows[reference_name] or {}
    now = rows["NOW"]
    favorable_sign = {
        "p_take": 1, "p_stop": -1, "barrier_ev_r": 1, "q50_r": 1,
        "tail_log_ratio": 1, "h_take": 1, "h_stop": -1,
        "hazard_log_ratio": 1,
    }
    improved, deteriorated = [], []
    for metric, sign in favorable_sign.items():
        current, prior = _number(now.get(metric)), _number(reference.get(metric))
        if current is None or prior is None:
            continue
        delta = current - prior
        if abs(delta) <= 1e-9:
            continue
        item = {"metric": metric, "delta": round(delta, 7), "reference": reference_name}
        (improved if delta * sign > 0 else deteriorated).append(item)

    ignored = []
    for name, metric in (state.get("metrics") or {}).items():
        if metric.get("available") and float(metric.get("confidence") or 0.0) < 0.30:
            ignored.append({
                "metric": name, "confidence": metric.get("confidence"),
                "reason": "below the existing 0.30 derived-history authority threshold",
            })
        elif not metric.get("available"):
            ignored.append({
                "metric": name, "confidence": 0.0,
                "reason": metric.get("reason") or "derivative unavailable",
            })
    changed = candidate_policy != old_policy
    return {
        "snapshots": rows,
        "reference_used": reference_name,
        "what_improved": improved,
        "what_deteriorated": deteriorated,
        "what_changed_candidate_policy": (
            [f"shadow ensemble changed {old_policy} -> {candidate_policy}"] if changed else []),
        "what_changed_production_policy": [],
        "what_did_not_influence_low_confidence": ignored[:12],
        "explicit_policy_effect": (
            "derived state changed only the shadow candidate; production action is unchanged"
            if changed else
            "derived state did not change either the shadow candidate or production action"
        ),
    }


def _compact_option_state(state: dict) -> dict:
    """Retain AI-relevant derivatives without duplicating the full API payload."""
    names = (
        "p_take", "p_stop", "p_no_touch", "barrier_ev", "bop",
        "q10", "q50", "q90", "width", "tail_log_ratio", "skew",
        "term_slope", "iv", "rv", "vrp", "h_take", "h_stop",
        "hazard_log_ratio", "gex_force", "gex_stiffness",
        "distance_to_zero_gamma",
    )
    metrics = {}
    for name in names:
        row = (state.get("metrics") or {}).get(name)
        if not isinstance(row, dict):
            continue
        metrics[name] = {
            key: row.get(key) for key in (
                "value", "slope", "acceleration", "noise", "sample_count",
                "normalization_noise", "numerical_effect_floor",
                "normalization_horizon_minutes", "time_span_minutes",
                "confidence", "source_quality", "available", "units",
                "value_units", "slope_units", "acceleration_units",
            )
        }
    return {
        "available": bool(state.get("available")),
        "version": state.get("version"),
        "family": "option_distribution", "independent_vote": False,
        "authority": "shadow_context", "shadow_mode": True,
        "policy_influence": "none", "sample_count": state.get("sample_count"),
        "source_quality": deepcopy(state.get("source_quality") or {}),
        "option_state_score": state.get("option_state_score"),
        "option_state_confidence": state.get("option_state_confidence"),
        "option_state_attribution": deepcopy(state.get("option_state_attribution") or {}),
        "option_state_aggregation": state.get("option_state_aggregation"),
        "option_state_redundancy_contract": deepcopy(
            state.get("option_state_redundancy_contract") or {}),
        "named_derivatives": deepcopy(state.get("named_derivatives") or {}),
        "metrics": metrics,
        "first_touch_hazard": deepcopy(state.get("first_touch_hazard") or {}),
        "gex_geometry": {
            key: (state.get("gex_geometry") or {}).get(key)
            for key in (
                "field_score", "force_score", "stiffness_score",
                "distance_to_zero_gamma", "distance_to_call_wall_r",
                "distance_to_put_wall_r", "quality",
            )
        },
    }


def _record_shadow(engine, tick: dict, result: dict, ensemble: dict,
                   cost_sensitivity: dict | None = None) -> dict:
    journal = getattr(engine, "journal", None)
    report = journal.policy_shadow_report() if journal and hasattr(
        journal, "policy_shadow_report") else {"promotion_allowed": False}
    trade = tick.get("trade") or {}
    if not journal or not trade or not hasattr(journal, "record_policy_shadow"):
        return report
    old = ensemble["old_policy"]
    candidate = ensemble["candidate_policy"]
    old_row = (ensemble.get("policies") or {}).get(old) or {}
    candidate_row = (ensemble.get("policies") or {}).get(candidate) or {}
    base_policies = result.get("policies") or {}
    old_cost = _number((base_policies.get(old) or {}).get("execution_cost_r")) or 0.0
    candidate_cost = _number((base_policies.get(candidate) or {}).get("execution_cost_r")) or 0.0
    quality = _number((((tick.get("option_derivative_state") or {}).get(
        "source_quality") or {}).get("weight")))
    derivative_state = tick.get("option_derivative_state") or {}
    scenario_weights = {
        row.get("name"): row.get("weight")
        for row in ensemble.get("scenarios") or [] if row.get("name")
    }
    material_scenarios = [
        {
            "name": row.get("name"), "weight": row.get("weight"),
            "driver_confidence": row.get("driver_confidence"),
            "source_quality": row.get("source_quality"),
        }
        for row in ensemble.get("scenarios") or []
        if row.get("material") and row.get("name") != "BASE"
    ]
    selected_derivatives = {
        name: value for name, value in (
            derivative_state.get("named_derivatives") or {}).items()
        if value is not None
    }
    journal.record_policy_shadow(
        int(trade["id"]), old_policy=old, candidate_policy=candidate,
        reason=(
            "candidate differs under derivative-weighted stress ensemble"
            if old != candidate else "candidate agrees with production policy"),
        review_r=float((result.get("inputs") or {}).get("r0") or 0.0),
        expected_delta_r=(
            (_number(candidate_row.get("expected_net_r")) or 0.0)
            - (_number(old_row.get("expected_net_r")) or 0.0)),
        cvar_delta_r=(
            (_number(candidate_row.get("cvar10_net_r")) or 0.0)
            - (_number(old_row.get("cvar10_net_r")) or 0.0)),
        execution_cost_delta_r=candidate_cost - old_cost,
        source_quality=quality,
        option_state_confidence=_number(
            derivative_state.get("option_state_confidence")),
        scenario_weights=scenario_weights,
        material_scenarios=material_scenarios,
        derivative_state=selected_derivatives,
        execution_cost_sensitivity=cost_sensitivity or {},
        instrument=str(trade.get("instrument") or "") or None,
        market_regime=str(((tick.get("regime") or {}).get("phase")
                           or (tick.get("regime") or {}).get("regime") or "")) or None,
    )
    return journal.policy_shadow_report()


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    old_policy = str((result.get("recommendation") or {}).get("policy") or "HOLD")
    inputs = extract_policy_inputs(tick)
    costs = result.get("execution_cost_model") or execution_cost_model(tick, trade)
    risk = result.get("risk_constraint") or risk_constraint(inputs, tick, trade)
    cost_token = _COST_CTX.set(costs)
    risk_token = _RISK_CTX.set(risk)
    try:
        ensemble = evaluate_derived_scenarios(
            inputs=inputs, tick=tick, old_policy=old_policy,
            run_once=_run_once, raw_policy_choice=_raw_policy_choice,
            floor_for_r=_floor_for_r, policy_fractions=POLICY_FRACTIONS,
            source_stability=(result.get("authority_stability")
                              or (result.get("gate") or {}).get("authority_stability")),
        )
    finally:
        _RISK_CTX.reset(risk_token)
        _COST_CTX.reset(cost_token)

    candidate = ensemble["candidate_policy"]
    thresholds = calibrated_switch_thresholds(ensemble, POLICY_FRACTIONS)
    cost_sensitivity = execution_cost_sensitivity(
        ensemble, POLICY_FRACTIONS, costs)
    # Per-scenario policy matrices are an internal calibration workspace.  The
    # aggregate policy table plus winner/weight/floor attribution is sufficient
    # for the persisted snapshot and avoids duplicating 35 metric rows.
    for scenario in ensemble.get("scenarios") or []:
        scenario.pop("policies", None)
    attribution = state_change_attribution(
        tick.get("option_derivative_state") or {}, previous_evidence,
        old_policy, candidate)
    validation = _record_shadow(
        engine, tick, result, ensemble, cost_sensitivity)
    ensemble["validation_gate"] = validation
    ensemble["promotion_allowed"] = bool(validation.get("promotion_allowed", False))

    # Production recommendation intentionally remains the v14 result.  A later
    # reviewed promotion must be an explicit code/config change, never an LLM act.
    result["derived_scenario_ensemble"] = ensemble
    result["state_change_attribution"] = attribution
    result["derivative_switch_thresholds"] = thresholds
    result["execution_cost_sensitivity"] = cost_sensitivity
    result["shadow_policy_contract"] = {
        "old_policy": old_policy,
        "new_candidate_policy": candidate,
        "reason_for_difference": (
            "derivative-weighted scenario robustness" if candidate != old_policy
            else "scenario ensemble agrees with the production policy"),
        "future_realized_outcome": None,
        "action_changed": False,
        "promotion_allowed": False,
        "explicit_effect": attribution["explicit_policy_effect"],
    }
    evidence = result.setdefault("evidence", {})
    evidence["derived_scenario_ensemble"] = {
        "version": ensemble.get("version"),
        "family": "option_distribution",
        "independent_vote": False,
        "authority": "shadow_robustness",
        "candidate_policy": candidate,
        "old_policy": old_policy,
        "candidate_differs": candidate != old_policy,
        "promotion_allowed": False,
        "drivers": deepcopy(ensemble.get("drivers") or {}),
    }
    context = evidence.setdefault("context_observations", [])
    if not any(row.get("metric") == "derived_scenario_ensemble"
               for row in context if isinstance(row, dict)):
        context.append({
            "metric": "derived_scenario_ensemble",
            "family": "option_distribution",
            "independent_vote": False,
            "authority": "shadow_robustness",
            "context_only": True,
            "meaning": attribution["explicit_policy_effect"],
        })
    compact_state = _compact_option_state(tick.get("option_derivative_state") or {})
    result["option_derivative_state"] = compact_state
    evidence["option_derivative_state"] = {
        "available": compact_state.get("available"),
        "family": "option_distribution", "independent_vote": False,
        "authority": "shadow_context", "policy_influence": "none",
        "sample_count": compact_state.get("sample_count"),
        "option_state_score": compact_state.get("option_state_score"),
        "option_state_confidence": compact_state.get("option_state_confidence"),
        "named_derivatives": compact_state.get("named_derivatives"),
    }
    evidence["interaction_state"] = {
        "family": "option_distribution", "independent_vote": False,
        "authority": "shadow_context",
        "available_count": (result.get("interaction_state") or {}).get("available_count"),
        "max_risk_score": (result.get("interaction_state") or {}).get("max_risk_score"),
    }
    result["version"] = "quant-policy-v15-derived-scenario-shadow-validation"
    return result


globals()["analyze_policies"] = analyze_policies
