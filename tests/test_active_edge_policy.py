from __future__ import annotations

from seiltanzer.edge_discovery import ml_challenger as ml
from seiltanzer.edge_discovery import universal_structured_discovery as structured
from seiltanzer.edge_discovery.active_edge_policy import (
    ACTIVE_EDGE_POLICY_VERSION,
    ML_ACTIVE_GATES,
    STRUCTURED_ACTIVE_GATES,
    STRICT_REFERENCE,
    run_active_ml_challenger,
    run_active_structured_discovery,
)
from seiltanzer.edge_discovery.candidate_admission import admit_discovery_candidate


def test_active_structured_policy_replaces_strict_gate_but_keeps_reference(monkeypatch):
    original = {
        "MAX_Q_VALUE": structured.MAX_Q_VALUE,
        "MIN_RELATIVE_IMPROVEMENT": structured.MIN_RELATIVE_IMPROVEMENT,
        "MIN_STABLE_FOLDS": structured.MIN_STABLE_FOLDS,
        "OUTER_SELECTION_LIMIT": structured.OUTER_SELECTION_LIMIT,
        "MIN_INNER_TRAIN_RAW": structured.MIN_INNER_TRAIN_RAW,
        "MIN_INNER_TRAIN_EFFECTIVE": structured.MIN_INNER_TRAIN_EFFECTIVE,
        "MIN_INNER_VALIDATION_RAW": structured.MIN_INNER_VALIDATION_RAW,
        "MIN_INNER_VALIDATION_EFFECTIVE": structured.MIN_INNER_VALIDATION_EFFECTIVE,
        "MIN_INNER_CLASS": structured.MIN_INNER_CLASS,
        "MIN_OUTER_TEST_RAW": structured.MIN_OUTER_TEST_RAW,
    }
    candidate = {
        "candidate_id": "candidate-risky",
        "target_id": "outcome.return_30m",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 30,
        "status": "DISCOVERY_SIGNAL",
        "q_value": 0.91,
        "primary_improvement": 0.018,
        "fold_positive": 2,
        "production_authority": False,
        "auto_promotion": False,
    }
    fake_report = {
        "horizons": [{
            "targets": [{
                "candidates": [candidate],
                "discovery_signal_count": 1,
                "rates_dependency_pending_count": 0,
            }]
        }],
        "discovery_signal_count": 1,
        "verdict": "ignored",
    }
    monkeypatch.setattr(
        structured, "run_universal_structured_discovery",
        lambda *args, **kwargs: fake_report,
    )
    try:
        report = run_active_structured_discovery(
            [], source_set_sha256="sha", rates_states=(), horizons=(30,))
        assert structured.MAX_Q_VALUE == 1.0
        assert structured.MIN_RELATIVE_IMPROVEMENT == 0.010
        assert structured.MIN_STABLE_FOLDS == 2
        assert structured.OUTER_SELECTION_LIMIT == 24
        assert structured.MIN_INNER_TRAIN_RAW == 60
        assert structured.MIN_INNER_TRAIN_EFFECTIVE == 30
        assert structured.MIN_INNER_VALIDATION_RAW == 15
        assert structured.MIN_INNER_VALIDATION_EFFECTIVE == 8
        assert structured.MIN_INNER_CLASS == 3
        assert structured.MIN_OUTER_TEST_RAW == 15
        assert report["edge_policy"] == ACTIVE_EDGE_POLICY_VERSION
        assert report["fdr_role"] == "DIAGNOSTIC_NOT_BLOCKING"
        assert report["strict_reference_is_blocking"] is False
        assert report["discovery_signal_count"] == 1
        out = report["horizons"][0]["targets"][0]["candidates"][0]
        assert out["active_policy_qualified"] is True
        assert out["strict_reference_qualified"] is False
        assert out["q_value"] > STRICT_REFERENCE["max_q_value"]
        assert out["risk_acceptance"] == "HIGH_FALSE_DISCOVERY_TOLERANCE"
    finally:
        for name, value in original.items():
            setattr(structured, name, value)


def test_active_ml_policy_relaxes_sample_coverage_and_fdr(monkeypatch):
    original = {
        "MAX_Q_VALUE": ml.MAX_Q_VALUE,
        "MIN_RELATIVE_IMPROVEMENT": ml.MIN_RELATIVE_IMPROVEMENT,
        "MIN_POSITIVE_FOLDS": ml.MIN_POSITIVE_FOLDS,
        "MIN_TRAIN_RAW": ml.MIN_TRAIN_RAW,
        "MIN_TRAIN_EFFECTIVE": ml.MIN_TRAIN_EFFECTIVE,
        "MIN_TEST_RAW": ml.MIN_TEST_RAW,
        "MIN_TEST_EFFECTIVE": ml.MIN_TEST_EFFECTIVE,
        "MIN_FEATURE_COVERAGE": ml.MIN_FEATURE_COVERAGE,
        "MAX_FEATURES": ml.MAX_FEATURES,
    }
    candidate = {
        "candidate_id": "ml-risky",
        "target_id": "outcome.mae_60m",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 60,
        "status": "ML_DISCOVERY_SIGNAL",
        "q_value": 0.77,
        "primary_improvement": 0.021,
        "fold_positive": 2,
        "production_authority": False,
        "auto_promotion": False,
    }
    fake_report = {
        "candidates": [candidate],
        "discovery_signal_count": 1,
        "verdict": "ignored",
    }
    monkeypatch.setattr(
        ml, "run_ml_challenger", lambda *args, **kwargs: fake_report)
    try:
        report = run_active_ml_challenger([], source_set_sha256="sha")
        assert ml.MAX_Q_VALUE == 1.0
        assert ml.MIN_RELATIVE_IMPROVEMENT == 0.010
        assert ml.MIN_POSITIVE_FOLDS == 2
        assert ml.MIN_TRAIN_RAW == 300
        assert ml.MIN_TRAIN_EFFECTIVE == 120
        assert ml.MIN_TEST_RAW == 60
        assert ml.MIN_TEST_EFFECTIVE == 25
        assert ml.MIN_FEATURE_COVERAGE == 0.50
        assert ml.MAX_FEATURES == 48
        assert report["edge_policy"] == ACTIVE_EDGE_POLICY_VERSION
        assert report["discovery_signal_count"] == 1
        assert report["candidates"][0]["strict_reference_qualified"] is False
    finally:
        for name, value in original.items():
            setattr(ml, name, value)


def test_active_signal_is_admissible_to_existing_prospective_lifecycle():
    candidate = {
        "candidate_id": "g1s-universal-risky",
        "target_id": "outcome.return_30m",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 30,
        "status": "DISCOVERY_SIGNAL",
        "contract_version": "g1s-universal-structured-discovery-v1",
        "edge_policy": ACTIVE_EDGE_POLICY_VERSION,
        "risk_acceptance": "HIGH_FALSE_DISCOVERY_TOLERANCE",
        "strict_reference_qualified": False,
        "production_authority": False,
        "auto_promotion": False,
    }
    admitted = admit_discovery_candidate(candidate)
    assert admitted["status"] == "HISTORICAL_CANDIDATE"
    assert admitted["edge_policy"] == ACTIVE_EDGE_POLICY_VERSION
    assert admitted["risk_acceptance"] == "HIGH_FALSE_DISCOVERY_TOLERANCE"
    assert admitted["strict_reference_qualified"] is False
    assert admitted["prospective_confirmation"] is False
    assert admitted["production_authority"] is False


def test_active_policy_is_materially_less_strict_than_reference():
    assert STRUCTURED_ACTIVE_GATES["max_q_value"] > STRICT_REFERENCE["max_q_value"]
    assert ML_ACTIVE_GATES["max_q_value"] > STRICT_REFERENCE["max_q_value"]
    assert STRUCTURED_ACTIVE_GATES["minimum_positive_outer_folds"] < STRICT_REFERENCE[
        "minimum_positive_outer_folds"]
    assert ML_ACTIVE_GATES["minimum_positive_folds"] < STRICT_REFERENCE[
        "minimum_positive_outer_folds"]
    # We intentionally ask for a more tangible effect while tolerating much
    # weaker statistical confidence.
    assert STRUCTURED_ACTIVE_GATES["minimum_relative_improvement"] > STRICT_REFERENCE[
        "minimum_relative_improvement"]
    assert ML_ACTIVE_GATES["minimum_relative_improvement"] > STRICT_REFERENCE[
        "minimum_relative_improvement"]
