from __future__ import annotations

from copy import deepcopy
import json

from seiltanzer.ai_provider_guard import (
    MAX_PROVIDER_TIMEOUT_SEC,
    PROVIDER_SNAPSHOT_LIMIT_BYTES,
    compact_provider_snapshot,
    provider_timeout_sec,
)


def _policy_rows() -> dict:
    return {
        name: {
            "expected_final_r": value,
            "median_final_r": value - 0.01,
            "cvar10_r": -0.25 + value,
            "p_next_rung_before_stop": 0.55,
            "p_stop_before_next_rung": 0.25,
            "no_event_probability": {"60m": 0.2},
            "eligible": True,
        }
        for name, value in {
            "HOLD": 0.31,
            "CLOSE_10": 0.29,
            "CLOSE_25": 0.24,
            "CLOSE_50": 0.16,
            "EXIT": 0.0,
        }.items()
    }


def _rich_snapshot() -> dict:
    return {
        "captured_ts": 1234.5,
        "trade_id": "trade-1",
        "strategy": {"direction": "LONG", "instrument": "NAS100"},
        "position_state": {"remaining_position_fraction": 1.0},
        "trade_geometry": {"current": 100.0, "entry": 99.0, "current_r": 0.4},
        "time_context": {"session": "US"},
        "observation": {"exact_levels": {"entry": 99.0, "stop": 98.0, "take": 102.0}},
        "policy_manager": {
            "version": "test",
            "management_decision": {"action": "HOLD"},
            "recommendation": {"policy": "HOLD", "action_ru": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ"},
            "policies": _policy_rows(),
            "selection_rule": {"cvar_floor_r": -0.4},
            "inputs": {"r0": 0.4, "chain_status": "LIVE"},
            "derived_scenario_ensemble": {
                "scenarios": [{"name": "base", "weight": 1.0, "winner": "HOLD"}]
            },
            "evidence": {
                f"evidence_{idx:03d}": {
                    "status": "AVAILABLE",
                    "detail": "market context " * 200,
                    "rows": [{"x": j, "text": "detail " * 80} for j in range(40)],
                }
                for idx in range(100)
            },
            "input_audit": {
                "rows": {
                    f"input_{idx:03d}": {"available": True, "detail": "audit " * 150}
                    for idx in range(80)
                }
            },
            "gate": {f"gate_{idx:03d}": "gate detail " * 100 for idx in range(70)},
            "raw_optimizer_stability": {"rows": list(range(5000))},
            "monte_carlo_validation": {"rows": list(range(5000))},
            "scenario_geometry": {"rows": list(range(5000))},
        },
        "metric_coverage": {"summary": {"coverage_ratio": 0.9}},
        "metric_history": {"rows": [{"x": i, "blob": "history " * 100} for i in range(1200)]},
        "previous_reviews": [{"ts": i, "metrics": {"blob": "review " * 100}} for i in range(300)],
        "validation": {
            "observations": 500,
            "resolved_trades": 100,
            "promotion_allowed": False,
            "huge_debug": "debug " * 10000,
        },
        "ede_causal_context": {
            f"family_{idx:03d}": [{"blob": "causal " * 100} for _ in range(50)]
            for idx in range(80)
        },
        "position_management_risk_long": 0.61,
        "active_edge_provisional_weight": 0.01,
        "active_edge": {"state": "ACTIVE", "eligible": True, "candidate_id": "edge-1"},
    }


def test_provider_projection_is_bounded_non_mutating_and_keeps_action_inputs():
    snapshot = _rich_snapshot()
    original = deepcopy(snapshot)

    projected = compact_provider_snapshot(snapshot)

    assert snapshot == original
    assert projected["policy_manager"]["recommendation"] == original["policy_manager"]["recommendation"]
    assert projected["policy_manager"]["policies"] == original["policy_manager"]["policies"]
    assert projected["position_management_risk_long"] == 0.61
    assert projected["active_edge_provisional_weight"] == 0.01
    assert projected["provider_history_summary"]["previous_review_count"] == 300
    assert "metric_history" not in projected
    assert "previous_reviews" not in projected
    assert "raw_optimizer_stability" not in projected["policy_manager"]
    assert projected["provider_projection"]["authority"] == "EXPLANATION_ONLY"
    assert projected["provider_projection"]["final_bytes"] <= PROVIDER_SNAPSHOT_LIMIT_BYTES
    assert len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) < len(
        json.dumps(original, ensure_ascii=False).encode("utf-8")
    )


def test_provider_timeout_defaults_to_existing_public_hard_cap(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER_TIMEOUT_SEC", raising=False)
    assert provider_timeout_sec() == MAX_PROVIDER_TIMEOUT_SEC == 8.0

    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SEC", "99")
    assert provider_timeout_sec() == MAX_PROVIDER_TIMEOUT_SEC
