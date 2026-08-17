"""Policy manager v16: Phase E plus bounded active-edge policy weighting.

The established v14/v15 path, simulations, CVaR eligibility and hard-risk floors stay
unchanged. Active structured edge may only re-rank already hard-risk-eligible
management policies, with a strict 30% soft-decision cap and no added execution
authority.
"""
from __future__ import annotations

import time
from typing import Any

from . import ai_policy_v15 as _impl
from .active_edge_ai_integration import build_active_edge_context
from .config import SETUPS


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_ANALYZE = _impl.analyze_policies

EDGE_WEIGHT_MAX = 0.30
EDGE_HIGH_RISK_ONLY_MAX = 0.15
EDGE_STRICT_MAX = 0.25
EDGE_STRICT_BROAD_MAX = 0.30

_POLICY_FRACTIONS = {
    "HOLD": 0.0,
    "CLOSE_10": 0.10,
    "CLOSE_25": 0.25,
    "CLOSE_50": 0.50,
    "EXIT": 1.0,
}
_ACTIONS_RU = {
    "HOLD": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
    "CLOSE_10": "ЗАКРЫТЬ 10% ПОЗИЦИИ СЕЙЧАС",
    "CLOSE_25": "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС",
    "CLOSE_50": "ЗАКРЫТЬ 50% ПОЗИЦИИ СЕЙЧАС",
    "EXIT": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
}


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _trade_instrument(trade: dict[str, Any]) -> str:
    direct = str(trade.get("instrument") or "")
    if direct:
        return direct
    try:
        setup = SETUPS.get(int(trade.get("setup") or 0))
    except (TypeError, ValueError):
        setup = None
    return str(getattr(setup, "instrument", "") or "")


def _active_edge_signal(context: dict[str, Any]) -> dict[str, Any]:
    """Collapse matched candidate votes to dependency-aware family×horizon evidence."""
    grouped: dict[tuple[str, int], dict[str, int]] = {}
    for row in context.get("matched_groups") or []:
        if not isinstance(row, dict):
            continue
        family = str(row.get("target_family") or "OTHER")
        try:
            horizon = int(row.get("signal_horizon_minutes") or 0)
        except (TypeError, ValueError):
            horizon = 0
        key = (family, horizon)
        bucket = grouped.setdefault(key, {
            "supporting": 0, "opposing": 0,
            "strict_supporting": 0, "strict_opposing": 0,
        })
        bucket["supporting"] += max(0, int(row.get("supporting_n") or 0))
        bucket["opposing"] += max(0, int(row.get("opposing_n") or 0))
        bucket["strict_supporting"] += max(
            0, int(row.get("strict_supporting_n") or 0))
        bucket["strict_opposing"] += max(
            0, int(row.get("strict_opposing_n") or 0))

    rows: list[dict[str, Any]] = []
    for (family, horizon), bucket in grouped.items():
        total = bucket["supporting"] + bucket["opposing"]
        if total <= 0:
            continue
        ratio = (bucket["supporting"] - bucket["opposing"]) / total
        strict_total = bucket["strict_supporting"] + bucket["strict_opposing"]
        strict_ratio = (
            (bucket["strict_supporting"] - bucket["strict_opposing"]) / strict_total
            if strict_total > 0 else None
        )
        rows.append({
            "target_family": family,
            "signal_horizon_minutes": horizon,
            "vote_ratio": ratio,
            "strict_vote_ratio": strict_ratio,
            "matched_n": total,
            "strict_matched_n": strict_total,
        })

    if not rows:
        return {
            "available": False,
            "direction": "BALANCED",
            "signed_strength": 0.0,
            "applied_weight": 0.0,
            "weight_cap": EDGE_HIGH_RISK_ONLY_MAX,
            "independent_group_n": 0,
            "strict_group_n": 0,
            "groups": [],
            "reason": "NO_DIRECTIONAL_MATCHED_GROUPS",
        }

    signed_strength = sum(float(row["vote_ratio"]) for row in rows) / len(rows)
    if abs(signed_strength) <= 1e-12:
        return {
            "available": True,
            "direction": "BALANCED",
            "signed_strength": 0.0,
            "applied_weight": 0.0,
            "weight_cap": EDGE_HIGH_RISK_ONLY_MAX,
            "independent_group_n": len(rows),
            "strict_group_n": sum(int(row["strict_matched_n"] > 0) for row in rows),
            "groups": rows,
            "reason": "BALANCED_FAMILY_HORIZON_VOTE",
        }

    strict_n = int(context.get("matched_strict_reference_signal_n") or 0)
    strict_ratio = _number(context.get("strict_net_position_vote_ratio"))
    strict_groups = [
        row for row in rows
        if int(row["strict_matched_n"]) > 0
        and row["strict_vote_ratio"] is not None
    ]
    aligned_strict_groups = sum(
        1 for row in strict_groups
        if float(row["strict_vote_ratio"]) * signed_strength > 0
    )

    if strict_n <= 0:
        cap = EDGE_HIGH_RISK_ONLY_MAX
        cap_reason = "HIGH_RISK_ONLY"
    elif strict_ratio is not None and strict_ratio * signed_strength < 0:
        cap = EDGE_HIGH_RISK_ONLY_MAX
        cap_reason = "STRICT_DISAGREES_WITH_ALL_ACTIVE"
    elif aligned_strict_groups >= 3:
        cap = EDGE_STRICT_BROAD_MAX
        cap_reason = "BROAD_STRICT_ALIGNMENT"
    else:
        cap = EDGE_STRICT_MAX
        cap_reason = "STRICT_REFERENCE_PRESENT"

    # One isolated family×horizon group cannot consume the full cap.
    breadth = min(1.0, len(rows) / 3.0)
    applied = min(
        EDGE_WEIGHT_MAX,
        max(0.0, cap * abs(signed_strength) * breadth),
    )
    return {
        "available": True,
        "direction": (
            "SUPPORTS_POSITION" if signed_strength > 0 else "OPPOSES_POSITION"
        ),
        "signed_strength": round(signed_strength, 6),
        "applied_weight": round(applied, 6),
        "weight_cap": cap,
        "weight_cap_reason": cap_reason,
        "breadth_factor": round(breadth, 6),
        "independent_group_n": len(rows),
        "strict_group_n": len(strict_groups),
        "aligned_strict_group_n": aligned_strict_groups,
        "groups": rows,
        "reason": "BOUNDED_PROVISIONAL_ACTIVE_EDGE",
    }


def _blend_policy_scores(result: dict[str, Any],
                         signal: dict[str, Any]) -> dict[str, Any]:
    """Blend base Expected-R utility with edge preference inside hard eligibility."""
    recommendation = result.get("recommendation") or {}
    base_policy = str(recommendation.get("policy") or "HOLD")
    policies = result.get("policies") or {}
    selection_rule = result.get("selection_rule") or {}
    eligible = [
        str(name) for name in (selection_rule.get("eligible") or [])
        if str(name) in policies and str(name) in _POLICY_FRACTIONS
    ]
    weight = _number(signal.get("applied_weight")) or 0.0
    direction = str(signal.get("direction") or "BALANCED")

    base = {
        "base_policy": base_policy,
        "adjusted_policy": base_policy,
        "changed": False,
        "applied_weight": min(EDGE_WEIGHT_MAX, max(0.0, weight)),
        "hard_eligible_policies": eligible,
        "policy_scores": {},
        "reason": None,
    }
    if weight <= 0.0 or direction not in {
        "SUPPORTS_POSITION", "OPPOSES_POSITION"
    }:
        base["reason"] = "NO_MATERIAL_DIRECTIONAL_EDGE_WEIGHT"
        return base
    if base_policy not in eligible:
        base["reason"] = "BASE_POLICY_NOT_IN_HARD_CVAR_ELIGIBLE_SET"
        return base
    if len(eligible) <= 1:
        base["reason"] = "ONLY_ONE_HARD_CVAR_ELIGIBLE_POLICY"
        return base

    base_fraction = _POLICY_FRACTIONS[base_policy]
    if direction == "SUPPORTS_POSITION":
        candidates = [
            name for name in eligible
            if _POLICY_FRACTIONS[name] <= base_fraction + 1e-12
        ]
    else:
        candidates = [
            name for name in eligible
            if _POLICY_FRACTIONS[name] >= base_fraction - 1e-12
        ]
    if len(candidates) <= 1:
        base["reason"] = "EDGE_DIRECTION_HAS_NO_ADMISSIBLE_POLICY_STEP"
        return base

    expected = {
        name: _number((policies.get(name) or {}).get("expected_final_r"))
        for name in eligible
    }
    finite_values = [value for value in expected.values() if value is not None]
    if not finite_values:
        base["reason"] = "EXPECTED_R_UNAVAILABLE"
        return base
    lo, hi = min(finite_values), max(finite_values)
    span = hi - lo

    scores: dict[str, dict[str, float | None]] = {}
    for name in candidates:
        value = expected.get(name)
        if value is None:
            continue
        base_utility = 0.5 if span <= 1e-12 else (value - lo) / span
        close_fraction = _POLICY_FRACTIONS[name]
        edge_preference = (
            1.0 - close_fraction
            if direction == "SUPPORTS_POSITION"
            else close_fraction
        )
        blended = (1.0 - weight) * base_utility + weight * edge_preference
        scores[name] = {
            "expected_final_r": round(value, 6),
            "base_utility": round(base_utility, 6),
            "edge_preference": round(edge_preference, 6),
            "blended_score": round(blended, 6),
        }

    if not scores or base_policy not in scores:
        base["reason"] = "NO_SCORABLE_ADMISSIBLE_POLICIES"
        base["policy_scores"] = scores
        return base

    if direction == "SUPPORTS_POSITION":
        winner = max(
            scores,
            key=lambda name: (
                float(scores[name]["blended_score"]),
                -_POLICY_FRACTIONS[name],
            ),
        )
    else:
        winner = max(
            scores,
            key=lambda name: (
                float(scores[name]["blended_score"]),
                _POLICY_FRACTIONS[name],
            ),
        )
    base.update({
        "adjusted_policy": winner,
        "changed": winner != base_policy,
        "policy_scores": scores,
        "reason": (
            "ACTIVE_EDGE_CHANGED_SOFT_POLICY_RANKING"
            if winner != base_policy
            else "ACTIVE_EDGE_DID_NOT_CHANGE_SOFT_POLICY_RANKING"
        ),
    })
    return base


def _apply_active_edge_policy(result: dict[str, Any],
                              context: dict[str, Any]) -> dict[str, Any]:
    signal = _active_edge_signal(context)
    blend = _blend_policy_scores(result, signal)
    base_policy = blend["base_policy"]
    adjusted = blend["adjusted_policy"]

    block = {
        "contract_version": "active-edge-policy-weight-v1",
        "max_decision_weight": EDGE_WEIGHT_MAX,
        "high_risk_only_max": EDGE_HIGH_RISK_ONLY_MAX,
        "strict_reference_max": EDGE_STRICT_MAX,
        "broad_strict_max": EDGE_STRICT_BROAD_MAX,
        "hard_risk_override_allowed": False,
        "cvar_eligibility_override_allowed": False,
        "automatic_execution_authority_added": False,
        "active_context": {
            key: context.get(key) for key in (
                "available", "matched_structured_signal_n",
                "supporting_position_n", "opposing_position_n",
                "net_position_vote", "net_position_vote_ratio",
                "matched_strict_reference_signal_n",
                "strict_net_position_vote_ratio", "matched_group_n",
            )
        },
        "signal": signal,
        "blend": blend,
    }
    result["active_edge_policy_weight"] = block
    evidence = result.setdefault("evidence", {})
    if isinstance(evidence, dict):
        evidence["active_edge_policy_weight"] = {
            "applied_weight": blend["applied_weight"],
            "direction": signal.get("direction"),
            "signed_strength": signal.get("signed_strength"),
            "base_policy": base_policy,
            "adjusted_policy": adjusted,
            "changed": blend["changed"],
            "hard_risk_override_allowed": False,
        }

    if not blend["changed"]:
        return result

    recommendation = result.setdefault("recommendation", {})
    fraction = _POLICY_FRACTIONS[adjusted]
    recommendation.update({
        "policy": adjusted,
        "close_fraction": fraction,
        "remaining_fraction": round(1.0 - fraction, 2),
        "action_ru": _ACTIONS_RU[adjusted],
        "computed_action_ru": _ACTIONS_RU[adjusted],
        "active_edge_base_policy": base_policy,
        "active_edge_adjusted": True,
        # The overlay changes the manual recommendation only. It cannot grant
        # automatic broker/execution authority that the base gate did not grant.
        "automatic_execution_allowed": False,
        "working_action_code": "KEEP_CURRENT_MANAGEMENT",
        "execution_action_ru": (
            "НИЧЕГО НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ; "
            "РАСЧЁТНОЕ ДЕЙСТВИЕ ТРЕБУЕТ РУЧНОГО РЕШЕНИЯ"
        ),
    })
    gate = result.get("gate")
    if isinstance(gate, dict):
        gate["active_edge_base_policy"] = base_policy
        gate["active_edge_adjusted_policy"] = adjusted
        gate["active_edge_weight_applied"] = blend["applied_weight"]
        gate["automatic_execution_allowed"] = False
        gate["execution_policy"] = None
    decision = result.get("management_decision")
    if isinstance(decision, dict):
        decision["active_edge_base_policy"] = base_policy
        decision["active_edge_adjusted_policy"] = adjusted
        decision["active_edge_weight_applied"] = blend["applied_weight"]
    return result


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    instrument = _trade_instrument(trade)
    edge_snapshot = {
        "captured_ts": time.time(),
        "strategy": {
            "instrument": instrument,
            "direction": str(trade.get("direction") or ""),
        },
    }
    context = build_active_edge_context(engine, edge_snapshot) if instrument else {
        "available": False, "matched_groups": [],
    }
    result = _apply_active_edge_policy(result, context)
    result["version"] = "quant-policy-v16-active-edge-bounded30"
    result["phase_e_authority_contract"] = {
        "production_recommendation_source": (
            "authoritative v14 policy path + bounded active-edge soft re-ranking"
        ),
        "active_edge_weight_cap": EDGE_WEIGHT_MAX,
        "active_edge_hard_risk_override": False,
        "derived_scenario_role": "shadow robustness candidate only",
        "promotion_allowed": False,
        "sample_count_auto_promotion": False,
    }
    contract = result.setdefault("shadow_policy_contract", {})
    contract["promotion_allowed"] = False
    contract["action_changed"] = False
    return result


globals()["analyze_policies"] = analyze_policies
