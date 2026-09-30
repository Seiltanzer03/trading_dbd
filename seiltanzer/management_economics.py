"""Affine translation of remaining-position forecasts to the original trade.

This view does not alter policy selection. All candidates share the same realised
result and positive remaining fraction, so ranking and CVaR eligibility are
unchanged when the corresponding floor is translated too. Historical fill costs
are not available in the ledger and are never invented as zero net costs.
"""
from __future__ import annotations

import math


def _number(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def intervention_basis(snapshot: dict, policy: str) -> dict:
    manager = snapshot.get("policy_manager") or {}
    gate = manager.get("gate") or {}
    evidence = (gate.get("degraded_authority_overlay") or {}).get("evidence") or {}
    policies = manager.get("policies") or {}
    candidate, hold = policies.get(policy) or {}, policies.get("HOLD") or {}
    def delta(key):
        a, b = _number(candidate.get(key)), _number(hold.get(key))
        return None if a is None or b is None else round(a - b, 6)
    return {
        "current_r": _number((snapshot.get("trade_geometry") or {}).get("current_r", (manager.get("inputs") or {}).get("r0"))),
        "raw_policy": (manager.get("recommendation") or {}).get("raw_optimizer_policy") or gate.get("raw_policy"),
        "expected_delta_r": delta("expected_final_r"),
        "cvar_gain_r": delta("cvar10_r"),
        "adverse_families": evidence.get("adverse_families"),
        "live_families": evidence.get("live_adverse_families"),
    }


def repeat_intervention_gate(snapshot: dict, decision: dict) -> dict | None:
    """Do not approximate EXIT by repeating an unchanged partial-cut thesis.

    Reuse the established 0.15R adverse move and Expected indifference band.
    All current evidence/CVaR/stress qualification still precedes this check.
    Timestamps, shrinking volume and a new review ID do not create new evidence.
    """
    previous = decision.get("previous_executed_reduction") or {}
    policy = decision.get("policy")
    if not previous or policy not in {"CLOSE_10", "CLOSE_25", "CLOSE_50"}:
        return None
    basis = decision.get("economic_review_basis") or intervention_basis(snapshot, policy)
    old = previous.get("economic_review_basis") or {}
    current_r = _number(basis.get("current_r"))
    previous_r = _number(old.get("current_r"))
    if previous_r is None:
        previous_r = _number(previous.get("execution_r"))
    reasons = []
    if current_r is not None and previous_r is not None and current_r <= previous_r - .15 + 1e-12:
        reasons.append("adverse_move_0_15_r")
    band = _number(((snapshot.get("policy_manager") or {}).get("selection_rule") or {}).get("indifference_band_r"))
    new_delta, old_delta = _number(basis.get("expected_delta_r")), _number(old.get("expected_delta_r"))
    if band is not None and band > 0 and new_delta is not None and old_delta is not None and new_delta - old_delta >= band - 1e-12:
        reasons.append("expected_benefit_increased_by_indifference_band")
    new_cvar, old_cvar = _number(basis.get("cvar_gain_r")), _number(old.get("cvar_gain_r"))
    if new_cvar is not None and old_cvar is not None and new_cvar - old_cvar >= .15 - 1e-12:
        reasons.append("tail_risk_benefit_increased_0_15_r")
    # An absent legacy baseline cannot prove that a family is new.
    for key in ("adverse_families", "live_families"):
        if isinstance(old.get(key), list) and isinstance(basis.get(key), list) and set(basis[key]) - set(old[key]):
            reasons.append("new_" + key)
    rank = {"CLOSE_10": 1, "CLOSE_25": 2, "CLOSE_50": 3, "EXIT": 4}
    if rank.get(policy, 0) > rank.get(previous.get("policy"), 0):
        reasons.append("stronger_current_qualified_policy")
    if old.get("raw_policy") == "HOLD" and basis.get("raw_policy") in rank:
        reasons.append("base_optimizer_now_selects_reduction")
    return {
        "contract_version": "repeat-intervention-gate-v1",
        "status": "confirmed_material_change" if reasons else "deferred_no_material_change",
        "allowed": bool(reasons), "candidate_policy": policy,
        "previous_executed_decision_id": previous.get("decision_id"),
        "reasons": reasons, "current_r": current_r, "previous_r": previous_r,
        "adverse_move_threshold_r": .15, "expected_benefit_step_r": band,
    }


def attach_position_economics(snapshot: dict) -> None:
    manager = snapshot.get("policy_manager") or {}
    position = snapshot.get("position_state") or {}
    remaining = _number(position.get("remaining_position_fraction"))
    if remaining is None or not 0 <= remaining <= 1:
        manager.pop("position_economics", None)
        return
    realised = _number(position.get("realized_r_weighted"))
    hold = (manager.get("policies") or {}).get("HOLD") or {}
    rows = {}
    for name, metrics in (manager.get("policies") or {}).items():
        if not isinstance(metrics, dict):
            continue
        row = {}
        for source, target in (
            ("expected_final_r", "expected_total_r"),
            ("median_final_r", "median_total_r"),
            ("cvar10_r", "cvar10_total_r"),
        ):
            value = _number(metrics.get(source))
            row[target] = (round(realised + remaining * value, 6)
                           if value is not None and realised is not None else None)
        for source, target in (
            ("expected_final_r", "expected_delta_total_r"),
            ("cvar10_r", "cvar_gain_total_r"),
        ):
            value, baseline = _number(metrics.get(source)), _number(hold.get(source))
            row[target] = (round(remaining * (value - baseline), 6)
                           if value is not None and baseline is not None else None)
        rows[name] = row
        metrics["expected_future_r_on_remaining"] = _number(metrics.get("expected_final_r"))
        metrics["expected_total_trade_r"] = row["expected_total_r"]
    floor = _number((manager.get("selection_rule") or {}).get("cvar_floor_r"))
    manager["position_economics"] = {
        "contract_version": "remaining-position-economics-v1",
        "remaining_fraction": remaining,
        "realized_r_weighted": realised,
        "state_version": position.get("state_version"),
        "basis": "initial_position_and_original_risk",
        "future_metrics_basis": "per_unit_of_current_remaining_position",
        "total_result_semantics": "realized_gross_plus_future_net_before_historical_fill_costs",
        "historical_fill_costs_status": position.get("realized_costs_status", "UNAVAILABLE"),
        "realized_price_basis": position.get("realized_price_basis", "UNAVAILABLE"),
        "total_cvar_floor_r": (round(realised + remaining * floor, 6)
                               if realised is not None and floor is not None else None),
        "policies": rows,
        "selection_changed": False,
        "statistically_validated_advantage": False,
    }
    snapshot["policy_manager"] = manager
