from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import seiltanzer.llm_edge_lifecycle as lifecycle
import seiltanzer.llm_edge_pr_c as prc
import seiltanzer.llm_edge_researcher as researcher


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("""CREATE TABLE g1s_observations(
                observation_id TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                frozen_features_json TEXT NOT NULL DEFAULT '{}',
                created_ts REAL NOT NULL
            )""")
            self._conn.execute("""CREATE TABLE g1s_resolutions(
                observation_id TEXT PRIMARY KEY,
                resolved_ts REAL NOT NULL,
                terminal_log_return REAL,
                direction_label TEXT,
                mfe_log_return REAL,
                mae_log_return REAL,
                path_quality_status TEXT
            )""")


def _engine(runtime: Runtime):
    return SimpleNamespace(short_horizon=runtime)


def _resolved(runtime: Runtime, count: int, *, start: int = 1) -> None:
    with runtime._conn:
        for index in range(start, start + count):
            observation_id = f"obs-{index:04d}"
            captured = 1_000.0 + index
            runtime._conn.execute(
                "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?)",
                (observation_id, "NAS100", captured, captured + 3600.0, 60, "{}", captured),
            )
            runtime._conn.execute(
                "INSERT INTO g1s_resolutions VALUES(?,?,?,?,?,?,?)",
                (observation_id, captured + 3601.0, 0.01, "UP", 0.02, -0.01, "VALID"),
            )


def test_automatic_research_requires_both_100_new_resolved_and_12h(monkeypatch):
    runtime = Runtime()
    prc._ensure_storage(runtime)
    _resolved(runtime, 99)

    called = []
    monkeypatch.setattr(prc, "propose_edge_hypotheses", lambda *a, **k: called.append(1))
    result = prc._automatic_research_tick(_engine(runtime), now=100_000.0)

    assert result["status"] == "NOT_DUE"
    assert result["new_resolved_t0_since_last_run"] == 99
    assert result["evidence_gate_met"] is False
    assert called == []

    _resolved(runtime, 1, start=100)
    prc._update_state(runtime, last_provider_call_ts=99_000.0)
    result = prc._automatic_research_tick(_engine(runtime), now=100_000.0)
    assert result["status"] == "NOT_DUE"
    assert result["evidence_gate_met"] is True
    assert result["time_gate_met"] is False
    assert called == []


def test_automatic_run_uses_max_five_and_advances_durable_evidence_cursor(monkeypatch):
    runtime = Runtime()
    prc._ensure_storage(runtime)
    _resolved(runtime, 100)
    engine = _engine(runtime)
    calls = []

    def fake_propose(_runtime, observation_id=None, *, max_hypotheses, provider):
        calls.append(max_hypotheses)
        return {
            "status": "OK", "run_id": "auto-run-1", "provider_called": True,
            "cache_hit": False, "hypotheses": [],
        }

    monkeypatch.setattr(prc, "propose_edge_hypotheses", fake_propose)
    monkeypatch.setattr(
        prc._evaluator, "evaluate_edge_research_run",
        lambda runtime, run_id: {"status": "OK", "run_id": run_id},
    )
    monkeypatch.setattr(
        prc._lifecycle, "freeze_discovery_signals",
        lambda engine, now: {"frozen_n": 0},
    )

    result = prc._automatic_research_tick(engine, now=100_000.0)
    state = prc._automation_state(runtime)

    assert result["status"] == "RAN"
    assert calls == [5]
    assert state["last_automatic_run_id"] == "auto-run-1"
    assert state["automatic_orchestrations"] == 1
    assert state["automatic_provider_attempts"] == 1
    assert float(state["last_provider_call_ts"]) == pytest.approx(100_000.0)
    remaining, _ = prc._resolved_evidence_since_cursor(runtime, state)
    assert remaining == 0


def test_provider_failure_isolated_and_does_not_consume_evidence(monkeypatch):
    runtime = Runtime()
    prc._ensure_storage(runtime)
    _resolved(runtime, 100)
    before = prc._automation_state(runtime)

    monkeypatch.setattr(prc, "propose_edge_hypotheses", lambda *a, **k: {
        "status": "UNAVAILABLE",
        "reason": "PROVIDER_HTTP_503",
        "provider_called": True,
        "cache_hit": False,
    })
    result = prc._automatic_research_tick(_engine(runtime), now=100_000.0)
    after = prc._automation_state(runtime)

    assert result["status"] == "SKIPPED"
    assert after["automatic_provider_failures"] == 1
    assert float(after["last_provider_call_ts"]) == pytest.approx(100_000.0)
    assert float(after["last_run_resolved_ts"]) == float(before["last_run_resolved_ts"])
    remaining, _ = prc._resolved_evidence_since_cursor(runtime, after)
    assert remaining == 100


def _provider_summary() -> dict:
    return {
        "observation_id": "obs-current",
        "horizon_minutes": 60,
        "features": [{
            "feature_id": "vol.rv15_over_rv60",
            "family": "volatility",
            "datatype": "float",
            "value": 1.2,
            "asof": 100.0,
            "quality": 1.0,
            "provenance": "FROZEN_T0",
            "allowed_kind": "train_relative",
            "allowed_states": ["ABOVE_MEDIAN", "BELOW_MEDIAN"],
        }],
        "allowed_feature_pairs": [],
    }


def _raw_hypothesis() -> dict:
    return {
        "name": "RV compression test",
        "target_id": "DIRECTION",
        "conditions": [{
            "feature_id": "vol.rv15_over_rv60",
            "kind": "train_relative",
            "state": "ABOVE_MEDIAN",
        }],
        "rationale": "causal test",
    }


def test_cross_run_semantic_duplicate_is_rejected_before_new_evaluation():
    runtime = Runtime()
    prc._ensure_storage(runtime)
    summary = _provider_summary()
    raw = _raw_hypothesis()
    hypothesis, rejection = researcher._validate_hypothesis(raw, summary, index=0)
    assert rejection is None and hypothesis is not None

    with runtime._conn:
        runtime._conn.execute(
            """INSERT INTO llm_edge_hypotheses(
                 hypothesis_id,first_run_id,first_observation_id,first_snapshot_sha256,
                 name,target_id,target_family,horizon_minutes,conditions_json,rationale,
                 source,status,evaluation_state,created_ts
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                hypothesis["hypothesis_id"], "old-run", "old-obs", "s" * 64,
                hypothesis["name"], hypothesis["target_id"], hypothesis["target_family"],
                hypothesis["horizon_minutes"], json.dumps(hypothesis["conditions"]),
                hypothesis["rationale"], hypothesis["source"], hypothesis["status"],
                hypothesis["evaluation_state"], 1.0,
            ),
        )

    rejected = []
    provider = prc._deduplicating_provider(
        runtime, lambda summary, model, limit: {"hypotheses": [raw]}, rejected
    )
    response = provider(summary, "cheap-model", 5)

    assert response == {"hypotheses": []}
    assert rejected == ["0:DUPLICATE_HYPOTHESIS"]
    with runtime._lock:
        events = runtime._conn.execute(
            "SELECT reason FROM llm_edge_hypothesis_dedup_events"
        ).fetchall()
    assert [row[0] for row in events] == ["DUPLICATE_HYPOTHESIS"]


def _validated_state(*, q=0.05, effect=0.02, fold_positive=3):
    confirmation = {
        "prospective_confirmed": True,
        "evidence_label": "LIVE_PROSPECTIVE_OOS",
        "checkpoint_n": 24,
        "raw_n": 24,
        "effective_n": 24,
        "dependency_cohort_even_n": 3,
        "dependency_cohort_odd_n": 3,
        "primary_improvement": 0.02,
        "p_value": 0.01,
        "q_value": 0.02,
        "q_value_max": 0.10 / 3,
        "family_sha256": "f" * 64,
        "sample_sha256": "s" * 64,
        "decision": "PASS",
    }
    return {
        "candidate_id": "llm-edge-candidate-test",
        "hypothesis_id": "hyp-test",
        "status": "VALIDATED",
        "target_id": "DIRECTION",
        "target_family": "DIRECTION",
        "target_kind": "BINARY",
        "horizon_minutes": 60,
        "fold_positive": fold_positive,
        "validation": {
            "prospective_confirmation": confirmation,
            "frozen_spec": {
                "target_id": "DIRECTION",
                "target_family": "DIRECTION",
                "target_kind": "BINARY",
                "target_classes": ["DOWN", "UP"],
                "horizon_minutes": 60,
                "rule": {"conditions": [{
                    "feature_id": "vol.rv15_over_rv60",
                    "kind": "train_relative",
                    "state": "ABOVE_MEDIAN",
                    "lower": 1.1,
                    "upper": 1.1,
                }]},
                "rule_sha256": "r" * 64,
                "state_residual": 0.1,
                "discovery_q_value": q,
                "discovery_effect": effect,
                "source_evaluation_id": "eval-1",
                "prospective_epoch_id": "epoch-1",
            },
        },
    }


def test_promotion_reuses_existing_strict_reference_contract():
    strict = prc._promotion_payload_with_parity(_validated_state())
    weak = prc._promotion_payload_with_parity(
        _validated_state(q=0.50, effect=0.02, fold_positive=3)
    )

    assert strict["strict_reference_qualified"] is True
    assert weak["strict_reference_qualified"] is False
    assert strict["strict_reference"] == prc.STRICT_REFERENCE
    assert strict["candidate_source"] == "LLM_EDGE_RESEARCHER"
    assert strict["evaluation_id"] == "eval-1"


def test_prospective_validation_alone_cannot_raise_30pct_cap_to_40pct():
    profile = {
        "available": True,
        "weight_fraction": 0.30,
        "max_weight_fraction": 0.30,
        "matched_directional_signal_n": 1,
        "strict_directional_signal_n": 0,
        "strict_directional_share": 0.0,
        "agreement": 1.0,
    }
    context = {
        "validated_supporting_position_n": 1,
        "validated_opposing_position_n": 0,
        "validated_strict_directional_n": 0,
    }
    result = prc._upgrade_weight_profile_strict_only(
        context, profile, SimpleNamespace()
    )
    assert result["weight_fraction"] == pytest.approx(0.30)
    assert result["max_weight_fraction"] == pytest.approx(0.30)
    assert result["strict_reference_required_for_40pct_cap"] is True


def test_true_strict_reference_keeps_existing_40pct_cap():
    profile = {
        "available": True,
        "weight_fraction": 0.40,
        "max_weight_fraction": 0.40,
        "matched_directional_signal_n": 1,
        "strict_directional_signal_n": 1,
        "strict_directional_share": 1.0,
        "agreement": 1.0,
    }
    context = {
        "validated_supporting_position_n": 1,
        "validated_opposing_position_n": 0,
        "validated_strict_directional_n": 1,
    }
    result = prc._upgrade_weight_profile_strict_only(
        context, profile, SimpleNamespace()
    )
    assert result["weight_fraction"] == pytest.approx(0.40)
    assert result["max_weight_fraction"] == pytest.approx(0.40)
    assert result["authority_grade_directional_share"] == pytest.approx(1.0)


def test_materialized_status_has_no_request_time_history_scan():
    runtime = Runtime()
    prc._ensure_storage(runtime)
    payload_json = json.dumps({
        "status": "OK",
        "researcher": {"proposal_runs": 2, "hypotheses": 4},
        "automation": {"manual_post_only": False},
        "research_quality": {"evaluations_total": 3},
    })
    with runtime._conn:
        runtime._conn.execute("""CREATE TABLE llm_edge_lifecycle_materialized(
            singleton_id INTEGER PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_ts REAL NOT NULL
        )""")
        runtime._conn.execute(
            "INSERT INTO llm_edge_lifecycle_materialized VALUES(1,?,?)",
            (payload_json, 100.0),
        )
    lifecycle.publish_materialized_lifecycle_cache(runtime, payload_json)

    status = prc._materialized_status(runtime)
    evaluator_status = prc._materialized_evaluator_status(runtime)
    assert status["request_time_history_scan"] is False
    assert status["automation"]["manual_post_only"] is False
    assert status["run_n"] == 2
    assert evaluator_status["request_time_history_scan"] is False
    assert evaluator_status["evaluation_n"] == 3
