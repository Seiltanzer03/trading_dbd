from __future__ import annotations

from seiltanzer.llm_shadow_working_action import build_working_action


def _snapshot():
    return {
        "captured_ts": 1_900_000_000.0,
        "strategy": {"direction": "long"},
        "trade_geometry": {
            "entry": 100.0,
            "original_stop": 90.0,
            "active_risk_barrier": 90.0,
            "current": 110.0,
            "final_take": 130.0,
        },
        "policy_manager": {
            "scenario_geometry": {"next_rung_r": 1.5, "full_horizon_minutes": 240},
            "option_derivative_state": {"gex_geometry": {
                "distance_to_zero_gamma": -0.3,
                "distance_to_call_wall_r": 2.5,
            }},
        },
    }


def _shadow(policy, confidence=0.80):
    return {
        "status": "ok",
        "blocked_by_hard_guard": False,
        "policy": policy,
        "confidence": confidence,
    }


def test_base_policy_becomes_exact_manual_variant_without_auto_execution():
    action = build_working_action(_snapshot(), _shadow("CLOSE_25"))
    assert action["status"] == "READY_FOR_MANUAL_CONFIRMATION"
    assert action["close_fraction"] == 0.25
    assert action["automatic_execution_allowed"] is False
    assert action["manual_confirmation_required"] is True


def test_gamma_flip_uses_authoritative_non_widening_price():
    action = build_working_action(_snapshot(), _shadow("TRAIL_GAMMA_FLIP"))
    assert action["status"] == "READY_FOR_MANUAL_CONFIRMATION"
    assert action["parameters"] == {"stop_price": 107.0, "anchor": "ZERO_GAMMA"}
    assert action["may_widen_stop"] is False


def test_move_to_be_is_blocked_when_it_would_not_tighten_stop():
    snapshot = _snapshot()
    snapshot["trade_geometry"]["current"] = 95.0
    snapshot["trade_geometry"]["active_risk_barrier"] = 100.0
    action = build_working_action(snapshot, _shadow("MOVE_TO_BE"))
    assert action["status"] == "NOT_ACTIONABLE"
    assert action["reason"] == "BREAK_EVEN_IS_NOT_A_VALID_TIGHTER_STOP"


def test_scale_out_and_time_stop_get_deterministic_parameters():
    scale = build_working_action(_snapshot(), _shadow("SCALE_OUT_ON_SPIKE"))
    assert scale["parameters"] == {
        "trigger_price": 115.0, "trigger_r": 1.5, "close_fraction": 0.10,
    }
    timed = build_working_action(_snapshot(), _shadow("TIME_STOP"))
    assert timed["parameters"] == {
        "deadline_ts": 1_900_014_400.0, "horizon_minutes": 240.0,
    }


def test_low_confidence_or_failed_guard_never_becomes_actionable():
    low = build_working_action(_snapshot(), _shadow("CLOSE_50", confidence=0.64))
    assert low["status"] == "NOT_ACTIONABLE"
    blocked = _shadow("EXIT")
    blocked.update({"status": "blocked", "blocked_by_hard_guard": True})
    action = build_working_action(_snapshot(), blocked)
    assert action["status"] == "NOT_ACTIONABLE"
    assert action["automatic_execution_allowed"] is False


def test_extend_take_requires_a_farther_authoritative_wall():
    action = build_working_action(_snapshot(), _shadow("EXTEND_TAKE"))
    assert action["status"] == "READY_FOR_MANUAL_CONFIRMATION"
    assert action["parameters"]["take_price"] == 135.0
    snapshot = _snapshot()
    snapshot["policy_manager"]["option_derivative_state"]["gex_geometry"][
        "distance_to_call_wall_r"
    ] = 1.0
    blocked = build_working_action(snapshot, _shadow("EXTEND_TAKE"))
    assert blocked["status"] == "NOT_ACTIONABLE"
