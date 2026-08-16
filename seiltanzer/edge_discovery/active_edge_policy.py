"""Active high-risk edge policy for the manual trading terminal.

The canonical PASS 5/PASS 6 machinery remains causal, purged and dependency-aware,
but the *active* acceptance threshold is deliberately riskier than the original
research-grade exam.  The old strict threshold is retained only as a reference
flag in reports; it no longer blocks an early edge signal.

This is an explicit user-risk choice.  Active signals may enter the existing
research/prospective lifecycle, but still have no automatic trade execution,
no automatic promotion and no production authority.
"""
from __future__ import annotations

from typing import Any, Iterable

from .rates import RatesState


ACTIVE_EDGE_POLICY_VERSION = "g1s-manual-trader-high-risk-edge-policy-v1"
ACTIVE_EDGE_POLICY_NAME = "MANUAL_TRADER_HIGH_RISK"

# Former research-grade threshold, retained as a diagnostic reference only.
STRICT_REFERENCE = {
    "max_q_value": 0.10,
    "minimum_relative_improvement": 0.005,
    "minimum_positive_outer_folds": 3,
}

# Main active PASS 5 gate.  FDR remains measured and reported, but no longer
# blocks an otherwise tangible/repeated OOS effect.  We compensate by demanding
# a larger practical effect than the old 0.5% minimum.
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

# Main active PASS 6 gate.  The fixed shallow residual model itself is unchanged;
# only sample/coverage/evidence acceptance is made more opportunistic.
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


def _annotate_structured_report(report: dict[str, Any]) -> dict[str, Any]:
    # In the active high-risk policy, a daily Treasury candidate is allowed to be
    # treated as an early signal if it passed the same day-clustered OOS exam.
    # This accepts the extra dependency risk explicitly instead of pretending it
    # is research-grade proof.
    promoted_rates = 0
    active_signals = 0
    for horizon in report.get("horizons") or []:
        for target in horizon.get("targets") or []:
            for candidate in target.get("candidates") or []:
                if candidate.get("status") == "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING":
                    candidate["status"] = "DISCOVERY_SIGNAL"
                    candidate["rates_dependency_risk_accepted"] = True
                    candidate["rates_dependency_note"] = (
                        "High-risk manual-trader policy accepts the existing UTC-day clustered "
                        "OOS evidence as actionable research context; not strict proof."
                    )
                    promoted_rates += 1
                _annotate_candidate(candidate)
                if candidate.get("status") == "DISCOVERY_SIGNAL":
                    active_signals += 1
            target["discovery_signal_count"] = sum(
                item.get("status") == "DISCOVERY_SIGNAL"
                for item in target.get("candidates") or []
            )
            target["rates_dependency_pending_count"] = sum(
                item.get("status") == "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING"
                for item in target.get("candidates") or []
            )
    report["edge_policy"] = ACTIVE_EDGE_POLICY_VERSION
    report["edge_policy_name"] = ACTIVE_EDGE_POLICY_NAME
    report["risk_acceptance"] = "HIGH_FALSE_DISCOVERY_TOLERANCE"
    report["strict_reference"] = dict(STRICT_REFERENCE)
    report["active_gates"] = dict(STRUCTURED_ACTIVE_GATES)
    report["fdr_role"] = "DIAGNOSTIC_NOT_BLOCKING"
    report["strict_reference_is_blocking"] = False
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
    for candidate in report.get("candidates") or []:
        _annotate_candidate(candidate)
        if candidate.get("status") == "ML_DISCOVERY_SIGNAL":
            active_signals += 1
    report["edge_policy"] = ACTIVE_EDGE_POLICY_VERSION
    report["edge_policy_name"] = ACTIVE_EDGE_POLICY_NAME
    report["risk_acceptance"] = "HIGH_FALSE_DISCOVERY_TOLERANCE"
    report["strict_reference"] = dict(STRICT_REFERENCE)
    report["active_gates"] = dict(ML_ACTIVE_GATES)
    report["fdr_role"] = "DIAGNOSTIC_NOT_BLOCKING"
    report["strict_reference_is_blocking"] = False
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
    """Run PASS 5 with the active manual-trader high-risk gate."""
    from . import universal_structured_discovery as structured

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
    structured.MIN_INNER_CLASS = int(STRUCTURED_ACTIVE_GATES["minimum_inner_class"])
    structured.MIN_OUTER_TEST_RAW = int(
        STRUCTURED_ACTIVE_GATES["minimum_outer_test_raw"])

    report = structured.run_universal_structured_discovery(
        sources,
        source_set_sha256=source_set_sha256,
        rates_states=rates_states,
        horizons=horizons,
    )
    return _annotate_structured_report(report)


def run_active_ml_challenger(
    sources: list[dict[str, Any]], *, source_set_sha256: str,
    rates_states: Iterable[RatesState] = (),
) -> dict[str, Any]:
    """Run PASS 6 with the active manual-trader high-risk gate."""
    from . import ml_challenger as ml

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
    return _annotate_ml_report(report)
