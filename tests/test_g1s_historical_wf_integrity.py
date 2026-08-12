from __future__ import annotations

import sqlite3
import threading

from seiltanzer.g1_short_horizon_historical_wf_integrity import (
    HISTORICAL_WF_INTEGRITY_VERSION,
    _current_source_set_runs,
)


class _Runtime:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("""
            CREATE TABLE g1s_historical_wf_runs(
                run_id TEXT PRIMARY KEY,
                contract_version TEXT NOT NULL,
                source_set_sha256 TEXT NOT NULL,
                target TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                model_family TEXT NOT NULL,
                fold_count INTEGER NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n INTEGER NOT NULL,
                positive_n INTEGER NOT NULL,
                negative_n INTEGER NOT NULL,
                historical_winner INTEGER NOT NULL,
                provisional_model_id TEXT,
                verdict TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")


def _insert(rt: _Runtime, run_id: str, source_set: str, target: str,
            horizon: int, winner: int = 0) -> None:
    rt._conn.execute(
        "INSERT INTO g1s_historical_wf_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, "g1s-historical-wf-real-bars-v1", source_set, target, horizon,
         "family", 4, 2000, 800, 1000, 1000, winner,
         (f"model-{run_id}" if winner else None),
         ("PROVISIONAL_LEARNED" if winner else "HISTORICAL_BASELINE_NOT_BEATEN"),
         1.0),
    )
    rt._conn.commit()


def test_current_complete_source_set_does_not_mix_old_retry_artifacts() -> None:
    rt = _Runtime()
    _insert(rt, "old-dir", "old-sha", "direction_up", 15, 1)
    _insert(rt, "old-ret", "old-sha", "terminal_log_return", 15, 0)
    _insert(rt, "new-dir", "new-sha", "direction_up", 15, 0)
    _insert(rt, "new-ret", "new-sha", "terminal_log_return", 15, 1)

    base = {
        "state": "COMPLETE",
        "source_set_sha256": "new-sha",
        "runs": [{"run_id": "old-dir"}, {"run_id": "old-ret"},
                 {"run_id": "new-dir"}, {"run_id": "new-ret"}],
        "run_count": 4,
        "provisional_count": 2,
    }
    out = _current_source_set_runs(rt, base)

    assert out["current_source_set_isolated"] is True
    assert out["source_set_integrity_version"] == HISTORICAL_WF_INTEGRITY_VERSION
    assert {row["run_id"] for row in out["runs"]} == {"new-dir", "new-ret"}
    assert out["run_count"] == 2
    assert out["provisional_count"] == 1


def test_unfinalized_source_set_is_explicit_not_fake_isolated() -> None:
    rt = _Runtime()
    out = _current_source_set_runs(rt, {"state": "RUNNING", "source_set_sha256": None})
    assert out["current_source_set_isolated"] is False
    assert out["current_source_set_filter_reason"] == "SOURCE_SET_NOT_FINALIZED"
