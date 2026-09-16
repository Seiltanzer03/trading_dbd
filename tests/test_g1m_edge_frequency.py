from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from seiltanzer.g1_management_edge_frequency import (
    EDGE_FREQUENCY_VERSION,
    _ensure_tables,
    _store_edge_decision_t0,
    edge_frequency,
)


class _Runtime:
    pass


def _runtime() -> _Runtime:
    runtime = _Runtime()
    runtime._lock = threading.RLock()
    runtime._conn = sqlite3.connect(":memory:", check_same_thread=False)
    runtime._conn.row_factory = sqlite3.Row
    runtime._conn.execute("""
        CREATE TABLE g1m_management_observations(
            observation_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL UNIQUE,
            origin TEXT NOT NULL,
            current_r REAL,
            remaining_before REAL NOT NULL
        )
    """)
    runtime._conn.commit()
    _ensure_tables(runtime)
    return runtime


def _snapshot(*, matched: bool) -> dict:
    if matched:
        exploratory = {
            "contract_version": "llm-edge-exploratory-policy-weight-v1",
            "available": True,
            "weight_fraction": 0.15,
            "direction_score": -1.0,
            "preferred_close_fraction": 1.0,
            "matched_limited_hypothesis_n": 2,
            "matched_status_counts": {
                "EARLY_ADVANTAGE": 1,
                "EARLY_DISADVANTAGE": 1,
            },
            "matched_advantage_supporting_n": 0,
            "matched_advantage_opposing_n": 1,
            "matched_horizons": [30, 60],
            "matched_target_families": ["RETURN", "FORWARD_VOLATILITY"],
            "matched_signals": [{
                "hypothesis_id": "early-opposes",
                "status": "EARLY_ADVANTAGE",
                "target_family": "RETURN",
                "horizon_minutes": 30,
                "position_relation": "OPPOSES_POSITION",
            }],
        }
        combined = {
            "available": True,
            "weight_fraction": 0.15,
            "direction_score": -1.0,
            "preferred_close_fraction": 1.0,
        }
        audit = {
            "applied": True,
            "raw_policy_without_edge": "HOLD",
            "raw_policy_with_edge": "CLOSE_25",
            "raw_policy_changed": True,
            "raw_policy_transition": "HOLD->CLOSE_25",
            "counterfactual_scope": "same_frozen_metrics_before_soft_edge_blend",
        }
        recommendation = "CLOSE_25"
    else:
        exploratory = {
            "contract_version": "llm-edge-exploratory-policy-weight-v1",
            "available": False,
            "weight_fraction": 0.0,
            "reason": "NO_MATCHED_LIMITED_DIRECTIONAL_ADVANTAGES",
            "matched_limited_hypothesis_n": 0,
            "matched_status_counts": {},
            "matched_horizons": [],
            "matched_target_families": [],
        }
        combined = {"available": False, "weight_fraction": 0.0,
                    "direction_score": 0.0, "reason": "NO_EDGE_WEIGHT"}
        audit = {}
        recommendation = "HOLD"
    return {
        "strategy": {"instrument": "XAUUSD", "direction": "long"},
        "policy_manager": {
            "llm_edge_exploratory_weight": exploratory,
            "active_edge_provisional_weight": {
                "contract_version": "active-edge-policy-weight-v3",
                "available": False,
                "weight_fraction": 0.0,
                "matched_directional_signal_n": 0,
                "reason": "NO_DIRECTIONAL_MATCHED_EDGE_GROUPS",
            },
            "combined_edge_soft_weight": combined,
            "selection_rule": {"combined_edge_soft_weight": audit},
            "recommendation": {"policy": recommendation},
            "management_decision": {"policy": recommendation},
        },
        "ede_causal_context": {"active_high_risk": {
            "supporting_position_n": 0,
            "opposing_position_n": 0,
            "matched_groups": [],
        }},
    }


def _store(runtime: _Runtime, index: int, *, matched: bool) -> bool:
    activation = runtime._conn.execute(
        "SELECT activation_ts FROM g1m_edge_frequency_activation WHERE id=1"
    ).fetchone()[0]
    review_id = f"review-{index}"
    runtime._conn.execute(
        "INSERT INTO g1m_management_observations VALUES(?,?,?,?,?)",
        (f"observation-{index}", review_id, "LIVE_PROSPECTIVE", -0.1, 1.0),
    )
    runtime._conn.commit()
    return _store_edge_decision_t0(runtime, {
        "review_id": review_id,
        "trade_id": index,
        "captured_ts": float(activation) + index,
        "snapshot_json": json.dumps(_snapshot(matched=matched)),
    })


def test_future_ledger_is_immutable_and_measures_real_policy_influence():
    runtime = _runtime()
    assert _store(runtime, 1, matched=True) is True
    assert _store(runtime, 2, matched=False) is True

    report = edge_frequency(runtime)
    assert report["contract_version"] == EDGE_FREQUENCY_VERSION
    all_time = report["windows"]["all"]
    assert all_time["captured_reviews"] == 2
    assert all_time["exploratory_eligible_reviews"] == 2
    assert all_time["unique_trades"] == 2
    assert all_time["early_any_match_reviews"] == 1
    assert all_time["early_any_match_rate"] == 0.5
    assert all_time["early_advantage_reviews"] == 1
    assert all_time["early_disadvantage_reviews"] == 1
    assert all_time["active_any_match_reviews"] == 0
    assert all_time["any_edge_match_rate"] == 0.5
    assert all_time["opposes_position_reviews"] == 1
    assert all_time["opposes_position_rate"] == 0.5
    assert all_time["raw_choice_changed_reviews"] == 1
    assert all_time["weighted_raw_reached_recommendation_reviews"] == 1
    assert all_time["raw_policy_transitions"] == {"HOLD->CLOSE_25": 1}
    assert all_time["combined_weight"]["median"] == 0.15
    assert all_time["by_signal_horizon"] == [
        {"horizon_minutes": 30, "matched_reviews": 1},
        {"horizon_minutes": 60, "matched_reviews": 1},
    ]
    assert report["authority"]["production_authority"] is False
    assert report["historical_backfill_included"] is False

    row = runtime._conn.execute(
        "SELECT * FROM g1m_edge_decision_t0 WHERE review_id='review-1'"
    ).fetchone()
    assert row["signal_state"] == "OPPOSES_POSITION"
    assert row["raw_policy_without_edge"] == "HOLD"
    assert row["raw_policy_with_edge"] == "CLOSE_25"
    assert len(row["context_sha256"]) == 64
    with pytest.raises(sqlite3.IntegrityError, match="immutable G1M edge-frequency row"):
        runtime._conn.execute(
            "UPDATE g1m_edge_decision_t0 SET combined_weight=0 WHERE review_id='review-1'"
        )


def test_pre_activation_snapshot_is_not_backfilled_as_prospective_truth():
    runtime = _runtime()
    activation = runtime._conn.execute(
        "SELECT activation_ts FROM g1m_edge_frequency_activation WHERE id=1"
    ).fetchone()[0]
    runtime._conn.execute(
        "INSERT INTO g1m_management_observations VALUES(?,?,?,?,?)",
        ("old-observation", "old-review", "LIVE_PROSPECTIVE", 0.0, 1.0),
    )
    runtime._conn.commit()
    stored = _store_edge_decision_t0(runtime, {
        "review_id": "old-review",
        "trade_id": 99,
        "captured_ts": float(activation) - 1.0,
        "snapshot_json": json.dumps(_snapshot(matched=True)),
    })
    assert stored is False
    assert runtime._conn.execute(
        "SELECT COUNT(*) FROM g1m_edge_decision_t0"
    ).fetchone()[0] == 0
