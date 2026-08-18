import json
import sqlite3
import threading

import pytest

import seiltanzer.llm_edge_evaluator as evaluator


class Runtime:
    def __init__(self, *, cutoff=1_800_000_000.0):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE llm_edge_research_runs(
                run_id TEXT PRIMARY KEY,
                hypothesis_ids_json TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        self._conn.execute("""
            CREATE TABLE llm_edge_hypotheses(
                hypothesis_id TEXT PRIMARY KEY,
                target_id TEXT NOT NULL,
                target_family TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                conditions_json TEXT NOT NULL,
                source TEXT NOT NULL
            )""")
        hypotheses = [
            ("h1", "DIRECTION", "DIRECTION", 60, [{
                "feature_id": "regime.asset", "kind": "categorical", "state": "NAS100"
            }]),
            ("h2", "DIRECTION", "DIRECTION", 60, [{
                "feature_id": "regime.session_utc", "kind": "categorical", "state": "US"
            }]),
        ]
        for hypothesis_id, target_id, family, horizon, conditions in hypotheses:
            self._conn.execute(
                "INSERT INTO llm_edge_hypotheses VALUES(?,?,?,?,?,?)",
                (hypothesis_id, target_id, family, horizon, json.dumps(conditions),
                 "LLM_EDGE_RESEARCHER"),
            )
        self._conn.execute(
            "INSERT INTO llm_edge_research_runs VALUES(?,?,?)",
            ("run-1", json.dumps(["h1", "h2"]), cutoff),
        )
        self._conn.commit()


def test_resolved_rows_at_cutoff_rejects_late_database_resolution(monkeypatch):
    cutoff = 1_800_000_000.0
    runtime = Runtime(cutoff=cutoff)
    captured = {}

    class FakeAdapter:
        def __init__(self, runtime, *, available_asof):
            assert available_asof == cutoff

        def rows(self, *, resolved_only, strict):
            assert resolved_only is False
            assert strict is False
            return [
                {
                    "observation_id": "old-known",
                    "instrument": "NAS100",
                    "captured_ts": cutoff - 4000,
                    "target_ts": cutoff - 400,
                    "resolved_ts": cutoff - 300,
                    "horizon_minutes": 60,
                    "outcome_available": True,
                },
                {
                    "observation_id": "late-resolution",
                    "instrument": "NAS100",
                    "captured_ts": cutoff - 4000,
                    "target_ts": cutoff - 400,
                    "resolved_ts": cutoff + 10,
                    "horizon_minutes": 60,
                    "outcome_available": True,
                },
            ]

    class FakeOutcomeAdapter:
        def __init__(self, runtime):
            pass

        def attach(self, rows):
            captured["ids"] = [row["observation_id"] for row in rows]
            return rows

    monkeypatch.setattr(evaluator, "ProspectiveFeatureAdapter", FakeAdapter)
    monkeypatch.setattr(evaluator, "ProspectiveUniversalOutcomeAdapter", FakeOutcomeAdapter)

    rows = evaluator._resolved_rows_at_cutoff(runtime, cutoff)
    assert [row["observation_id"] for row in rows] == ["old-known"]
    assert captured["ids"] == ["old-known"]


def test_evaluation_applies_run_level_fdr_but_never_grants_policy_authority(monkeypatch):
    runtime = Runtime()
    monkeypatch.setattr(evaluator, "_resolved_rows_at_cutoff", lambda runtime, cutoff: [])
    monkeypatch.setattr(evaluator, "_specs", lambda rows: {})

    def fake_evaluate(rows, hypothesis, *, cutoff_ts, specs):
        strong = hypothesis["hypothesis_id"] == "h1"
        return {
            "hypothesis_id": hypothesis["hypothesis_id"],
            "target_id": hypothesis["target_id"],
            "target_family": hypothesis["target_family"],
            "horizon_minutes": hypothesis["horizon_minutes"],
            "dataset_sha256": "dataset-" + hypothesis["hypothesis_id"],
            "evaluation_state": "DETERMINISTIC_EVALUATED",
            "status": "PENDING_MULTIPLE_TESTING_GATE",
            "reason": None,
            "raw_rows": 200,
            "target_rows": 180,
            "fold_count": 4,
            "evaluated_fold_count": 4,
            "fold_positive": 3 if strong else 1,
            "p_value": 0.01 if strong else 0.20,
            "q_value": None,
            "primary_improvement": 0.02 if strong else 0.001,
            "model": {},
            "baseline": {},
            "improvement": {},
            "folds": [],
            "fold_rules": [],
            "production_authority": False,
            "eligible_for_policy": False,
            "auto_promotion": False,
            "prospective_confirmation": False,
        }

    monkeypatch.setattr(evaluator, "_evaluate_one", fake_evaluate)
    report = evaluator.evaluate_edge_research_run(runtime, "run-1")

    assert report["status"] == "OK"
    assert report["discovery_signal_n"] == 1
    by_id = {item["hypothesis_id"]: item for item in report["results"]}
    assert by_id["h1"]["q_value"] == pytest.approx(0.02)
    assert by_id["h1"]["status"] == "DISCOVERY_SIGNAL"
    assert by_id["h2"]["q_value"] == pytest.approx(0.20)
    assert by_id["h2"]["status"] == "RESEARCH_DIAGNOSTIC"
    for item in report["results"]:
        assert item["production_authority"] is False
        assert item["eligible_for_policy"] is False
        assert item["auto_promotion"] is False
        assert item["prospective_confirmation"] is False
    assert report["writes_active_edge_registry"] is False
    assert report["may_change_position_manager"] is False
    assert report["may_change_cvar_stop_or_size"] is False
    assert report["next_step"] == "FREEZE_SEPARATELY_FOR_FUTURE_PROSPECTIVE_CONFIRMATION"

    second = evaluator.evaluate_edge_research_run(runtime, "run-1")
    assert second["new_artifact_n"] == 0
    assert second["discovery_signal_n"] == 1


def test_evaluation_rows_are_immutable(monkeypatch):
    runtime = Runtime()
    monkeypatch.setattr(evaluator, "_resolved_rows_at_cutoff", lambda runtime, cutoff: [])
    monkeypatch.setattr(evaluator, "_specs", lambda rows: {})
    monkeypatch.setattr(evaluator, "_evaluate_one", lambda rows, hypothesis, cutoff_ts, specs: {
        "hypothesis_id": hypothesis["hypothesis_id"],
        "target_id": hypothesis["target_id"],
        "target_family": hypothesis["target_family"],
        "horizon_minutes": hypothesis["horizon_minutes"],
        "dataset_sha256": "dataset-" + hypothesis["hypothesis_id"],
        "evaluation_state": "INSUFFICIENT_DATA",
        "status": "INSUFFICIENT_DATA",
        "reason": "TEST",
        "raw_rows": 0,
        "target_rows": 0,
        "fold_count": 0,
        "p_value": None,
        "q_value": None,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
        "prospective_confirmation": False,
    })
    evaluator.evaluate_edge_research_run(runtime, "run-1")
    with pytest.raises(sqlite3.IntegrityError, match="immutable llm edge evaluation row"):
        with runtime._conn:
            runtime._conn.execute(
                "UPDATE llm_edge_evaluations SET measurement_contract='tampered'"
            )


def test_missing_research_run_fails_closed():
    runtime = Runtime()
    report = evaluator.evaluate_edge_research_run(runtime, "missing")
    assert report["status"] == "UNAVAILABLE"
    assert report["reason"] == "RESEARCH_RUN_NOT_FOUND"
    assert report["production_authority"] is False
    assert report["eligible_for_policy"] is False
