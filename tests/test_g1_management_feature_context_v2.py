from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from seiltanzer.g1_management_feature_context_v2 import (
    MANAGEMENT_CONTEXT_V2,
    build_management_context_v2,
    install_g1_management_feature_context_v2,
)
from seiltanzer.g1_management_runtime import ManagementEdgeRuntime, _json, _sha_text


install_g1_management_feature_context_v2()


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
        CREATE TABLE decision_replays(
            review_id TEXT PRIMARY KEY, trade_id INTEGER NOT NULL,
            resolved_ts REAL NOT NULL, resolution_kind TEXT NOT NULL,
            replay_version TEXT NOT NULL, replay_json TEXT NOT NULL
        );
        CREATE TABLE trades(
            id INTEGER PRIMARY KEY, instrument TEXT, direction TEXT,
            setup INTEGER, entry REAL, stop REAL, take REAL, status TEXT
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
    """)
    conn.commit()


def _metric(value, slope=None, acceleration=None, confidence=.75):
    return {
        "value": value, "slope": slope, "acceleration": acceleration,
        "noise": .01, "sample_count": 12, "time_span_minutes": 35.0,
        "confidence": confidence, "source_quality": .8, "available": True,
        "value_units": "test", "slope_units": "test/min",
        "acceleration_units": "test/min^2",
    }


def _snapshot(trade_id: int, captured_ts: float, *, current_r: float = 0.0):
    snapshots = {
        "ENTRY": {"p_take": .32, "p_stop": .20},
        "TRADE_LIFE_AVG": {"p_take": .35, "p_stop": .19},
        "PREVIOUS_AI_REVIEW": {"p_take": .37, "p_stop": .18},
        "NOW": {"p_take": .40, "p_stop": .16},
    }
    return {
        "trade_id": trade_id,
        "captured_ts": captured_ts,
        "observation": {
            "exact_levels": {"current": 100.0},
            "position": {"r": current_r},
            "cross_asset": {"available": True, "network_tension": .22},
            "macro_regime": {"available": True, "regime": "CALM TREND"},
            "wavelet": {"available": True, "dominant_period_hours": 2.0},
        },
        "position_state": {
            "remaining_position_fraction": .8,
            "realized_r_weighted": .1,
            "original_stop": 90.0,
            "active_stop_price": 95.0,
        },
        "policy_manager": {
            "version": "policy-v15-test",
            "management_decision": {"decision_id": "placeholder", "policy": "CLOSE_25"},
            # r0 intentionally disagrees with observation.r to prove a true 0.0
            # is not treated as a missing/falsy value by H2.
            "inputs": {"r0": .4, "max_r": .6, "T": 2.0,
                       "rungs": [1.0, 2.0], "rung_fraction": .10, "be_after": 1.5},
            "policies": {
                name: {"expected_net_r": .1, "median_net_r": .05,
                       "cvar10_net_r": -.7, "p_loss": .4}
                for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
            },
            "option_derivative_state": {
                "available": True,
                "metrics": {
                    "p_take": _metric(.40, .006, .0004),
                    "p_stop": _metric(.16, -.004, -.0003),
                    "p_no_touch": _metric(.44, -.002, None),
                    "barrier_ev": _metric(.64, .01, .0008),
                    "h_take": _metric(.18, .003, None),
                    "h_stop": _metric(.07, -.002, None),
                    "gex_force": _metric(.31, .015, .001),
                    "gex_stiffness": _metric(.22, .008, None),
                    "distance_to_zero_gamma": _metric(.45, -.01, None),
                },
                "first_touch_hazard": {"next_window": {"h_take": .18, "h_stop": .07}},
                "gex_geometry": {
                    "field_score": .12, "force_score": .31,
                    "stiffness_score": .22, "distance_to_zero_gamma": .45,
                    "quality": .8,
                },
                "named_derivatives": {"dP_take_dt": .006, "dP_stop_dt": -.004},
                "option_state_score": .42,
                "option_state_confidence": .74,
                "option_state_attribution": {"positive": ["dP_take_dt"]},
                "option_state_redundancy_contract": {"family_total_vote": 1},
            },
            "derived_scenario_ensemble": {
                "version": "scenario-test-v1",
                "old_policy": "HOLD",
                "candidate_policy": "CLOSE_25",
                "promotion_allowed": False,
                "drivers": {"barrier_deterioration": .6},
                "scenarios": [
                    {"name": "BASE", "weight": .7, "material": False,
                     "driver_confidence": .8, "source_quality": .8},
                    {"name": "ADVERSE", "weight": .3, "material": True,
                     "driver_confidence": .7, "source_quality": .8},
                ],
            },
            "shadow_policy_contract": {
                "new_candidate_policy": "CLOSE_25", "promotion_allowed": False},
            "state_change_attribution": {
                "snapshots": snapshots,
                "explicit_policy_effect": "shadow only; production unchanged",
            },
        },
    }


def _runtime(tmp_path):
    engine = _Engine(tmp_path / "g1m-context.sqlite3")
    _source_schema(engine.passive._conn)
    return ManagementEdgeRuntime(engine)


def _insert_source(runtime: ManagementEdgeRuntime, *, trade_id: int, captured_ts: float):
    snapshot = _snapshot(trade_id, captured_ts)
    review_id = f"review-{trade_id}-{int(captured_ts*1000)}"
    decision_id = f"decision-{trade_id}"
    snapshot["policy_manager"]["management_decision"]["decision_id"] = decision_id
    raw = _json(snapshot)
    sha = _sha_text(raw)
    runtime._conn.execute(
        "INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?,?)",
        (review_id, trade_id, captured_ts, raw, sha, "CLOSE_25", "policy-v15-test"),
    )
    runtime._conn.execute(
        "INSERT INTO management_decisions("
        "decision_id,review_id,trade_id,created_ts,policy,status,close_fraction_current,"
        "remaining_before,remaining_after,geometry_version,entry,original_stop,take_price,"
        "executed_ts,execution_price,execution_r,payload_json) "
        "VALUES(?,?,?,?,?,'pending_execution',.25,.8,.8,'geometry-test',100,90,120,"
        "NULL,NULL,NULL,'{}')",
        (decision_id, review_id, trade_id, captured_ts, "CLOSE_25"),
    )
    runtime._conn.execute(
        "INSERT INTO trades VALUES(?,?,?,?,?,?,?,?)",
        (trade_id, "NAS100", "long", 1, 100.0, 90.0, 120.0, "open"),
    )
    runtime._conn.commit()
    return review_id, sha


def test_build_context_preserves_zero_r_and_frozen_policy_state():
    captured = 2_000_000_000.0
    snapshot = _snapshot(7, captured, current_r=0.0)
    observation = {
        "observation_id": "g1m-7", "review_id": "review-7", "trade_id": 7,
        "captured_ts": captured, "production_policy": "CLOSE_25",
    }
    context = {"instrument": "NAS100", "direction": "long", "setup": 1}
    decision = {"entry": 100.0, "original_stop": 90.0, "take_price": 120.0}
    result = build_management_context_v2(snapshot, observation, context, decision)

    assert result["contract_version"] == MANAGEMENT_CONTEXT_V2
    assert result["position_geometry"]["current_r"] == 0.0
    assert result["position_geometry"]["distance_to_original_stop_r"] == 1.0
    assert result["position_geometry"]["distance_to_take_r"] == 2.0
    assert result["option_derivatives"]["metrics"]["p_take"]["slope"] == .006
    assert result["option_derivatives"]["metrics"]["gex_force"]["acceleration"] == .001
    assert result["gex"]["dealer_inventory_claim"] is False
    assert result["policy_state"]["production_policy"] == "CLOSE_25"
    assert result["policy_state"]["shadow_candidate_policy"] == "CLOSE_25"
    assert set(result["policy_state"]["entry_avg_prev_now"]) == {
        "ENTRY", "TRADE_LIFE_AVG", "PREVIOUS_AI_REVIEW", "NOW"}
    assert result["policy_state"]["production_action_changed_by_context_v2"] is False
    assert result["semantics"]["recomputed_after_t0"] is False
    assert all(row["training_enabled"] is False
               for row in result["feature_families"].values())


def test_context_v2_is_future_only_hash_linked_and_immutable(tmp_path):
    runtime = _runtime(tmp_path)
    activation = float(runtime._conn.execute(
        "SELECT activation_ts FROM g1m_feature_context_v2_activation WHERE id=1"
    ).fetchone()[0])

    old_review, _ = _insert_source(
        runtime, trade_id=10, captured_ts=activation-5.0)
    new_review, new_sha = _insert_source(
        runtime, trade_id=11, captured_ts=activation+1.0)
    assert runtime.capture_new() == 2

    assert runtime._conn.execute(
        "SELECT 1 FROM g1m_t0_feature_context_v2 WHERE review_id=?", (old_review,)
    ).fetchone() is None
    row = runtime._conn.execute(
        "SELECT * FROM g1m_t0_feature_context_v2 WHERE review_id=?", (new_review,)
    ).fetchone()
    assert row is not None
    assert row["source_snapshot_sha256"] == new_sha
    payload = json.loads(row["context_json"])
    assert payload["contract_version"] == MANAGEMENT_CONTEXT_V2
    assert payload["semantics"]["no_historical_retrofit"] is True
    assert payload["semantics"]["production_authority"] is False
    assert payload["position_geometry"]["instrument"] == "NAS100"

    with pytest.raises(sqlite3.IntegrityError, match="immutable G1M context v2 row"):
        with runtime._conn:
            runtime._conn.execute(
                "UPDATE g1m_t0_feature_context_v2 SET captured_ts=captured_ts+1 "
                "WHERE review_id=?", (new_review,))

    status = runtime.status()["management_context_v2"]
    assert status["collection_count"] is None
    assert status["collection_count_source"] == "not_scanned_on_request"
    assert status["training_enabled"] is False
    assert status["production_authority"] is False
