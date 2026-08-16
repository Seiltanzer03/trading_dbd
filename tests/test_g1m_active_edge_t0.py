from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from seiltanzer.g1_management_active_edge_t0 import (
    G1M_ACTIVE_EDGE_T0_VERSION,
    MAX_FROZEN_SIGNALS,
    _ensure_active_edge_tables,
    _store_active_edge_t0,
    compact_active_edge_t0,
)


def _snapshot(*, available=True, signals=None, groups=None):
    signals = list(signals or [])
    groups = list(groups or [])
    return {
        "policy_manager": {
            "evidence": {
                "active_high_risk_edge": {
                    "edge_policy": "g1s-manual-trader-high-risk-edge-policy-v1",
                    "available": available,
                    "risk_acceptance": "HIGH_FALSE_DISCOVERY_TOLERANCE",
                    "matched_structured_signal_n": 4,
                    "supporting_position_n": 3,
                    "opposing_position_n": 1,
                    # Deliberately inconsistent: freezer must recompute 3-1.
                    "net_position_vote": 999,
                }
            }
        },
        "ede_causal_context": {
            "active_high_risk": {
                "contract_version": "ai-active-high-risk-edge-context-v1",
                "edge_policy": "g1s-manual-trader-high-risk-edge-policy-v1",
                "available": available,
                "risk_acceptance": "HIGH_FALSE_DISCOVERY_TOLERANCE",
                "matched_groups": groups,
                "signals": signals,
            }
        },
    }


def _signal(index, *, source="STRUCTURED", strict=False):
    return {
        "candidate_id": f"candidate-{index}",
        "source": source,
        "target_id": "FORWARD_VOL_RATIO",
        "horizon_minutes": 60,
        "primary_improvement": 0.01 + index / 1000,
        "q_value_diagnostic": 0.2,
        "fold_positive": 4,
        "strict_reference_qualified": strict,
        "conditions_match_current_t0": source == "STRUCTURED",
        "market_bias": "BULLISH" if source == "STRUCTURED" else "NON_DIRECTIONAL_MODEL_CONFIRMATION",
        "position_relation": "SUPPORTS_POSITION" if source == "STRUCTURED" else "CONTEXT_ONLY",
    }


def test_compact_active_edge_t0_is_bounded_and_recomputes_vote():
    signals = [
        _signal(i, source="ML" if i in {1, 4, 7, 9} else "STRUCTURED", strict=i in {0, 2})
        for i in range(10)
    ]
    frozen = compact_active_edge_t0(_snapshot(signals=signals))

    assert frozen["contract_version"] == G1M_ACTIVE_EDGE_T0_VERSION
    assert frozen["available"] is True
    assert frozen["edge_policy"] == "g1s-manual-trader-high-risk-edge-policy-v1"
    assert frozen["risk_acceptance"] == "HIGH_FALSE_DISCOVERY_TOLERANCE"
    assert frozen["matched_structured_signal_n"] == 4
    assert frozen["supporting_position_n"] == 3
    assert frozen["opposing_position_n"] == 1
    assert frozen["net_position_vote"] == 2
    assert frozen["net_position_vote_ratio"] == 0.5
    assert len(frozen["signals"]) == MAX_FROZEN_SIGNALS
    assert frozen["ml_signal_n"] == 3  # Legacy/fallback snapshots use bounded rows.
    assert frozen["strict_reference_signal_n"] == 2
    assert frozen["decision_weight_applied"] is False
    assert frozen["production_authority"] is False
    assert frozen["automatic_execution"] is False
    assert frozen["auto_promotion"] is False


def test_compact_active_edge_t0_preserves_full_pretruncation_aggregates():
    groups = [{
        "target_id": "FORWARD_VOL_RATIO",
        "target_family": "VOLATILITY",
        "signal_horizon_minutes": 60,
        "matched_n": 9,
        "supporting_n": 7,
        "opposing_n": 2,
        "strict_matched_n": 3,
        "strict_supporting_n": 2,
        "strict_opposing_n": 1,
    }]
    snapshot = _snapshot(signals=[_signal(i) for i in range(8)], groups=groups)
    summary = snapshot["policy_manager"]["evidence"]["active_high_risk_edge"]
    summary.update({
        "aggregate_scope": "ALL_ACTIVE_CANDIDATES_WITH_ALL_MATCHED_STRUCTURED_VOTES",
        "total_active_signal_n": 143,
        "structured_signal_n": 127,
        "ml_signal_n": 16,
        "strict_reference_signal_n": 19,
        "matched_strict_reference_signal_n": 3,
        "strict_supporting_position_n": 2,
        "strict_opposing_position_n": 1,
        "serialized_signal_n": 8,
    })
    frozen = compact_active_edge_t0(snapshot)
    assert frozen["aggregate_scope"] == "ALL_ACTIVE_CANDIDATES_WITH_ALL_MATCHED_STRUCTURED_VOTES"
    assert frozen["total_active_signal_n"] == 143
    assert frozen["structured_signal_n"] == 127
    assert frozen["ml_signal_n"] == 16
    assert frozen["strict_reference_signal_n"] == 19
    assert frozen["matched_strict_reference_signal_n"] == 3
    assert frozen["strict_supporting_position_n"] == 2
    assert frozen["strict_opposing_position_n"] == 1
    assert frozen["strict_net_position_vote"] == 1
    assert frozen["high_risk_only_supporting_position_n"] == 1
    assert frozen["high_risk_only_opposing_position_n"] == 0
    assert frozen["high_risk_only_net_position_vote"] == 1
    assert frozen["serialized_signal_n"] == 8
    assert len(frozen["signals"]) == 8
    assert frozen["matched_group_n"] == 1
    assert frozen["matched_groups"][0]["target_family"] == "VOLATILITY"
    assert frozen["matched_groups"][0]["net_vote"] == 5
    assert frozen["matched_groups"][0]["strict_net_vote"] == 1


def test_compact_active_edge_t0_explicitly_freezes_absence():
    frozen = compact_active_edge_t0({})
    assert frozen["available"] is False
    assert frozen["matched_structured_signal_n"] == 0
    assert frozen["supporting_position_n"] == 0
    assert frozen["opposing_position_n"] == 0
    assert frozen["net_position_vote"] == 0
    assert frozen["ml_signal_n"] == 0
    assert frozen["signals"] == []
    assert frozen["matched_groups"] == []
    assert frozen["production_authority"] is False


def test_sidecar_is_hash_frozen_and_immutable():
    class Runtime:
        pass

    runtime = Runtime()
    runtime._lock = threading.RLock()
    runtime._conn = sqlite3.connect(":memory:", check_same_thread=False)
    runtime._conn.row_factory = sqlite3.Row
    runtime._conn.execute("""
        CREATE TABLE g1m_management_observations(
            observation_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL UNIQUE,
            captured_ts REAL NOT NULL
        )
    """)
    runtime._conn.execute(
        "INSERT INTO g1m_management_observations VALUES(?,?,?)",
        ("g1m-test-observation", "review-test", 1234.5),
    )
    runtime._conn.commit()
    _ensure_active_edge_tables(runtime)

    source = {
        "review_id": "review-test",
        "snapshot_json": json.dumps(
            _snapshot(signals=[_signal(1, source="ML"), _signal(2, strict=True)]),
            sort_keys=True,
        ),
    }
    assert _store_active_edge_t0(runtime, source) is True
    row = runtime._conn.execute("SELECT * FROM g1m_active_edge_t0").fetchone()
    assert row["observation_id"] == "g1m-test-observation"
    assert row["review_id"] == "review-test"
    assert row["contract_version"] == G1M_ACTIVE_EDGE_T0_VERSION
    assert row["available"] == 1
    assert row["net_position_vote"] == 2
    assert row["ml_signal_n"] == 1
    assert len(row["context_sha256"]) == 64
    payload = json.loads(row["context_json"])
    assert payload["production_authority"] is False
    assert payload["decision_weight_applied"] is False

    with pytest.raises(sqlite3.IntegrityError, match="immutable G1M active-edge T0 row"):
        runtime._conn.execute(
            "UPDATE g1m_active_edge_t0 SET net_position_vote=0 "
            "WHERE observation_id='g1m-test-observation'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable G1M active-edge T0 row"):
        runtime._conn.execute(
            "DELETE FROM g1m_active_edge_t0 WHERE observation_id='g1m-test-observation'"
        )
