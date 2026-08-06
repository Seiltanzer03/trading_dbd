from seiltanzer.ai_policy import PolicyInputs, select_final_policy


def test_unresolved_gate_conflict_cannot_be_marked_auto_executable():
    metrics = {
        "HOLD": {"expected_final_r": .379, "cvar10_r": -.886},
        "CLOSE_10": {"expected_final_r": .372, "cvar10_r": -.767},
        "CLOSE_25": {"expected_final_r": .361, "cvar10_r": -.588},
        "CLOSE_50": {"expected_final_r": .343, "cvar10_r": -.290},
        "EXIT": {"expected_final_r": .306, "cvar10_r": .306},
    }
    stability = {
        "checks": 11,
        "policy_stats": {
            "HOLD": {"winner_share": 0.0},
            "CLOSE_10": {"winner_share": 0.0},
            "CLOSE_25": {"winner_share": 0.0},
            "CLOSE_50": {"winner_share": 2 / 11},
            "EXIT": {"winner_share": 1 / 11},
        },
    }
    inputs = PolicyInputs(
        r0=.306, T=2.5, sigma_R=1.0, drift_R=0.0, skew_R=0.0,
        term_slope=0.0, horizon_minutes=1440, max_r=.306,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2), rung_fraction=.10,
        be_after=1.5, option_available=True, chain_age_sec=120,
        chain_status="ok", proxy_quality="reference_proxy", source="test",
    )
    rule = {
        "cvar_floor_r": -.494,
        "eligible": ["CLOSE_50", "EXIT"],
        "ineligible": {"HOLD": {"cvar10_r": -.886}},
    }
    result = select_final_policy(
        "CLOSE_50", stability, metrics,
        {"adverse_confirmation_count": 2}, inputs, rule,
    )
    assert result["status"] == "conflict"
    assert result["automatic_execution_allowed"] is False
