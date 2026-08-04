"""Public facade for the quantitative AI policy manager.

The implementation lives in :mod:`seiltanzer.ai_policy_base`.  This facade keeps
all existing imports stable and applies the remaining-position normalization for
future ladder executions: each rung is 10% of the original position, therefore
its weight must be rescaled after earlier rungs have already reduced the position.
"""

from __future__ import annotations

from . import ai_policy_base as _base

# Preserve the complete public and test-facing module surface, including private
# helpers used by the deterministic test suite.
globals().update({
    name: value
    for name, value in vars(_base).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})


def baseline_strategy_outcomes(
    sim: _base.PathSimulation,
    inputs: _base.PolicyInputs,
) -> _base.np.ndarray:
    """Outcome per unit of the position that remains at the review moment.

    The strategy closes ``rung_fraction`` of the original position at each rung.
    If earlier rungs have already been executed, each future original-position
    slice is a larger fraction of the current remainder and must be normalized.
    """
    past_count = sum(inputs.max_r >= rung - 1e-12 for rung in inputs.rungs)
    original_remaining = max(1.0 - inputs.rung_fraction * past_count, 1e-9)
    future_fraction = min(inputs.rung_fraction / original_remaining, 1.0)

    future = _base.np.asarray(
        [rung for rung in inputs.rungs if rung > inputs.max_r + 1e-8],
        dtype=float,
    )
    if future.size:
        crossed = sim.max_r[:, None] >= future[None, :] - 1e-12
        realized = future_fraction * (crossed * future[None, :]).sum(axis=1)
        closed = _base.np.minimum(1.0, future_fraction * crossed.sum(axis=1))
    else:
        realized = _base.np.zeros_like(sim.terminal)
        closed = _base.np.zeros_like(sim.terminal)

    remaining = _base.np.maximum(0.0, 1.0 - closed)
    exit_r = sim.terminal.copy()
    be_armed = (inputs.max_r >= inputs.be_after - 1e-12) | (
        sim.max_r >= inputs.be_after - 1e-12
    )
    exit_r = _base.np.where(be_armed & (exit_r < 0.0), 0.0, exit_r)
    return realized + remaining * exit_r


# Functions defined in ai_policy_base resolve globals in that module. Rebind the
# corrected implementation there as well, so analyze_policies/_run_once use it.
_base.baseline_strategy_outcomes = baseline_strategy_outcomes

# Keep the facade's own symbol authoritative after the bulk namespace copy.
globals()["baseline_strategy_outcomes"] = baseline_strategy_outcomes
