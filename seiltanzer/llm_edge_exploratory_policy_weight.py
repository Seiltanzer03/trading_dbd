"""Bounded production soft weight for rolling exploratory LLM hypotheses.

Only worker-materialized LIMITED-confidence results are read on the request
path.  A hypothesis must match the current frozen T0 feature context before it
can contribute.  The resulting vote is capped at fifteen percent and can only
rank policies which already passed the deterministic hard-risk/CVaR gate.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


CONTRACT_VERSION = "llm-edge-exploratory-policy-weight-v1"
MAX_EXPLORATORY_WEIGHT = 0.15
ELIGIBLE_POLICIES = ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _prediction_shift(verdict: dict[str, Any]) -> dict[str, Any]:
    target_id = str(verdict.get("target_id") or "")
    shift = verdict.get("prediction_shift")
    if isinstance(shift, dict):
        values = {str(key): _finite(value) for key, value in shift.items()}
        values = {key: value for key, value in values.items() if value is not None}
        if not values:
            return {}
        strongest = max(values, key=values.get)
        return {
            "kind": "MULTICLASS_PROBABILITY_SHIFT",
            "classes": values,
            "strongest_class": strongest,
            "strongest_shift": values[strongest],
        }
    value = _finite(shift)
    if value is None:
        return {}
    mapping = {
        "DIRECTION": ("MORE_UP", "MORE_DOWN", "probability"),
        "RETURN_SIGMA": ("MORE_UPSIDE_RETURN", "MORE_DOWNSIDE_RETURN", "sigma"),
        "MFE_SIGMA": ("MORE_UPSIDE_EXCURSION", "LESS_UPSIDE_EXCURSION", "sigma"),
        "MAE_SIGMA": ("LESS_DOWNSIDE_EXCURSION", "MORE_DOWNSIDE_EXCURSION", "sigma"),
        "FORWARD_VOL_RATIO": ("VOL_EXPANSION", "VOL_COMPRESSION", "ratio"),
    }
    positive, negative, unit = mapping.get(
        target_id, ("POSITIVE_SHIFT", "NEGATIVE_SHIFT", "target")
    )
    return {
        "kind": "SCALAR_TARGET_SHIFT",
        "candidate_minus_structural_baseline": value,
        "interpretation": positive if value > 0 else negative if value < 0 else "NEUTRAL",
        "unit": unit,
    }


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "available": False,
        "weight_fraction": 0.0,
        "max_weight_fraction": MAX_EXPLORATORY_WEIGHT,
        "direction_score": 0.0,
        "preferred_close_fraction": None,
        "reason": reason,
        "production_role": "BOUNDED_EARLY_SOFT_POLICY_RANKING",
        "may_influence_policy_selection": False,
        "eligible_policies": list(ELIGIBLE_POLICIES),
        "hard_risk_override": False,
        "may_override_cvar_floor": False,
        "may_widen_stop": False,
        "may_increase_position": False,
        "automatic_execution_source": False,
        **extra,
    }


def exploratory_weight_profile(
    engine: Any, snapshot: dict[str, Any], integration: Any,
) -> dict[str, Any]:
    """Build a current-context vote from materialized exploratory verdicts."""
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return _unavailable("G1S_RUNTIME_UNAVAILABLE")
    try:
        from .llm_edge_lifecycle import read_cached_materialized_lifecycle

        lifecycle = read_cached_materialized_lifecycle(runtime)
    except Exception:
        return _unavailable("MATERIALIZED_LIFECYCLE_UNAVAILABLE")
    if str(lifecycle.get("status") or "") != "OK":
        return _unavailable("MATERIALIZED_LIFECYCLE_NOT_READY")

    values = integration._current_values(engine, snapshot)
    if not values:
        return _unavailable("CURRENT_T0_FEATURES_UNAVAILABLE")
    direction = str((snapshot.get("strategy") or {}).get("direction") or "")
    if direction.lower() not in {"long", "buy", "short", "sell"}:
        return _unavailable("POSITION_DIRECTION_UNAVAILABLE")
    snapshot_ts = _finite(snapshot.get("captured_ts"))
    max_age = _finite(getattr(getattr(engine, "settings", None), "ede_context_max_age_sec", 900.0))
    if snapshot_ts is None or max_age is None or max_age <= 0:
        return _unavailable("CURRENT_T0_FRESHNESS_BOUNDARY_UNAVAILABLE")

    buckets: dict[tuple[str, int], list[float]] = defaultdict(list)
    matched_limited_n = 0
    matched_directional_n = 0
    uncertainty_n = 0
    very_low_ignored_n = 0
    details = []
    matched_details = []
    matched_status_counts: dict[str, int] = defaultdict(int)
    matched_horizons: set[int] = set()
    matched_target_families: set[str] = set()
    matched_advantage_supporting_n = 0
    matched_advantage_opposing_n = 0
    for hypothesis in lifecycle.get("research_hypotheses") or []:
        if not isinstance(hypothesis, dict):
            continue
        verdict = hypothesis.get("exploratory_verdict") or {}
        confidence = str(verdict.get("confidence") or "")
        status = str(verdict.get("status") or "")
        if confidence != "LIMITED":
            very_low_ignored_n += int(confidence == "VERY_LOW")
            continue
        deployment_rule = verdict.get("deployment_rule") or []
        conditions = (
            deployment_rule.get("conditions") or []
            if isinstance(deployment_rule, dict)
            else deployment_rule
        )
        candidate = {
            "conditions": conditions if isinstance(conditions, list) else [],
            "prediction_shift": _prediction_shift(verdict),
        }
        fresh = bool(candidate["conditions"])
        for condition in candidate["conditions"]:
            row = values.get(str(condition.get("feature_id") or "")) or {}
            asof = _finite(row.get("asof"))
            if (
                not row.get("available")
                or row.get("live_applicability") != "LIVE_APPLICABLE"
                or bool(row.get("stale"))
                or asof is None
                or asof > snapshot_ts + 1e-6
                or snapshot_ts - asof > max_age
            ):
                fresh = False
                break
        if not fresh or not integration._conditions_match(values, candidate):
            continue
        matched_limited_n += 1
        bias = integration._bias(candidate)
        relation = integration._relation(direction, bias)
        target_id = str(verdict.get("target_id") or hypothesis.get("target") or "UNKNOWN")
        horizon = int(verdict.get("horizon_minutes") or hypothesis.get("horizon_minutes") or 0)
        family = integration._target_family(target_id)
        matched_status_counts[status or "UNKNOWN"] += 1
        matched_horizons.add(horizon)
        matched_target_families.add(family)
        matched_details.append({
            "hypothesis_id": hypothesis.get("hypothesis_id"),
            "status": status or "UNKNOWN",
            "confidence": confidence,
            "target_id": target_id,
            "target_family": family,
            "horizon_minutes": horizon,
            "condition_count": int(verdict.get("condition_count") or len(candidate["conditions"])),
            "conditions": [
                {
                    "feature_id": condition.get("feature_id"),
                    "state": condition.get("state"),
                }
                for condition in candidate["conditions"][:3]
                if isinstance(condition, dict)
            ],
            "position_relation": relation,
            "prediction_shift": candidate.get("prediction_shift"),
            "selected_test_n": verdict.get("selected_test_n"),
            "selected_effective_n": verdict.get("selected_effective_n"),
            "evaluated_fold_count": verdict.get("evaluated_fold_count"),
            "positive_fold_count": verdict.get("positive_fold_count"),
            "primary_improvement": verdict.get("primary_improvement"),
        })
        if status != "EARLY_ADVANTAGE" or relation not in {
            "SUPPORTS_POSITION", "OPPOSES_POSITION",
        }:
            uncertainty_n += 1
            continue
        vote = 1.0 if relation == "SUPPORTS_POSITION" else -1.0
        matched_advantage_supporting_n += int(relation == "SUPPORTS_POSITION")
        matched_advantage_opposing_n += int(relation == "OPPOSES_POSITION")
        buckets[(family, horizon)].append(vote)
        matched_directional_n += 1
        details.append({
            "hypothesis_id": hypothesis.get("hypothesis_id"),
            "target_id": target_id,
            "target_family": family,
            "horizon_minutes": horizon,
            "condition_count": int(verdict.get("condition_count") or len(candidate["conditions"])),
            "position_relation": relation,
            "selected_test_n": verdict.get("selected_test_n"),
            "evaluated_fold_count": verdict.get("evaluated_fold_count"),
            "primary_improvement": verdict.get("primary_improvement"),
        })

    bucket_scores = [sum(votes) / len(votes) for votes in buckets.values() if votes]
    if not bucket_scores:
        return _unavailable(
            "NO_MATCHED_LIMITED_DIRECTIONAL_ADVANTAGES",
            matched_limited_hypothesis_n=matched_limited_n,
            matched_directional_advantage_n=0,
            matched_uncertainty_n=uncertainty_n,
            very_low_ignored_n=very_low_ignored_n,
            matched_status_counts=dict(matched_status_counts),
            matched_horizons=sorted(matched_horizons),
            matched_target_families=sorted(matched_target_families),
            matched_advantage_supporting_n=matched_advantage_supporting_n,
            matched_advantage_opposing_n=matched_advantage_opposing_n,
            matched_signals=matched_details[:8],
        )

    raw_direction = sum(bucket_scores) / len(bucket_scores)
    reliability = matched_directional_n / max(1, matched_directional_n + uncertainty_n)
    direction_score = max(-1.0, min(1.0, raw_direction * reliability))
    agreement = abs(direction_score)
    weight = min(MAX_EXPLORATORY_WEIGHT, MAX_EXPLORATORY_WEIGHT * agreement)
    if weight <= 0.0:
        return _unavailable(
            "DIRECTIONAL_VOTES_CANCELLED",
            matched_limited_hypothesis_n=matched_limited_n,
            matched_directional_advantage_n=matched_directional_n,
            matched_uncertainty_n=uncertainty_n,
            independent_bucket_n=len(bucket_scores),
        )
    return {
        "contract_version": CONTRACT_VERSION,
        "available": True,
        "weight_fraction": round(weight, 6),
        "max_weight_fraction": MAX_EXPLORATORY_WEIGHT,
        "direction_score": round(direction_score, 6),
        "agreement": round(agreement, 6),
        "preferred_close_fraction": round((1.0 - direction_score) / 2.0, 6),
        "matched_limited_hypothesis_n": matched_limited_n,
        "matched_directional_advantage_n": matched_directional_n,
        "matched_uncertainty_n": uncertainty_n,
        "very_low_ignored_n": very_low_ignored_n,
        "matched_status_counts": dict(matched_status_counts),
        "matched_horizons": sorted(matched_horizons),
        "matched_target_families": sorted(matched_target_families),
        "matched_advantage_supporting_n": matched_advantage_supporting_n,
        "matched_advantage_opposing_n": matched_advantage_opposing_n,
        "independent_bucket_n": len(bucket_scores),
        "basis": "current_t0_matched_limited_target_family_x_horizon_votes",
        "rolling_result": True,
        "strict_gate_passed": False,
        "production_authority": False,
        "production_role": "BOUNDED_EARLY_SOFT_POLICY_RANKING",
        "may_influence_policy_selection": True,
        "eligible_policies": list(ELIGIBLE_POLICIES),
        "hard_risk_override": False,
        "may_override_cvar_floor": False,
        "may_widen_stop": False,
        "may_increase_position": False,
        "automatic_execution_source": False,
        "signals": details[:8],
        "matched_signals": matched_details[:8],
    }


def combine_weight_profiles(
    active: dict[str, Any], exploratory: dict[str, Any], *, absolute_cap: float,
) -> dict[str, Any]:
    """Combine directional utilities while preserving the established total cap."""
    active_weight = max(0.0, _finite(active.get("weight_fraction")) or 0.0)
    exploratory_weight = max(0.0, _finite(exploratory.get("weight_fraction")) or 0.0)
    active_direction = _finite(active.get("direction_score")) or 0.0
    exploratory_direction = _finite(exploratory.get("direction_score")) or 0.0
    raw_weight = active_weight + exploratory_weight
    if raw_weight <= 0.0:
        return dict(active)
    direction = (
        active_weight * active_direction + exploratory_weight * exploratory_direction
    ) / raw_weight
    weight = min(float(absolute_cap), raw_weight)
    combined = dict(active)
    combined.pop("reason", None)
    combined.update({
        "contract_version": "active-plus-exploratory-policy-weight-v1",
        "available": weight > 0.0,
        "weight_fraction": round(weight, 6),
        "max_weight_fraction": float(absolute_cap),
        "direction_score": round(direction, 6),
        "agreement": round(abs(direction), 6),
        "preferred_close_fraction": round((1.0 - direction) / 2.0, 6),
        "active_edge_component_weight": round(active_weight, 6),
        "exploratory_component_weight": round(exploratory_weight, 6),
        "exploratory_contract_version": exploratory.get("contract_version"),
        "independent_bucket_n": (
            max(0, int(active.get("independent_bucket_n") or 0))
            + max(0, int(exploratory.get("independent_bucket_n") or 0))
        ),
        "basis": "bounded_active_edge_plus_current_matched_exploratory_votes",
    })
    return combined
