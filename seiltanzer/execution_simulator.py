"""Authoritative path-dependent execution contract for managed positions.

The option model produces paths in R-space.  This module is the only place
where those paths are converted into strategy cash flows.  In particular,
break-even is an absorbing barrier after it is armed; it is not a terminal
payoff adjustment based on the path maximum.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


SIMULATOR_VERSION = "execution-simulator-f0-v1"
EPSILON = 1e-10


@dataclass(frozen=True)
class ExecutionSpec:
    """Execution rules expressed per unit of the position remaining now."""

    current_r: float
    max_r: float
    take_r: float
    rungs: tuple[float, ...]
    rung_fraction_original: float
    be_after_r: float
    stop_r: float = -1.0

    @classmethod
    def from_values(
        cls, *, current_r: float, max_r: float, take_r: float,
        rungs: Iterable[float], rung_fraction_original: float,
        be_after_r: float, stop_r: float = -1.0,
    ) -> "ExecutionSpec":
        return cls(
            current_r=float(current_r), max_r=float(max_r),
            take_r=float(take_r),
            rungs=tuple(sorted({float(value) for value in rungs})),
            rung_fraction_original=float(rung_fraction_original),
            be_after_r=float(be_after_r), stop_r=float(stop_r),
        )

    @property
    def past_rung_count(self) -> int:
        return sum(self.max_r >= rung - EPSILON for rung in self.rungs)

    @property
    def original_remaining(self) -> float:
        return max(1.0 - self.rung_fraction_original * self.past_rung_count,
                   EPSILON)

    @property
    def future_fill_fraction(self) -> float:
        return min(self.rung_fraction_original / self.original_remaining, 1.0)

    @property
    def future_rungs(self) -> tuple[float, ...]:
        return tuple(
            rung for rung in self.rungs
            if rung > self.max_r + EPSILON and rung <= self.take_r + EPSILON
        )


@dataclass(frozen=True)
class ExecutionResult:
    outcome_r: float
    exit_r: float
    exit_reason: str
    exit_step: int
    exit_fraction: float
    be_armed: bool
    be_armed_step: int | None
    filled_rungs: tuple[float, ...]
    remaining_fraction: float
    events: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        return {
            "simulator_version": SIMULATOR_VERSION,
            "outcome_r": self.outcome_r,
            "exit_r": self.exit_r,
            "exit_reason": self.exit_reason,
            "exit_step": self.exit_step,
            "exit_fraction": self.exit_fraction,
            "be_armed": self.be_armed,
            "be_armed_step": self.be_armed_step,
            "filled_rungs": list(self.filled_rungs),
            "remaining_fraction": self.remaining_fraction,
            "events": [dict(event) for event in self.events],
        }


def _crossing_fraction(previous: float, current: float, level: float) -> float:
    delta = current - previous
    if abs(delta) <= EPSILON:
        return 0.0
    return min(max((level - previous) / delta, 0.0), 1.0)


def replay_execution_path(path: Sequence[float], spec: ExecutionSpec) -> ExecutionResult:
    """Replay one piecewise-linear R path using explicit event ordering.

    Each adjacent pair is treated as a continuous segment.  This is exact for
    the diffusion paths used by the Monte Carlo discretisation.  For observed
    bar-close replay it is an explicit ``barrier-fill/no-slippage`` assumption;
    callers must not silently represent it as tick-exact execution.
    """
    values = np.asarray(path, dtype=float)
    if values.ndim != 1 or values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("path must be a non-empty finite one-dimensional sequence")
    if abs(float(values[0]) - spec.current_r) > 1e-7:
        raise ValueError("path must start at ExecutionSpec.current_r")

    remaining = 1.0
    realized = 0.0
    filled: list[float] = []
    event_timeline: list[dict] = []
    pending = list(spec.future_rungs)
    be_armed = spec.max_r >= spec.be_after_r - EPSILON
    be_step: int | None = 0 if be_armed else None

    def finish(exit_r: float, reason: str, step: int,
               fraction: float) -> ExecutionResult:
        events = [*event_timeline, {
            "type": reason, "r": float(exit_r), "step": int(step),
            "segment_fraction": float(fraction), "remaining_after": 0.0,
        }]
        return ExecutionResult(
            outcome_r=float(realized + remaining * exit_r),
            exit_r=float(exit_r), exit_reason=reason, exit_step=step,
            exit_fraction=float(fraction), be_armed=be_armed,
            be_armed_step=be_step, filled_rungs=tuple(filled),
            remaining_fraction=float(remaining), events=tuple(events),
        )

    if values.size == 1:
        return finish(float(values[0]), "horizon", 0, 0.0)

    for step, (previous, current) in enumerate(zip(values[:-1], values[1:]), 1):
        previous, current = float(previous), float(current)
        if current >= previous:
            events: list[tuple[float, int, str, float]] = []
            for rung in pending:
                if previous < rung - EPSILON <= current + EPSILON:
                    # A rung at the BE level is booked before BE is armed.  Both
                    # occur at the same price, so this only makes cashflow order
                    # explicit and does not change the barrier price.
                    events.append((_crossing_fraction(previous, current, rung),
                                   0, "rung", rung))
            if (not be_armed and
                    previous < spec.be_after_r - EPSILON <= current + EPSILON):
                events.append((_crossing_fraction(previous, current, spec.be_after_r),
                               1, "be_arm", spec.be_after_r))
            if previous < spec.take_r - EPSILON <= current + EPSILON:
                events.append((_crossing_fraction(previous, current, spec.take_r),
                               2, "take", spec.take_r))
            for fraction, _priority, kind, level in sorted(events):
                if kind == "rung" and level in pending and remaining > EPSILON:
                    fill = min(spec.future_fill_fraction, remaining)
                    realized += fill * level
                    remaining -= fill
                    filled.append(level)
                    pending.remove(level)
                    event_timeline.append({
                        "type": "rung", "r": float(level), "step": int(step),
                        "segment_fraction": float(fraction),
                        "fill_fraction": float(fill),
                        "remaining_after": float(remaining),
                    })
                    if remaining <= EPSILON:
                        # A fully exhausted ladder is a favourable terminal
                        # resolution even when the geometric final TAKE lies above it.
                        return finish(level, "take", step, fraction)
                elif kind == "be_arm":
                    be_armed = True
                    be_step = step
                    event_timeline.append({
                        "type": "be_arm", "r": float(level), "step": int(step),
                        "segment_fraction": float(fraction),
                        "remaining_after": float(remaining),
                    })
                elif kind == "take":
                    return finish(spec.take_r, "take", step, fraction)
        else:
            active_stop = 0.0 if be_armed else spec.stop_r
            if current <= active_stop + EPSILON < previous:
                return finish(
                    active_stop, "breakeven" if be_armed else "stop", step,
                    _crossing_fraction(previous, current, active_stop),
                )

    return finish(float(values[-1]), "horizon", values.size - 1, 1.0)


def execution_contract(spec: ExecutionSpec) -> dict:
    """Serializable assumptions attached to policy snapshots and audits."""
    return {
        "simulator_version": SIMULATOR_VERSION,
        "state_space": "R_multiple_of_initial_risk",
        "event_ordering": "continuous_segment_first_crossing",
        "break_even": "absorbing_0R_after_be_after",
        "ladder_fill": "level_fill_original_position_fraction",
        "current_remainder_normalization": spec.future_fill_fraction,
        "observed_bar_assumption": "piecewise_linear_barrier_fill_no_slippage",
        "execution_costs": "applied_by_policy_cost_layer",
        "common_random_numbers": True,
    }
