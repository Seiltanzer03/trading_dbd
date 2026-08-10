import numpy as np

from seiltanzer.ai_policy import PolicyInputs, first_touch_clock, simulate_option_paths
from seiltanzer.ai_policy_base import PathSimulation


def _inputs(**changes):
    values = dict(
        r0=0.0, T=2.0, sigma_R=1.0, drift_R=0.0, skew_R=0.0,
        term_slope=0.0, horizon_minutes=600.0, max_r=0.0,
        rungs=(1.0, 1.5, 1.75), rung_fraction=0.10, be_after=1.5,
        option_available=True, chain_age_sec=60.0, chain_status="ok",
        proxy_quality="direct", source="test",
    )
    values.update(changes)
    return PolicyInputs(**values)


def _simulation(reasons, times):
    n = len(reasons)
    return PathSimulation(
        terminal=np.zeros(n), max_r=np.zeros(n), min_r=np.zeros(n),
        stop_time=np.full(n, np.nan), take_time=np.full(n, np.nan),
        rung_times={}, horizon_minutes=600.0,
        strategy_exit_time=np.asarray(times, dtype=float),
        strategy_exit_reason=np.asarray(reasons), step_count=10,
    )


def test_49_percent_resolved_has_no_unconditional_median():
    sim = _simulation(["take"] * 49 + ["horizon"] * 51, [0.4] * 49 + [1.0] * 51)
    clock = first_touch_clock(sim, _inputs())
    assert clock["median_resolution_minutes"] is None
    assert clock["median_status"] == "beyond_horizon"
    assert clock["resolved_probability_horizon"] == 0.49


def test_exact_50_percent_identifies_earliest_cdf_crossing():
    sim = _simulation(
        ["take"] * 25 + ["stop"] * 25 + ["horizon"] * 50,
        [0.2] * 25 + [0.6] * 25 + [1.0] * 50,
    )
    clock = first_touch_clock(sim, _inputs())
    assert clock["median_status"] == "identified"
    assert clock["median_resolution_minutes"] == 360.0


def test_competing_risk_cdf_identity_and_monotonicity():
    sim = _simulation(
        ["take", "stop", "breakeven", "horizon"] * 25,
        [0.2, 0.4, 0.6, 1.0] * 25,
    )
    clock = first_touch_clock(sim, _inputs())
    cdf = clock["cdf"]
    assert all(a <= b + 1e-12 for a, b in zip(cdf["take"], cdf["take"][1:]))
    assert all(a <= b + 1e-12 for a, b in zip(cdf["stop_or_be"], cdf["stop_or_be"][1:]))
    for take, risk, survival in zip(cdf["take"], cdf["stop_or_be"], cdf["survival"]):
        assert abs(take + risk + survival - 1.0) < 1e-12


def test_already_armed_be_uses_zero_risk_barrier():
    sim = simulate_option_paths(_inputs(r0=0.8, max_r=1.6), n_paths=600, n_steps=80, seed=7)
    clock = first_touch_clock(sim, _inputs(r0=0.8, max_r=1.6))
    assert clock["risk_barrier_r"] == 0.0
    assert np.count_nonzero(sim.strategy_exit_reason == "breakeven") > 0


def test_unavailable_option_state_cannot_fabricate_p50():
    sim = _simulation(["take"] * 100, [0.2] * 100)
    clock = first_touch_clock(sim, _inputs(option_available=False))
    assert clock["available"] is False
    assert clock["median_status"] == "unavailable"


def test_fixed_seed_clock_reproducibility():
    inputs = _inputs()
    first = first_touch_clock(
        simulate_option_paths(inputs, n_paths=600, n_steps=80, seed=88), inputs)
    second = first_touch_clock(
        simulate_option_paths(inputs, n_paths=600, n_steps=80, seed=88), inputs)
    assert first == second
