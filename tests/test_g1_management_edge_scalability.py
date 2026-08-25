from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from seiltanzer import g1_management_active_edge_attribution as attribution
from seiltanzer import g1_management_edge_scalability as scalability
from seiltanzer import g1_management_local_edge_v2 as local_edge


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0]


class _Conn:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        normalized = " ".join(sql.split())
        self.sql.append(normalized)
        if "COUNT(*) AS sidecar_observation_n" in normalized:
            return _Cursor([{
                "sidecar_observation_n": 1,
                "available_observation_n": 1,
            }])
        if "v2.context_json" in normalized:
            return _Cursor([{
                "window_id": "w1",
                "horizon_minutes": 15,
                "trade_id": 7,
                "observation_id": "o1",
                "captured_ts": 100.0,
                "evidence_eligible": 1,
                "origin": "LIVE_PROSPECTIVE",
                "production_policy": "HOLD",
                "current_r": 0.2,
                "instrument": "NAS100",
                "mfe_r": 0.4,
                "mae_r": -0.1,
                "context_json": json.dumps({"market_context": {}}),
            }])
        if "p.regret_r" in normalized:
            return _Cursor([
                {"window_id": "w1", "policy_name": "HOLD", "terminal_r": 0.3, "regret_r": 0.0},
                {"window_id": "w1", "policy_name": "EXIT", "terminal_r": 0.1, "regret_r": 0.2},
            ])
        if "ae.context_json AS active_edge_context_json" in normalized:
            return _Cursor([{
                "window_id": "w1",
                "horizon_minutes": 15,
                "trade_id": 7,
                "observation_id": "o1",
                "captured_ts": 100.0,
                "evidence_eligible": 1,
                "origin": "LIVE_PROSPECTIVE",
                "active_edge_context_json": json.dumps({
                    "supporting_position_n": 1,
                    "opposing_position_n": 0,
                    "strict_supporting_position_n": 1,
                    "strict_opposing_position_n": 0,
                    "matched_groups": [],
                }),
            }])
        if "SELECT p.window_id,p.policy_name,p.terminal_r" in normalized:
            return _Cursor([
                {"window_id": "w1", "policy_name": "HOLD", "terminal_r": 0.3},
                {"window_id": "w1", "policy_name": "EXIT", "terminal_r": 0.1},
                {"window_id": "w1", "policy_name": "CLOSE_25", "terminal_r": 0.25},
                {"window_id": "w1", "policy_name": "CLOSE_50", "terminal_r": 0.2},
            ])
        raise AssertionError(normalized)


def _runtime():
    return SimpleNamespace(_conn=_Conn(), _lock=threading.RLock())


def test_scalability_installer_replaces_only_report_helpers():
    assert local_edge._pairwise_rows is scalability._pairwise_rows_bounded_io
    assert local_edge._context_labels is scalability._context_labels_cached
    assert attribution._window_records is scalability._window_records_bounded_io


def test_base_edge_reads_context_once_per_window_not_once_per_policy():
    runtime = _runtime()
    rows = scalability._pairwise_rows_bounded_io(runtime)

    assert len(rows) == 1
    assert rows[0]["policies"]["HOLD"]["terminal_r"] == 0.3
    assert rows[0]["policies"]["EXIT"]["terminal_r"] == 0.1
    context_queries = [sql for sql in runtime._conn.sql if "context_json" in sql]
    assert len(context_queries) == 1
    assert "g1m_local_policy_outcomes" not in context_queries[0]


def test_base_edge_decodes_context_labels_once_per_window(monkeypatch):
    runtime = _runtime()
    original = scalability._ORIGINAL_CONTEXT_LABELS
    calls = []

    def counted(row):
        calls.append(str(row["window_id"]))
        return original(row)

    monkeypatch.setattr(scalability, "_ORIGINAL_CONTEXT_LABELS", counted)
    rows = scalability._pairwise_rows_bounded_io(runtime)
    assert calls == ["w1"]

    # Pairwise records are shallow copies. They retain the one immutable cached
    # label dictionary and must not decode the same context again.
    for _ in range(5):
        copied = {**rows[0]}
        assert local_edge._context_labels(copied)["instrument"] == "NAS100"
    assert calls == ["w1"]


def test_attribution_reads_active_context_once_per_window_not_once_per_policy():
    runtime = _runtime()
    windows, coverage = scalability._window_records_bounded_io(runtime)

    assert len(windows) == 1
    assert coverage["resolved_prospective_window_n"] == 1
    assert windows[0]["policies"]["CLOSE_50"] == 0.2
    context_queries = [sql for sql in runtime._conn.sql if "active_edge_context_json" in sql]
    assert len(context_queries) == 1
    assert "g1m_local_policy_outcomes" not in context_queries[0]
