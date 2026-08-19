from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

from seiltanzer.llm_edge_active_promotion_reader import active_promotions_readonly


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row


def test_request_reader_fails_closed_without_schema_and_creates_nothing():
    runtime = Runtime()
    engine = SimpleNamespace(short_horizon=runtime)

    assert active_promotions_readonly(engine) == []
    tables = runtime._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert tables == []


def test_request_reader_exposes_only_validated_promotion_rows():
    runtime = Runtime()
    runtime._conn.execute("""CREATE TABLE llm_edge_active_promotions(
        candidate_id TEXT PRIMARY KEY,
        promotion_sha256 TEXT NOT NULL UNIQUE,
        payload_json TEXT NOT NULL,
        promoted_ts REAL NOT NULL,
        contract_version TEXT NOT NULL
    )""")
    good = {
        "candidate_id": "good",
        "prospective_validated": True,
        "eligible_for_active_edge": True,
        "promotion_basis": "VALIDATED_LIVE_PROSPECTIVE_OOS",
    }
    bad = {
        "candidate_id": "bad",
        "prospective_validated": False,
        "eligible_for_active_edge": True,
        "promotion_basis": "VALIDATED_LIVE_PROSPECTIVE_OOS",
    }
    runtime._conn.executemany(
        "INSERT INTO llm_edge_active_promotions VALUES(?,?,?,?,?)",
        [
            ("good", "g" * 64, json.dumps(good), 10.0, "v1"),
            ("bad", "b" * 64, json.dumps(bad), 11.0, "v1"),
        ],
    )
    runtime._conn.commit()

    rows = active_promotions_readonly(SimpleNamespace(short_horizon=runtime))

    assert [row["candidate_id"] for row in rows] == ["good"]
    assert rows[0]["promotion_sha256"] == "g" * 64
