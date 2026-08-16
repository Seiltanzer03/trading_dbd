from __future__ import annotations

import pytest

from seiltanzer.edge_discovery.candidate_registry import (
    CandidateRegistry,
    hypothesis_id,
    validation_scope_id,
)
from seiltanzer.edge_discovery.filters import CandidateTemplate, ConditionTemplate
from seiltanzer.edge_discovery.frozen_candidate import (
    build_structured_frozen_spec,
    predict_structured_frozen,
)
from seiltanzer.edge_discovery.ml_challenger import MODEL_FAMILY
from seiltanzer.edge_discovery.prospective_confirmation import (
    ProspectiveConfirmationLedger,
    record_registered_prediction,
)
from seiltanzer.edge_discovery.universal_target_scoring import UniversalTargetSpec


def _template(*, state: str = "ABOVE_MEDIAN") -> CandidateTemplate:
    return CandidateTemplate((ConditionTemplate(
        "market.x1", "train_relative", state),))


def _candidate(candidate_id: str, *, state: str = "ABOVE_MEDIAN") -> dict:
    template = _template(state=state)
    return {
        "candidate_id": candidate_id,
        "template_id": template.template_id,
        "conditions": [{
            "feature_id": "market.x1",
            "kind": "train_relative",
            "state": state,
        }],
        "target_id": "RETURN_SIGMA",
        "target_family": "RETURN",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 30,
        "status": "HISTORICAL_CANDIDATE",
        "model": {"mae": 0.2, "rmse": 0.3},
        "improvement": {"mae": 0.2, "rmse": 0.2},
        "p_value": 0.01,
        "q_value": 0.03,
        "production_authority": False,
        "auto_promotion": False,
    }


def _ml_candidate(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "target_id": "RETURN_SIGMA",
        "target_family": "RETURN",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 30,
        "model_family": MODEL_FAMILY,
        "model_library_version": "test",
        "status": "HISTORICAL_CANDIDATE",
        "model": {"mae": 0.1, "rmse": 0.2},
        "improvement": {"mae": 0.1, "rmse": 0.1},
        "p_value": 0.02,
        "q_value": 0.04,
        "production_authority": False,
        "auto_promotion": False,
    }


def _rows() -> list[dict]:
    rows = []
    timestamp = 1000.0
    for instrument, base in (("USDCAD", 1.0), ("NAS100", -1.0)):
        for index in range(60):
            high = index >= 30
            rows.append({
                "instrument": instrument,
                "captured_ts": timestamp,
                "target_ts": timestamp+1800.0,
                "horizon_minutes": 30,
                "ede_features": {"market.x1": float(index)},
                "universal_target_id": "RETURN_SIGMA",
                "universal_target_value": base+(1.0 if high else 0.0),
            })
            timestamp += 1800.0
    rows.sort(key=lambda item: float(item["captured_ts"]))
    return rows


def _spec() -> UniversalTargetSpec:
    return UniversalTargetSpec(
        "RETURN_SIGMA", "RETURN", "CONTINUOUS", (), ("mae", "rmse"))


def _register(registry: CandidateRegistry, candidate: dict) -> None:
    registry.register_evaluation(
        candidate,
        dataset_sha256="dataset-v1",
        research_run="accepted-historical-audit",
        measurement_contract="universal-outcomes-v2",
        created_ts=10.0,
    )


def test_legacy_hypothesis_id_is_unchanged_by_universal_extension() -> None:
    legacy = {
        "template_id": "LEGACY_T",
        "template": {"x": 1},
        "signal": "ret5",
        "horizon_minutes": 60,
    }
    assert hypothesis_id(legacy) == "ede-hypothesis-f7e7909d778f8440071125ad"


def test_universal_target_is_part_of_hypothesis_identity() -> None:
    left = _candidate("return")
    right = dict(left)
    right["candidate_id"] = "mfe"
    right["target_id"] = "MFE_SIGMA"
    assert hypothesis_id(left) != hypothesis_id(right)


def test_structured_freeze_preserves_dynamic_instrument_baseline() -> None:
    rows = _rows()
    frozen = build_structured_frozen_spec(
        _candidate("structured"), rows, _spec(), source_set_sha256="dataset-v1")
    assert frozen["historical_selected_n"] == 60
    assert set(frozen["structural_baseline"]["instrument_values"]) == {
        "NAS100", "USDCAD"}

    future_ts = float(frozen["training_cutoff_ts"])+3600.0
    usdcad = predict_structured_frozen(frozen, {
        "instrument": "USDCAD",
        "captured_ts": future_ts,
        "ede_features": {"market.x1": 59.0},
    })
    nas100 = predict_structured_frozen(frozen, {
        "instrument": "NAS100",
        "captured_ts": future_ts,
        "ede_features": {"market.x1": 59.0},
    })
    assert usdcad["qualified"] is True
    assert nas100["qualified"] is True
    assert usdcad["baseline_prediction"] == pytest.approx(1.5)
    assert nas100["baseline_prediction"] == pytest.approx(-0.5)
    assert usdcad["candidate_prediction"] == pytest.approx(2.0)
    assert nas100["candidate_prediction"] == pytest.approx(0.0)


def test_structured_champion_and_ml_challenger_share_validation_scope(tmp_path) -> None:
    registry = CandidateRegistry(tmp_path / "registry.jsonl")
    structured = _candidate("structured")
    ml = _ml_candidate("ml")
    _register(registry, structured)
    _register(registry, ml)
    assert validation_scope_id(registry.current("structured") or {}) == validation_scope_id(
        registry.current("ml") or {})

    rows = _rows()
    frozen = build_structured_frozen_spec(
        structured, rows, _spec(), source_set_sha256="dataset-v1")
    freeze_ts = float(frozen["training_cutoff_ts"])+60.0
    registry.freeze_for_validation(
        "structured",
        frozen_spec=frozen,
        training_cutoff_ts=float(frozen["training_cutoff_ts"]),
        frozen_at=freeze_ts,
        role="CHAMPION",
    )
    ml_frozen_stub = {
        "candidate_id": "ml",
        "target_id": "RETURN_SIGMA",
        "horizon_minutes": 30,
        "training_cutoff_ts": float(frozen["training_cutoff_ts"]),
        "source_set_sha256": "dataset-v1",
        "contract_version": "test-only-ml-freeze",
    }
    registry.freeze_for_validation(
        "ml",
        frozen_spec=ml_frozen_stub,
        training_cutoff_ts=float(frozen["training_cutoff_ts"]),
        frozen_at=freeze_ts+1.0,
        role="CHALLENGER",
    )
    scope_id = validation_scope_id(registry.current("structured") or {})
    active = registry.validation_candidates(scope_id=scope_id)
    assert [item["candidate_id"] for item in active] == ["structured", "ml"]
    assert active[0]["validation"]["role"] == "CHAMPION"
    assert active[1]["validation"]["role"] == "CHALLENGER"


def test_second_champion_in_same_target_horizon_is_refused(tmp_path) -> None:
    registry = CandidateRegistry(tmp_path / "registry.jsonl")
    first = _candidate("first", state="ABOVE_MEDIAN")
    second = _candidate("second", state="BELOW_MEDIAN")
    _register(registry, first)
    _register(registry, second)
    rows = _rows()
    first_frozen = build_structured_frozen_spec(
        first, rows, _spec(), source_set_sha256="dataset-v1")
    second_frozen = build_structured_frozen_spec(
        second, rows, _spec(), source_set_sha256="dataset-v1")
    freeze_ts = float(first_frozen["training_cutoff_ts"])+60.0
    registry.freeze_for_validation(
        "first", frozen_spec=first_frozen,
        training_cutoff_ts=float(first_frozen["training_cutoff_ts"]),
        frozen_at=freeze_ts, role="CHAMPION")
    with pytest.raises(ValueError, match="already has a frozen/live champion"):
        registry.freeze_for_validation(
            "second", frozen_spec=second_frozen,
            training_cutoff_ts=float(second_frozen["training_cutoff_ts"]),
            frozen_at=freeze_ts+1.0, role="CHAMPION")


def test_prediction_record_is_required_before_live_validation_transition(tmp_path) -> None:
    registry = CandidateRegistry(tmp_path / "registry.jsonl")
    ledger = ProspectiveConfirmationLedger(tmp_path / "prospective.jsonl")
    candidate = _candidate("candidate")
    _register(registry, candidate)
    rows = _rows()
    frozen = build_structured_frozen_spec(
        candidate, rows, _spec(), source_set_sha256="dataset-v1")
    freeze_ts = float(frozen["training_cutoff_ts"])+60.0
    registry.freeze_for_validation(
        "candidate", frozen_spec=frozen,
        training_cutoff_ts=float(frozen["training_cutoff_ts"]),
        frozen_at=freeze_ts, role="CHAMPION")
    assert (registry.current("candidate") or {})["status"] == "FROZEN_FOR_VALIDATION"

    t0 = freeze_ts+300.0
    target_ts = t0+1800.0
    record_id = record_registered_prediction(
        registry,
        ledger,
        candidate_id="candidate",
        instrument="USDCAD",
        t0=t0,
        target_ts=target_ts,
        prediction={"candidate": 2.0, "baseline": 1.5},
        qualified=True,
        feature_values={
            "market.x1": {
                "value": 59.0,
                "asof": t0,
                "available": True,
                "stale": False,
            }
        },
        recorded_ts=t0+1.0,
    )
    current = registry.current("candidate") or {}
    assert current["status"] == "LIVE_VALIDATING"
    assert current["first_prospective_record_id"] == record_id
    assert ledger.cohort_status(current["validation"]["validation_cohort_id"])[
        "raw_predictions"] == 1


def test_confirmation_refuses_pre_freeze_future_feature_and_early_outcome(tmp_path) -> None:
    registry = CandidateRegistry(tmp_path / "registry.jsonl")
    ledger = ProspectiveConfirmationLedger(tmp_path / "prospective.jsonl")
    candidate = _candidate("candidate")
    _register(registry, candidate)
    rows = _rows()
    frozen = build_structured_frozen_spec(
        candidate, rows, _spec(), source_set_sha256="dataset-v1")
    freeze_ts = float(frozen["training_cutoff_ts"])+60.0
    registry.freeze_for_validation(
        "candidate", frozen_spec=frozen,
        training_cutoff_ts=float(frozen["training_cutoff_ts"]),
        frozen_at=freeze_ts, role="CHAMPION")
    current = registry.current("candidate") or {}

    with pytest.raises(ValueError, match="strictly after"):
        ledger.record_prediction(
            candidate=current, instrument="NAS100", t0=freeze_ts,
            target_ts=freeze_ts+1800.0, prediction=0.0, qualified=True,
            feature_values={
                "market.x1": {
                    "value": 59.0, "asof": freeze_ts,
                    "available": True, "stale": False}},
            recorded_ts=freeze_ts)

    t0 = freeze_ts+300.0
    target_ts = t0+1800.0
    with pytest.raises(ValueError, match="future feature"):
        ledger.record_prediction(
            candidate=current, instrument="NAS100", t0=t0,
            target_ts=target_ts, prediction=0.0, qualified=True,
            feature_values={
                "market.x1": {
                    "value": 59.0, "asof": t0+1.0,
                    "available": True, "stale": False}},
            recorded_ts=t0+1.0)

    record_id = ledger.record_prediction(
        candidate=current, instrument="NAS100", t0=t0,
        target_ts=target_ts, prediction=0.0, qualified=True,
        feature_values={
            "market.x1": {
                "value": 59.0, "asof": t0,
                "available": True, "stale": False}},
        recorded_ts=t0+1.0)
    with pytest.raises(ValueError, match="before target"):
        ledger.resolve(record_id, outcome=0.1, observed_ts=target_ts-1.0)
    ledger.resolve(record_id, outcome=0.1, observed_ts=target_ts)
