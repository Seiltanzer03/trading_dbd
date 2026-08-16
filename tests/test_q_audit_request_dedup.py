from __future__ import annotations

import sqlite3

from seiltanzer.g1_q_audit_scalability import (
    _candidate_requests,
    _deduplicate_candidate_requests,
    _terminal_candidate_batch_snapshot,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE passive_market_bars(
            instrument TEXT, bar_end_ts REAL, kind TEXT, quality REAL
        );
        CREATE TABLE passive_market_path(
            instrument TEXT, ts REAL, kind TEXT, quality REAL
        );
        CREATE INDEX ix_test_bars ON passive_market_bars(instrument,bar_end_ts)
          WHERE kind='direct' AND COALESCE(quality,0)>=0.90;
        CREATE INDEX ix_test_path ON passive_market_path(instrument,ts)
          WHERE kind='direct' AND COALESCE(quality,0)>=0.90;
        """
    )
    return conn


def test_duplicate_instrument_target_seeks_are_executed_once_and_fanned_out():
    conn = _connection()
    statements: list[str] = []
    try:
        conn.execute(
            "INSERT INTO passive_market_bars VALUES(?,?,?,?)",
            ("NAS100", 1000.0, "direct", 1.0),
        )
        conn.commit()
        requests = [(index, "NAS100", 1000.0) for index in range(1500)]
        unique, fanout = _deduplicate_candidate_requests(requests)
        assert unique == [(0, "NAS100", 1000.0)]
        assert len(fanout[0]) == 1500

        conn.set_trace_callback(statements.append)
        resolved = _terminal_candidate_batch_snapshot(conn, requests)
        assert len(resolved) == 1500
        assert set(resolved.values()) == {(1000.0, "direct_1m_bar")}
        selects = [sql for sql in statements if "WITH requested" in sql]
        assert len(selects) == 1
    finally:
        conn.close()


def test_summary_candidate_requests_skip_diagnostic_only_blocked_rows():
    now = 10_000.0
    rows = [
        {
            "observation_created": 1,
            "created_observation_id": "blocked",
            "target_ts": 1000.0,
            "resolution_status": "blocked",
            "instrument": "NAS100",
            "target_instrument": "NAS100",
        },
        {
            "observation_created": 1,
            "created_observation_id": "overdue",
            "target_ts": 2000.0,
            "resolution_status": "pending",
            "instrument": "NAS100",
            "target_instrument": "NAS100",
        },
        {
            "observation_created": 1,
            "created_observation_id": "resolved",
            "target_ts": 3000.0,
            "resolution_status": "resolved",
            "instrument": "NAS100",
            "target_instrument": "NAS100",
        },
    ]
    full = _candidate_requests(rows, now=now, include_diagnostics=True)
    summary = _candidate_requests(rows, now=now, include_diagnostics=False)
    assert [row[0] for row in full] == [0, 1]
    assert [row[0] for row in summary] == [1]
