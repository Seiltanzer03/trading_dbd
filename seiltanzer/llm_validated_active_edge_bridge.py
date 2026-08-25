"""Expose only prospectively VALIDATED, fresh LLM rules to Active Edge."""
from __future__ import annotations

import math
from typing import Any

from .llm_edge_prospective_evaluation import active_promotions

CONTRACT_VERSION = "llm-validated-active-edge-bridge-v1.1"
_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _prediction_shift(payload: dict[str, Any]) -> dict[str, Any]:
    target_id = str(payload.get("target_id") or "")
    kind = str(payload.get("target_kind") or "")
    residual = payload.get("state_residual")
    if kind in {"BINARY", "CONTINUOUS"}:
        value = _finite(residual)
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
    if kind == "MULTICLASS" and isinstance(residual, list):
        classes = [str(item) for item in payload.get("target_classes") or []]
        values = [_finite(item) for item in residual]
        if len(classes) != len(values) or not values or any(item is None for item in values):
            return {}
        numeric = [float(item) for item in values if item is not None]
        strongest_index = max(range(len(numeric)), key=lambda index: numeric[index])
        return {
            "kind": "MULTICLASS_PROBABILITY_SHIFT",
            "classes": {name: numeric[index] for index, name in enumerate(classes)},
            "strongest_class": classes[strongest_index],
            "strongest_shift": numeric[strongest_index],
        }
    return {}


def _fresh_conditions_match(
    engine: Any,
    snapshot: dict[str, Any],
    values: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
    integration: Any,
) -> tuple[bool, str]:
    """Use the same explicit freshness boundary required by causal EDE context."""
    try:
        snapshot_ts = float(snapshot["captured_ts"])
        max_age = float(engine.settings.ede_context_max_age_sec)
        if not math.isfinite(snapshot_ts) or not math.isfinite(max_age) or max_age <= 0:
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        return False, "STALE_OR_UNAVAILABLE_CONTEXT"

    conditions = candidate.get("conditions") or []
    if not conditions:
        return False, "DEPLOYMENT_RULE_MISSING"
    for condition in conditions:
        feature_id = str(condition.get("feature_id") or "")
        row = values.get(feature_id)
        if (
            not feature_id
            or not row
            or row.get("live_applicability") != "LIVE_APPLICABLE"
            or not bool(row.get("available"))
            or bool(row.get("stale"))
        ):
            return False, "STALE_OR_UNAVAILABLE_CONTEXT"
        asof = _finite(row.get("asof"))
        if (
            asof is None
            or asof > snapshot_ts + 1e-6
            or snapshot_ts - asof < -1e-6
            or snapshot_ts - asof > max_age
        ):
            return False, "STALE_OR_UNAVAILABLE_CONTEXT"
    return (
        (True, "MATCHED_FRESH_VALIDATED_RULE")
        if integration._conditions_match(values, candidate)
        else (False, "CONDITIONS_NOT_MATCHED")
    )


def _merge_group(
    groups: list[dict[str, Any]], row: dict[str, Any], target_family_fn
) -> list[dict[str, Any]]:
    target = str(row.get("target_id") or "UNKNOWN")
    horizon = int(row.get("horizon_minutes") or 0)
    output = [dict(item) for item in groups]
    found = next(
        (
            item
            for item in output
            if str(item.get("target_id") or "") == target
            and int(item.get("signal_horizon_minutes") or 0) == horizon
        ),
        None,
    )
    if found is None:
        found = {
            "target_id": target,
            "target_family": target_family_fn(target),
            "signal_horizon_minutes": horizon,
            "matched_n": 0,
            "supporting_n": 0,
            "opposing_n": 0,
            "net_vote": 0,
            "net_vote_ratio": None,
            "strict_matched_n": 0,
            "strict_supporting_n": 0,
            "strict_opposing_n": 0,
            "strict_net_vote": 0,
            "strict_net_vote_ratio": None,
            "validated_matched_n": 0,
            "validated_supporting_n": 0,
            "validated_opposing_n": 0,
        }
        output.append(found)

    relation = str(row.get("position_relation") or "")
    found["matched_n"] = int(found.get("matched_n") or 0) + 1
    found["validated_matched_n"] = int(found.get("validated_matched_n") or 0) + 1
    if relation == "SUPPORTS_POSITION":
        found["supporting_n"] = int(found.get("supporting_n") or 0) + 1
        found["validated_supporting_n"] = int(found.get("validated_supporting_n") or 0) + 1
    elif relation == "OPPOSES_POSITION":
        found["opposing_n"] = int(found.get("opposing_n") or 0) + 1
        found["validated_opposing_n"] = int(found.get("validated_opposing_n") or 0) + 1
    supporting = int(found.get("supporting_n") or 0)
    opposing = int(found.get("opposing_n") or 0)
    directional = supporting + opposing
    found["net_vote"] = supporting - opposing
    found["net_vote_ratio"] = (
        (supporting - opposing) / directional if directional else None
    )
    output.sort(
        key=lambda item: (
            -int(item.get("matched_n") or 0),
            str(item.get("target_family") or ""),
            int(item.get("signal_horizon_minutes") or 0),
            str(item.get("target_id") or ""),
        )
    )
    return output[:64]


def _validated_rows(
    engine: Any, snapshot: dict[str, Any], integration: Any
) -> list[dict[str, Any]]:
    values = integration._current_values(engine, snapshot)
    direction = str((snapshot.get("strategy") or {}).get("direction") or "")
    output = []
    for payload in active_promotions(engine):
        candidate = {
            "conditions": payload.get("conditions") or [],
            "prediction_shift": _prediction_shift(payload),
        }
        matched, reason = _fresh_conditions_match(
            engine, snapshot, values, candidate, integration
        )
        bias = integration._bias(candidate)
        checkpoint = payload.get("prospective_checkpoint") or {}
        output.append({
            "candidate_id": payload.get("candidate_id"),
            "source": "LLM_VALIDATED",
            "target_id": payload.get("target_id"),
            "horizon_minutes": payload.get("horizon_minutes"),
            "primary_improvement": checkpoint.get("primary_improvement"),
            "q_value_diagnostic": checkpoint.get("q_value"),
            "fold_positive": None,
            "strict_reference_qualified": False,
            "prospective_validated": True,
            "prospective_checkpoint_n": checkpoint.get("checkpoint_n"),
            "conditions_match_current_t0": matched,
            "applicability_reason": reason,
            "prediction_shift": candidate["prediction_shift"],
            "market_bias": bias,
            "position_relation": (
                integration._relation(direction, bias) if matched else "NOT_APPLICABLE"
            ),
            "promotion_sha256": payload.get("promotion_sha256"),
        })
    return output


def _augment_context(
    engine: Any, snapshot: dict[str, Any], context: dict[str, Any], integration: Any
) -> dict[str, Any]:
    context = dict(context)
    context["validated_promotion_bridge"] = True
    context["validated_promotion_contract"] = CONTRACT_VERSION
    if context.get("measurement_available") is False:
        context["validated_llm_signal_n"] = 0
        context["validated_bridge_blocked_by_measurement"] = True
        return context

    validated = _validated_rows(engine, snapshot, integration)
    context["validated_llm_signal_n"] = len(validated)
    context["validated_bridge_blocked_by_measurement"] = False
    if not validated:
        return context

    matched = [
        row for row in validated if row.get("conditions_match_current_t0") is True
    ]
    supporting = sum(
        row.get("position_relation") == "SUPPORTS_POSITION" for row in matched
    )
    opposing = sum(
        row.get("position_relation") == "OPPOSES_POSITION" for row in matched
    )
    context["contract_version"] = CONTRACT_VERSION
    context["available"] = True
    context["aggregate_scope"] = (
        "ALL_EXISTING_ACTIVE_CANDIDATES_PLUS_PROSPECTIVELY_VALIDATED_LLM"
    )
    context["total_active_signal_n"] = (
        int(context.get("total_active_signal_n") or 0) + len(validated)
    )
    context["matched_validated_llm_signal_n"] = len(matched)
    context["matched_structured_signal_n"] = (
        int(context.get("matched_structured_signal_n") or 0) + len(matched)
    )
    context["supporting_position_n"] = (
        int(context.get("supporting_position_n") or 0) + supporting
    )
    context["opposing_position_n"] = (
        int(context.get("opposing_position_n") or 0) + opposing
    )
    context["validated_supporting_position_n"] = supporting
    context["validated_opposing_position_n"] = opposing
    context["net_position_vote"] = (
        int(context["supporting_position_n"]) - int(context["opposing_position_n"])
    )
    directional = (
        int(context["supporting_position_n"]) + int(context["opposing_position_n"])
    )
    context["net_position_vote_ratio"] = (
        context["net_position_vote"] / directional if directional else None
    )

    groups = context.get("matched_groups") or []
    for row in matched:
        groups = _merge_group(groups, row, integration._target_family)
    context["matched_groups"] = groups
    context["matched_group_n"] = len(groups)

    details = list(context.get("signals") or []) + validated
    details.sort(
        key=lambda item: (
            item.get("conditions_match_current_t0") is not True,
            item.get("prospective_validated") is not True,
            -float(_finite(item.get("primary_improvement")) or 0.0),
            int(item.get("horizon_minutes") or 0),
            str(item.get("candidate_id") or ""),
        )
    )
    context["signals"] = details[:8]
    context["serialized_signal_n"] = len(context["signals"])
    context["details_truncated"] = (
        int(context["total_active_signal_n"]) > len(context["signals"])
    )
    context["premature_llm_influence"] = False
    context["automatic_execution"] = False
    return context


def _upgrade_weight_profile(
    context: dict[str, Any], profile: dict[str, Any], weight_module: Any
) -> dict[str, Any]:
    validated_directional = max(
        0,
        int(context.get("validated_supporting_position_n") or 0)
        + int(context.get("validated_opposing_position_n") or 0),
    )
    profile = dict(profile)
    profile["prospective_validated_directional_n"] = validated_directional
    if validated_directional <= 0 or not profile.get("available"):
        return profile

    directional_n = max(0, int(profile.get("matched_directional_signal_n") or 0))
    strict_n = max(0, int(profile.get("strict_directional_signal_n") or 0))
    authority_n = min(directional_n, strict_n + validated_directional)
    authority_share = authority_n / directional_n if directional_n > 0 else 0.0
    max_weight = weight_module.HIGH_RISK_ONLY_CAP + (
        weight_module.MAX_EDGE_WEIGHT - weight_module.HIGH_RISK_ONLY_CAP
    ) * authority_share
    agreement = float(profile.get("agreement") or 0.0)
    profile.update({
        "max_weight_fraction": round(max_weight, 6),
        "weight_fraction": round(
            min(weight_module.MAX_EDGE_WEIGHT, max_weight * agreement), 6
        ),
        "validated_directional_share": round(
            validated_directional / directional_n if directional_n > 0 else 0.0, 6
        ),
        "authority_grade_directional_share": round(authority_share, 6),
        "prospective_calibration_pending": False,
        "validated_promotion_bridge": CONTRACT_VERSION,
    })
    return profile


def install_validated_llm_active_edge_bridge() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import active_edge_ai_integration as integration
    from . import active_edge_policy_weight as weight_module

    original_context = integration.build_active_edge_context
    original_profile = weight_module.edge_weight_profile

    def build_active_edge_context(
        engine: Any, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        return _augment_context(
            engine, snapshot, original_context(engine, snapshot), integration
        )

    def edge_weight_profile(context: dict[str, Any]) -> dict[str, Any]:
        return _upgrade_weight_profile(
            context, original_profile(context), weight_module
        )

    integration.build_active_edge_context = build_active_edge_context
    weight_module.edge_weight_profile = edge_weight_profile
    _INSTALLED = True
