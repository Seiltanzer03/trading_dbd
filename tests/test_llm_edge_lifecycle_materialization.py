import json
import sqlite3
import threading
from types import SimpleNamespace

from seiltanzer.llm_edge_lifecycle import materialize_lifecycle


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row


def test_materialization_reports_cutoff_from_latest_evaluation(tmp_path, monkeypatch):
    monkeypatch.setenv("SEILTANZER_EDE_CANDIDATE_REGISTRY", str(tmp_path / "registry.jsonl"))
    runtime = Runtime()
    with runtime._conn:
        runtime._conn.execute("CREATE TABLE llm_edge_research_runs(run_id TEXT PRIMARY KEY)")
        runtime._conn.execute("""CREATE TABLE llm_edge_hypotheses(
            hypothesis_id TEXT PRIMARY KEY, name TEXT, target_id TEXT,
            target_family TEXT, horizon_minutes INTEGER, conditions_json TEXT,
            status TEXT, evaluation_state TEXT, created_ts REAL)""")
        runtime._conn.execute("""CREATE TABLE llm_edge_evaluations(
            evaluation_id TEXT PRIMARY KEY, run_id TEXT, hypothesis_id TEXT,
            evaluation_cutoff_ts REAL, result_json TEXT, created_ts REAL)""")
        runtime._conn.execute(
            "INSERT INTO llm_edge_hypotheses VALUES(?,?,?,?,?,?,?,?,?)",
            ("hyp-1", "test", "DIRECTION", "DIRECTION", 60, "[]",
             "RESEARCH", "INSUFFICIENT_DATA", 1000.0),
        )
        for evaluation_id, cutoff, created, raw_rows in (
            ("eval-old", 1010.0, 1011.0, 10),
            ("eval-new", 2020.0, 2021.0, 20),
        ):
            runtime._conn.execute(
                "INSERT INTO llm_edge_evaluations VALUES(?,?,?,?,?,?)",
                (evaluation_id, "run-1", "hyp-1", cutoff,
                 json.dumps({"status": "INSUFFICIENT_DATA", "raw_rows": raw_rows,
                             "reason": "NO_ELIGIBLE_TARGET_ROWS"}), created),
            )

    engine = SimpleNamespace(short_horizon=runtime, settings=SimpleNamespace(data_dir=tmp_path))
    payload = materialize_lifecycle(engine, now=3000.0)
    hypothesis = payload["research_hypotheses"][0]
    assert len(payload["research_hypotheses"]) == 1
    assert hypothesis["evaluation_sample"]["evaluation_cutoff_ts"] == 2020.0
    assert hypothesis["evaluation_sample"]["raw_rows"] == 20
