from copy import deepcopy

import pytest

from seiltanzer import ai_policy_v14
from seiltanzer.option_shadow_state import (
    OptionShadowTracker,
    robust_derivative,
    standardized_derivative_signal,
)


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


def test_derivative_units_distinguish_value_slope_and_acceleration():
    state = _run_tracker(improving=True)
    expected = {
        "p_take": ("probability", "probability/min", "probability/min^2"),
        "barrier_ev": ("R", "R/min", "R/min^2"),
        "q50": ("R", "R/min", "R/min^2"),
        "iv": ("annualized_volatility", "annualized_volatility/min",
               "annualized_volatility/min^2"),
        "price": ("price", "price/min", "price/min^2"),
    }
    for name, units in expected.items():
        row = state["metrics"][name]
        assert (row["value_units"], row["slope_units"], row["acceleration_units"]) == units
        assert row["units_compatibility"] == "deprecated_slope_units_alias"
    assert all({"value_units", "slope_units", "acceleration_units"} <= set(row)
               for row in state["metrics"].values())


def _series(values, minutes=None):
    minutes = minutes or list(range(len(values)))
    return [{"ts": minute * 60, "x": value}
            for minute, value in zip(minutes, values)]


def test_normalization_resists_zero_noise_long_span_and_numerical_artifacts():
    flat = robust_derivative(_series([0.5] * 12), "x")
    assert abs(flat["slope"]) < 1e-12
    assert abs(standardized_derivative_signal(flat)) < 1e-9

    tiny20 = robust_derivative(_series([0.5 + i * 1e-6 for i in range(21)]), "x")
    tiny40 = robust_derivative(_series([0.5 + i * 1e-6 for i in range(41)]), "x")
    assert abs(standardized_derivative_signal(tiny20)) < 0.01
    assert standardized_derivative_signal(tiny40) == pytest.approx(
        standardized_derivative_signal(tiny20), rel=0.02)
    assert tiny40["normalization_horizon_minutes"] == 20.0


def test_robust_derivative_handles_outlier_noise_regime_change_spike_and_irregular_time():
    clean = robust_derivative(_series([i * 0.01 for i in range(12)]), "x")
    outlier_values = [i * 0.01 for i in range(12)]
    outlier_values[5] += 2.0
    outlier = robust_derivative(_series(outlier_values), "x")
    assert clean["slope"] > 0
    assert outlier["slope"] > 0
    assert outlier["slope"] == pytest.approx(clean["slope"], abs=0.01)

    noisy = robust_derivative(_series([
        0.00, 0.03, 0.01, 0.05, 0.04, 0.08,
        0.07, 0.11, 0.10, 0.14, 0.13, 0.17,
    ]), "x")
    assert noisy["slope"] > 0
    assert noisy["noise"] > clean["noise"]

    regime = robust_derivative(_series([
        .20, .18, .16, .14, .12, .10, .12, .16, .22, .30, .40, .52,
    ]), "x")
    assert regime["slope"] > 0

    temporary_spike = robust_derivative(_series([
        .2, .2, .2, .2, .2, 1.5, .2, .2, .2, .2, .2, .2,
    ]), "x")
    assert abs(temporary_spike["slope"]) < 0.02

    irregular = robust_derivative(
        _series([0, .01, .03, .06, .10, .15, .21], [0, 1, 3, 6, 10, 15, 21]), "x")
    assert irregular["slope"] > 0
    assert irregular["time_span_minutes"] == 21


def test_duplicate_timestamps_do_not_inflate_samples_and_stale_state_is_unavailable():
    rows = _series([0, .1, .2, .3, .4, .5], [0, 1, 2, 3, 4, 5])
    rows += [{"ts": 5 * 60, "x": 99.0}] * 5
    result = robust_derivative(rows, "x")
    assert result["sample_count"] == 6
    stale = robust_derivative(
        rows, "x", reference_ts=20 * 60, stale_after_minutes=5)
    assert stale["available"] is False
    assert "stale" in stale["reason"]


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
    assert set(state["option_state_attribution"]) == {
        "EDGE", "TAIL", "LOCAL_HAZARD", "VOLATILITY", "GEX_CONTEXT"}
    assert state["option_state_attribution"]["GEX_CONTEXT"] is None
    assert state["option_state_redundancy_contract"]["GEX_CONTEXT"][
        "included_in_score"] is False
    assert "each subfamily contributes once" in state["option_state_aggregation"]


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
