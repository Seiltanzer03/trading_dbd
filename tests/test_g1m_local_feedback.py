from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from seiltanzer.g1_management_runtime import ManagementEdgeRuntime, _json, _sha_text
from seiltanzer.g1_management_local_runtime import ManagementLocalRuntime


class _Passive:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row


class _Engine:
    def __init__(self, path):
        self.passive = _Passive(path)


def _source_schema(conn):
    conn.executescript("""
        CREATE TABLE decision_snapshots(
            review_id TEXT PRIMARY KEY, trade_id INTEGER NOT NULL,
            captured_ts REAL NOT NULL, snapshot_json TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL, production_policy TEXT NOT NULL,
            policy_version TEXT
        );
        CREATE TABLE management_decisions(
            decision_id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
            trade_id INTEGER NOT NULL, created_ts REAL NOT NULL,
            policy TEXT NOT NULL, status TEXT NOT NULL,
            close_fraction_current REAL NOT NULL,
            remaining_before REAL NOT NULL, remaining_after REAL NOT NULL,
            geometry_version TEXT NOT NULL,
            entry REAL NOT NULL, original_stop REAL NOT NULL, take_price REAL NOT NULL,
            executed_ts REAL, execution_price REAL, execution_r REAL,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE position_management_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL, timestamp REAL NOT NULL,
            event_type TEXT NOT NULL, source TEXT NOT NULL,
            review_id TEXT, decision_id TEXT,
            fraction_before REAL NOT NULL, fraction_closed REAL NOT NULL,
            fraction_after REAL NOT NULL, execution_price REAL, execution_r REAL,
            original_stop REAL NOT NULL, active_stop REAL NOT NULL,
            take REAL NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE decision_path_points(
            review_id TEXT NOT NULL, ts REAL NOT NULL, price REAL, r REAL NOT NULL,
            PRIMARY KEY(review_id,ts)
        );
        CREATE TABLE decision_replays(
            review_id TEXT PRIMARY KEY, trade_id INTEGER NOT NULL,
            resolved_ts REAL NOT NULL, resolution_kind TEXT NOT NULL,
            replay_version TEXT NOT NULL, replay_json TEXT NOT NULL
        );
        CREATE TABLE trades(
            id INTEGER PRIMARY KEY, opened_at REAL, closed_at REAL,
            instrument TEXT, direction TEXT, setup INTEGER, entry REAL,
            stop REAL, take REAL, status TEXT, result_r REAL
        );
    """)
    conn.commit()


def _snapshot(trade_id, policy="CLOSE_25"):
    return {
        "trade_id": trade_id,
        "observation": {
            "exact_levels": {"current": 102.0},
            "position": {"r": 0.2},
        },
        "position_state": {
            "remaining_position_fraction": 1.0,
            "realized_r_weighted": 0.0,
            "original_stop": 90.0,
            "active_stop_price": 90.0,
        },
        "policy_manager": {
            "version": "policy-test-v1",
            "management_decision": {"decision_id": "placeholder", "policy": policy},
            "inputs": {"r0": 0.2, "max_r": 0.2, "T": 2.0,
                       "rungs": [1.0, 2.0], "rung_fraction": 0.10,
                       "be_after": 1.5},
            "execution_cost_model": {
                "version": "zero-cost-test",
                "immediate_full_close_r": 0.0,
                "deferred_full_close_r": 0.0,
            },
            "policies": {
                name: {"expected_net_r": 0.1, "median_net_r": 0.0,
                       "cvar10_net_r": -0.8, "p_loss": 0.45}
                for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
            },
        },
    }


def _insert_source(engine, management, *, trade_id, captured_ts, policy="CLOSE_25"):
    snap = _snapshot(trade_id, policy)
    decision_id = f"decision-{trade_id}"
    review_id = f"review-{trade_id}"
    snap["captured_ts"] = captured_ts
    snap["policy_manager"]["management_decision"]["decision_id"] = decision_id
    raw = _json(snap)
    conn = engine.passive._conn
    conn.execute(
        "INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?,?)",
        (review_id, trade_id, captured_ts, raw, _sha_text(raw), policy, "policy-test-v1"),
    )
    conn.execute(
        "INSERT INTO management_decisions(decision_id,review_id,trade_id,created_ts,policy,status,"
        "close_fraction_current,remaining_before,remaining_after,geometry_version,entry,original_stop,"
        "take_price,executed_ts,execution_price,execution_r,payload_json) "
        "VALUES(?,?,?,?,?,'pending_execution',?,1,1,'geometry-test',100,90,120,NULL,NULL,NULL,'{}')",
        (decision_id, review_id, trade_id, captured_ts, policy,
         {"HOLD":0.0,"CLOSE_10":0.1,"CLOSE_25":0.25,"CLOSE_50":0.5,"EXIT":1.0}[policy]),
    )
    conn.execute(
        "INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, captured_ts-60, None, "NAS100", "long", 1, 100.0, 90.0, 120.0,
         "open", None),
    )
    conn.commit()
    assert management.capture_new() == 1
    return review_id, snap


def _make_runtime(tmp_path):
    engine = _Engine(tmp_path / "local.sqlite3")
    _source_schema(engine.passive._conn)
    management = ManagementEdgeRuntime(engine)
    engine.management = management
    return engine, management


def test_preexisting_g1m_decision_is_descriptive_but_new_window_is_evidence_eligible(tmp_path):
    engine, management = _make_runtime(tmp_path)
    old_ts = management.activation_ts + 1.0
    _insert_source(engine, management, trade_id=1, captured_ts=old_ts)

    local = ManagementLocalRuntime(engine)
    engine.management_local = local
    assert local.materialize_windows() == 4
    old = engine.passive._conn.execute(
        "SELECT DISTINCT origin,evidence_eligible FROM g1m_local_windows WHERE trade_id=1"
    ).fetchone()
    assert old["origin"] == "PREEXISTING_PROSPECTIVE_DESCRIPTIVE"
    assert old["evidence_eligible"] == 0

    new_ts = local.activation_ts + 1.0
    _insert_source(engine, management, trade_id=2, captured_ts=new_ts)
    assert local.materialize_windows() == 4
    new = engine.passive._conn.execute(
        "SELECT DISTINCT origin,evidence_eligible FROM g1m_local_windows WHERE trade_id=2"
    ).fetchone()
    assert new["origin"] == "LIVE_PROSPECTIVE"
    assert new["evidence_eligible"] == 1


def test_local_h15_resolves_on_truncated_real_path_without_waiting_trade_terminal(tmp_path):
    engine, management = _make_runtime(tmp_path)
    local = ManagementLocalRuntime(engine)
    engine.management_local = local
    captured = local.activation_ts + 1.0
    review_id, _ = _insert_source(engine, management, trade_id=10, captured_ts=captured)
    local.materialize_windows()
    window = engine.passive._conn.execute(
        "SELECT * FROM g1m_local_windows WHERE trade_id=10 AND horizon_minutes=15"
    ).fetchone()
    target = float(window["target_ts"])

    # No future horizon observation -> no local outcome.
    engine.passive._conn.execute(
        "INSERT INTO decision_path_points VALUES(?,?,?,?)",
        (review_id, captured, 102.0, 0.2),
    )
    engine.passive._conn.commit()
    assert local.resolve_due(now=target + 1) == 0

    # Add only points <= frozen horizon, including exact target. No terminal trade
    # replay/close exists; G.1-M.1 must still produce local feedback.
    mid = captured + (target-captured) / 2.0
    engine.passive._conn.execute(
        "INSERT INTO decision_path_points VALUES(?,?,?,?)",
        (review_id, mid, 105.0, 0.5),
    )
    engine.passive._conn.execute(
        "INSERT INTO decision_path_points VALUES(?,?,?,?)",
        (review_id, target, 108.0, 0.8),
    )
    engine.passive._conn.commit()
    assert local.resolve_due(now=target + 1) == 1

    out = local.outcomes()["items"][0]
    assert out["horizon_minutes"] == 15
    assert out["path_end_ts"] <= target + 1e-9
    assert json.loads(out["outcome_json"])["semantics"] == \
        "LOCAL_DECISION_QUALITY_NOT_TERMINAL_MANAGEMENT_EDGE"
    assert local.status()["eligible_resolved"] == 1
    assert local.edge()["production_authority"] is False


def test_local_window_and_outcome_are_immutable(tmp_path):
    engine, management = _make_runtime(tmp_path)
    local = ManagementLocalRuntime(engine)
    captured = local.activation_ts + 1
    review, _ = _insert_source(engine, management, trade_id=20, captured_ts=captured)
    local.materialize_windows()
    window = engine.passive._conn.execute(
        "SELECT * FROM g1m_local_windows WHERE trade_id=20 AND horizon_minutes=15"
    ).fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with engine.passive._conn:
            engine.passive._conn.execute(
                "UPDATE g1m_local_windows SET target_ts=target_ts+1 WHERE window_id=?",
                (window["window_id"],))
