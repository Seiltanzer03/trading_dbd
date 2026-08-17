"""Bounded provisional production weight for matched active structured edge.

This layer does not change EDE discovery, path simulation, policy definitions,
CVaR eligibility, hard-risk floors or execution. It only blends the existing
expected-R soft ranking with the already-materialized active-edge direction.
The blend is capped at 30%; high-risk-only evidence is capped at 15%.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from types import ModuleType
from typing import Any


CONTRACT_VERSION = "active-edge-policy-weight-v1"
MAX_EDGE_WEIGHT = 0.30
HIGH_RISK_ONLY_CAP = 0.15
SOFT_DECISION_SCALE_FLOOR_R = 0.12

_PROFILE_CTX: ContextVar[dict[str, Any] | None] = ContextVar(
    "active_edge_policy_weight_profile", default=None)
_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _strategy_snapshot(engine: Any, tick: dict, trade: dict) -> dict[str, Any] | None:
    try:
        from .config import SETUPS

        setup = SETUPS.get(int(trade.get("setup") or 0))
        instrument = str(
            (setup.instrument if setup is not None else None)
            or trade.get("instrument")
            or tick.get("instrument")
            or ""
        )
        direction = str(trade.get("direction") or "")
        if not instrument or direction.lower() not in {"long", "buy", "short", "sell"}:
            return None
        return {
            "captured_ts": time.time(),
            "strategy": {"instrument": instrument, "direction": direction},
        }
    except Exception:
        return None


def _active_context(engine: Any, tick: dict, trade: dict) -> dict[str, Any]:
    snapshot = _strategy_snapshot(engine, tick, trade)
    if snapshot is None:
        return {}
    try:
        from .active_edge_ai_integration import build_active_edge_context

        context = build_active_edge_context(engine, snapshot)
        return context if isinstance(context, dict) else {}
    except Exception:
        return {}


def edge_weight_profile(context: dict[str, Any]) -> dict[str, Any]:
    """Convert matched group votes into one bounded soft-ranking weight.

    Correlated target rows are first collapsed to target-family x horizon buckets.
    Unanimous high-risk-only evidence can use at most 15% of the soft ranking;
    strict-reference participation raises that cap linearly to at most 30%.
    Disagreement reduces the effective weight toward zero.
    """
    groups = context.get("matched_groups") or []
    collapsed: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in groups:
        if not isinstance(row, dict) or int(row.get("net_vote") or 0) == 0:
            continue
        ratio = _finite(row.get("net_vote_ratio"))
        if ratio is None:
            continue
        family = str(row.get("target_family") or "OTHER")
        horizon = int(row.get("signal_horizon_minutes") or 0)
        collapsed[(family, horizon)].append(max(-1.0, min(1.0, ratio)))

    bucket_scores = [sum(values) / len(values) for values in collapsed.values() if values]
    if not bucket_scores:
        return {
            "contract_version": CONTRACT_VERSION,
            "available": False,
            "weight_fraction": 0.0,
            "max_weight_fraction": 0.0,
            "direction_score": 0.0,
            "preferred_close_fraction": None,
            "independent_bucket_n": 0,
            "reason": "NO_DIRECTIONAL_MATCHED_EDGE_GROUPS",
        }

    direction = max(-1.0, min(1.0, sum(bucket_scores) / len(bucket_scores)))
    agreement = abs(direction)
    supporting = max(0, int(context.get("supporting_position_n") or 0))
    opposing = max(0, int(context.get("opposing_position_n") or 0))
    strict_supporting = max(0, int(context.get("strict_supporting_position_n") or 0))
    strict_opposing = max(0, int(context.get("strict_opposing_position_n") or 0))
    directional_n = supporting + opposing
    strict_directional_n = min(directional_n, strict_supporting + strict_opposing)
    strict_share = (
        strict_directional_n / directional_n if directional_n > 0 else 0.0
    )
    max_weight = HIGH_RISK_ONLY_CAP + (
        MAX_EDGE_WEIGHT - HIGH_RISK_ONLY_CAP) * strict_share
    weight = min(MAX_EDGE_WEIGHT, max_weight * agreement)
    preferred_close = (1.0 - direction) / 2.0

    return {
        "contract_version": CONTRACT_VERSION,
        "available": weight > 0.0,
        "weight_fraction": round(weight, 6),
        "max_weight_fraction": round(max_weight, 6),
        "direction_score": round(direction, 6),
        "agreement": round(agreement, 6),
        "preferred_close_fraction": round(preferred_close, 6),
        "strict_directional_share": round(strict_share, 6),
        "independent_bucket_n": len(bucket_scores),
        "matched_directional_signal_n": directional_n,
        "strict_directional_signal_n": strict_directional_n,
        "basis": "matched_target_family_x_horizon_vote_agreement",
        "high_risk_only_cap": HIGH_RISK_ONLY_CAP,
        "absolute_cap": MAX_EDGE_WEIGHT,
        "prospective_calibration_pending": True,
    }


def adjust_metrics_for_edge(
    metrics: dict[str, dict], profile: dict[str, Any], r0: float,
    *, cvar_floor: float | None, policy_fractions: dict[str, float],
) -> tuple[dict[str, dict], dict[str, Any]]:
    """Blend only soft expected-R ranking; leave CVaR and raw policies untouched."""
    weight = _finite(profile.get("weight_fraction")) or 0.0
    preferred = _finite(profile.get("preferred_close_fraction"))
    if weight <= 0.0 or preferred is None:
        return metrics, {"applied": False, "reason": "ZERO_EDGE_WEIGHT"}

    floor = max(-0.60, float(r0) - 0.80) if cvar_floor is None else float(cvar_floor)
    eligible = []
    for name, row in metrics.items():
        cvar = _finite(row.get("cvar10_r"))
        expected = _finite(row.get("expected_final_r"))
        if cvar is not None and expected is not None and cvar >= floor:
            eligible.append(name)
    if len(eligible) < 2:
        return metrics, {
            "applied": False,
            "reason": "FEWER_THAN_TWO_HARD_RISK_ELIGIBLE_POLICIES",
            "cvar_floor_r": round(floor, 6),
        }

    expected_values = [float(metrics[name]["expected_final_r"]) for name in eligible]
    low = min(expected_values)
    high = max(expected_values)
    scale = max(high - low, SOFT_DECISION_SCALE_FLOOR_R)
    adjusted = deepcopy(metrics)
    audit_rows = []

    for name in eligible:
        base = float(metrics[name]["expected_final_r"])
        fraction = float(policy_fractions.get(name, 0.0))
        base_utility = (base - low) / scale
        edge_utility = max(0.0, 1.0 - abs(fraction - preferred))
        combined = (1.0 - weight) * base_utility + weight * edge_utility
        new_expected = low + scale * combined
        adjusted[name]["expected_final_r"] = round(new_expected, 6)
        audit_rows.append({
            "policy": name,
            "close_fraction": fraction,
            "base_expected_r": round(base, 6),
            "edge_utility": round(edge_utility, 6),
            "weighted_expected_r": round(new_expected, 6),
        })

    return adjusted, {
        "applied": True,
        "contract_version": CONTRACT_VERSION,
        "weight_fraction": round(weight, 6),
        "max_weight_fraction": profile.get("max_weight_fraction"),
        "direction_score": profile.get("direction_score"),
        "preferred_close_fraction": profile.get("preferred_close_fraction"),
        "independent_bucket_n": profile.get("independent_bucket_n"),
        "strict_directional_share": profile.get("strict_directional_share"),
        "soft_decision_scale_r": round(scale, 6),
        "cvar_floor_r": round(floor, 6),
        "hard_risk_modified": False,
        "cvar_modified": False,
        "path_simulation_modified": False,
        "rows": audit_rows,
    }


def _module_chain(root: ModuleType) -> list[ModuleType]:
    output: list[ModuleType] = []
    seen: set[int] = set()
    stack: list[ModuleType] = [root]
    while stack:
        module = stack.pop()
        if id(module) in seen:
            continue
        seen.add(id(module))
        output.append(module)
        for attr in ("_impl", "_base"):
            child = getattr(module, attr, None)
            if isinstance(child, ModuleType):
                stack.append(child)
    return output


def install_active_edge_policy_weight(policy_module: ModuleType) -> None:
    """Install one ContextVar-backed wrapper over the existing raw selector."""
    global _INSTALLED
    if _INSTALLED or getattr(policy_module, "_active_edge_weight_version", None) == CONTRACT_VERSION:
        return

    original_raw = policy_module._raw_policy_choice
    original_analyze = policy_module.analyze_policies
    policy_fractions = dict(policy_module.POLICY_FRACTIONS)

    def weighted_raw_policy_choice(
        metrics: dict[str, dict], r0: float, *, cvar_floor: float | None = None,
    ):
        profile = _PROFILE_CTX.get()
        if not profile or not profile.get("available"):
            return original_raw(metrics, r0, cvar_floor=cvar_floor)
        adjusted, audit = adjust_metrics_for_edge(
            metrics, profile, r0, cvar_floor=cvar_floor,
            policy_fractions=policy_fractions,
        )
        choice, rule = original_raw(adjusted, r0, cvar_floor=cvar_floor)
        rule = dict(rule or {})
        rule["active_edge_provisional_weight"] = audit
        return choice, rule

    for module in _module_chain(policy_module):
        if hasattr(module, "_raw_policy_choice"):
            module._raw_policy_choice = weighted_raw_policy_choice

    def analyze_policies(
        engine, tick: dict, ridge: dict, trade: dict,
        *, previous_policy_inputs: dict | None = None,
        previous_evidence: dict | None = None,
    ):
        context = _active_context(engine, tick, trade)
        profile = edge_weight_profile(context)
        token = _PROFILE_CTX.set(profile)
        try:
            result = original_analyze(
                engine, tick, ridge, trade,
                previous_policy_inputs=previous_policy_inputs,
                previous_evidence=previous_evidence,
            )
        finally:
            _PROFILE_CTX.reset(token)

        result["active_edge_provisional_weight"] = {
            **profile,
            "production_role": "BOUNDED_SOFT_POLICY_RANKING",
            "hard_risk_override": False,
            "may_override_cvar_floor": False,
            "may_widen_stop": False,
            "automatic_execution_source": False,
        }
        phase = result.get("phase_e_authority_contract")
        if isinstance(phase, dict) and profile.get("available"):
            phase["active_edge_soft_weight"] = (
                "provisional historical-OOS weight, bounded to 30%, inside hard-risk eligible set"
            )
            phase["production_recommendation_source"] = (
                "authoritative policy path + bounded active-edge soft ranking"
            )
        return result

    # Preserve the public facade identity contract: this remains v16 behavior
    # with one installed bounded ranking modifier, not a new policy architecture.
    analyze_policies.__module__ = policy_module.__name__
    weighted_raw_policy_choice.__module__ = policy_module.__name__
    policy_module.analyze_policies = analyze_policies
    policy_module._raw_policy_choice = weighted_raw_policy_choice
    policy_module._active_edge_weight_version = CONTRACT_VERSION
    _INSTALLED = True
