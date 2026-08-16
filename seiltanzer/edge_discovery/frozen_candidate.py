"""Immutable pre-OOS specs for interpretable universal candidates.

The final validation rule is refit once on the complete already-resolved
historical cohort.  Crucially, PASS 5's instrument/family/global structural
baseline is frozen as parameters, not collapsed to one representative scalar.
Prospective inference therefore preserves asset-specific base rates without
reading historical rows or refitting after the freeze boundary.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _weights

from .filters import (
    CandidateTemplate,
    ConditionTemplate,
    FittedCondition,
    FittedRule,
    condition_matches,
    fit_rule,
    rule_mask,
)
from .universal_target_scoring import (
    MIN_PROBABILITY,
    StructuralBaselineModel,
    UniversalTargetSpec,
    fit_structural_baseline,
)


FROZEN_STRUCTURED_SPEC_VERSION = "g1s-universal-structured-frozen-spec-v2"
FROZEN_BASELINE_VERSION = "g1s-frozen-structural-baseline-v1"


def _json_value(value: float | np.ndarray) -> float | list[float]:
    array = np.asarray(value, dtype=float)
    if array.ndim == 0:
        return float(array)
    if array.ndim == 1:
        return [float(item) for item in array]
    raise ValueError("unsupported structural baseline value shape")


def serialize_structural_baseline(model: StructuralBaselineModel) -> dict[str, Any]:
    return {
        "contract_version": FROZEN_BASELINE_VERSION,
        "global_value": _json_value(model.global_value),
        "instrument_values": {
            str(key): _json_value(value)
            for key, value in sorted(model.instrument_values.items())
        },
        "family_values": {
            str(key): _json_value(value)
            for key, value in sorted(model.family_values.items())
        },
    }


def _asset_family(row: dict[str, Any]) -> str | None:
    value = row.get("asset_family")
    if value is None:
        features = row.get("ede_features") or {}
        value = features.get("regime.asset_family")
        if value is None:
            value = features.get("asset_family")
    return None if value in {None, ""} else str(value)


def _prediction_array(value: Any, spec: UniversalTargetSpec) -> np.ndarray:
    if spec.kind == "MULTICLASS":
        array = np.asarray(value, dtype=float)
        if array.ndim != 1 or len(array) != len(spec.classes):
            raise ValueError("frozen multiclass baseline has invalid shape")
        return array.reshape(1, -1)
    return np.asarray([float(value)], dtype=float)


def frozen_structural_baseline_prediction(
    frozen_baseline: dict[str, Any], row: dict[str, Any], spec: UniversalTargetSpec,
) -> np.ndarray:
    if frozen_baseline.get("contract_version") != FROZEN_BASELINE_VERSION:
        raise ValueError("unsupported frozen structural baseline contract")
    instrument = str(row.get("instrument") or "")
    value = (frozen_baseline.get("instrument_values") or {}).get(instrument)
    if value is None:
        family = _asset_family(row)
        if family is not None:
            value = (frozen_baseline.get("family_values") or {}).get(family)
    if value is None:
        value = frozen_baseline["global_value"]
    return _prediction_array(value, spec)


def _target_spec_from_frozen(frozen_spec: dict[str, Any]) -> UniversalTargetSpec:
    return UniversalTargetSpec(
        str(frozen_spec["target_id"]),
        str(frozen_spec.get("target_family") or "UNKNOWN"),
        str(frozen_spec["target_kind"]),
        tuple(str(item) for item in frozen_spec.get("target_classes") or []),
        tuple(str(item) for item in frozen_spec.get("primary_metrics") or []),
    )


def _template_from_candidate(candidate: dict[str, Any]) -> CandidateTemplate:
    conditions = candidate.get("conditions") or []
    if not conditions:
        raise ValueError("structured candidate has no discovery conditions")
    template = CandidateTemplate(tuple(
        ConditionTemplate(
            str(item["feature_id"]), str(item["kind"]), str(item["state"]))
        for item in conditions
    ))
    expected = candidate.get("template_id")
    if expected and str(expected) != template.template_id:
        raise ValueError("candidate conditions do not match immutable template_id")
    return template


def _state_residual(
    selected: list[dict[str, Any]], frozen_baseline: dict[str, Any],
    spec: UniversalTargetSpec,
) -> float | list[float]:
    weights, _effective = _weights(selected)
    den = max(float(weights.sum()), 1e-12)
    baselines = [
        frozen_structural_baseline_prediction(frozen_baseline, row, spec)
        for row in selected
    ]
    if spec.kind == "CONTINUOUS":
        baseline = np.asarray([float(value[0]) for value in baselines])
        target = np.asarray([
            float(row["universal_target_value"]) for row in selected])
        return float(np.sum(weights*(target-baseline))/den)
    if spec.kind == "BINARY":
        positive = spec.classes[-1]
        baseline = np.asarray([float(value[0]) for value in baselines])
        target = np.asarray([
            1.0 if str(row["universal_target_value"]) == positive else 0.0
            for row in selected
        ])
        return float(np.sum(weights*(target-baseline))/(den+2.0))
    if spec.kind == "MULTICLASS":
        baseline = np.vstack(baselines)
        index = {label: idx for idx, label in enumerate(spec.classes)}
        target = np.zeros_like(baseline)
        for row_index, row in enumerate(selected):
            target[row_index, index[str(row["universal_target_value"])]] = 1.0
        residual = np.sum(
            (target-baseline)*weights[:, None], axis=0)/(den+2.0)
        return [float(value) for value in residual]
    raise ValueError(f"unsupported target kind: {spec.kind}")


def _apply_state_residual(
    baseline: np.ndarray, residual: float | list[float],
    spec: UniversalTargetSpec,
) -> np.ndarray:
    if spec.kind == "CONTINUOUS":
        return np.asarray(baseline, dtype=float)+float(residual)
    if spec.kind == "BINARY":
        return np.clip(
            np.asarray(baseline, dtype=float)+float(residual),
            MIN_PROBABILITY, 1.0-MIN_PROBABILITY)
    if spec.kind == "MULTICLASS":
        prediction = np.asarray(baseline, dtype=float)+np.asarray(
            residual, dtype=float)[None, :]
        prediction = np.maximum(prediction, MIN_PROBABILITY)
        return prediction/np.maximum(
            prediction.sum(axis=1, keepdims=True), 1e-12)
    raise ValueError(f"unsupported target kind: {spec.kind}")


def _output_prediction(value: np.ndarray, spec: UniversalTargetSpec) -> float | list[float]:
    array = np.asarray(value, dtype=float)
    if spec.kind == "MULTICLASS":
        if array.shape != (1, len(spec.classes)):
            raise ValueError("invalid frozen multiclass prediction shape")
        return [float(item) for item in array[0]]
    if array.shape != (1,):
        raise ValueError("invalid frozen scalar prediction shape")
    return float(array[0])


def build_structured_frozen_spec(
    candidate: dict[str, Any], rows: list[dict[str, Any]], spec: UniversalTargetSpec,
    *, source_set_sha256: str,
) -> dict[str, Any]:
    """Fit one final rule + structural baseline + state residual before OOS."""
    if not rows:
        raise ValueError("cannot freeze a candidate without historical rows")
    if candidate.get("target_id") != spec.target_id:
        raise ValueError("candidate target does not match frozen target spec")
    if int(candidate.get("horizon_minutes") or 0) <= 0:
        raise ValueError("candidate horizon is missing")
    if any(row.get("universal_target_id") != spec.target_id for row in rows):
        raise ValueError("frozen cohort contains a different target")

    template = _template_from_candidate(candidate)
    rule = fit_rule(template, rows)
    if rule is None:
        raise ValueError("candidate rule cannot be refit on final historical cohort")
    selected = [
        row for row, keep in zip(rows, rule_mask(rows, rule)) if bool(keep)]
    if not selected:
        raise ValueError("final historical rule selects no rows")

    baseline_model = fit_structural_baseline(rows, spec)
    frozen_baseline = serialize_structural_baseline(baseline_model)
    residual = _state_residual(selected, frozen_baseline, spec)
    training_cutoff_ts = max(float(row["target_ts"]) for row in rows)
    feature_cutoff_ts = max(float(row["captured_ts"]) for row in rows)
    if feature_cutoff_ts > training_cutoff_ts + 1e-6:
        raise ValueError("historical target chronology is malformed")

    return {
        "contract_version": FROZEN_STRUCTURED_SPEC_VERSION,
        "candidate_id": candidate.get("candidate_id"),
        "hypothesis_id": candidate.get("hypothesis_id"),
        "template_id": template.template_id,
        "model_family": "INTERPRETABLE_STRUCTURED_RULE",
        "target_id": spec.target_id,
        "target_family": spec.family,
        "target_kind": spec.kind,
        "target_classes": list(spec.classes),
        "primary_metrics": list(spec.primary_metrics),
        "horizon_minutes": int(candidate["horizon_minutes"]),
        "rule": rule.as_dict(),
        "conditions": [asdict(item) for item in rule.conditions],
        "structural_baseline": frozen_baseline,
        "state_residual": residual,
        "historical_raw_n": len(rows),
        "historical_selected_n": len(selected),
        "feature_cutoff_ts": feature_cutoff_ts,
        "training_cutoff_ts": training_cutoff_ts,
        "source_set_sha256": str(source_set_sha256),
        "evidence_label": "HISTORICAL_WALK_FORWARD",
        "prospective_evidence_counted": False,
        "production_authority": False,
        "auto_promotion": False,
    }


def _fitted_rule_from_spec(frozen_spec: dict[str, Any]) -> FittedRule:
    rule = frozen_spec.get("rule") or {}
    conditions = tuple(FittedCondition(
        feature_id=str(item["feature_id"]),
        kind=str(item["kind"]),
        state=str(item["state"]),
        lower=item.get("lower"),
        upper=item.get("upper"),
        train_cutoff_ts=item.get("train_cutoff_ts"),
    ) for item in rule.get("conditions") or [])
    if not conditions:
        raise ValueError("frozen structured spec has no rule conditions")
    return FittedRule(str(rule["template_id"]), conditions)


def predict_structured_frozen(
    frozen_spec: dict[str, Any], row: dict[str, Any],
) -> dict[str, Any]:
    if frozen_spec.get("contract_version") != FROZEN_STRUCTURED_SPEC_VERSION:
        raise ValueError("unsupported frozen structured candidate contract")
    spec = _target_spec_from_frozen(frozen_spec)
    rule = _fitted_rule_from_spec(frozen_spec)
    qualified = all(
        condition_matches(row, condition) for condition in rule.conditions)
    baseline = frozen_structural_baseline_prediction(
        frozen_spec["structural_baseline"], row, spec)
    candidate = _apply_state_residual(
        baseline, frozen_spec["state_residual"], spec)
    return {
        "qualified": bool(qualified),
        "candidate_prediction": _output_prediction(candidate, spec),
        "baseline_prediction": _output_prediction(baseline, spec),
        "target_id": frozen_spec["target_id"],
        "target_kind": frozen_spec["target_kind"],
        "horizon_minutes": frozen_spec["horizon_minutes"],
        "frozen_training_cutoff_ts": frozen_spec["training_cutoff_ts"],
        "production_authority": False,
        "auto_promotion": False,
    }
