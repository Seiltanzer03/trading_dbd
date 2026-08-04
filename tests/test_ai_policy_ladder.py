import math

import numpy as np

from seiltanzer.ai_policy import (
    PathSimulation,
    PolicyInputs,
    baseline_strategy_outcomes,
)


def test_future_ladder_closes_original_fraction_normalized_to_current_remainder():
    """Past 10% rungs must not make future 10% rungs too small for the remainder."""
    inputs = PolicyInputs(
        r0=1.25,
        T=2.5,
        sigma_R=1.0,
        drift_R=0.0,
        skew_R=0.0,
        term_slope=0.0,
        horizon_minutes=1440.0,
        max_r=1.25,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10,
        be_after=1.5,
        option_available=True,
        chain_age_sec=60.0,
        chain_status="ok",
        proxy_quality="reference_proxy",
        source="option_barrier_first_touch",
    )
    sim = PathSimulation(
        terminal=np.array([2.5]),
        max_r=np.array([2.5]),
        min_r=np.array([1.25]),
        stop_time=np.array([np.nan]),
        take_time=np.array([1.0]),
        rung_times={},
        horizon_minutes=1440.0,
    )

    outcome = baseline_strategy_outcomes(sim, inputs)[0]

    # Two past rungs left 80% of the original position. Each future 10% of the
    # original therefore equals 12.5% of the current remainder.
    expected = 0.125 * (1.5 + 1.75 + 2.0 + 2.2) + 0.50 * 2.5
    assert math.isclose(outcome, expected, abs_tol=1e-12)
