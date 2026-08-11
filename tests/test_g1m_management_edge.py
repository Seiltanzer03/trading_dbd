from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from seiltanzer.decision_research import counterfactual_replay
from seiltanzer.g1_management_runtime import (
    G1M_CONTRACT_VERSION,
    ManagementEdgeRuntime,
    _json,
    _sha_text,
)


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
            entry REAL NOT NULL, original_stop REAL NOT NULL,
            take_price REAL NOT NULL
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
            id INTEGER PRIMARY KEY, instrument TEXT, direction TEXT,
            entry REAL, stop REAL, take REAL, status TEXT
        );
    """)
    conn.commit()


def _snapshot(ts: float, *, trade_id: int = 1, policy: str = "CLOSE_50",
              remaining: float = 1.0, realized: float = 0.0, demo: bool = False):
    return {
        "captured_ts": ts,
        "trade_id": trade_id,
        "demo": demo,
        "observation": {
            "exact_levels": {"current": 102.0},
            "position": {"r": 0.2},
        },
        "position_state": {
            "remaining_position_fraction": remaining,
            "realized_r_weighted": realized,
            "original_stop": 90.0,
            "active_stop_price": 90.0,
        },
        "policy_manager": {
            "version": "policy-test-v1",
            "management_decision": {
                "decision_id": f"d-{trade_id}-{int(ts)}",
                "policy": policy,
            },
            "inputs": {
                "r0": 0.2,
                "max_r": 0.2,
                "T": 2.0,
                "rungs": [1.0, 2.0],
                "rung_fraction": 0.10,
                "be_after": 1.5,
            },
            "execution_cost_model": {
                "version": "zero-cost-test",
                "immediate_full_close_r": 0.0,
                "deferred_full_close_r": 0.0,
            },
            "policies": {
                name: {
                    "expected_net_r": 0.1,
                    "median_net_r": 0.0,
                    "cvar10_net_r": -0.8,
                    "p_loss": 0.45,
                }
                for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
            },
        },
    }


def _insert_decision(runtime: ManagementEdgeRuntime, snapshot: dict, *,
                     status: str = "pending_execution", old: bool = False):
    conn = runtime._conn
    ts = runtime.activation_ts - 100.0 if old else runtime.activation_ts + 1.0
    snapshot = dict(snapshot)
    snapshot["captured_ts"] = ts
    snapshot["policy_manager"] = json.loads(json.dumps(snapshot["policy_manager"]))
    decision_id = snapshot["policy_manager"]["management_decision"]["decision_id"]
    review_id = f"review-{snapshot['trade_id']}-{int(ts * 1000)}"
    raw = _json(snapshot)
    sha = _sha_text(raw)
    policy = snapshot["policy_manager"]["management_decision"]["policy"]
    conn.execute(
        "INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?,?)",
        (review_id, snapshot["trade_id"], ts, raw, sha, policy, "policy-test-v1"),
    )
    conn.execute(
        "INSERT INTO management_decisions VALUES(?,?,?,?,?,?,?,?,?)",
        (decision_id, review_id, snapshot["trade_id"], ts, policy, status,
         100.0, 90.0, 120.0),
    )
    conn.execute(
        "INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?)",
        (snapshot["trade_id"], "NAS100", "long", 100.0, 90.0, 120.0, "open"),
    )
    conn.commit()
    return review_id, snapshot


def _resolve(runtime: ManagementEdgeRuntime, review_id: str, snapshot: dict,
             path=(0.2, 0.8, -1.0)):
    points = []
    for i, r in enumerate(path):
        row = {"ts": snapshot["captured_ts"] + i * 60.0, "r": float(r),
               "price": 102.0 + i}
        points.append(row)
        runtime._conn.execute(
            "INSERT INTO decision_path_points VALUES(?,?,?,?)",
            (review_id, row["ts"], row["price"], row["r"]),
        )
    replay = counterfactual_replay(snapshot, points)
    runtime._conn.execute(
        "INSERT INTO decision_replays VALUES(?,?,?,?,?,?)",
        (review_id, snapshot["trade_id"], snapshot["captured_ts"] + 300,
         "manual_close", replay["version"], _json(replay)),
    )
    runtime._conn.commit()
    runtime.resolve_new()
    return replay


def _runtime(tmp_path):
    engine = _Engine(tmp_path / "g1m.sqlite3")
    _source_schema(engine.passive._conn)
    return ManagementEdgeRuntime(engine)


def test_live_prospective_decision_is_frozen_and_resolved_path_dependently(tmp_path):
    runtime = _runtime(tmp_path)
    review_id, snap = _insert_decision(runtime, _snapshot(0), status="executed")
    assert runtime.capture_new() == 1
    obs = runtime.observations()["items"][0]
    assert obs["origin"] == "LIVE_PROSPECTIVE"
    assert obs["policy_edge_eligible"] == 1
    _resolve(runtime, review_id, snap, path=(0.2, 0.8, -1.0))
    detail = runtime.decision(obs["observation_id"])
    outcomes = {row["policy_name"]: row for row in detail["realized_outcomes"]}
    assert outcomes["HOLD"]["terminal_r"] == pytest.approx(-1.0)
    assert outcomes["CLOSE_50"]["terminal_r"] == pytest.approx(-0.4)
    assert outcomes["PRODUCTION_POLICY"]["mva_vs_hold_r"] == pytest.approx(0.6)
    assert detail["execution_attribution"]["compliance_state"] == "FOLLOWED"
    assert detail["execution_attribution"]["actual_policy"] == "CLOSE_50"


def test_backfill_is_measurement_visible_but_never_policy_edge_eligible(tmp_path):
    runtime = _runtime(tmp_path)
    review_id, snap = _insert_decision(runtime, _snapshot(0), old=True)
    assert runtime.capture_new() == 1
    obs = runtime.observations()["items"][0]
    assert obs["origin"] == "RESEARCH_BACKFILL"
    assert obs["measurement_eligible"] == 0
    assert obs["policy_edge_eligible"] == 0
    assert obs["exclusion_reason"] == "NON_PROSPECTIVE_ORIGIN"
    _resolve(runtime, review_id, snap)
    assert runtime.status()["prospective_resolved"] == 0


def test_pre_t0_realized_pnl_is_not_double_counted(tmp_path):
    runtime = _runtime(tmp_path)
    review_id, snap = _insert_decision(
        runtime, _snapshot(0, remaining=0.5, realized=0.3), status="executed")
    runtime.capture_new()
    _resolve(runtime, review_id, snap, path=(0.2, 0.8, -1.0))
    obs = runtime.observations()["items"][0]
    detail = runtime.decision(obs["observation_id"])
    outcomes = {row["policy_name"]: row for row in detail["realized_outcomes"]}
    # HOLD = already-realized 0.3 + remaining 0.5 * future -1R.
    assert outcomes["HOLD"]["terminal_r"] == pytest.approx(-0.2)
    assert outcomes["HOLD"]["management_incremental_r"] == pytest.approx(-0.5)
    # CLOSE50 future on remaining = .5*.2 + .5*-1 = -.4, weighted by remaining .5.
    assert outcomes["CLOSE_50"]["terminal_r"] == pytest.approx(0.1)
    assert outcomes["CLOSE_50"]["mva_vs_hold_r"] == pytest.approx(0.3)


def test_original_plan_is_distinct_from_managed_be_ladder_continuation(tmp_path):
    runtime = _runtime(tmp_path)
    review_id, snap = _insert_decision(runtime, _snapshot(0, policy="HOLD"), status="not_required")
    runtime.capture_new()
    # Managed continuation arms BE at 1.5R; original plan has only -1/TAKE barriers.
    _resolve(runtime, review_id, snap, path=(0.2, 1.6, 0.0, 2.0))
    obs = runtime.observations()["items"][0]
    outcomes = {row["policy_name"]: row for row in runtime.decision(obs["observation_id"])["realized_outcomes"]}
    assert outcomes["ORIGINAL_PLAN"]["terminal_r"] == pytest.approx(2.0)
    assert outcomes["HOLD"]["terminal_r"] < outcomes["ORIGINAL_PLAN"]["terminal_r"]


def test_ignored_recommendation_is_not_recorded_as_policy_execution(tmp_path):
    runtime = _runtime(tmp_path)
    review_id, snap = _insert_decision(
        runtime, _snapshot(0, policy="CLOSE_50"), status="recommended_not_executed")
    runtime.capture_new()
    _resolve(runtime, review_id, snap, path=(0.2, 0.8, -1.0))
    obs = runtime.observations()["items"][0]
    attr = runtime.decision(obs["observation_id"])["execution_attribution"]
    assert attr["compliance_state"] == "IGNORED"
    assert attr["actual_policy"] == "HOLD"
    assert attr["production_terminal_r"] == pytest.approx(-0.4)
    assert attr["actual_terminal_r"] == pytest.approx(-1.0)
    assert attr["compliance_delta_r"] == pytest.approx(0.6)


def test_dependency_weighting_counts_trade_not_repeated_decisions(tmp_path):
    runtime = _runtime(tmp_path)
    for trade_id in (1, 1, 2):
        snap = _snapshot(0, trade_id=trade_id, policy="CLOSE_50")
        # unique decision ids/review timestamps for repeated observations
        snap["policy_manager"]["management_decision"]["decision_id"] += f"-{len(runtime.observations()['items'])}"
        review_id, frozen = _insert_decision(runtime, snap, status="executed")
        runtime.capture_new()
        _resolve(runtime, review_id, frozen)
    status = runtime.status()
    assert status["prospective_resolved"] == 3
    assert status["unique_trades"] == 2
    assert status["effective_n"] == pytest.approx(2.0)


def test_g1m_ledger_is_immutable_and_restart_idempotent(tmp_path):
    runtime = _runtime(tmp_path)
    review_id, snap = _insert_decision(runtime, _snapshot(0))
    runtime.capture_new()
    obs = runtime.observations()["items"][0]
    with pytest.raises(sqlite3.DatabaseError, match="immutable G1M"):
        runtime._conn.execute(
            "UPDATE g1m_management_observations SET production_policy='HOLD' "
            "WHERE observation_id=?", (obs["observation_id"],))
    runtime._conn.rollback()
    second = ManagementEdgeRuntime(runtime.engine)
    assert second.activation_ts == runtime.activation_ts
    assert second.capture_new() == 0
    assert second.observations()["items"][0]["observation_id"] == obs["observation_id"]


def test_authority_remains_research_only(tmp_path):
    runtime = _runtime(tmp_path)
    status = runtime.status()
    assert status["g1m_contract_version"] == G1M_CONTRACT_VERSION
    assert status["evidence_status"] == "NO_EVIDENCE"
    assert status["ready_for_oos"] is False
    assert status["authority"] == {
        "research_only": True,
        "production_authority": False,
        "auto_execution_allowed": False,
        "policy_promotion_allowed": False,
        "oos_validated": False,
        "edge_claim_allowed": False,
    }
