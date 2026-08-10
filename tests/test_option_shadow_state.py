from copy import deepcopy

import pytest

from seiltanzer import ai_policy_v14
from seiltanzer.option_shadow_state import OptionShadowTracker, robust_derivative


def _payload(ts, index, *, improving=True, downside_expansion=False):
    direction = 1 if improving else -1
    p_take = 0.40 + direction * index * 0.012
    p_stop = 0.32 - direction * index * 0.008
    q10 = -0.8 - (index * 0.04 if downside_expansion else 0.0)
    q50 = 0.10 + direction * index * 0.025
    q90 = 1.2 + direction * index * 0.025
    no_touch = 1.0 - p_take - p_stop
    return {
        "ts": ts,
        "trade": {"id": 71, "direction": "long", "entry": 100, "stop": 90, "take": 120},
        "prob": {"T": 2.0, "r": 0.10 + direction * index * 0.02},
        "levels": {"price": 101 + direction * index * 0.2},
        "market": {
            "available": True, "p_take_horizon": p_take, "p_stop_horizon": p_stop,
            "p_unresolved_horizon": no_touch,
            "horizon_barrier_ev": 2.0 * p_take - p_stop,
            "scenario_p10_r": q10, "scenario_median_r": q50,
            "scenario_p90_r": q90, "horizon_years": 1 / 365,
        },
        "cone": {
            "skew": 0.1 - direction * index * 0.002, "term_slope": 0.03,
            "first_touch_hazard": {"next_window": {
                "h_take": 0.08 + direction * index * 0.002,
                "h_stop": 0.07 - direction * index * 0.002,
                "log_hazard_ratio": 0.1 + direction * index * 0.03,
            }},
        },
        "vrp": {"available": True, "iv": 0.24, "rv": 0.18, "vrp": 0.06},
        "gamma": {"field_geometry": {
            "available": True, "field": 0.3, "force_score": 0.1,
            "stiffness_score": -0.2, "distance_to_zero_gamma": 0.7,
            "distance_to_call_wall_r": 1.1, "distance_to_put_wall_r": -0.6,
        }},
        "feeds": {
            "proxy_price": {"status": "live"},
            "chain": {"status": "live", "age_sec": 30},
        },
        "options_summary": {"experimental": False, "sigma_annual": 0.24},
    }


def _run_tracker(**kwargs):
    tracker = OptionShadowTracker(sample_interval_sec=0)
    state = None
    for i in range(12):
        state = tracker.update(_payload(1_700_000_000 + i * 60, i, **kwargs))
    return state


def test_robust_derivative_refuses_two_noisy_points():
    result = robust_derivative(
        [{"ts": 0, "x": 0.0}, {"ts": 600, "x": 10.0}], "x")
    assert result["available"] is False
    assert result["slope"] is None
    assert result["sample_count"] == 2


def test_improving_distribution_has_positive_ev_take_and_bop_velocity():
    state = _run_tracker(improving=True)
    derivatives = state["named_derivatives"]
    assert derivatives["dBarrierEV/dt"] > 0
    assert derivatives["dP_take/dt"] > 0
    assert derivatives["dP_stop/dt"] < 0
    assert derivatives["dBOP/dt"] > 0
    assert state["metrics"]["barrier_ev"]["sample_count"] == 12
    assert state["metrics"]["barrier_ev"]["time_span_minutes"] == 11


def test_deteriorating_distribution_has_negative_ev_velocity():
    state = _run_tracker(improving=False)
    assert state["named_derivatives"]["dBarrierEV/dt"] < 0
    assert state["named_derivatives"]["dP_take/dt"] < 0


def test_downside_only_expansion_is_adverse_tail_geometry():
    state = _run_tracker(improving=True, downside_expansion=True)
    assert state["named_derivatives"]["dWidth/dt"] > 0
    assert state["metrics"]["tail_log_ratio"]["slope"] < 0


def test_all_derived_option_metrics_are_one_non_independent_shadow_family():
    state = _run_tracker(improving=True)
    assert state["family"] == "option_distribution"
    assert state["independent_vote"] is False
    assert state["policy_influence"] == "none"
    assert state["interaction_state"]["family"] == "option_distribution"
    assert all(item["independent_vote"] is False
               for item in state["interaction_state"]["items"])
    assert all(item["score"] is None or -1 <= item["score"] <= 1
               for item in state["interaction_state"]["items"])
    assert all(item["components"] for item in state["interaction_state"]["items"])


def test_policy_v14_exposes_shadow_context_without_changing_action(monkeypatch):
    baseline = {
        "risk_constraint": {"net_cvar_floor_r": -0.91, "gross_cvar_floor_r": -0.9},
        "selection_rule": {},
        "recommendation": {"action": "HOLD", "policy": "HOLD"},
        "policies": {"HOLD": {"expected_net_r": 0.2}},
        "evidence": {"context_observations": [], "decision_roles": {}},
    }
    monkeypatch.setattr(ai_policy_v14, "_BASE_ANALYZE", lambda *a, **k: deepcopy(baseline))
    state = _run_tracker(improving=True)
    result = ai_policy_v14.analyze_policies(
        object(), {"option_derivative_state": state,
                   "interaction_state": state["interaction_state"]}, {}, {})
    assert result["recommendation"] == baseline["recommendation"]
    assert result["policies"] == baseline["policies"]
    assert result["shadow_policy_contract"]["action_changed"] is False
    option = result["evidence"]["option_derivative_state"]
    assert option["family"] == "option_distribution"
    assert option["independent_vote"] is False
    assert option["policy_influence"] == "none"

