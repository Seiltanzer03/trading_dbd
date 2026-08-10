import math

import numpy as np

import seiltanzer.ai_policy as policy
from seiltanzer.mc_validation import (
    convergence_study, execution_step_convergence, seed_robustness,
)


def _inputs():
    return policy.PolicyInputs(
        r0=0.0, T=1.0, sigma_R=2.5, drift_R=0.0, skew_R=0.0,
        term_slope=0.0, horizon_minutes=1440.0, max_r=0.0,
        rungs=(), rung_fraction=0.10, be_after=1.5,
        option_available=True, chain_age_sec=30.0, chain_status="ok",
        proxy_quality="direct", source="analytic_fixture",
    )


def test_policy_metrics_report_sampling_error_and_effective_paths():
    metrics, _ = policy._run_once(_inputs(), n_paths=1200, n_steps=160, seed=7)
    uncertainty = metrics["HOLD"]["monte_carlo_uncertainty"]
    assert uncertainty["effective_path_count"] == 1200
    assert uncertainty["expected_final_r"]["standard_error_r"] > 0
    assert len(uncertainty["expected_final_r"]["ci95_r"]) == 2
    assert uncertainty["cvar10_r"]["tail_path_count"] == 120
    assert 0 <= uncertainty["p_final_loss"]["standard_error"] <= 0.5


def test_driftless_symmetric_no_ladder_no_be_is_symmetric():
    sim = policy.simulate_option_paths(_inputs(), n_paths=12000, n_steps=260, seed=44)
    p_take = float(np.mean(~np.isnan(sim.take_time)))
    p_stop = float(np.mean(~np.isnan(sim.stop_time)))
    assert abs(p_take - p_stop) < 0.025
    assert abs(float(np.mean(sim.terminal))) < 0.04


def test_seed_robustness_is_deterministic_and_bounded():
    kwargs = dict(
        inputs=_inputs(), run_once=policy._run_once,
        choose=policy._raw_policy_choice, seeds=(11, 12, 13),
        n_paths=600, n_steps=100,
    )
    first = seed_robustness(**kwargs)
    second = seed_robustness(**kwargs)
    assert first == second
    assert sum(first["winner_counts"].values()) == 3
    assert 0 <= first["winner_stability"] <= 1
    assert 0 <= first["ranking_agreement"] <= 1


def test_convergence_study_keeps_full_policy_metric_contract():
    result = convergence_study(
        _inputs(), run_once=policy._run_once, choose=policy._raw_policy_choice,
        path_counts=(400, 800, 1600), n_steps=100, seed=99,
    )
    assert [row["scenario_count"] for row in result["rows"]] == [400, 800, 1600]
    assert result["winner_stable_from_scenarios"] in (400, 800, 1600)
    for row in result["rows"]:
        assert set(row["policies"]) == set(policy.POLICY_FRACTIONS)
        assert math.isfinite(row["policies"]["HOLD"]["expected_final_r"])
    assert result["method"] == "fixed_seed_path_count_convergence"
    assert result["path_sets_nested"] is False


def test_be_bridge_step_convergence_is_labelled_as_approximation():
    result = execution_step_convergence(
        _inputs(), run_once=policy._run_once,
        step_counts=(40, 80, 160), n_paths=600, seed=101,
    )
    assert result["reference_steps"] == 160
    assert "approximation" in result["bridge_assumption"]
    for row in result["rows"]:
        assert abs(row["p_take"] + row["p_stop_or_be"] + row["p_no_touch"] - 1) < 1e-12
