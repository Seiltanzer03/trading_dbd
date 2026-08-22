from __future__ import annotations

from copy import deepcopy

from seiltanzer.ai_provider_guard import (
    PROVIDER_SNAPSHOT_LIMIT_BYTES,
    _json_bytes,
    compact_provider_snapshot,
)


def _large_management_snapshot() -> dict:
    policies = {}
    for index, name in enumerate(("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")):
        policies[name] = {
            "name": name,
            "close_fraction": (0.0, 0.1, 0.25, 0.5, 1.0)[index],
            "expected_final_r": round(0.20 - index * 0.03, 3),
            "median_final_r": round(0.18 - index * 0.02, 3),
            "cvar10_r": round(-0.35 + index * 0.04, 3),
            "p_final_profit": 0.55,
            "p_final_loss": 0.30,
            "p_next_rung_before_stop": 0.32,
            "p_stop_before_next_rung": 0.31,
            "next_rung_r": 1.0,
            "eligible": True,
            "reason": "eligible under deterministic hard-risk contract",
        }
    return {
        "captured_ts": 1_787_306_000.0,
        "trade_id": 42,
        "strategy": {"symbol": "NAS100", "direction": "long", "setup": "test"},
        "position_state": {"remaining_fraction": 1.0, "quantity": 1.0},
        "observation": {
            "symbol": "NAS100",
            "price": 10_735.0,
            "exact_levels": {
                "entry": 10_735.0,
                "stop": 10_685.0,
                "current": 10_742.0,
                "take": 10_862.0,
            },
        },
        "policy_manager": {
            "recommendation": {
                "policy": "HOLD",
                "action_ru": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
                "raw_optimizer_policy": "HOLD",
                "remaining_fraction": 1.0,
                "next_rung_r": 1.0,
                "automatic_execution_allowed": False,
            },
            "management_decision": {
                "decision_id": "decision-42",
                "policy": "HOLD",
                "execution_status": "strategy_active",
                "instruction_ru": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
                "continuity": "new_decision",
            },
            "policies": policies,
            "selection_rule": {"cvar_floor_r": -0.5, "winner": "HOLD"},
            "inputs": {
                "r0": 0.14,
                "sigma_R": 0.9,
                "drift_R": 0.02,
                "skew_R": -0.01,
                "term_slope": 0.03,
                "horizon_minutes": 240,
                "chain_age_sec": 30.0,
                "chain_status": "live",
                "proxy_quality": "DIRECT",
            },
            "gate": {"status": "confirmed_hold", "automatic_execution_allowed": False},
            "scenario_geometry": {
                "scenario_count": 6500,
                "p_next_rung_before_stop": 0.318,
                "p_stop_before_next_rung": 0.320,
                "p_unresolved_full_horizon": 0.362,
                "full_horizon_minutes": 240,
            },
            # These are valid canonical research/debug surfaces but must never be
            # copied wholesale into the provider request.
            "derived_scenario_ensemble": {
                "paths": [{"debug": "x" * 8_000, "values": list(range(100))} for _ in range(80)]
            },
            "execution_cost_sensitivity": {
                "grid": [{"debug": "y" * 4_000} for _ in range(60)]
            },
            "decision_inputs": {f"metric_{i}": "z" * 2_000 for i in range(80)},
            "decision_influence": {f"metric_{i}": "q" * 2_000 for i in range(80)},
            "influence_report": {"workspace": "w" * 200_000},
            "evidence": {
                "live_price": {"available": True, "value": 10_742.0},
                "option_barrier": {"available": True, "p_take": 0.318, "p_stop": 0.320},
            },
            "input_audit": {
                "available_count": 8,
                "total_count": 12,
                "rows": {f"family_{i}": {"available": True, "status": "ok"} for i in range(40)},
            },
        },
        "ede_causal_context": {
            "contract_version": "test",
            "families": ["OPTIONS", "PRICE", "VOLATILITY"],
            "production_authority": False,
            "production_directional_authority": False,
            "workspace": "e" * 100_000,
        },
        "metric_history": [{"blob": "m" * 20_000} for _ in range(20)],
        "previous_reviews": [{"blob": "r" * 20_000} for _ in range(20)],
    }


def test_oversized_canonical_snapshot_is_projected_not_rejected():
    snapshot = _large_management_snapshot()
    original = deepcopy(snapshot)

    projected = compact_provider_snapshot(snapshot)

    assert _json_bytes(snapshot) > PROVIDER_SNAPSHOT_LIMIT_BYTES
    assert _json_bytes(projected) <= PROVIDER_SNAPSHOT_LIMIT_BYTES
    assert projected["provider_projection"]["authority"] == "EXPLANATION_ONLY"
    assert projected["provider_projection"]["compaction_tier"] in {"minimal", "essential"}
    assert projected["provider_projection"]["canonical_snapshot_unchanged"] is True
    assert projected["policy_manager"]["recommendation"]["policy"] == "HOLD"
    assert projected["policy_manager"]["recommendation"]["action_ru"] == "НЕ СОКРАЩАТЬ ПОЗИЦИЮ"
    assert set(projected["policy_manager"]["policies"]) == {
        "HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"
    }
    for name, row in projected["policy_manager"]["policies"].items():
        assert row["expected_final_r"] == original["policy_manager"]["policies"][name]["expected_final_r"]
        assert row["cvar10_r"] == original["policy_manager"]["policies"][name]["cvar10_r"]
    assert snapshot == original


def test_projection_budget_metadata_matches_actual_payload_size():
    projected = compact_provider_snapshot(_large_management_snapshot())
    recorded = projected["provider_projection"]["final_bytes"]
    actual = _json_bytes(projected)
    # final_bytes contains its own decimal representation, so the second pass
    # should make metadata exact for stable-size values used in production.
    assert recorded == actual
    assert actual <= PROVIDER_SNAPSHOT_LIMIT_BYTES
