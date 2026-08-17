from __future__ import annotations

from seiltanzer import ai_verdict


def _snapshot() -> dict:
    policies = {
        "HOLD": {
            "expected_final_r": 0.42,
            "median_final_r": 0.31,
            "cvar10_r": -0.28,
            "p_final_profit": 0.64,
            "p_final_loss": 0.36,
            "p_giveback_0_25_from_now": 0.18,
            "p_giveback_0_50_from_now": 0.08,
            "p_next_rung_before_stop": 0.542,
            "p_stop_before_next_rung": 0.00046,
        },
        "CLOSE_25": {
            "expected_final_r": 0.39,
            "median_final_r": 0.30,
            "cvar10_r": -0.18,
            "p_final_profit": 0.62,
            "p_final_loss": 0.38,
        },
    }
    return {
        "trade_geometry": {
            "current_r": 0.76,
            "take_first": 0.61,
            "stop_or_be_first": 0.17,
            "no_touch": 0.22,
            "p50_resolution_minutes": 47.0,
        },
        "monte_carlo_quality": {
            "effective_paths": 6400,
            "expected_final_r_ci_width": 0.021,
            "cvar10_r_ci_width": 0.047,
        },
        "policy_manager": {
            "inputs": {"T": 2.5},
            "policies": policies,
            "input_audit": {
                "available_count": 31,
                "total_count": 40,
                "all_required_available": True,
                "missing_required": [],
                "degraded_inputs": [],
                "rows": {},
            },
            "evidence": {
                "option_barrier": {
                    "available": True,
                    "p_take": 0.58,
                    "p_stop": 0.21,
                    "no_touch": 0.21,
                    "barrier_ev_r": 0.34,
                },
            },
            "raw_optimizer_stability": {
                "selected_count": 7,
                "checks": 9,
                "selected_share": 7 / 9,
                "debug_blob": "x" * 18000,
            },
            "stability": {
                "selected_count": 8,
                "checks": 9,
                "selected_share": 8 / 9,
            },
            "scenario_geometry": {
                "scenario_count": 6500,
                "next_rung_r": 1.0,
                "p_next_rung_before_stop": 0.542,
                "rung_first_count": 3522,
                "p_stop_before_next_rung": 3 / 6500,
                "stop_first_count": 3,
                "p_unresolved_full_horizon": 0.45754,
                "unresolved_count": 2974,
                "resolved_count": 3526,
                "full_horizon_minutes": 240,
                "mean_event_minutes_given_resolved": 71.4,
                "no_event_windows": {
                    "60m": {
                        "events": 1100,
                        "scenarios": 6500,
                        "no_event_count": 5400,
                        "no_event_probability": 5400 / 6500,
                    }
                },
                "debug_blob": "y" * 18000,
            },
            "monte_carlo_validation": {
                "status": "PASS",
                "checks": 5,
                "winner": "HOLD",
                "winner_share": 0.8,
                "decision_uncertain": False,
                "debug_blob": "z" * 18000,
            },
            "risk_tradeoff": {
                "expected_delta_vs_hold_r": -0.03,
                "cvar_improvement_vs_hold_r": 0.10,
            },
            "active_edge_provisional_weight": {
                "contract_version": "active-edge-policy-weight-v2",
                "available": True,
                "weight_fraction": 0.37,
                "max_weight_fraction": 0.40,
                "direction_score": 0.75,
                "agreement": 0.75,
                "strict_directional_share": 0.70,
                "production_role": "BOUNDED_SOFT_POLICY_RANKING",
                "hard_risk_override": False,
                "may_override_cvar_floor": False,
                "may_widen_stop": False,
                "automatic_execution_source": False,
            },
        },
    }


def test_report_facts_survive_byte_compaction():
    snapshot = _snapshot()
    ai_verdict._enforce_snapshot_budget_with_report_integrity(snapshot)

    manager = snapshot["policy_manager"]
    integrity = snapshot["report_integrity"]

    assert snapshot["snapshot_budget"]["final_bytes"] < ai_verdict.SNAPSHOT_LIMIT_BYTES
    assert integrity["contract_version"] == "ai-verdict-report-integrity-v1"
    assert integrity["missing_is_zero"] is False
    assert manager["raw_optimizer_stability"]["selected_count"] == 7
    assert manager["raw_optimizer_stability"]["checks"] == 9
    assert manager["scenario_geometry"]["scenario_count"] == 6500
    assert manager["input_audit"]["available_count"] == 31
    assert manager["input_audit"]["total_count"] == 40
    assert manager["evidence"]["option_barrier"]["p_take"] == 0.58
    assert manager["active_edge_provisional_weight"]["weight_fraction"] == 0.37
    assert manager["active_edge_provisional_weight"]["production_role"] == "BOUNDED_SOFT_POLICY_RANKING"
    assert manager["policies"]["HOLD"]["p_final_loss"] == 0.36
    assert "debug_blob" not in manager["raw_optimizer_stability"]
    assert "debug_blob" not in manager["scenario_geometry"]


def test_v19_renderer_receives_real_counts_after_compaction():
    snapshot = _snapshot()
    ai_verdict._enforce_snapshot_budget_with_report_integrity(snapshot)

    risk_lines = ai_verdict._v19._risk_lines(snapshot)
    geometry_lines = ai_verdict._v19._scenario_geometry_lines(snapshot)
    quality_lines = ai_verdict._v19._quality_lines(snapshot)

    assert any("7/9" in line for line in risk_lines)
    assert not any("0/0" in line for line in risk_lines)
    assert any("6500" in line and "3522/6500" in line for line in geometry_lines)
    assert any("Input audit: 31/40" in line for line in quality_lines)


def test_prompt_forbids_missing_equals_zero_and_separates_edge_authority():
    normalized = " ".join(ai_verdict.SYSTEM_PROMPT.split())
    assert "missing/unavailable != 0" in normalized
    assert "active_edge_provisional_weight" in normalized
    assert "EDE causal/prospective shadow" in normalized
    assert "execution-MC" in normalized
    assert "PRIMARY → FALLBACK_SOURCE → LAST_GOOD_CACHE → MATHEMATICAL_PROXY" in normalized
