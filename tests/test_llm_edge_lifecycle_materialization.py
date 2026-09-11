import json
import sqlite3
import threading
from types import SimpleNamespace

from seiltanzer.llm_edge_evaluator import _ensure_tables as ensure_evaluation_tables
from seiltanzer.llm_edge_lifecycle import materialize_lifecycle
from seiltanzer.llm_edge_researcher import _ensure_tables as ensure_research_tables


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row


def test_materialization_reports_cutoff_from_latest_evaluation(tmp_path, monkeypatch):
    monkeypatch.setenv("SEILTANZER_EDE_CANDIDATE_REGISTRY", str(tmp_path / "registry.jsonl"))
    runtime = Runtime()
    with runtime._conn:
        runtime._conn.execute("""CREATE TABLE g1s_observations(
            observation_id TEXT PRIMARY KEY, captured_ts REAL,
            horizon_minutes INTEGER)""")
        runtime._conn.execute("""CREATE TABLE g1s_resolutions(
            observation_id TEXT PRIMARY KEY, resolved_ts REAL)""")
    ensure_research_tables(runtime)
    ensure_evaluation_tables(runtime)
    with runtime._conn:
        runtime._conn.execute(
            """INSERT INTO llm_edge_hypotheses(
                hypothesis_id, first_run_id, first_observation_id,
                first_snapshot_sha256, name, target_id, target_family,
                horizon_minutes, conditions_json, rationale, source, status,
                evaluation_state, created_ts
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("hyp-1", "run-1", "obs-1", "s" * 64, "test", "DIRECTION",
             "DIRECTION", 60, "[]", "test", "LLM", "RESEARCH",
             "INSUFFICIENT_DATA", 1000.0),
        )
        for evaluation_id, cutoff, created, raw_rows in (
            ("eval-old", 1010.0, 1011.0, 10),
            ("eval-new", 2020.0, 2021.0, 20),
        ):
            runtime._conn.execute(
                """INSERT INTO llm_edge_evaluations(
                    evaluation_id, run_id, hypothesis_id, evaluation_cutoff_ts,
                    dataset_sha256, measurement_contract, result_json, created_ts
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (evaluation_id, "run-1", "hyp-1", cutoff, str(raw_rows) * 64, "test",
                 json.dumps({"status": "INSUFFICIENT_DATA", "raw_rows": raw_rows,
                             "reason": "NO_ELIGIBLE_TARGET_ROWS"}), created),
            )

    engine = SimpleNamespace(short_horizon=runtime, settings=SimpleNamespace(data_dir=tmp_path))
    payload = materialize_lifecycle(engine, now=3000.0)
    hypothesis = payload["research_hypotheses"][0]
    assert len(payload["research_hypotheses"]) == 1
    assert hypothesis["evaluation_sample"]["evaluation_cutoff_ts"] == 2020.0
    assert hypothesis["evaluation_sample"]["raw_rows"] == 20
