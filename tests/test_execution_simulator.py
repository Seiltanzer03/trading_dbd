import math

import numpy as np

from seiltanzer.ai_policy import PolicyInputs, baseline_strategy_outcomes, simulate_option_paths
from seiltanzer.execution_simulator import (
    SIMULATOR_VERSION,
    ExecutionSpec,
    replay_execution_path,
)


def _spec(*, current=0.0, max_r=0.0, take=2.0):
    return ExecutionSpec.from_values(
        current_r=current,
        max_r=max_r,
        take_r=take,
        rungs=(1.0, 1.5, 1.75),
        rung_fraction_original=0.10,
        be_after_r=1.5,
    )


def _inputs():
    return PolicyInputs(
        r0=0.2, T=2.0, sigma_R=1.1, drift_R=0.0, skew_R=0.0,
        term_slope=0.0, horizon_minutes=600.0, max_r=0.2,
        rungs=(1.0, 1.5, 1.75), rung_fraction=0.10, be_after=1.5,
        option_available=True, chain_age_sec=60.0, chain_status="ok",
        proxy_quality="direct", source="test",
    )


def test_case_a_be_is_absorbing_and_later_take_is_ignored():
    result = replay_execution_path([0.0, 1.5, 0.5, 0.0, 2.0], _spec())
    assert result.exit_reason == "breakeven"
    assert result.exit_r == 0.0
    assert result.exit_step == 3
    assert result.filled_rungs == (1.0, 1.5)
    assert math.isclose(result.outcome_r, 0.25, abs_tol=1e-12)


def test_case_b_ladder_cashflows_and_take_are_ordered_once():
    result = replay_execution_path([0.0, 1.0, 1.5, 1.75, 2.0], _spec())
    assert result.exit_reason == "take"
    assert result.filled_rungs == (1.0, 1.5, 1.75)
    assert math.isclose(result.remaining_fraction, 0.70, abs_tol=1e-12)
    assert math.isclose(result.outcome_r, 0.1 + 0.15 + 0.175 + 0.7 * 2.0,
                        abs_tol=1e-12)


def test_case_c_be_prevents_terminal_loss():
    result = replay_execution_path([0.0, 1.6, 1.0, -0.1], _spec())
    assert result.exit_reason == "breakeven"
    assert result.exit_r == 0.0
    assert result.outcome_r >= 0.0


def test_cases_d_and_e_original_stop_and_take_are_absorbing():
    stopped = replay_execution_path([0.0, -1.0, 2.0], _spec())
    taken = replay_execution_path([0.0, 2.0, -1.0], _spec())
    assert (stopped.exit_reason, stopped.exit_step, stopped.outcome_r) == (
        "stop", 1, -1.0)
    assert taken.exit_reason == "take"
    assert taken.exit_step == 1


def test_case_f_future_fills_are_normalized_to_current_remainder():
    spec = ExecutionSpec.from_values(
        current_r=1.5, max_r=1.5, take_r=2.5,
        rungs=(1.0, 1.5, 1.75, 2.0), rung_fraction_original=0.10,
        be_after_r=1.5,
    )
    result = replay_execution_path([1.5, 1.75, 2.0, 2.5], spec)
    assert math.isclose(spec.future_fill_fraction, 0.125, abs_tol=1e-12)
    assert result.filled_rungs == (1.75, 2.0)
    assert math.isclose(result.outcome_r,
                        0.125 * 1.75 + 0.125 * 2.0 + 0.75 * 2.5,
                        abs_tol=1e-12)


def test_case_g_gap_orders_rungs_be_and_take_by_crossing_price():
    result = replay_execution_path([0.0, 2.1], _spec())
    assert result.exit_reason == "take"
    assert result.filled_rungs == (1.0, 1.5, 1.75)
    assert result.be_armed is True
    assert result.exit_fraction == 2.0 / 2.1


def test_monte_carlo_execution_is_reproducible_and_attaches_contract():
    inputs = _inputs()
    first = simulate_option_paths(inputs, n_paths=600, n_steps=80, seed=91)
    second = simulate_option_paths(inputs, n_paths=600, n_steps=80, seed=91)
    np.testing.assert_array_equal(first.terminal, second.terminal)
    np.testing.assert_array_equal(first.strategy_outcome, second.strategy_outcome)
    np.testing.assert_array_equal(first.strategy_exit_reason, second.strategy_exit_reason)
    assert first.execution_contract["simulator_version"] == SIMULATOR_VERSION
    assert first.execution_contract["common_random_numbers"] is True


def test_monte_carlo_be_paths_are_absorbed_at_zero_not_terminally_clipped():
    inputs = _inputs()
    sim = simulate_option_paths(inputs, n_paths=3000, n_steps=120, seed=90210)
    be = sim.strategy_exit_reason == "breakeven"
    assert np.count_nonzero(be) > 0
    assert np.all(sim.strategy_exit_r[be] == 0.0)
    # Cash flows may be positive due to earlier ladder fills, but the remaining
    # exposure has no terminal loss and cannot later be paid at take.
    assert np.all(sim.strategy_outcome[be] >= 0.0)
    np.testing.assert_array_equal(
        baseline_strategy_outcomes(sim, inputs), sim.strategy_outcome)
