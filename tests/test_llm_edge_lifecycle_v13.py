import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import seiltanzer.llm_edge_candidate_lifecycle as freeze
import seiltanzer.llm_edge_prospective_journal as journal


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""CREATE TABLE g1s_observations(
            observation_id TEXT PRIMARY KEY,
            instrument TEXT NOT NULL,
            captured_ts REAL NOT NULL,
            target_ts REAL NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            frozen_features_json TEXT,
            created_ts REAL NOT NULL
        )""")
        self._conn.execute("""CREATE TABLE g1s_resolutions(
            observation_id TEXT PRIMARY KEY,
            resolved_ts REAL NOT NULL
        )""")
        self._conn.commit()


class FakeRegistry:
    def __init__(self):
        self.registered = []
        self.frozen = []

    def current(self, candidate_id):
        return None

    def register_evaluation(self, candidate, **kwargs):
        self.registered.append((candidate, kwargs))
        return "registry-eval"

    def freeze_for_validation(self, candidate_id, **kwargs):
        self.frozen.append((candidate_id, kwargs))


def _discovery(status="DISCOVERY_SIGNAL", state="DETERMINISTIC_EVALUATED"):
    return {
        "evaluation_id": "eval-1",
        "run_id": "run-1",
        "hypothesis_id": "hyp-1",
        "evaluation_cutoff_ts": 1005.0,
        "dataset_sha256": "d" * 64,
        "measurement_contract": "test",
        "created_ts": 1005.0,
        "result": {
            "status": status,
            "evaluation_state": state,
            "target_id": "DIRECTION",
            "target_family": "DIRECTION",
            "horizon_minutes": 60,
            "q_value": 0.01,
            "p_value": 0.005,
            "primary_improvement": 0.1,
            "evaluated_fold_count": 4,
            "fold_positive": 4,
            "production_authority": False,
            "prospective_confirmation": False,
        },
    }


def test_non_discovery_states_cannot_freeze():
    registry = FakeRegistry()
    for status, state in (
        ("RESEARCH_DIAGNOSTIC", "DETERMINISTIC_EVALUATED"),
        ("INSUFFICIENT_DATA", "INSUFFICIENT_DATA"),
    ):
        result = freeze.freeze_one(
            SimpleNamespace(), SimpleNamespace(), registry,
            _discovery(status, state), frozen_ts=1007.0,
        )
        assert result["frozen"] is False
    assert not registry.registered
    assert not registry.frozen


def test_discovery_freezes_at_freeze_time_and_keeps_final_threshold(monkeypatch):
    runtime = Runtime()
    runtime._conn.execute("""CREATE TABLE llm_edge_hypotheses(
        hypothesis_id TEXT PRIMARY KEY, name TEXT, target_family TEXT,
        conditions_json TEXT
    )""")
    runtime._conn.execute("""CREATE TABLE llm_edge_research_runs(
        run_id TEXT PRIMARY KEY, snapshot_sha256 TEXT, model TEXT,
        prompt_version TEXT, created_ts REAL
    )""")
    runtime._conn.execute(
        "INSERT INTO llm_edge_hypotheses VALUES(?,?,?,?)",
        ("hyp-1", "RV rule", "DIRECTION", json.dumps([{
            "feature_id": "vol.rv15_over_rv60",
            "kind": "train_relative", "state": "ABOVE_MEDIAN",
        }])),
    )
    runtime._conn.execute(
        "INSERT INTO llm_edge_research_runs VALUES(?,?,?,?,?)",
        ("run-1", "s" * 64, "cheap-model", "prompt-v1", 1000.0),
    )
    runtime._conn.commit()

    spec = SimpleNamespace(kind="DIRECTION")
    monkeypatch.setattr(freeze, "_resolved_rows_at_cutoff", lambda runtime, cutoff: [{"horizon_minutes": 60}])
    monkeypatch.setattr(freeze, "_specs", lambda rows: {"DIRECTION": spec})
    monkeypatch.setattr(freeze, "eligible_target_rows", lambda rows, spec: rows)
    monkeypatch.setattr(freeze, "_template", lambda hypothesis: SimpleNamespace(template_id="template-1"))
    monkeypatch.setattr(freeze, "admit_discovery_candidate", lambda value: {**value, "status": "HISTORICAL_CANDIDATE"})
    monkeypatch.setattr(freeze, "build_structured_frozen_spec", lambda *args, **kwargs: {
        "target_id": "DIRECTION",
        "horizon_minutes": 60,
        "training_cutoff_ts": 1005.0,
        "rule": {"conditions": [{
            "feature_id": "vol.rv15_over_rv60",
            "kind": "train_relative", "state": "ABOVE_MEDIAN",
            "lower": 1.234, "upper": 1.234, "train_cutoff_ts": 1005.0,
        }]},
        "conditions": [{
            "feature_id": "vol.rv15_over_rv60",
            "kind": "train_relative", "state": "ABOVE_MEDIAN",
            "lower": 1.234, "upper": 1.234, "train_cutoff_ts": 1005.0,
        }],
    })

    registry = FakeRegistry()
    result = freeze.freeze_one(
        SimpleNamespace(), runtime, registry, _discovery(), frozen_ts=1007.0,
    )
    assert result["frozen"] is True
    candidate_id, kwargs = registry.frozen[0]
    assert candidate_id.startswith("llm-edge-candidate-")
    assert kwargs["frozen_at"] == 1007.0
    frozen = kwargs["frozen_spec"]
    assert frozen["prospective_start_ts"] == 1007.0
    assert frozen["rule"]["conditions"][0]["lower"] == 1.234
    assert frozen["production_authority"] is False
    assert frozen["prospective_confirmed"] is False
    assert frozen["rule_sha256"]


def _candidate(frozen_ts=1007.0):
    return {
        "candidate_id": "llm-edge-candidate-test",
        "horizon_minutes": 60,
        "validation": {
            "oos_start_ts_exclusive": frozen_ts,
            "frozen_spec": {
                "rule_sha256": "r" * 64,
                "rule": {"conditions": [{
                    "feature_id": "option.iv",
                    "kind": "train_relative", "state": "ABOVE_MEDIAN",
                    "lower": 1.234, "upper": 1.234,
                }]},
            },
        },
    }


def test_leakage_boundary_excludes_t0_before_freeze(monkeypatch):
    runtime = Runtime()
    journal.initialize_journal_storage(runtime)
    for observation_id, captured in (("old", 1006.0), ("future", 1008.0)):
        runtime._conn.execute(
            "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?)",
            (observation_id, "NAS100", captured, 2000.0, 60, "{}", captured),
        )
    runtime._conn.commit()

    candidate = _candidate()
    monkeypatch.setattr(journal, "registry_for_engine", lambda engine: object())
    monkeypatch.setattr(journal, "active_llm_candidates", lambda registry: [candidate])
    monkeypatch.setattr(journal, "ledger_for_engine", lambda engine: object())
    monkeypatch.setattr(journal, "_context", lambda *args: {
        "available": True, "stale": False, "reason": "AVAILABLE_CONTEXT",
        "feature_values": {"option.iv": {
            "value": 2.0, "available": True, "availability": "AVAILABLE",
            "stale": False, "asof": 1008.0,
        }},
        "ede_features": {"option.iv": 2.0},
    })
    monkeypatch.setattr(journal, "predict_structured_frozen", lambda *args: {
        "qualified": True, "candidate_prediction": 1.0,
        "baseline_prediction": 0.0, "target_id": "DIRECTION",
        "target_kind": "DIRECTION",
    })
    seen = []
    monkeypatch.setattr(journal, "record_registered_prediction", lambda *args, **kwargs: seen.append(kwargs["t0"]) or "rec")

    result = journal.collect_opportunities(SimpleNamespace(short_horizon=runtime), now=1010.0)
    assert result["opportunities_inserted"] == 1
    assert seen == [1008.0]
    rows = runtime._conn.execute(
        "SELECT observation_id FROM llm_edge_candidate_opportunities"
    ).fetchall()
    assert [row[0] for row in rows] == ["future"]


def test_stale_feature_is_unavailable_not_no_match(monkeypatch):
    observation = {
        "observation_id": "x", "instrument": "NAS100",
        "captured_ts": 1010.0, "target_ts": 2000.0,
        "horizon_minutes": 60, "frozen_features_json": "{}",
        "created_ts": 1010.0,
    }
    monkeypatch.setattr(journal, "_frozen_t0_records", lambda *args: {
        "option.iv": {
            "feature_id": "option.iv", "value": 2.0,
            "availability": "AVAILABLE", "stale": True, "asof": 1000.0,
        }
    })
    context = journal._context(None, _candidate(), observation)
    assert context["available"] is False
    assert context["reason"] == "UNAVAILABLE_CONTEXT"
    assert context["stale"] is True


def test_duplicate_opportunity_and_cursor_restart_are_idempotent():
    runtime = Runtime()
    journal.initialize_journal_storage(runtime)
    values = (
        "c", "o", 1008.0, "NAS100", 60, 2000.0, "r",
        1, 0, 1, "MATCH", "rec", "{}", 1010.0,
    )
    assert journal._insert_opportunity(runtime, values) is True
    assert journal._insert_opportunity(runtime, values) is False
    assert runtime._conn.execute(
        "SELECT COUNT(*) FROM llm_edge_candidate_opportunities"
    ).fetchone()[0] == 1

    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?)",
        ("a", "NAS100", 1008.0, 2000.0, 60, "{}", 1008.0),
    )
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?)",
        ("b", "NAS100", 1009.0, 2001.0, 60, "{}", 1009.0),
    )
    runtime._conn.commit()
    journal._advance_cursor(runtime, 1008.0, "a")
    assert [row["observation_id"] for row in journal._new_observations(runtime, 10)] == ["b"]


def test_frozen_threshold_object_never_refits():
    conditions = journal._conditions(_candidate())
    assert len(conditions) == 1
    assert conditions[0].lower == pytest.approx(1.234)
    assert conditions[0].upper == pytest.approx(1.234)
