from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

import pytest

import seiltanzer.llm_edge_prospective_evaluation as evaluation
import seiltanzer.llm_validated_active_edge_bridge as bridge


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row


class Registry:
    def __init__(self, state):
        self.state = dict(state)
        self.transitions = []

    def events(self):
        return [{
            "event": "EVALUATION_RECORDED",
            "evaluation": {"candidate_id": self.state["candidate_id"]},
        }]

    def current(self, candidate_id):
        return dict(self.state) if candidate_id == self.state["candidate_id"] else None

    def transition(self, candidate_id, to_status, **kwargs):
        assert candidate_id == self.state["candidate_id"]
        self.transitions.append((to_status, kwargs))
        self.state["status"] = to_status
        if kwargs.get("validation") is not None:
            self.state["validation"] = kwargs["validation"]


def _state(status="LIVE_VALIDATING"):
    condition = {
        "feature_id": "vol.rv15_over_rv60",
        "kind": "train_relative",
        "state": "ABOVE_MEDIAN",
        "lower": 1.1,
        "upper": 1.1,
    }
    return {
        "candidate_id": "llm-edge-candidate-test",
        "hypothesis_id": "hyp-test",
        "status": status,
        "target_id": "DIRECTION",
        "target_family": "DIRECTION",
        "target_kind": "BINARY",
        "horizon_minutes": 60,
        "validation": {
            "frozen_at": 1000.0,
            "training_cutoff_ts": 900.0,
            "evidence_label": "LIVE_PROSPECTIVE_OOS",
            "production_authority": False,
            "auto_promotion": False,
            "frozen_spec_sha256": "s" * 64,
            "frozen_spec": {
                "prospective_epoch_id": "epoch-1",
                "target_id": "DIRECTION",
                "target_family": "DIRECTION",
                "target_kind": "BINARY",
                "target_classes": ["DOWN", "UP"],
                "primary_metrics": ["brier", "logloss"],
                "horizon_minutes": 60,
                "state_residual": 0.20,
                "rule_sha256": "r" * 64,
                "conditions": [condition],
                "rule": {"conditions": [condition]},
                "discovery_q_value": 0.05,
                "discovery_effect": 0.08,
            },
        },
    }


def _eval(checkpoint_n, *, dependency_n=3):
    return {
        "raw_n": checkpoint_n,
        "effective_n": checkpoint_n,
        "dependency_cohort_even_n": dependency_n,
        "dependency_cohort_odd_n": dependency_n,
        "model": {
            "brier": 0.10, "logloss": 0.20,
            "raw_n": checkpoint_n, "effective_n": checkpoint_n,
        },
        "baseline": {
            "brier": 0.15, "logloss": 0.30,
            "raw_n": checkpoint_n, "effective_n": checkpoint_n,
        },
        "improvement": {"brier": 1/3, "logloss": 1/3},
        "primary_improvement": 1/3,
        "p_value": 0.001,
        "sample_sha256": f"{checkpoint_n:064x}"[-64:],
    }


def _engine(runtime):
    return SimpleNamespace(
        short_horizon=runtime,
        settings=SimpleNamespace(ede_context_max_age_sec=60.0),
    )


def _insert_checkpoint(runtime, *, decision="PASS", checkpoint_n=24):
    evaluation.initialize_evaluation_storage(runtime)
    runtime._conn.execute(
        """INSERT INTO llm_edge_candidate_checkpoints(
             candidate_id,checkpoint_n,prospective_epoch_id,target_id,horizon_minutes,
             family_sha256,sample_sha256,raw_n,effective_n,
             dependency_cohort_even_n,dependency_cohort_odd_n,
             model_json,baseline_json,improvement_json,primary_improvement,
             p_value,q_value,q_value_max,decision,evaluated_ts,contract_version
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "llm-edge-candidate-test", checkpoint_n, "epoch-1", "DIRECTION", 60,
            "f" * 64, "s" * 64, checkpoint_n, checkpoint_n, 3, 3,
            "{}", "{}", "{}", 0.10, 0.001, 0.001,
            evaluation.LOOK_ADJUSTED_Q_MAX, decision, 2000.0,
            evaluation.CONTRACT_VERSION,
        ),
    )
    runtime._conn.commit()


def test_underpowered_candidate_cannot_reach_active_edge(monkeypatch):
    runtime = Runtime()
    registry = Registry(_state())
    engine = _engine(runtime)
    monkeypatch.setattr(evaluation, "registry_for_engine", lambda engine: registry)
    monkeypatch.setattr(evaluation, "_resolved_samples", lambda *args: [{}] * 23)

    result = evaluation.evaluate_and_promote(engine, now=2000.0)

    assert result["checkpoints_inserted"] == 0
    assert result["promotions_inserted"] == 0
    assert registry.state["status"] == "LIVE_VALIDATING"
    assert evaluation.active_promotions(engine) == []


def test_pass_checkpoint_validates_then_promotes_exactly_once(monkeypatch):
    runtime = Runtime()
    registry = Registry(_state())
    engine = _engine(runtime)
    monkeypatch.setattr(evaluation, "registry_for_engine", lambda engine: registry)
    monkeypatch.setattr(evaluation, "_resolved_samples", lambda *args: [{}] * 24)
    monkeypatch.setattr(
        evaluation, "_evaluate_sample",
        lambda rows, candidate, checkpoint_n: _eval(checkpoint_n),
    )
    monkeypatch.setattr(
        evaluation, "_fdr_for_candidate",
        lambda *args, **kwargs: (0.001, "f" * 64),
    )

    first = evaluation.evaluate_and_promote(engine, now=2000.0)
    second = evaluation.evaluate_and_promote(engine, now=2001.0)

    assert first["transitions"][0]["to_status"] == "VALIDATED"
    assert registry.state["status"] == "VALIDATED"
    assert first["promotions_inserted"] == 1
    assert second["promotions_inserted"] == 0
    promotions = evaluation.active_promotions(engine)
    assert len(promotions) == 1
    assert promotions[0]["prospective_validated"] is True
    assert promotions[0]["promotion_basis"] == "VALIDATED_LIVE_PROSPECTIVE_OOS"


def test_final_exam_waits_for_independent_days_instead_of_false_failure(monkeypatch):
    runtime = Runtime()
    registry = Registry(_state())
    engine = _engine(runtime)
    monkeypatch.setattr(evaluation, "registry_for_engine", lambda engine: registry)
    monkeypatch.setattr(evaluation, "_resolved_samples", lambda *args: [{}] * 96)
    monkeypatch.setattr(
        evaluation, "_evaluate_sample",
        lambda rows, candidate, checkpoint_n: _eval(
            checkpoint_n, dependency_n=(1 if checkpoint_n == 96 else 3)
        ),
    )
    monkeypatch.setattr(
        evaluation, "_fdr_for_candidate",
        lambda *args, **kwargs: (0.50, "f" * 64),
    )

    result = evaluation.evaluate_and_promote(engine, now=2000.0)

    assert result["awaiting_independent_days"] == 1
    assert registry.state["status"] == "LIVE_VALIDATING"
    assert evaluation._checkpoint_row(runtime, registry.state["candidate_id"], 96) is None
    assert evaluation.active_promotions(engine) == []


def test_final_checkpoint_failure_never_promotes(monkeypatch):
    runtime = Runtime()
    registry = Registry(_state())
    engine = _engine(runtime)
    monkeypatch.setattr(evaluation, "registry_for_engine", lambda engine: registry)
    monkeypatch.setattr(evaluation, "_resolved_samples", lambda *args: [{}] * 96)
    monkeypatch.setattr(
        evaluation, "_evaluate_sample",
        lambda rows, candidate, checkpoint_n: _eval(checkpoint_n),
    )
    monkeypatch.setattr(
        evaluation, "_fdr_for_candidate",
        lambda *args, **kwargs: (0.50, "f" * 64),
    )

    result = evaluation.evaluate_and_promote(engine, now=2000.0)

    assert [row["to_status"] for row in result["transitions"]] == ["FAILED_LIVE"]
    assert registry.state["status"] == "FAILED_LIVE"
    assert result["promotions_inserted"] == 0
    assert evaluation.active_promotions(engine) == []


def test_restart_recovers_pass_checkpoint_before_registry_transition(monkeypatch):
    runtime = Runtime()
    _insert_checkpoint(runtime, decision="PASS", checkpoint_n=24)
    registry = Registry(_state())
    engine = _engine(runtime)
    monkeypatch.setattr(evaluation, "registry_for_engine", lambda engine: registry)
    monkeypatch.setattr(evaluation, "_resolved_samples", lambda *args: [])

    result = evaluation.evaluate_and_promote(engine, now=2001.0)

    assert result["transitions"] == [{
        "candidate_id": "llm-edge-candidate-test",
        "checkpoint_n": 24,
        "to_status": "VALIDATED",
        "recovered_from_persisted_checkpoint": True,
    }]
    assert result["promotions_inserted"] == 1
    assert registry.state["status"] == "VALIDATED"


def test_checkpoint_storage_is_immutable():
    runtime = Runtime()
    _insert_checkpoint(runtime)
    with pytest.raises(sqlite3.IntegrityError):
        runtime._conn.execute(
            "UPDATE llm_edge_candidate_checkpoints SET decision='FAIL' "
            "WHERE candidate_id='llm-edge-candidate-test'"
        )


def test_repeated_looks_have_stricter_gate_than_overall_fdr_budget():
    assert evaluation.CHECKPOINTS == (24, 48, 96)
    assert evaluation.LOOK_ADJUSTED_Q_MAX == pytest.approx(
        evaluation.OVERALL_FDR_BUDGET / len(evaluation.CHECKPOINTS)
    )
    assert evaluation.LOOK_ADJUSTED_Q_MAX < evaluation.OVERALL_FDR_BUDGET


def test_validated_directional_rule_stays_high_risk_only_without_strict_reference():
    context = {
        "validated_supporting_position_n": 1,
        "validated_opposing_position_n": 0,
        "validated_strict_directional_n": 0,
    }
    profile = {
        "available": True,
        "matched_directional_signal_n": 1,
        "strict_directional_signal_n": 0,
        "strict_directional_share": 0.0,
        "agreement": 1.0,
        "weight_fraction": 0.30,
        "max_weight_fraction": 0.30,
    }
    weight_module = SimpleNamespace(HIGH_RISK_ONLY_CAP=0.30, MAX_EDGE_WEIGHT=0.40)
    upgraded = bridge._upgrade_weight_profile(context, profile, weight_module)
    assert upgraded["prospective_validated_directional_n"] == 1
    assert upgraded["prospective_calibration_pending"] is False
    # Prospective confirmation grants Active Edge eligibility. The shared
    # STRICT_REFERENCE gate, not LLM provenance or validation alone, is what
    # can raise the existing high-risk-only 30% cap toward 40%.
    assert upgraded["weight_fraction"] == pytest.approx(0.30)
    assert upgraded["max_weight_fraction"] == pytest.approx(0.30)


def test_validated_rule_still_rejects_stale_current_context():
    engine = _engine(Runtime())
    snapshot = {"captured_ts": 100.0}
    candidate = {"conditions": [{
        "feature_id": "vol.rv15_over_rv60",
        "kind": "train_relative",
        "state": "ABOVE_MEDIAN",
    }]}
    values = {"vol.rv15_over_rv60": {
        "available": True,
        "stale": False,
        "live_applicability": "LIVE_APPLICABLE",
        "asof": 10.0,
        "value": 2.0,
    }}
    integration = SimpleNamespace(_conditions_match=lambda values, candidate: True)

    matched, reason = bridge._fresh_conditions_match(
        engine, snapshot, values, candidate, integration
    )

    assert matched is False
    assert reason == "STALE_OR_UNAVAILABLE_CONTEXT"


def test_unvalidated_state_cannot_form_promotion_payload():
    with pytest.raises(ValueError, match="only prospectively validated"):
        evaluation._promotion_payload(_state("LIVE_VALIDATING"))
