from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from seiltanzer.decision_research import counterfactual_replay
from seiltanzer.g1_management_refinement import install_g1_management_refinement
from seiltanzer.g1_management_execution_refinement import install_g1_management_execution_refinement
from seiltanzer.g1_management_runtime import (
    G1M_CONTRACT_VERSION,
    ManagementEdgeRuntime,
    _json,
    _sha_text,
)

install_g1_management_refinement()
install_g1_management_execution_refinement()


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
            id INTEGER PRIMARY KEY, instrument TEXT, direction TEXT,
            setup INTEGER, entry REAL, stop REAL, take REAL, status TEXT
        );
    """)
    conn.commit()


def _snapshot(*, trade_id=1, policy="CLOSE_50", remaining=1.0,
              realized=0.0, demo=False):
    return {
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
            "management_decision": {"decision_id": "placeholder", "policy": policy},
            "inputs": {
                "r0": 0.2, "max_r": 0.2, "T": 2.0,
                "rungs": [1.0, 2.0], "rung_fraction": 0.10, "be_after": 1.5,
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


def _insert_decision(runtime, snapshot, *, status="pending_execution", old=False):
    conn = runtime._conn
    n = int(conn.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0])
    ts = runtime.activation_ts - 100.0 - n if old else runtime.activation_ts + 1.0 + n
    snapshot = json.loads(json.dumps(snapshot))
    snapshot["captured_ts"] = ts
    decision_id = f"decision-{snapshot['trade_id']}-{n}"
    snapshot["policy_manager"]["management_decision"]["decision_id"] = decision_id
    review_id = f"review-{snapshot['trade_id']}-{int(ts * 1000)}"
    raw = _json(snapshot)
    sha = _sha_text(raw)
    policy = snapshot["policy_manager"]["management_decision"]["policy"]
    conn.execute(
        "INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?,?)",
        (review_id, snapshot["trade_id"], ts, raw, sha, policy, "policy-test-v1"),
    )
    executed = status == "executed"
    conn.execute(
        "INSERT INTO management_decisions("
        "decision_id,review_id,trade_id,created_ts,policy,status,"
        "close_fraction_current,remaining_before,remaining_after,geometry_version,"
        "entry,original_stop,take_price,executed_ts,execution_price,execution_r,payload_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision_id, review_id, snapshot["trade_id"], ts, policy, status,
         {"HOLD": 0.0, "CLOSE_10": 0.1, "CLOSE_25": 0.25,
          "CLOSE_50": 0.5, "EXIT": 1.0}[policy],
         snapshot["position_state"]["remaining_position_fraction"],
         snapshot["position_state"]["remaining_position_fraction"],
         "geometry-test", 100.0, 90.0, 120.0,
         ts + 15 if executed else None, 103.0 if executed else None,
         0.3 if executed else None, "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?)",
        (snapshot["trade_id"], "NAS100", "long", 1,
         100.0, 90.0, 120.0, "open"),
    )
    conn.commit()
    return review_id, decision_id, snapshot


def _event(runtime, *, trade_id, ts, event_type, before, closed, after,
           execution_r=None, decision_id=None, review_id=None, source="test_ledger"):
    runtime._conn.execute(
        "INSERT INTO position_management_events("
        "trade_id,timestamp,event_type,source,review_id,decision_id,"
        "fraction_before,fraction_closed,fraction_after,execution_price,execution_r,"
        "original_stop,active_stop,take,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, ts, event_type, source, review_id, decision_id,
         before, closed, after, 103.0 if execution_r is not None else None,
         execution_r, 90.0, 90.0, 120.0, "{}"),
    )
    runtime._conn.commit()


def _open_ledger(runtime, snapshot):
    _event(runtime, trade_id=snapshot["trade_id"], ts=snapshot["captured_ts"] - 100,
           event_type="TRADE_OPEN", before=1.0, closed=0.0, after=1.0,
           execution_r=0.0)


def _followed_ledger(runtime, review_id, decision_id, snapshot):
    _open_ledger(runtime, snapshot)
    _event(runtime, trade_id=snapshot["trade_id"], ts=snapshot["captured_ts"] + 15,
           event_type="AI_CLOSE_50", before=1.0, closed=0.5, after=0.5,
           execution_r=0.3, decision_id=decision_id, review_id=review_id,
           source="human_confirmed_ai")
    _event(runtime, trade_id=snapshot["trade_id"], ts=snapshot["captured_ts"] + 240,
           event_type="STOP_EXIT", before=0.5, closed=0.5, after=0.0,
           execution_r=-1.0)
    # Actual observed terminal = .5*.3 + .5*-1 = -.35R.


def _ignored_ledger(runtime, snapshot):
    _open_ledger(runtime, snapshot)
    _event(runtime, trade_id=snapshot["trade_id"], ts=snapshot["captured_ts"] + 240,
           event_type="MANUAL_EXIT", before=1.0, closed=1.0, after=0.0,
           execution_r=-0.6)


def _resolve(runtime, review_id, snapshot, path=(0.2, 0.8, -1.0)):
    points = []
    for i, r in enumerate(path):
        point = {"ts": snapshot["captured_ts"] + i * 60.0,
                 "r": float(r), "price": 102.0 + i}
        points.append(point)
        runtime._conn.execute(
            "INSERT INTO decision_path_points VALUES(?,?,?,?)",
            (review_id, point["ts"], point["price"], point["r"]),
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


def test_policy_edge_and_actual_execution_are_separate(tmp_path):
    runtime = _runtime(tmp_path)
    review, decision, snap = _insert_decision(runtime, _snapshot(), status="executed")
    assert runtime.capture_new() == 1
    _followed_ledger(runtime, review, decision, snap)
    _resolve(runtime, review, snap)
    obs = runtime.observations()["items"][0]
    detail = runtime.decision(obs["observation_id"])
    outcomes = {row["policy_name"]: row for row in detail["realized_outcomes"]}
    assert outcomes["HOLD"]["terminal_r"] == pytest.approx(-1.0)
    assert outcomes["CLOSE_50"]["terminal_r"] == pytest.approx(-0.4)
    assert outcomes["PRODUCTION_POLICY"]["mva_vs_hold_r"] == pytest.approx(0.6)
    attr = detail["execution_attribution"]
    payload = json.loads(attr["attribution_json"])
    assert attr["compliance_state"] == "FOLLOWED"
    assert attr["actual_terminal_r"] == pytest.approx(-0.35)
    assert attr["actual_terminal_r"] != pytest.approx(outcomes["CLOSE_50"]["terminal_r"])
    assert attr["compliance_delta_r"] == pytest.approx(-0.05)
    assert payload["execution_source"] == "POSITION_MANAGEMENT_EVENT_LEDGER"
    assert payload["decision_execution_event"]["execution_r"] == pytest.approx(0.3)
    assert payload["broker_confirmed"] is False
    assert runtime.status()["execution_edge_resolved_n"] == 1


def test_ignored_recommendation_uses_observed_ledger_not_fake_hold(tmp_path):
    runtime = _runtime(tmp_path)
    review, _, snap = _insert_decision(
        runtime, _snapshot(policy="CLOSE_50"), status="recommended_not_executed")
    runtime.capture_new()
    _ignored_ledger(runtime, snap)
    _resolve(runtime, review, snap)
    obs = runtime.observations()["items"][0]
    detail = runtime.decision(obs["observation_id"])
    attr = detail["execution_attribution"]
    outcomes = {row["policy_name"]: row for row in detail["realized_outcomes"]}
    assert attr["compliance_state"] == "IGNORED"
    assert attr["actual_policy"] == "OBSERVED_POSITION_LEDGER"
    assert attr["actual_terminal_r"] == pytest.approx(-0.6)
    assert outcomes["HOLD"]["terminal_r"] == pytest.approx(-1.0)
    assert attr["actual_terminal_r"] != pytest.approx(outcomes["HOLD"]["terminal_r"])


def test_open_position_has_no_execution_terminal_truth(tmp_path):
    runtime = _runtime(tmp_path)
    review, decision, snap = _insert_decision(runtime, _snapshot(), status="executed")
    runtime.capture_new()
    _open_ledger(runtime, snap)
    _event(runtime, trade_id=1, ts=snap["captured_ts"] + 15,
           event_type="AI_CLOSE_50", before=1.0, closed=0.5, after=0.5,
           execution_r=0.3, decision_id=decision, review_id=review)
    _resolve(runtime, review, snap)
    obs = runtime.observations()["items"][0]
    attr = runtime.decision(obs["observation_id"])["execution_attribution"]
    assert attr["actual_terminal_r"] is None
    assert runtime.status()["execution_edge_resolved_n"] == 0


def test_backfill_never_enters_policy_edge(tmp_path):
    runtime = _runtime(tmp_path)
    review, _, snap = _insert_decision(runtime, _snapshot(), old=True)
    assert runtime.capture_new() == 1
    obs = runtime.observations()["items"][0]
    assert obs["origin"] == "RESEARCH_BACKFILL"
    assert obs["policy_edge_eligible"] == 0
    assert obs["exclusion_reason"] == "NON_PROSPECTIVE_ORIGIN"
    _open_ledger(runtime, snap)
    _event(runtime, trade_id=1, ts=snap["captured_ts"] + 10,
           event_type="MANUAL_EXIT", before=1.0, closed=1.0, after=0.0,
           execution_r=-1.0)
    _resolve(runtime, review, snap)
    assert runtime.status()["prospective_resolved"] == 0


def test_decision_after_known_outcome_is_rejected(tmp_path):
    runtime = _runtime(tmp_path)
    review, _, snap = _insert_decision(runtime, _snapshot())
    replay = counterfactual_replay(snap, [{
        "ts": snap["captured_ts"], "price": 102.0, "r": 0.2,
    }])
    runtime._conn.execute(
        "INSERT INTO decision_replays VALUES(?,?,?,?,?,?)",
        (review, 1, snap["captured_ts"] - 1.0,
         "invalid_known_outcome", replay["version"], _json(replay)),
    )
    runtime._conn.commit()
    assert runtime.capture_new() == 0
    error = runtime._conn.execute(
        "SELECT error_code,critical FROM g1m_contract_errors ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert error["error_code"] == "DECISION_AFTER_OUTCOME"
    assert error["critical"] == 1


def test_pre_t0_realized_pnl_is_not_double_counted(tmp_path):
    runtime = _runtime(tmp_path)
    review, _, snap = _insert_decision(
        runtime, _snapshot(remaining=0.5, realized=0.3), status="pending_execution")
    runtime.capture_new()
    _resolve(runtime, review, snap)
    obs = runtime.observations()["items"][0]
    outcomes = {row["policy_name"]: row for row in runtime.decision(obs["observation_id"])["realized_outcomes"]}
    assert outcomes["HOLD"]["terminal_r"] == pytest.approx(-0.2)
    assert outcomes["HOLD"]["management_incremental_r"] == pytest.approx(-0.5)
    assert outcomes["CLOSE_50"]["terminal_r"] == pytest.approx(0.1)
    assert outcomes["CLOSE_50"]["mva_vs_hold_r"] == pytest.approx(0.3)


def test_original_plan_is_distinct_from_managed_continuation(tmp_path):
    runtime = _runtime(tmp_path)
    review, _, snap = _insert_decision(runtime, _snapshot(policy="HOLD"), status="not_required")
    runtime.capture_new()
    _resolve(runtime, review, snap, path=(0.2, 1.6, 0.0, 2.0))
    obs = runtime.observations()["items"][0]
    outcomes = {row["policy_name"]: row for row in runtime.decision(obs["observation_id"])["realized_outcomes"]}
    assert outcomes["ORIGINAL_PLAN"]["terminal_r"] == pytest.approx(2.0)
    assert outcomes["HOLD"]["terminal_r"] < outcomes["ORIGINAL_PLAN"]["terminal_r"]


def test_dependency_weighting_counts_trades_not_decisions(tmp_path):
    runtime = _runtime(tmp_path)
    for trade_id in (1, 1, 2):
        review, _, snap = _insert_decision(runtime, _snapshot(trade_id=trade_id))
        runtime.capture_new()
        _resolve(runtime, review, snap)
    status = runtime.status()
    assert status["prospective_resolved"] == 3
    assert status["unique_trades"] == 2
    assert status["dependency_groups"] == 2
    assert status["effective_n"] == pytest.approx(2.0)


def test_cohort_identity_is_frozen_at_t0(tmp_path):
    runtime = _runtime(tmp_path)
    _insert_decision(runtime, _snapshot())
    runtime.capture_new()
    runtime._conn.execute(
        "UPDATE trades SET instrument='SP500',direction='short',setup=2 WHERE id=1"
    )
    runtime._conn.commit()
    cohort = runtime.cohorts()["items"][0]
    assert (cohort["instrument"], cohort["direction"], cohort["setup"]) == (
        "NAS100", "long", 1)


def test_research_cut_contains_only_resolved_prospective_rows(tmp_path):
    runtime = _runtime(tmp_path)
    old_review, _, old_snap = _insert_decision(runtime, _snapshot(trade_id=1), old=True)
    runtime.capture_new(); _resolve(runtime, old_review, old_snap)
    live_review, _, live_snap = _insert_decision(runtime, _snapshot(trade_id=2))
    runtime.capture_new(); _resolve(runtime, live_review, live_snap)
    cut = runtime.create_research_cut(cutoff_ts=live_snap["captured_ts"] + 400)
    assert cut["raw_n"] == 1
    assert cut["unique_trade_n"] == 1
    assert cut["effective_n"] == pytest.approx(1.0)
    with pytest.raises(sqlite3.DatabaseError, match="immutable G1M"):
        runtime._conn.execute(
            "UPDATE g1m_research_cuts SET raw_n=99 WHERE cut_id=?", (cut["cut_id"],)
        )
    runtime._conn.rollback()


def test_ledgers_errors_and_activation_are_immutable_across_restart(tmp_path):
    runtime = _runtime(tmp_path)
    _insert_decision(runtime, _snapshot())
    runtime.capture_new()
    obs = runtime.observations()["items"][0]
    with pytest.raises(sqlite3.DatabaseError, match="immutable G1M"):
        runtime._conn.execute(
            "UPDATE g1m_management_observations SET production_policy='HOLD' "
            "WHERE observation_id=?", (obs["observation_id"],))
    runtime._conn.rollback()
    runtime._error(code="TEST_ERROR", detail="x")
    with pytest.raises(sqlite3.DatabaseError, match="immutable G1M"):
        runtime._conn.execute("DELETE FROM g1m_contract_errors")
    runtime._conn.rollback()
    restarted = ManagementEdgeRuntime(runtime.engine)
    assert restarted.activation_ts == runtime.activation_ts
    assert restarted.capture_new() == 0


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
