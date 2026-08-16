"""Main high-risk edge policy for the user's manual trading terminal.

The PASS 5/PASS 6 machinery remains causal, purged, structural-baseline-aware and
dependency-aware.  What changes is the active evidence threshold: the terminal
accepts materially weaker statistical confidence in exchange for a tangible OOS
effect and repeated positive folds.  The old research-grade threshold remains
visible only as reference metadata.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _weights

from .rates import RatesState


ACTIVE_EDGE_POLICY_VERSION = "g1s-manual-trader-high-risk-edge-policy-v1"
ACTIVE_EDGE_POLICY_NAME = "MANUAL_TRADER_HIGH_RISK"

STRICT_REFERENCE = {
    "max_q_value": 0.10,
    "minimum_relative_improvement": 0.005,
    "minimum_positive_outer_folds": 3,
}

STRUCTURED_ACTIVE_GATES = {
    "max_q_value": 1.0,
    "minimum_relative_improvement": 0.010,
    "minimum_positive_outer_folds": 2,
    "outer_selection_limit": 24,
    "minimum_inner_train_raw": 60,
    "minimum_inner_train_effective": 30,
    "minimum_inner_validation_raw": 15,
    "minimum_inner_validation_effective": 8,
    "minimum_inner_class": 3,
    "minimum_outer_test_raw": 15,
}

ML_ACTIVE_GATES = {
    "max_q_value": 1.0,
    "minimum_relative_improvement": 0.010,
    "minimum_positive_folds": 2,
    "minimum_train_raw": 300,
    "minimum_train_effective": 120,
    "minimum_test_raw": 60,
    "minimum_test_effective": 25,
    "minimum_feature_coverage": 0.50,
    "maximum_features": 48,
}


def _strict_reference_qualified(candidate: dict[str, Any]) -> bool:
    try:
        return (
            float(candidate.get("q_value", 1.0)) <= STRICT_REFERENCE["max_q_value"]
            and float(candidate.get("primary_improvement", 0.0))
            >= STRICT_REFERENCE["minimum_relative_improvement"]
            and int(candidate.get("fold_positive", 0))
            >= STRICT_REFERENCE["minimum_positive_outer_folds"]
        )
    except (TypeError, ValueError):
        return False


def _annotate_candidate(candidate: dict[str, Any]) -> None:
    candidate["edge_policy"] = ACTIVE_EDGE_POLICY_VERSION
    candidate["risk_acceptance"] = "HIGH_FALSE_DISCOVERY_TOLERANCE"
    candidate["strict_reference_qualified"] = _strict_reference_qualified(candidate)
    candidate["strict_reference"] = dict(STRICT_REFERENCE)
    candidate["active_policy_qualified"] = str(candidate.get("status") or "") in {
        "DISCOVERY_SIGNAL", "ML_DISCOVERY_SIGNAL"
    }
    candidate["production_authority"] = False
    candidate["auto_promotion"] = False


def _structured_prediction_shift(
    occurrences: list[dict[str, Any]], spec: Any,
) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    model_parts: list[np.ndarray] = []
    baseline_parts: list[np.ndarray] = []
    for occurrence in occurrences:
        evaluation = occurrence.get("evaluation") or {}
        model_prediction = evaluation.get("model_prediction")
        baseline_prediction = evaluation.get("baseline_prediction")
        evaluation_rows = evaluation.get("rows") or []
        if model_prediction is None or baseline_prediction is None or not evaluation_rows:
            continue
        rows.extend(evaluation_rows)
        model_parts.append(np.asarray(model_prediction, dtype=float))
        baseline_parts.append(np.asarray(baseline_prediction, dtype=float))
    if not rows or not model_parts or len(model_parts) != len(baseline_parts):
        return None

    model = np.concatenate(model_parts, axis=0)
    baseline = np.concatenate(baseline_parts, axis=0)
    weights, _effective = _weights(rows)
    denominator = max(float(np.sum(weights)), 1e-12)

    if str(spec.kind) == "MULTICLASS":
        weighted = np.sum((model-baseline)*weights[:, None], axis=0)/denominator
        classes = {
            str(name): float(value)
            for name, value in zip(tuple(spec.classes), weighted)
        }
        strongest = max(classes, key=lambda name: abs(classes[name])) if classes else None
        return {
            "kind": "MULTICLASS_PROBABILITY_SHIFT",
            "classes": classes,
            "strongest_class": strongest,
            "strongest_shift": classes.get(strongest) if strongest else None,
        }

    weighted = float(np.sum(
        np.asarray(model-baseline, dtype=float).reshape(-1)*weights
    )/denominator)
    target_id = str(spec.target_id)
    if target_id == "DIRECTION":
        interpretation = "MORE_UP" if weighted > 0 else "MORE_DOWN" if weighted < 0 else "NEUTRAL"
        unit = "probability"
    elif target_id == "RETURN_SIGMA":
        interpretation = "MORE_UPSIDE_RETURN" if weighted > 0 else "MORE_DOWNSIDE_RETURN" if weighted < 0 else "NEUTRAL"
        unit = "sigma"
    elif target_id == "MFE_SIGMA":
        interpretation = "MORE_UPSIDE_EXCURSION" if weighted > 0 else "LESS_UPSIDE_EXCURSION" if weighted < 0 else "NEUTRAL"
        unit = "sigma"
    elif target_id == "MAE_SIGMA":
        # MAE_SIGMA is signed (normally <=0): a positive shift is less adverse.
        interpretation = "LESS_DOWNSIDE_EXCURSION" if weighted > 0 else "MORE_DOWNSIDE_EXCURSION" if weighted < 0 else "NEUTRAL"
        unit = "sigma"
    elif target_id == "FORWARD_VOL_RATIO":
        interpretation = "VOL_EXPANSION" if weighted > 0 else "VOL_COMPRESSION" if weighted < 0 else "NEUTRAL"
        unit = "ratio"
    else:
        interpretation = "POSITIVE_SHIFT" if weighted > 0 else "NEGATIVE_SHIFT" if weighted < 0 else "NEUTRAL"
        unit = "target"
    return {
        "kind": "SCALAR_TARGET_SHIFT",
        "candidate_minus_structural_baseline": weighted,
        "interpretation": interpretation,
        "unit": unit,
    }


def _annotate_structured_report(report: dict[str, Any]) -> dict[str, Any]:
    base_signal_count = int(report.get("discovery_signal_count") or 0)
    promoted_rates = 0
    strict_reference_visible = 0
    for horizon in report.get("horizons") or []:
        for target in horizon.get("targets") or []:
            for candidate in target.get("candidates") or []:
                if candidate.get("status") == "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING":
                    candidate["status"] = "DISCOVERY_SIGNAL"
                    candidate["rates_dependency_risk_accepted"] = True
                    candidate["rates_dependency_note"] = (
                        "High-risk manual-trader policy accepts the existing UTC-day clustered "
                        "OOS evidence as actionable context; this is not strict proof."
                    )
                    promoted_rates += 1
                _annotate_candidate(candidate)
                strict_reference_visible += bool(candidate["strict_reference_qualified"])
            target["discovery_signal_count"] = sum(
                item.get("status") == "DISCOVERY_SIGNAL"
                for item in target.get("candidates") or []
            )
            target["rates_dependency_pending_count"] = sum(
                item.get("status") == "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING"
                for item in target.get("candidates") or []
            )

    active_signals = base_signal_count + promoted_rates
    report["edge_policy"] = ACTIVE_EDGE_POLICY_VERSION
    report["edge_policy_name"] = ACTIVE_EDGE_POLICY_NAME
    report["risk_acceptance"] = "HIGH_FALSE_DISCOVERY_TOLERANCE"
    report["strict_reference"] = dict(STRICT_REFERENCE)
    report["active_gates"] = dict(STRUCTURED_ACTIVE_GATES)
    report["fdr_role"] = "DIAGNOSTIC_NOT_BLOCKING"
    report["strict_reference_is_blocking"] = False
    report["strict_reference_qualified_visible_count"] = int(strict_reference_visible)
    report["rates_dependency_risk_accepted"] = promoted_rates > 0
    report["rates_promoted_under_active_policy"] = promoted_rates
    report["discovery_signal_count"] = active_signals
    report["verdict"] = (
        "ACTIVE_HIGH_RISK_DISCOVERY_SIGNALS_FOUND"
        if active_signals else "NO_ACTIVE_HIGH_RISK_DISCOVERY_SIGNAL_ON_CURRENT_EVIDENCE"
    )
    return report


def _annotate_ml_report(report: dict[str, Any]) -> dict[str, Any]:
    active_signals = 0
    strict_reference = 0
    for candidate in report.get("candidates") or []:
        _annotate_candidate(candidate)
        candidate["active_context_role"] = "NONLINEAR_TARGET_CONFIRMATION"
        if candidate.get("status") == "ML_DISCOVERY_SIGNAL":
            active_signals += 1
        strict_reference += bool(candidate["strict_reference_qualified"])
    report["edge_policy"] = ACTIVE_EDGE_POLICY_VERSION
    report["edge_policy_name"] = ACTIVE_EDGE_POLICY_NAME
    report["risk_acceptance"] = "HIGH_FALSE_DISCOVERY_TOLERANCE"
    report["strict_reference"] = dict(STRICT_REFERENCE)
    report["active_gates"] = dict(ML_ACTIVE_GATES)
    report["fdr_role"] = "DIAGNOSTIC_NOT_BLOCKING"
    report["strict_reference_is_blocking"] = False
    report["strict_reference_qualified_count"] = int(strict_reference)
    report["discovery_signal_count"] = active_signals
    report["verdict"] = (
        "ACTIVE_HIGH_RISK_ML_SIGNALS_FOUND"
        if active_signals else "NO_ACTIVE_HIGH_RISK_ML_SIGNAL_ON_CURRENT_EVIDENCE"
    )
    return report


def run_active_structured_discovery(
    sources: list[dict[str, Any]], *, source_set_sha256: str,
    rates_states: Iterable[RatesState] = (), horizons: Iterable[int],
) -> dict[str, Any]:
    """Run PASS 5 with the active high-risk gate, scoped to this call."""
    from . import universal_structured_discovery as structured

    names = (
        "MAX_Q_VALUE", "MIN_RELATIVE_IMPROVEMENT", "MIN_STABLE_FOLDS",
        "OUTER_SELECTION_LIMIT", "MIN_INNER_TRAIN_RAW", "MIN_INNER_TRAIN_EFFECTIVE",
        "MIN_INNER_VALIDATION_RAW", "MIN_INNER_VALIDATION_EFFECTIVE",
        "MIN_INNER_CLASS", "MIN_OUTER_TEST_RAW",
    )
    original_values = {name: getattr(structured, name) for name in names}
    original_aggregate = structured._aggregate_candidate

    def aggregate_with_shift(template_id, occurrences, spec, *, horizon):
        candidate = original_aggregate(template_id, occurrences, spec, horizon=horizon)
        shift = _structured_prediction_shift(occurrences, spec)
        if shift is not None:
            candidate["prediction_shift"] = shift
        return candidate

    try:
        structured.MAX_Q_VALUE = float(STRUCTURED_ACTIVE_GATES["max_q_value"])
        structured.MIN_RELATIVE_IMPROVEMENT = float(
            STRUCTURED_ACTIVE_GATES["minimum_relative_improvement"])
        structured.MIN_STABLE_FOLDS = int(
            STRUCTURED_ACTIVE_GATES["minimum_positive_outer_folds"])
        structured.OUTER_SELECTION_LIMIT = int(
            STRUCTURED_ACTIVE_GATES["outer_selection_limit"])
        structured.MIN_INNER_TRAIN_RAW = int(
            STRUCTURED_ACTIVE_GATES["minimum_inner_train_raw"])
        structured.MIN_INNER_TRAIN_EFFECTIVE = int(
            STRUCTURED_ACTIVE_GATES["minimum_inner_train_effective"])
        structured.MIN_INNER_VALIDATION_RAW = int(
            STRUCTURED_ACTIVE_GATES["minimum_inner_validation_raw"])
        structured.MIN_INNER_VALIDATION_EFFECTIVE = int(
            STRUCTURED_ACTIVE_GATES["minimum_inner_validation_effective"])
        structured.MIN_INNER_CLASS = int(
            STRUCTURED_ACTIVE_GATES["minimum_inner_class"])
        structured.MIN_OUTER_TEST_RAW = int(
            STRUCTURED_ACTIVE_GATES["minimum_outer_test_raw"])
        structured._aggregate_candidate = aggregate_with_shift
        report = structured.run_universal_structured_discovery(
            sources,
            source_set_sha256=source_set_sha256,
            rates_states=rates_states,
            horizons=horizons,
        )
    finally:
        structured._aggregate_candidate = original_aggregate
        for name, value in original_values.items():
            setattr(structured, name, value)
    return _annotate_structured_report(report)


def run_active_ml_challenger(
    sources: list[dict[str, Any]], *, source_set_sha256: str,
    rates_states: Iterable[RatesState] = (),
) -> dict[str, Any]:
    """Run PASS 6 with the active high-risk gate, scoped to this call."""
    from . import ml_challenger as ml

    names = (
        "MAX_Q_VALUE", "MIN_RELATIVE_IMPROVEMENT", "MIN_POSITIVE_FOLDS",
        "MIN_TRAIN_RAW", "MIN_TRAIN_EFFECTIVE", "MIN_TEST_RAW",
        "MIN_TEST_EFFECTIVE", "MIN_FEATURE_COVERAGE", "MAX_FEATURES",
    )
    original_values = {name: getattr(ml, name) for name in names}
    try:
        ml.MAX_Q_VALUE = float(ML_ACTIVE_GATES["max_q_value"])
        ml.MIN_RELATIVE_IMPROVEMENT = float(
            ML_ACTIVE_GATES["minimum_relative_improvement"])
        ml.MIN_POSITIVE_FOLDS = int(ML_ACTIVE_GATES["minimum_positive_folds"])
        ml.MIN_TRAIN_RAW = int(ML_ACTIVE_GATES["minimum_train_raw"])
        ml.MIN_TRAIN_EFFECTIVE = int(ML_ACTIVE_GATES["minimum_train_effective"])
        ml.MIN_TEST_RAW = int(ML_ACTIVE_GATES["minimum_test_raw"])
        ml.MIN_TEST_EFFECTIVE = int(ML_ACTIVE_GATES["minimum_test_effective"])
        ml.MIN_FEATURE_COVERAGE = float(ML_ACTIVE_GATES["minimum_feature_coverage"])
        ml.MAX_FEATURES = int(ML_ACTIVE_GATES["maximum_features"])
        report = ml.run_ml_challenger(
            sources, source_set_sha256=source_set_sha256, rates_states=rates_states)
    finally:
        for name, value in original_values.items():
            setattr(ml, name, value)
    return _annotate_ml_report(report)
