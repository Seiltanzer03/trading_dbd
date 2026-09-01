"""Deterministic option-aware position policy manager for AI trade reviews.

The language model is not allowed to invent position-management arithmetic.
This module converts the current option cone and the rest of the terminal evidence
into comparable, reproducible policies for the *currently remaining* position.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable

import numpy as np

from .execution_simulator import SIMULATOR_VERSION, ExecutionSpec, execution_contract


POLICY_FRACTIONS = {
    "HOLD": 0.0,
    "CLOSE_10": 0.10,
    "CLOSE_25": 0.25,
    "CLOSE_50": 0.50,
    "EXIT": 1.0,
}
LOCAL_HOURS = (1.0, 2.0, 4.0, 8.0, 12.0, 18.0, 24.0)
MONEYNESS_GRID = tuple(float(x) for x in range(-12, 13, 2))


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _rnd(value: Any, digits: int = 4) -> float | None:
    value = _num(value)
    return round(value, digits) if value is not None else None


def _at(value: Any, *path: str, default=None):
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    vals = values[order]
    w = weights[order]
    total = float(w.sum())
    if total <= 0:
        return float("nan")
    target = min(max(float(q), 0.0), 1.0) * total
    idx = int(np.searchsorted(np.cumsum(w), target, side="left"))
    return float(vals[min(idx, len(vals) - 1)])


def _cvar(values: np.ndarray, alpha: float = 0.10) -> float:
    if values.size == 0:
        return float("nan")
    k = max(1, int(math.ceil(values.size * alpha)))
    return float(np.mean(np.partition(values, k - 1)[:k]))


def _mean_uncertainty(values: np.ndarray) -> dict:
    n = int(values.size)
    if n < 2:
        return {"standard_error_r": None, "ci95_r": [None, None]}
    mean = float(np.mean(values))
    se = float(np.std(values, ddof=1) / math.sqrt(n))
    return {
        "standard_error_r": round(se, 6),
        "ci95_r": [round(mean - 1.96 * se, 6), round(mean + 1.96 * se, 6)],
    }


def _probability_uncertainty(events: np.ndarray) -> dict:
    n = int(events.size)
    estimate = float(np.mean(events)) if n else float("nan")
    se = math.sqrt(max(estimate * (1.0 - estimate), 0.0) / n) if n else None
    return {
        "estimate": round(estimate, 6) if n else None,
        "standard_error": round(se, 6) if se is not None else None,
        "ci95": ([round(max(0.0, estimate - 1.96 * se), 6),
                  round(min(1.0, estimate + 1.96 * se), 6)]
                 if se is not None else [None, None]),
        "method": "binomial_normal_approximation",
    }


def _cvar_uncertainty(values: np.ndarray, alpha: float = 0.10) -> dict:
    n = int(values.size)
    k = max(1, int(math.ceil(n * alpha)))
    tail = np.partition(values, k - 1)[:k]
    estimate = float(np.mean(tail))
    se = (float(np.std(tail, ddof=1) / math.sqrt(k)) if k > 1 else None)
    return {
        "tail_path_count": k,
        "standard_error_r": round(se, 6) if se is not None else None,
        "ci95_r": ([round(estimate - 1.96 * se, 6), round(estimate + 1.96 * se, 6)]
                   if se is not None else [None, None]),
        "method": "conditional_tail_mean_se; seed stability is reported separately",
    }


def _centered_skew_noise(z: np.ndarray, skew: float) -> np.ndarray:
    k = float(min(max(skew, -0.45), 0.45))
    if abs(k) < 1e-15:
        return z
    raw = np.where(z < 0.0, (1.0 + k) * z, (1.0 - k) * z)
    mean = -2.0 * k / math.sqrt(2.0 * math.pi)
    variance = 1.0 + k * k * (1.0 - 2.0 / math.pi)
    return (raw - mean) / math.sqrt(variance)


def _bridge_probs(prev: np.ndarray, cur: np.ndarray, lower: float,
                  upper: float, variance: float) -> tuple[np.ndarray, np.ndarray]:
    variance = max(float(variance), 1e-12)
    p_lower = np.exp(-2.0 * (prev - lower) * (cur - lower) / variance)
    p_upper = np.exp(-2.0 * (upper - prev) * (upper - cur) / variance)
    total = p_lower + p_upper
    scale = np.maximum(total, 1.0)
    return p_lower / scale, p_upper / scale


@dataclass(frozen=True)
class PolicyInputs:
    r0: float
    T: float
    sigma_R: float
    drift_R: float
    skew_R: float
    term_slope: float
    horizon_minutes: float
    max_r: float
    rungs: tuple[float, ...]
    rung_fraction: float
    be_after: float
    option_available: bool
    chain_age_sec: float | None
    chain_status: str | None
    proxy_quality: str | None
    source: str | None

    def as_dict(self) -> dict:
        return {
            "r0": _rnd(self.r0, 4), "T": _rnd(self.T, 4),
            "sigma_R": _rnd(self.sigma_R, 4), "drift_R": _rnd(self.drift_R, 4),
            "skew_R": _rnd(self.skew_R, 4), "term_slope": _rnd(self.term_slope, 4),
            "horizon_minutes": _rnd(self.horizon_minutes, 1),
            "max_r": _rnd(self.max_r, 4), "rungs": list(self.rungs),
            "rung_fraction": self.rung_fraction, "be_after": self.be_after,
            "option_available": self.option_available,
            "chain_age_sec": _rnd(self.chain_age_sec, 1),
            "chain_status": self.chain_status, "proxy_quality": self.proxy_quality,
            "source": self.source,
        }


@dataclass
class PathSimulation:
    terminal: np.ndarray
    max_r: np.ndarray
    min_r: np.ndarray
    stop_time: np.ndarray
    take_time: np.ndarray
    rung_times: dict[float, np.ndarray]
    horizon_minutes: float
    strategy_outcome: np.ndarray | None = None
    strategy_exit_r: np.ndarray | None = None
    strategy_exit_time: np.ndarray | None = None
    strategy_exit_reason: np.ndarray | None = None
    be_arm_time: np.ndarray | None = None
    execution_contract: dict | None = None
    step_count: int | None = None


@dataclass
class PolicyDistribution:
    name: str
    close_fraction: float
    outcomes: np.ndarray


def _strategy_risk_exit_time(sim: PathSimulation) -> np.ndarray:
    """Time of an absorbing adverse/BE exit, excluding take and horizon."""
    if sim.strategy_exit_time is None or sim.strategy_exit_reason is None:
        return sim.stop_time
    return np.where(
        np.isin(sim.strategy_exit_reason, ("stop", "breakeven")),
        sim.strategy_exit_time, np.nan,
    )


def extract_policy_inputs(tick: dict) -> PolicyInputs:
    prob = tick.get("prob") or {}
    cone = tick.get("cone") or {}
    market = tick.get("market") or {}
    ladder = tick.get("ladder") or {}
    chain = _at(tick, "feeds", "chain", default={}) or {}
    r0 = _num(prob.get("r")) or 0.0
    T = max(_num(prob.get("T")) or 1.0, 0.05)
    sigma_R = max(_num(cone.get("sigma_R")) or _num(prob.get("sigma_R")) or 1.0, 0.08)
    horizon_years = _num(market.get("horizon_years")) or _num(cone.get("horizon_years"))
    horizon_minutes = (
        horizon_years * 365.0 * 24.0 * 60.0 if horizon_years and horizon_years > 0
        else 5.0 * 24.0 * 60.0
    )
    return PolicyInputs(
        r0=float(r0), T=float(T), sigma_R=float(min(sigma_R, 8.0)),
        drift_R=float(_num(cone.get("drift_R")) or 0.0),
        skew_R=float(_num(cone.get("skew")) or 0.0),
        term_slope=float(_num(cone.get("term_slope")) or 0.0),
        horizon_minutes=float(max(horizon_minutes, 1.0)),
        max_r=float(_num(ladder.get("max_r")) if _num(ladder.get("max_r")) is not None else r0),
        rungs=tuple(float(x) for x in (ladder.get("rungs") or (1.0, 1.25, 1.5, 1.75, 2.0, 2.2))),
        rung_fraction=float(_num(ladder.get("fraction")) or 0.10),
        be_after=float(_num(ladder.get("be_after")) or 1.5),
        option_available=bool(prob.get("available") and market.get("available")),
        chain_age_sec=_num(market.get("chain_age_sec")) or _num(chain.get("age_sec")),
        chain_status=chain.get("status"), proxy_quality=market.get("quality"),
        source=market.get("source") or prob.get("source"),
    )


def simulate_option_paths(inputs: PolicyInputs, *, n_paths: int = 6000,
                          n_steps: int = 320, seed: int = 0xA17E) -> PathSimulation:
    """Simulate the same option drivers used by the cone, retaining path events.

    The total variance is exactly ``sigma_R**2`` over the option horizon. Term
    structure changes when that variance is spent; skew changes the two tails;
    drift is the robust BL-forward drift already produced by the engine.
    """
    n_paths = max(int(n_paths), 300)
    n_steps = max(int(n_steps), 40)
    rng = np.random.default_rng(int(seed))
    r0 = min(max(inputs.r0, -1.0 + 1e-8), inputs.T - 1e-8)
    r = np.full(n_paths, r0, dtype=float)
    max_r = r.copy()
    min_r = r.copy()
    alive = np.ones(n_paths, dtype=bool)
    terminal = np.full(n_paths, np.nan)
    stop_time = np.full(n_paths, np.nan)
    take_time = np.full(n_paths, np.nan)
    future_rungs = tuple(x for x in inputs.rungs if x > inputs.max_r + 1e-8 and x < inputs.T + 1e-8)
    rung_times = {x: np.full(n_paths, np.nan) for x in future_rungs}

    # Economic execution state is separate from the raw underlying path.  Once
    # BE/stop/take closes the managed remainder, later market movement must not
    # resurrect that position.  All policies consume this one shared state.
    past_count = sum(inputs.max_r >= rung - 1e-12 for rung in inputs.rungs)
    original_remaining = max(1.0 - inputs.rung_fraction * past_count, 1e-9)
    future_fraction = min(inputs.rung_fraction / original_remaining, 1.0)
    strategy_alive = np.ones(n_paths, dtype=bool)
    strategy_remaining = np.ones(n_paths, dtype=float)
    strategy_realized = np.zeros(n_paths, dtype=float)
    strategy_exit_r = np.full(n_paths, np.nan)
    strategy_exit_time = np.full(n_paths, np.nan)
    strategy_exit_reason = np.full(n_paths, "", dtype="<U10")
    be_armed = np.full(n_paths, inputs.max_r >= inputs.be_after - 1e-12,
                       dtype=bool)
    be_arm_time = np.full(n_paths, 0.0 if bool(be_armed[0]) else np.nan)
    # Separate bridge stream prevents added execution bookkeeping from changing
    # the sampled endpoint paths for an existing seed.
    execution_rng = np.random.default_rng(int(seed) ^ 0x5E17A0F0)

    t_mid = (np.arange(n_steps, dtype=float) + 0.5) / n_steps
    # Positive slope puts more variance later; negative slope spends it earlier.
    vol_shape = np.exp(float(min(max(inputs.term_slope, -0.8), 0.8)) * (t_mid - 0.5))
    var_steps = (inputs.sigma_R ** 2) * (vol_shape ** 2) / float(np.sum(vol_shape ** 2))
    drift_step = inputs.drift_R / n_steps

    for step in range(n_steps):
        idx = np.flatnonzero(alive)
        if idx.size == 0:
            break
        prev = r[idx].copy()
        z = _centered_skew_noise(rng.standard_normal(idx.size), inputs.skew_R)
        cur = prev + drift_step + math.sqrt(float(var_steps[step])) * z
        r[idx] = cur
        max_r[idx] = np.maximum(max_r[idx], cur)
        min_r[idx] = np.minimum(min_r[idx], cur)

        frac = (step + 1) / n_steps
        for rung, times in rung_times.items():
            active = strategy_alive[idx]
            crossed = active & np.isnan(times[idx]) & (cur >= rung)
            if crossed.any():
                target = idx[crossed]
                times[target] = frac
                fill = np.minimum(future_fraction, strategy_remaining[target])
                strategy_realized[target] += fill * rung
                strategy_remaining[target] -= fill
                fully_closed = target[strategy_remaining[target] <= 1e-10]
                if fully_closed.size:
                    strategy_exit_r[fully_closed] = rung
                    strategy_exit_time[fully_closed] = frac
                    strategy_exit_reason[fully_closed] = "take"
                    strategy_alive[fully_closed] = False

        active = strategy_alive[idx]
        newly_armed = active & ~be_armed[idx] & (cur >= inputs.be_after - 1e-12)
        if newly_armed.any():
            target = idx[newly_armed]
            be_armed[target] = True
            be_arm_time[target] = frac

        # Endpoint crossings are ordered by direction.  An upward segment books
        # crossed ladder levels before take; a downward segment exits at the
        # currently active stop (0R after BE, otherwise -1R).
        active = strategy_alive[idx]
        economic_take = active & (cur >= inputs.T)
        if economic_take.any():
            target = idx[economic_take]
            strategy_exit_r[target] = inputs.T
            strategy_exit_time[target] = frac
            strategy_exit_reason[target] = "take"
            strategy_alive[target] = False
        active = strategy_alive[idx]
        active_floor = np.where(be_armed[idx], 0.0, -1.0)
        economic_stop = active & (cur <= active_floor)
        if economic_stop.any():
            target = idx[economic_stop]
            strategy_exit_r[target] = active_floor[economic_stop]
            strategy_exit_time[target] = frac
            strategy_exit_reason[target] = np.where(
                be_armed[target], "breakeven", "stop")
            strategy_alive[target] = False

        hit_take = cur >= inputs.T
        hit_stop = cur <= -1.0
        inside = ~hit_take & ~hit_stop
        if inside.any():
            lo_p, up_p = _bridge_probs(prev[inside], cur[inside], -1.0, inputs.T,
                                        float(var_steps[step]))
            u = rng.random(inside.sum())
            bridge_stop = u < lo_p
            bridge_take = ~bridge_stop & (u < lo_p + up_p)
            ii = np.flatnonzero(inside)
            hit_stop[ii[bridge_stop]] = True
            hit_take[ii[bridge_take]] = True
            if bridge_take.any():
                max_r[idx[ii[bridge_take]]] = np.maximum(max_r[idx[ii[bridge_take]]], inputs.T)
                for rung, times in rung_times.items():
                    miss = np.isnan(times[idx[ii[bridge_take]]])
                    if miss.any() and rung <= inputs.T:
                        target = idx[ii[bridge_take]][miss]
                        economic_target = target[strategy_alive[target]]
                        if economic_target.size:
                            times[economic_target] = frac
                            fill = np.minimum(
                                future_fraction, strategy_remaining[economic_target])
                            strategy_realized[economic_target] += fill * rung
                            strategy_remaining[economic_target] -= fill

            # Use the same raw bridge absorption for economic stop/take.  This
            # keeps ladder fills, outer barriers and policy cash flows ordered on
            # a common path.  BE-only crossings are handled just below.
            raw_bridge_stop = idx[ii[bridge_stop]]
            econ_stop = raw_bridge_stop[strategy_alive[raw_bridge_stop]]
            if econ_stop.size:
                strategy_exit_r[econ_stop] = np.where(be_armed[econ_stop], 0.0, -1.0)
                strategy_exit_time[econ_stop] = frac
                strategy_exit_reason[econ_stop] = np.where(
                    be_armed[econ_stop], "breakeven", "stop")
                strategy_alive[econ_stop] = False
            raw_bridge_take = idx[ii[bridge_take]]
            econ_take = raw_bridge_take[strategy_alive[raw_bridge_take]]
            if econ_take.size:
                # A continuous path reaching take necessarily crossed every
                # pending rung and the BE arm level first.
                for rung, times in rung_times.items():
                    missing = np.isnan(times[econ_take])
                    if missing.any():
                        target = econ_take[missing]
                        times[target] = frac
                        fill = np.minimum(future_fraction, strategy_remaining[target])
                        strategy_realized[target] += fill * rung
                        strategy_remaining[target] -= fill
                arm = ~be_armed[econ_take]
                if arm.any():
                    target = econ_take[arm]
                    be_armed[target] = True
                    be_arm_time[target] = frac
                strategy_exit_r[econ_take] = inputs.T
                strategy_exit_time[econ_take] = frac
                strategy_exit_reason[econ_take] = "take"
                strategy_alive[econ_take] = False

        # Brownian bridges can cross an armed 0R barrier and return while both
        # endpoints remain positive.  Sample that event explicitly.  This state
        # is absorbing and therefore cannot later become a take.
        bridge_candidates = idx[
            strategy_alive[idx] & be_armed[idx] & (prev > 0.0) & (cur > 0.0)]
        if bridge_candidates.size:
            positions = np.searchsorted(idx, bridge_candidates)
            p_be = np.exp(
                -2.0 * prev[positions] * cur[positions]
                / max(float(var_steps[step]), 1e-12))
            hit_be = execution_rng.random(bridge_candidates.size) < p_be
            if hit_be.any():
                target = bridge_candidates[hit_be]
                strategy_exit_r[target] = 0.0
                strategy_exit_time[target] = frac
                strategy_exit_reason[target] = "breakeven"
                strategy_alive[target] = False

        if hit_take.any():
            j = idx[hit_take]
            terminal[j] = inputs.T
            take_time[j] = frac
            alive[j] = False
        if hit_stop.any():
            j = idx[hit_stop & ~hit_take]
            terminal[j] = -1.0
            stop_time[j] = frac
            alive[j] = False

    terminal[alive] = r[alive]
    strategy_exit_r[strategy_alive] = terminal[strategy_alive]
    strategy_exit_time[strategy_alive] = 1.0
    strategy_exit_reason[strategy_alive] = "horizon"
    strategy_outcome = strategy_realized + strategy_remaining * strategy_exit_r
    spec = ExecutionSpec.from_values(
        current_r=inputs.r0, max_r=inputs.max_r, take_r=inputs.T,
        rungs=inputs.rungs, rung_fraction_original=inputs.rung_fraction,
        be_after_r=inputs.be_after,
    )
    return PathSimulation(terminal=terminal, max_r=max_r, min_r=min_r,
                          stop_time=stop_time, take_time=take_time,
                          rung_times=rung_times,
                          horizon_minutes=inputs.horizon_minutes,
                          strategy_outcome=strategy_outcome,
                          strategy_exit_r=strategy_exit_r,
                          strategy_exit_time=strategy_exit_time,
                          strategy_exit_reason=strategy_exit_reason,
                          be_arm_time=be_arm_time,
                          execution_contract=execution_contract(spec),
                          step_count=n_steps)


FIRST_TOUCH_CLOCK_VERSION = "first-touch-clock-f1-v1"


def first_touch_clock(sim: PathSimulation, inputs: PolicyInputs) -> dict:
    """Measure competing-risk resolution from the existing execution paths."""
    base = {
        "version": FIRST_TOUCH_CLOCK_VERSION,
        "available": False,
        "source": "authoritative_execution_mc",
        "time_basis": "calendar_elapsed",
        "horizon_minutes": round(float(inputs.horizon_minutes), 6),
        "path_count": int(np.asarray(sim.terminal).size),
        "step_count": int(sim.step_count or 0),
        "risk_barrier_r": 0.0 if inputs.max_r >= inputs.be_after - 1e-12 else -1.0,
        "authority": "measurement_only",
        "changes_distribution": False,
    }
    if not inputs.option_available:
        return {**base, "reason": "option_distribution_unavailable",
                "median_status": "unavailable"}
    if sim.strategy_exit_time is None or sim.strategy_exit_reason is None:
        return {**base, "reason": "execution_event_state_unavailable",
                "median_status": "unavailable"}

    times = np.asarray(sim.strategy_exit_time, dtype=float)
    reasons = np.asarray(sim.strategy_exit_reason)
    if times.size == 0 or reasons.size != times.size:
        return {**base, "reason": "invalid_execution_event_state",
                "median_status": "unavailable"}
    take = reasons == "take"
    risk = np.isin(reasons, ("stop", "breakeven"))
    resolved = take | risk
    n = int(times.size)
    resolved_probability = float(np.count_nonzero(resolved) / n)

    def unconditional_quantile(q: float) -> float | None:
        # The denominator is every path. Horizon-censored paths therefore do
        # not silently become a conditional-on-resolution time statistic.
        if resolved_probability + 1e-12 < q:
            return None
        ordered = np.sort(times[resolved])
        rank = max(0, int(math.ceil(q * n - 1e-12)) - 1)
        return float(ordered[min(rank, ordered.size - 1)] * inputs.horizon_minutes)

    p25, p50, p75 = (unconditional_quantile(q) for q in (0.25, 0.50, 0.75))
    conditional = (
        float(np.median(times[resolved]) * inputs.horizon_minutes)
        if np.any(resolved) else None
    )
    grid_count = max(1, int(sim.step_count or 40))
    grid = np.arange(1, grid_count + 1, dtype=float) / grid_count
    take_cdf = [float(np.mean(take & (times <= frac + 1e-12))) for frac in grid]
    risk_cdf = [float(np.mean(risk & (times <= frac + 1e-12))) for frac in grid]
    survival = [max(0.0, 1.0 - a - b) for a, b in zip(take_cdf, risk_cdf)]
    restricted_times = np.where(resolved, times, 1.0)
    return {
        **base,
        "available": True,
        "resolved_probability_horizon": round(resolved_probability, 6),
        "survival_probability_horizon": round(1.0 - resolved_probability, 6),
        "resolution_time": {
            "p25_minutes": None if p25 is None else round(p25, 6),
            "p50_minutes": None if p50 is None else round(p50, 6),
            "p75_minutes": None if p75 is None else round(p75, 6),
        },
        "median_resolution_minutes": None if p50 is None else round(p50, 6),
        "median_resolution_years": (
            None if p50 is None else round(p50 / (365.0 * 24.0 * 60.0), 12)),
        "median_status": "identified" if p50 is not None else "beyond_horizon",
        "conditional_median_given_resolved_minutes": (
            None if conditional is None else round(conditional, 6)),
        "conditional_label": "P50 among paths resolved within horizon",
        "restricted_mean_time_to_resolution_minutes": round(
            float(np.mean(restricted_times)) * inputs.horizon_minutes, 6),
        "cause_probability_horizon": {
            "take": round(float(np.mean(take)), 6),
            "stop_or_be": round(float(np.mean(risk)), 6),
        },
        "cdf": {
            "times_frac": grid.tolist(),
            "take": take_cdf,
            "stop_or_be": risk_cdf,
            "survival": survival,
        },
    }


def baseline_strategy_outcomes(sim: PathSimulation, inputs: PolicyInputs) -> np.ndarray:
    """Outcome per unit of the currently remaining position.

    Already crossed rungs are not paid twice. Future rungs close the configured
    fraction of the *current remainder*. Break-even is treated identically to the
    terminal's existing MC approximation: after 1.5R, a negative final exit is 0R.
    """
    if sim.strategy_outcome is not None:
        return sim.strategy_outcome.copy()
    future = np.asarray([x for x in inputs.rungs if x > inputs.max_r + 1e-8], dtype=float)
    if future.size:
        crossed = sim.max_r[:, None] >= future[None, :] - 1e-12
        realized = inputs.rung_fraction * (crossed * future[None, :]).sum(axis=1)
        closed = np.minimum(1.0, inputs.rung_fraction * crossed.sum(axis=1))
    else:
        realized = np.zeros_like(sim.terminal)
        closed = np.zeros_like(sim.terminal)
    remaining = np.maximum(0.0, 1.0 - closed)
    exit_r = sim.terminal.copy()
    be_armed = (inputs.max_r >= inputs.be_after - 1e-12) | (
        sim.max_r >= inputs.be_after - 1e-12)
    exit_r = np.where(be_armed & (exit_r < 0.0), 0.0, exit_r)
    return realized + remaining * exit_r


def build_policy_distributions(sim: PathSimulation, inputs: PolicyInputs) -> dict[str, PolicyDistribution]:
    baseline = baseline_strategy_outcomes(sim, inputs)
    out: dict[str, PolicyDistribution] = {}
    for name, fraction in POLICY_FRACTIONS.items():
        outcomes = np.full_like(baseline, inputs.r0) if fraction >= 1.0 else (
            fraction * inputs.r0 + (1.0 - fraction) * baseline)
        out[name] = PolicyDistribution(name=name, close_fraction=fraction, outcomes=outcomes)
    return out


def _next_rung(inputs: PolicyInputs) -> float | None:
    return next((x for x in inputs.rungs if x > max(inputs.r0, inputs.max_r) + 1e-8), None)


def policy_metrics(policy: PolicyDistribution, sim: PathSimulation,
                   inputs: PolicyInputs) -> dict:
    values = policy.outcomes
    next_rung = _next_rung(inputs)
    stop_t = _strategy_risk_exit_time(sim)
    rung_t = sim.rung_times.get(next_rung) if next_rung is not None else sim.take_time
    if rung_t is None:
        rung_t = np.full_like(stop_t, np.nan)
    rung_first = ~np.isnan(rung_t) & (np.isnan(stop_t) | (rung_t < stop_t))
    stop_first = ~np.isnan(stop_t) & (np.isnan(rung_t) | (stop_t < rung_t))
    event_t = np.where(rung_first, rung_t, np.where(stop_first, stop_t, np.nan))
    resolved = event_t[~np.isnan(event_t)]

    no_event: dict[str, float] = {}
    for minutes in (15, 30, 60, 120):
        frac = min(minutes / max(inputs.horizon_minutes, 1.0), 1.0)
        event_by = (~np.isnan(event_t)) & (event_t <= frac + 1e-12)
        no_event[f"{minutes}m"] = round(float(1.0 - np.mean(event_by)), 4)

    p_loss_events = values < 0.0
    uncertainty = {
        "expected_final_r": _mean_uncertainty(values),
        "cvar10_r": _cvar_uncertainty(values, 0.10),
        "p_final_loss": _probability_uncertainty(p_loss_events),
        "p_next_rung_before_stop": _probability_uncertainty(rung_first),
        "p_stop_before_next_rung": _probability_uncertainty(stop_first),
        "effective_path_count": int(values.size),
    }
    return {
        "name": policy.name,
        "close_fraction": policy.close_fraction,
        "expected_final_r": round(float(np.mean(values)), 4),
        "median_final_r": round(float(np.median(values)), 4),
        "cvar10_r": round(_cvar(values, 0.10), 4),
        "p_final_profit": round(float(np.mean(values > 0.0)), 4),
        "p_final_loss": round(float(np.mean(values < 0.0)), 4),
        "p_giveback_0_25_from_now": round(float(np.mean(values <= inputs.r0 - 0.25)), 4),
        "p_giveback_0_50_from_now": round(float(np.mean(values <= inputs.r0 - 0.50)), 4),
        "p_next_rung_before_stop": round(float(np.mean(rung_first)), 4),
        "p_stop_before_next_rung": round(float(np.mean(stop_first)), 4),
        "next_rung_r": _rnd(next_rung, 3),
        "expected_event_minutes": (
            round(float(np.mean(resolved)) * inputs.horizon_minutes, 1)
            if resolved.size else None),
        "no_event_probability": no_event,
        "monte_carlo_uncertainty": uncertainty,
    }


def _raw_policy_choice(metrics: dict[str, dict], r0: float,
                       *, cvar_floor: float | None = None) -> tuple[str, dict]:
    floor = max(-0.60, r0 - 0.80) if cvar_floor is None else float(cvar_floor)
    score = lambda row: -999.0 if row.get("cvar10_r") is None else float(row["cvar10_r"])
    eligible = [m for m in metrics.values() if score(m) >= floor]
    if not eligible:
        best = max(metrics.values(), key=score)
        return best["name"], {"cvar_floor_r": round(floor, 3), "eligible": []}
    best_mean = max(m["expected_final_r"] for m in eligible)
    # Do not trade a difference smaller than 0.03R: choose the least intervention.
    near = [m for m in eligible if best_mean - m["expected_final_r"] <= 0.03 + 1e-12]
    best = min(near, key=lambda x: POLICY_FRACTIONS[x["name"]])
    return best["name"], {
        "cvar_floor_r": round(floor, 3),
        "eligible": [m["name"] for m in eligible],
        "best_expected_r": round(best_mean, 4),
        "indifference_band_r": 0.03,
    }


def _run_once(inputs: PolicyInputs, *, n_paths: int, n_steps: int,
              seed: int) -> tuple[dict[str, dict], PathSimulation]:
    sim = simulate_option_paths(inputs, n_paths=n_paths, n_steps=n_steps, seed=seed)
    distributions = build_policy_distributions(sim, inputs)
    metrics = {name: policy_metrics(policy, sim, inputs)
               for name, policy in distributions.items()}
    return metrics, sim


def stability_analysis(inputs: PolicyInputs, base_choice: str) -> dict:
    scenarios: list[PolicyInputs] = [inputs]
    scenarios.extend([
        replace(inputs, r0=min(max(inputs.r0 + d, -0.98), inputs.T - 0.02))
        for d in (-0.10, 0.10)
    ])
    scenarios.extend([replace(inputs, sigma_R=max(0.08, inputs.sigma_R * m))
                      for m in (0.95, 1.05)])
    scenarios.extend([replace(inputs, drift_R=inputs.drift_R + d) for d in (-0.04, 0.04)])
    scenarios.extend([replace(inputs, skew_R=min(max(inputs.skew_R + d, -0.45), 0.45))
                      for d in (-0.05, 0.05)])
    scenarios.extend([replace(inputs, term_slope=min(max(inputs.term_slope + d, -0.8), 0.8))
                      for d in (-0.10, 0.10)])
    counts = {name: 0 for name in POLICY_FRACTIONS}
    for idx, scenario in enumerate(scenarios):
        metrics, _ = _run_once(scenario, n_paths=1400, n_steps=180,
                               seed=0xB100)
        choice, _ = _raw_policy_choice(metrics, scenario.r0)
        counts[choice] += 1
    total = len(scenarios)
    return {
        "checks": total,
        "selected_count": counts.get(base_choice, 0),
        "selected_share": round(counts.get(base_choice, 0) / total, 4),
        "winner_counts": counts,
        "perturbations": "r±0.10R; sigma±5%; drift±0.04R; skew±0.05; term±0.10",
    }


def _policy_advantage(metrics: dict[str, dict], chosen: str) -> dict:
    selected = metrics[chosen]
    hold = metrics["HOLD"]
    exit_ = metrics["EXIT"]
    alternatives = sorted(metrics.values(), key=lambda x: x["expected_final_r"], reverse=True)
    return {
        "vs_hold_expected_r": round(selected["expected_final_r"] - hold["expected_final_r"], 4),
        "vs_hold_cvar10_r": round(selected["cvar10_r"] - hold["cvar10_r"], 4),
        "vs_exit_expected_r": round(selected["expected_final_r"] - exit_["expected_final_r"], 4),
        "ranking_by_expected_r": [x["name"] for x in alternatives],
    }


def _confirmation_gate(chosen: str, stability: dict, metrics: dict[str, dict],
                       evidence: dict, inputs: PolicyInputs) -> tuple[str, list[str]]:
    if chosen == "HOLD":
        return chosen, []
    selected = metrics[chosen]
    hold = metrics["HOLD"]
    advantage = selected["expected_final_r"] - hold["expected_final_r"]
    share = stability["selected_share"]
    confirmations = int(evidence.get("adverse_confirmation_count") or 0)
    reasons: list[str] = []

    requirements = {
        "CLOSE_10": (0.02, 0.64, 2),
        "CLOSE_25": (0.03, 0.73, 2),
        "CLOSE_50": (0.05, 0.82, 3),
        "EXIT": (0.10, 0.88, 3),
    }
    min_adv, min_share, min_conf = requirements[chosen]
    if advantage < min_adv:
        reasons.append(f"преимущество над HOLD {advantage:+.3f}R < {min_adv:.2f}R")
    if share < min_share:
        reasons.append(f"устойчивость {share:.0%} < {min_share:.0%}")
    if confirmations < min_conf:
        reasons.append(f"подтверждений {confirmations} < {min_conf}")
    if not inputs.option_available:
        reasons.append("нет валидного option first-touch anchor")
    if inputs.chain_age_sec is not None and inputs.chain_age_sec > 1800:
        reasons.append("цепочка старше 30 минут")
    if not reasons:
        return chosen, []

    # Downgrade one step at a time, then HOLD. The report exposes why.
    order = ["HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"]
    idx = order.index(chosen)
    for fallback in reversed(order[:idx]):
        if fallback == "HOLD":
            return fallback, reasons
        fallback_adv = metrics[fallback]["expected_final_r"] - hold["expected_final_r"]
        f_adv, f_share, f_conf = requirements[fallback]
        if fallback_adv >= f_adv and share >= f_share and confirmations >= f_conf:
            return fallback, reasons
    return "HOLD", reasons


def _interp(x: float, xs: Iterable[float], ys: Iterable[float]) -> float | None:
    pairs = sorted((float(a), float(b)) for a, b in zip(xs, ys)
                   if _num(a) is not None and _num(b) is not None)
    if not pairs:
        return None
    xx = np.asarray([p[0] for p in pairs], dtype=float)
    yy = np.asarray([p[1] for p in pairs], dtype=float)
    return float(np.interp(float(x), xx, yy, left=yy[0], right=yy[-1]))


def _iv_pct(value: float) -> float:
    value = float(value)
    return value if value > 3.0 else value * 100.0


def _project_total_variance(samples: list[dict], target_days: float) -> float | None:
    """Match the frontend LOCAL 24H projection exactly."""
    pts = sorted(
        (float(x["days"]), float(x["iv_pct"]))
        for x in samples
        if (_num(x.get("days")) or 0) > 0 and (_num(x.get("iv_pct")) or 0) > 0
    )
    if not pts or target_days <= 0:
        return None
    tau = target_days / 365.0
    tw = [(days / 365.0, (iv_pct / 100.0) ** 2 * (days / 365.0))
          for days, iv_pct in pts]
    if len(tw) == 1 or tau <= tw[0][0]:
        variance = tw[0][1] * tau / tw[0][0]
    elif tau >= tw[-1][0]:
        last, previous = tw[-1], tw[-2]
        slope = max(0.0, (last[1] - previous[1]) / max(last[0] - previous[0], 1e-9))
        variance = last[1] + slope * (tau - last[0])
    else:
        hi = next(i for i in range(1, len(tw)) if tw[i][0] >= tau)
        lo = hi - 1
        frac = (tau - tw[lo][0]) / max(tw[hi][0] - tw[lo][0], 1e-9)
        variance = tw[lo][1] + (tw[hi][1] - tw[lo][1]) * frac
    return math.sqrt(max(variance, 1e-12) / tau) * 100.0


def local_iv_surface(payload: dict) -> dict:
    """Backend summary of the exact LOCAL 24H surface now shown in the browser."""
    rows = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"available": False, "reason": "no surface rows"}
    smiles = []
    for row in rows:
        spot = _num(row.get("spot_at_snapshot"))
        days = _num(row.get("days"))
        strikes = row.get("strikes") or []
        ivs = row.get("ivs") or []
        if not spot or not days or days <= 0:
            continue
        pairs = []
        for strike, iv in zip(strikes, ivs):
            strike_n, iv_n = _num(strike), _num(iv)
            if strike_n is None or iv_n is None or iv_n <= 0:
                continue
            pairs.append(((strike_n / spot - 1.0) * 100.0, _iv_pct(iv_n)))
        pairs.sort()
        if len(pairs) >= 3:
            smiles.append({"days": float(days), "xs": [x for x, _ in pairs],
                           "ys": [y for _, y in pairs], "spot": float(spot),
                           "expiry": row.get("expiry")})
    if not smiles:
        return {"available": False, "reason": "invalid surface rows"}
    smiles.sort(key=lambda x: x["days"])

    x_lo = max(-15.0, max(x["xs"][0] for x in smiles))
    x_hi = min(15.0, min(x["xs"][-1] for x in smiles))
    if not x_hi > x_lo + 1.0:
        x_lo = max(-15.0, smiles[0]["xs"][0])
        x_hi = min(15.0, smiles[0]["xs"][-1])
    if not x_hi > x_lo + 1.0:
        return {"available": False, "reason": "surface moneyness overlap is empty"}
    grid = np.linspace(x_lo, x_hi, 41)
    real_rows = [
        [float(_interp(x, smile["xs"], smile["ys"])) for x in grid]
        for smile in smiles
    ]
    local_rows = []
    for hours in LOCAL_HOURS:
        target_days = hours / 24.0
        row = []
        for x in grid:
            samples = [{"days": smile["days"],
                        "iv_pct": _interp(x, smile["xs"], smile["ys"])}
                       for smile in smiles]
            row.append(_project_total_variance(samples, target_days))
        local_rows.append(row)

    snapshot_spot = smiles[0]["spot"]
    live_spot = _num(payload.get("spot_current")) or snapshot_spot
    live_shift = (live_spot / snapshot_spot - 1.0) * 100.0

    def metrics(row: list[float]) -> dict:
        atm = _interp(live_shift, grid, row)
        put = _interp(live_shift - 5.0, grid, row)
        call = _interp(live_shift + 5.0, grid, row)
        if atm is None or put is None or call is None:
            return {"atm_iv_pct": None, "put_call_skew_pp": None,
                    "curvature_pp": None}
        return {
            "atm_iv_pct": round(atm, 3),
            "put_call_skew_pp": round(put - call, 3),
            "curvature_pp": round(put + call - 2.0 * atm, 3),
        }

    local = [{"hours": h, **metrics(row)} for h, row in zip(LOCAL_HOURS, local_rows)]
    real = [{"days": smiles[i]["days"], "expiry": smiles[i]["expiry"],
             **metrics(real_rows[i])} for i in range(len(smiles))]
    ts = _num(payload.get("ts"))
    return {
        "available": True, "kind": "total_variance_projection",
        "frontend_formula_match": True,
        "live_moneyness_pct": round(live_shift, 4),
        "moneyness_range_pct": [round(x_lo, 3), round(x_hi, 3)],
        "local_24h": local, "real_expiries": real,
        "snapshot_age_sec": round(time.time() - ts, 1) if ts else None,
        "spot_status": payload.get("spot_status"), "spot_source": payload.get("spot_source"),
    }

def full_correlation_summary(payload: dict, instrument: str | None = None) -> dict:
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return {"available": False, "status": _at(payload, "status")}
    assets = value.get("assets") or []
    short = value.get("matrix_short") or value.get("matrix") or []
    baseline = value.get("matrix_baseline") or []
    delta = value.get("matrix_delta") or []
    pairs = []
    for i in range(len(assets)):
        for j in range(i + 1, len(assets)):
            def cell(matrix):
                return _rnd(matrix[i][j], 4) if i < len(matrix) and j < len(matrix[i]) else None
            pair = {"pair": f"{assets[i]}-{assets[j]}",
                    "rolling": cell(short), "baseline": cell(baseline),
                    "delta": cell(delta)}
            if any(pair[key] is not None for key in ("rolling", "baseline", "delta")):
                pairs.append(pair)
    pairs.sort(key=lambda x: abs(x["delta"] or 0.0), reverse=True)
    aliases = {
        "NAS100": ("NAS", "VXN", "SP500", "VIX"),
        "SP500": ("SP500", "VIX", "NAS", "VXN"),
        "US30": ("US30", "SP500", "NAS", "VIX"),
        "XAU": ("GOLD", "GVZ", "DXY", "VIX"),
        "XAG": ("XAGUSD", "GOLD", "GVZ", "EURUSD"),
        "EURUSD": ("EURUSD", "USDCAD", "VIX", "GOLD"),
        "USDCAD": ("USDCAD", "EURUSD", "OIL", "GOLD"),
        "GER40": ("GER40", "SP500", "VIX", "EURUSD"),
        "UK100": ("UK100", "SP500", "VIX", "EURUSD"),
        "JPY100": ("JPY100", "NAS", "SP500", "VIX"),
    }
    relevant_keys = aliases.get(instrument or "", (instrument or "",))
    relevant = [x for x in pairs if any(k and k in x["pair"] for k in relevant_keys)]
    return {"available": bool(pairs), "status": payload.get("status"),
            "source": payload.get("source"), "all_pairs": pairs,
            "instrument_relevant": relevant[:8], "largest_changes": pairs[:6]}


def _level_r(level: Any, trade: dict) -> float | None:
    level = _num(level)
    entry, stop = _num(trade.get("entry")), _num(trade.get("stop"))
    if level is None or entry is None or stop is None or entry == stop:
        return None
    sign = 1.0 if trade.get("direction") == "long" else -1.0
    return round(sign * (level - entry) / abs(entry - stop), 4)


def _ridge_context(ridge: dict, trade: dict) -> dict:
    if not ridge.get("available"):
        return {"available": False, "reason": ridge.get("reason")}
    snaps = ridge.get("snapshots") or []
    latest = snaps[-1] if snaps else {}
    previous = snaps[-2] if len(snaps) > 1 else {}
    walls = ridge.get("oi_walls") or {}
    latest_rr = _num(_at(latest, "skew", "rr"))
    previous_rr = _num(_at(previous, "skew", "rr"))
    gex = latest.get("gex") or {}
    top = []
    for item in gex.get("top") or []:
        price = item.get("price") if item.get("price") is not None else item.get("strike")
        top.append({"r": _level_r(price, trade), "gex": _rnd(item.get("gex"), 2)})
    return {
        "available": True, "snapshots": len(snaps), "proxy": ridge.get("proxy"),
        "rn_tail": ridge.get("rn_probs"),
        "implied_move_frac": _rnd(_at(latest, "implied_move", "move_frac"), 5),
        "skew": latest.get("skew"),
        "skew_delta_snapshot": _rnd(latest_rr - previous_rr, 5)
        if latest_rr is not None and previous_rr is not None else None,
        "term": latest.get("term"),
        "oi_walls": {"call_r": _level_r(walls.get("call_wall"), trade),
                     "put_r": _level_r(walls.get("put_wall"), trade)},
        "gex": {"available": bool(gex.get("available", gex)),
                "zero_flip_r": _level_r(gex.get("zero_flip"), trade),
                "top": top, "authority": "context_only_unobserved_dealer_sign"},
    }


def _price_tape(engine, tick: dict, trade: dict) -> dict:
    points = [x for x in getattr(engine.market, "intraday", []) if len(x) >= 2]
    points = [(float(x[0]), float(x[1])) for x in points
              if _num(x[0]) is not None and _num(x[1]) is not None]
    current = _num(_at(tick, "feeds", "price", "value"))
    risk = abs((_num(trade.get("entry")) or 0) - (_num(trade.get("stop")) or 0))
    if not points or current is None or risk <= 0:
        return {"available": False, "samples": len(points)}
    offset = current - points[-1][1]
    aligned = [(ts, price + offset) for ts, price in points[-120:]]

    def move(minutes: float) -> dict:
        target = aligned[-1][0] - minutes * 60.0
        base = min(aligned, key=lambda x: abs(x[0] - target))
        delta = current - base[1]
        signed = delta / risk
        if trade.get("direction") == "short":
            signed = -signed
        return {"points": round(delta, 4), "directional_r": round(signed, 4),
                "actual_minutes": round((aligned[-1][0] - base[0]) / 60.0, 2)}

    recent = aligned[-30:]
    ups = sum(recent[i][1] > recent[i - 1][1] for i in range(1, len(recent)))
    downs = sum(recent[i][1] < recent[i - 1][1] for i in range(1, len(recent)))
    return {"available": True, "samples": len(points),
            "moves": {"5m": move(5), "15m": move(15), "60m": move(60)},
            "up_ticks": ups, "down_ticks": downs,
            "range_r": round((max(p for _, p in recent) - min(p for _, p in recent)) / risk, 4)}


def build_metric_evidence(engine, tick: dict, ridge: dict, trade: dict,
                          inputs: PolicyInputs, sim: PathSimulation,
                          policy_metrics_map: dict[str, dict]) -> dict:
    market = tick.get("market") or {}
    cone = tick.get("cone") or {}
    levels = tick.get("levels") or {}
    gamma = tick.get("gamma") or {}
    vp = levels.get("volume_profile") or {}
    tape = _price_tape(engine, tick, trade)
    iv = local_iv_surface(tick.get("iv_surface") or {})
    corr = full_correlation_summary(tick.get("correlation") or {}, tick.get("instrument"))
    ridge_ctx = _ridge_context(ridge, trade)
    next_rung = _next_rung(inputs)
    hold = policy_metrics_map["HOLD"]

    confirmations = []
    contradictions = []
    barrier_ev = _num(market.get("horizon_barrier_ev"))
    if barrier_ev is not None:
        (confirmations if barrier_ev < -0.03 else contradictions if barrier_ev > 0.03 else []).append(
            {"metric": "barrier_ev_r", "value": round(barrier_ev, 4)})
    center = _num(market.get("scenario_median_r"))
    if center is not None and next_rung is not None:
        if center < inputs.r0 - 0.05:
            confirmations.append({"metric": "rnd_median_r", "value": round(center, 4)})
        elif center > inputs.r0 + 0.05:
            contradictions.append({"metric": "rnd_median_r", "value": round(center, 4)})
    if hold["p_stop_before_next_rung"] > hold["p_next_rung_before_stop"] + 0.03:
        confirmations.append({"metric": "stop_vs_next_rung",
                              "p_stop": hold["p_stop_before_next_rung"],
                              "p_next": hold["p_next_rung_before_stop"]})
    elif hold["p_next_rung_before_stop"] > hold["p_stop_before_next_rung"] + 0.03:
        contradictions.append({"metric": "next_rung_vs_stop",
                               "p_next": hold["p_next_rung_before_stop"],
                               "p_stop": hold["p_stop_before_next_rung"]})

    move15 = _num(_at(tape, "moves", "15m", "directional_r"))
    move60 = _num(_at(tape, "moves", "60m", "directional_r"))
    if move15 is not None and move15 <= -0.08:
        confirmations.append({"metric": "live_15m_r", "value": move15})
    elif move15 is not None and move15 >= 0.08:
        contradictions.append({"metric": "live_15m_r", "value": move15})
    if move60 is not None and move60 <= -0.15:
        confirmations.append({"metric": "live_60m_r", "value": move60})
    elif move60 is not None and move60 >= 0.15:
        contradictions.append({"metric": "live_60m_r", "value": move60})

    # IV surface is confirmation only: live spot moves along a fixed option smile;
    # it must not be counted as a fresh tick-by-tick option quote.
    local_24 = next((x for x in iv.get("local_24h", []) if x["hours"] == 24.0), None)
    if local_24 and _num(local_24.get("put_call_skew_pp")) is not None:
        skew_pp = float(local_24["put_call_skew_pp"])
        adverse = skew_pp > 0.4 if trade.get("direction") == "long" else skew_pp < -0.4
        supportive = skew_pp < -0.4 if trade.get("direction") == "long" else skew_pp > 0.4
        if adverse:
            confirmations.append({"metric": "local24h_put_call_skew_pp", "value": skew_pp})
        elif supportive:
            contradictions.append({"metric": "local24h_put_call_skew_pp", "value": skew_pp})

    ridge_skew_delta = _num(ridge_ctx.get("skew_delta_snapshot"))
    if ridge_skew_delta is not None and abs(ridge_skew_delta) >= 0.005:
        adverse_skew_change = (ridge_skew_delta < 0 if trade.get("direction") == "long"
                               else ridge_skew_delta > 0)
        target = confirmations if adverse_skew_change else contradictions
        target.append({"metric": "option_skew_snapshot_delta",
                       "value": round(ridge_skew_delta, 5)})

    level_r = {
        "vwap": _level_r(levels.get("vwap"), trade),
        "day_low": _level_r(levels.get("day_low"), trade),
        "day_high": _level_r(levels.get("day_high"), trade),
        "implied_low": _level_r(_at(levels, "implied_band", "low"), trade),
        "implied_high": _level_r(_at(levels, "implied_band", "high"), trade),
        "poc": _level_r(vp.get("poc"), trade),
        "value_area_low": _level_r(vp.get("value_area_low"), trade),
        "value_area_high": _level_r(vp.get("value_area_high"), trade),
    }
    nearby_adverse = [k for k, v in level_r.items()
                      if v is not None and inputs.r0 - 0.20 <= v < inputs.r0]
    nearby_supportive = [k for k, v in level_r.items()
                         if v is not None and inputs.r0 < v <= inputs.r0 + 0.20]
    # Location relative to accepted-value references is directional in R-space.
    overhead_refs = [k for k in ("vwap", "poc", "value_area_high")
                     if level_r.get(k) is not None and level_r[k] >= inputs.r0 + 0.05]
    accepted_below = [k for k in ("vwap", "poc", "value_area_low")
                      if level_r.get(k) is not None and level_r[k] <= inputs.r0 - 0.05]
    if len(overhead_refs) >= 2:
        confirmations.append({"metric": "accepted_value_overhead", "levels": overhead_refs})
    elif len(accepted_below) >= 2:
        contradictions.append({"metric": "accepted_value_below", "levels": accepted_below})

    vp_delta = _volume_profile_delta(vp, trade)
    delta_ratio = _num(vp_delta.get("directional_delta_ratio"))
    if delta_ratio is not None and delta_ratio <= -0.10:
        confirmations.append({"metric": "directional_volume_delta", "value": delta_ratio})
    elif delta_ratio is not None and delta_ratio >= 0.10:
        contradictions.append({"metric": "directional_volume_delta", "value": delta_ratio})

    required_blocks = [x.get("key") for x in (tick.get("filters") or [])
                       if x.get("required") and x.get("decision_weight", True)
                       and x.get("state") == "block"]
    if required_blocks:
        confirmations.append({"metric": "required_filter_blocks", "values": required_blocks})

    gex_context = {
        "available": gamma.get("available"), "zone": gamma.get("zone"),
        "magnet_r": _rnd(gamma.get("magnet_r"), 4),
        "strength": _rnd(gamma.get("strength"), 4), "toward": gamma.get("toward"),
        "decision_weight": gamma.get("decision_weight", False),
    }
    # It can confirm context, never create the active action alone.
    if gamma.get("available") and gamma.get("toward") == "стопу":
        confirmations.append({"metric": "gamma_context_toward", "value": "стопу",
                              "context_only": True})
    elif gamma.get("available") and gamma.get("toward") == "тейку":
        contradictions.append({"metric": "gamma_context_toward", "value": "тейку",
                               "context_only": True})

    return {
        "option_barrier": {
            "p_take": _rnd(market.get("p_take_horizon")),
            "p_stop": _rnd(market.get("p_stop_horizon")),
            "no_touch": _rnd(market.get("p_unresolved_horizon")),
            "barrier_ev_r": _rnd(barrier_ev),
        },
        "cone_rnd": {
            "p10_r": _rnd(market.get("scenario_p10_r"), 4),
            "mode_r": _rnd(market.get("scenario_mode_r"), 4),
            "median_r": _rnd(market.get("scenario_median_r"), 4),
            "p90_r": _rnd(market.get("scenario_p90_r"), 4),
            "alive_mass": _rnd(market.get("scenario_slice_alive")),
            "center_path": _center_path(cone),
        },
        "iv_surface": iv,
        "live_price": tape,
        "atr_regime": {"atr": tick.get("atr"), "regime": tick.get("regime"),
                       "sigma": tick.get("sigma"), "vrp": tick.get("vrp")},
        "levels": {"r": level_r, "nearby_adverse": nearby_adverse,
                   "nearby_supportive": nearby_supportive,
                   "volume_profile_delta": vp_delta},
        "correlation": corr,
        "strike_oi_gex": ridge_ctx,
        "gamma_context": gex_context,
        "filters": tick.get("filters") or [],
        "data_quality": {
            "price": {k: _at(tick, "feeds", "price", default={}).get(k)
                      for k in ("status", "source", "age_sec", "fresh", "derived", "error")},
            "chain": {k: _at(tick, "feeds", "chain", default={}).get(k)
                      for k in ("status", "source", "age_sec", "delay_hint_sec", "error")},
            "option_available": inputs.option_available,
            "proxy_quality": inputs.proxy_quality,
        },
        "decision_roles": {
            "core_path_inputs": ["r", "T", "sigma_R", "drift_R", "skew_R",
                                 "term_slope", "option_horizon"],
            "independent_confirmation": ["barrier_EV", "RND_center", "live_tape",
                                         "local_IV_skew", "skew_snapshot_change",
                                         "levels", "volume_delta", "hard_filters"],
            "confidence_only": ["full_correlation_regime", "VRP_extreme",
                                "snapshot_age", "proxy_quality"],
            "context_only": ["OI_walls", "GEX", "gamma_without_dealer_sign"],
        },
        "adverse_confirmations": confirmations,
        "supportive_contradictions": contradictions,
        "uncertainty_flags": _uncertainty_flags(tick, corr, iv, ridge_ctx),
        "adverse_confirmation_count": sum(1 for x in confirmations if not x.get("context_only")),
    }



def _uncertainty_flags(tick: dict, corr: dict, iv: dict, ridge: dict) -> list[dict]:
    flags = []
    atr_phase = _at(tick, "atr", "phase")
    if atr_phase == "shock":
        flags.append({"metric": "atr_phase", "value": "shock"})
    vrp_ratio = _num(_at(tick, "vrp", "iv_rv_ratio"))
    if vrp_ratio is not None and (vrp_ratio >= 1.35 or vrp_ratio <= 0.75):
        flags.append({"metric": "iv_rv_ratio", "value": round(vrp_ratio, 3)})
    relevant = corr.get("instrument_relevant") or []
    large = [x for x in relevant if abs(_num(x.get("delta")) or 0.0) >= 0.30]
    if large:
        flags.append({"metric": "correlation_regime_shift", "pairs": large[:3]})
    if iv.get("available") and (_num(iv.get("snapshot_age_sec")) or 0) > 1800:
        flags.append({"metric": "iv_surface_age_sec", "value": iv.get("snapshot_age_sec")})
    if ridge.get("available") and int(ridge.get("snapshots") or 0) < 2:
        flags.append({"metric": "ridge_history", "value": ridge.get("snapshots")})
    return flags

def _center_path(cone: dict) -> list[dict]:
    density = cone.get("density") or []
    edges = cone.get("edges") or []
    times = cone.get("times_frac") or []
    if len(edges) < 2:
        return []
    centers = np.asarray([(edges[i] + edges[i + 1]) / 2.0 for i in range(len(edges) - 1)])
    out = []
    for idx, row in enumerate(density):
        probs = np.asarray(row, dtype=float)
        mass = float(probs.sum())
        if mass <= 0 or len(probs) != len(centers):
            median = None
        else:
            median = _weighted_quantile(centers, probs, 0.5)
        out.append({"t_frac": _rnd(times[idx] if idx < len(times) else None, 4),
                    "median_r": _rnd(median, 4), "alive": round(mass, 4)})
    return out


def _volume_profile_delta(vp: dict, trade: dict) -> dict:
    bins = vp.get("bins") or []
    if vp.get("is_tpo") or not vp.get("flow_available", True):
        return {
            "directional_delta_ratio": None,
            "kind": "TPO_occupancy_only",
            "bins": len(bins),
            "available": False,
            "reason": "no_observed_volume_for_directional_flow",
            "authority": "none",
        }
    total_delta = sum(_num(x.get("delta")) or 0.0 for x in bins)
    total_volume = sum(_num(x.get("volume")) or 0.0 for x in bins)
    signed = total_delta / total_volume if total_volume else None
    if signed is not None and trade.get("direction") == "short":
        signed = -signed
    return {"directional_delta_ratio": _rnd(signed, 4),
            "kind": "observed_volume_tick_rule",
            "bins": len(bins), "available": signed is not None,
            "authority": "context_only"}


def metric_coverage(evidence: dict, history: dict | None = None) -> dict:
    roles = {
        "option_first_touch_no_touch_barrier_ev": (
            evidence.get("option_barrier"), "core_distribution"),
        "rnd_quantiles_and_full_center_path": (
            evidence.get("cone_rnd"), "core_distribution_and_diagnostics"),
        "iv_real_expiries_and_local_1_24h": (
            evidence.get("iv_surface"), "core_sigma_skew_term_plus_confirmation"),
        "live_tape_5_15_60m": (
            evidence.get("live_price"), "independent_confirmation"),
        "atr_regime_sigma_vrp": (
            evidence.get("atr_regime"), "core_volatility_plus_confidence"),
        "vwap_day_implied_poc_value_area_delta": (
            evidence.get("levels"), "independent_confirmation"),
        "full_correlation_matrix": (
            evidence.get("correlation"), "regime_confidence_not_directional_edge"),
        "strike_rnd_oi_walls_gex_history": (
            evidence.get("strike_oi_gex"), "context_and_snapshot_change"),
        "gamma_context": (
            evidence.get("gamma_context"), "context_only_without_dealer_sign"),
        "strategy_filters": (
            evidence.get("filters"), "hard_constraint_when_decision_weight_true"),
        "feed_and_proxy_quality": (
            evidence.get("data_quality"), "confidence_and_action_gate"),
        "metric_history": (
            history or {}, "dynamic_change_and_attribution"),
    }
    result = {}
    available = 0
    for name, (value, role) in roles.items():
        ok = bool(value) and (not isinstance(value, dict)
                              or value.get("available", True) is not False)
        result[name] = {"available": ok, "decision_role": role}
        available += int(ok)
    result["summary"] = {
        "available_groups": available, "total_groups": len(roles),
        "coverage_ratio": round(available / max(len(roles), 1), 4),
        "all_groups_have_explicit_role": True,
    }
    return result

def metric_change_summary(current: dict, previous: dict | None) -> dict:
    """Explicit changes for every decision-bearing evidence family."""
    if not previous:
        return {"available": False, "reason": "previous evidence unavailable", "changes": []}

    paths = {
        "option.p_take": ("option_barrier", "p_take"),
        "option.p_stop": ("option_barrier", "p_stop"),
        "option.no_touch": ("option_barrier", "no_touch"),
        "option.barrier_ev_r": ("option_barrier", "barrier_ev_r"),
        "rnd.p10_r": ("cone_rnd", "p10_r"),
        "rnd.mode_r": ("cone_rnd", "mode_r"),
        "rnd.median_r": ("cone_rnd", "median_r"),
        "rnd.p90_r": ("cone_rnd", "p90_r"),
        "rnd.alive_mass": ("cone_rnd", "alive_mass"),
        "tape.15m_r": ("live_price", "moves", "15m", "directional_r"),
        "tape.60m_r": ("live_price", "moves", "60m", "directional_r"),
        "atr.ratio": ("atr_regime", "atr", "ratio"),
        "sigma.ratio": ("atr_regime", "sigma", "ratio"),
        "vrp.iv_rv_ratio": ("atr_regime", "vrp", "iv_rv_ratio"),
        "volume.directional_delta": ("levels", "volume_profile_delta", "directional_delta_ratio"),
        "ridge.skew_delta": ("strike_oi_gex", "skew_delta_snapshot"),
        "gamma.strength": ("gamma_context", "strength"),
        "gamma.magnet_r": ("gamma_context", "magnet_r"),
    }

    def local24(evidence: dict, field: str):
        rows = _at(evidence, "iv_surface", "local_24h", default=[]) or []
        row = next((x for x in rows if _num(x.get("hours")) == 24.0), None)
        return _num((row or {}).get(field))

    changes = []
    for name, path in paths.items():
        old, new_value = _num(_at(previous, *path)), _num(_at(current, *path))
        if old is not None and new_value is not None:
            changes.append({"metric": name, "previous": round(old, 5),
                            "current": round(new_value, 5),
                            "delta": round(new_value - old, 5)})
    for field in ("atm_iv_pct", "put_call_skew_pp", "curvature_pp"):
        old, new_value = local24(previous, field), local24(current, field)
        if old is not None and new_value is not None:
            changes.append({"metric": f"iv24h.{field}", "previous": round(old, 5),
                            "current": round(new_value, 5),
                            "delta": round(new_value - old, 5)})

    old_levels = _at(previous, "levels", "r", default={}) or {}
    new_levels = _at(current, "levels", "r", default={}) or {}
    for key in sorted(set(old_levels) | set(new_levels)):
        old, new_value = _num(old_levels.get(key)), _num(new_levels.get(key))
        if old is not None and new_value is not None:
            changes.append({"metric": f"level.{key}_r", "previous": round(old, 5),
                            "current": round(new_value, 5),
                            "delta": round(new_value - old, 5)})

    def pairs(evidence: dict) -> dict:
        return {x.get("pair"): x for x in
                (_at(evidence, "correlation", "all_pairs", default=[]) or [])
                if x.get("pair")}
    old_pairs, new_pairs = pairs(previous), pairs(current)
    for pair in sorted(set(old_pairs) & set(new_pairs)):
        old, new_value = _num(old_pairs[pair].get("rolling")), _num(new_pairs[pair].get("rolling"))
        if old is not None and new_value is not None:
            changes.append({"metric": f"correlation.{pair}", "previous": round(old, 5),
                            "current": round(new_value, 5),
                            "delta": round(new_value - old, 5)})

    # Order by absolute movement only for display; units remain explicit in names.
    changes.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return {"available": bool(changes), "changes": changes,
            "compared_groups": ["option", "RND", "IV24h", "tape", "ATR/sigma/VRP",
                                "levels/delta", "correlation", "ridge", "gamma"]}


def counterfactual_attribution(current: PolicyInputs,
                               previous: dict | None) -> dict:
    if not previous:
        return {"available": False, "reason": "previous policy inputs unavailable"}
    try:
        old = PolicyInputs(
            r0=float(previous["r0"]), T=float(previous["T"]),
            sigma_R=float(previous["sigma_R"]), drift_R=float(previous["drift_R"]),
            skew_R=float(previous["skew_R"]), term_slope=float(previous["term_slope"]),
            horizon_minutes=float(previous["horizon_minutes"]),
            max_r=float(previous.get("max_r", previous["r0"])),
            rungs=tuple(float(x) for x in previous.get("rungs", current.rungs)),
            rung_fraction=float(previous.get("rung_fraction", current.rung_fraction)),
            be_after=float(previous.get("be_after", current.be_after)),
            option_available=bool(previous.get("option_available", True)),
            chain_age_sec=_num(previous.get("chain_age_sec")),
            chain_status=previous.get("chain_status"),
            proxy_quality=previous.get("proxy_quality"), source=previous.get("source"),
        )
    except (KeyError, TypeError, ValueError):
        return {"available": False, "reason": "previous policy inputs incompatible"}

    def hold_mean(inp: PolicyInputs, seed: int) -> float:
        metrics, _ = _run_once(inp, n_paths=2400, n_steps=220, seed=seed)
        return float(metrics["HOLD"]["expected_final_r"])

    stages = [
        ("previous", old),
        ("price_r", replace(old, r0=current.r0, max_r=max(old.max_r, current.r0))),
        ("sigma_R", replace(old, r0=current.r0, max_r=max(old.max_r, current.r0),
                            sigma_R=current.sigma_R)),
        ("drift_R", replace(old, r0=current.r0, max_r=max(old.max_r, current.r0),
                            sigma_R=current.sigma_R, drift_R=current.drift_R)),
        ("skew_R", replace(old, r0=current.r0, max_r=max(old.max_r, current.r0),
                           sigma_R=current.sigma_R, drift_R=current.drift_R,
                           skew_R=current.skew_R)),
        ("term_horizon", current),
    ]
    values = [(name, hold_mean(inp, 0xCAFE)) for name, inp in stages]
    contributions = []
    for (name, value), (_, prior) in zip(values[1:], values[:-1]):
        contributions.append({"component": name, "delta_expected_r": round(value - prior, 4)})
    return {"available": True, "previous_expected_r": round(values[0][1], 4),
            "current_expected_r": round(values[-1][1], 4),
            "total_change_r": round(values[-1][1] - values[0][1], 4),
            "sequential_contributions": contributions,
            "method": "common-random sequential replacement; order is explicit"}


def cancellation_boundaries(inputs: PolicyInputs, selected: str) -> dict:
    if selected == "HOLD":
        return {"available": False, "reason": "HOLD has no pre-execution cancellation"}
    grid = np.linspace(max(-0.95, inputs.r0 - 0.50), min(inputs.T - 0.02, inputs.r0 + 0.50), 21)
    rows = []
    for idx, r in enumerate(grid):
        scenario = replace(inputs, r0=float(r), max_r=max(inputs.max_r, float(r)))
        metrics, sim = _run_once(scenario, n_paths=1200, n_steps=160, seed=0xD000)
        choice, _ = _raw_policy_choice(metrics, scenario.r0)
        p_take = float(np.mean(~np.isnan(sim.take_time)))
        p_stop = float(np.mean(~np.isnan(sim.stop_time)))
        rows.append({"r": round(float(r), 4), "choice": choice,
                     "barrier_ev_r": round(inputs.T * p_take - p_stop, 4)})
    hold_rows = [x for x in rows if x["choice"] == "HOLD"]
    nearest = min(hold_rows, key=lambda x: abs(x["r"] - inputs.r0)) if hold_rows else None
    return {"available": bool(nearest), "hold_switch": nearest,
            "grid_min_r": rows[0]["r"], "grid_max_r": rows[-1]["r"],
            "method": "recompute all policies over r-grid; other option inputs fixed"}


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None) -> dict:
    inputs = extract_policy_inputs(tick)
    if hasattr(engine, "authoritative_execution_mc"):
        sim = engine.authoritative_execution_mc(inputs)
        distributions = build_policy_distributions(sim, inputs)
        metrics = {name: policy_metrics(policy, sim, inputs)
                   for name, policy in distributions.items()}
    else:
        metrics, sim = _run_once(inputs, n_paths=6500, n_steps=340, seed=0xA17E)
    raw_choice, selection_rule = _raw_policy_choice(metrics, inputs.r0)
    evidence = build_metric_evidence(engine, tick, ridge, trade, inputs, sim, metrics)
    stability = stability_analysis(inputs, raw_choice)
    selected, downgrade_reasons = _confirmation_gate(raw_choice, stability, metrics,
                                                     evidence, inputs)
    if selected != raw_choice:
        # Stability is reported for both the raw optimizer and the gated action.
        selected_stability = stability_analysis(inputs, selected)
    else:
        selected_stability = stability
    advantage = _policy_advantage(metrics, selected)
    attribution = counterfactual_attribution(inputs, previous_policy_inputs)
    metric_changes = metric_change_summary(evidence, previous_evidence)
    cancellation = cancellation_boundaries(inputs, selected)
    next_rung = _next_rung(inputs)
    recommendation = {
        "policy": selected,
        "close_fraction": POLICY_FRACTIONS[selected],
        "action_ru": {
            "HOLD": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
            "CLOSE_10": "ЗАКРЫТЬ 10% ПОЗИЦИИ СЕЙЧАС",
            "CLOSE_25": "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС",
            "CLOSE_50": "ЗАКРЫТЬ 50% ПОЗИЦИИ СЕЙЧАС",
            "EXIT": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
        }[selected],
        "remaining_fraction": round(1.0 - POLICY_FRACTIONS[selected], 2),
        "remaining_management": (
            "остаток вести по исходному стопу; БУ/trailing запрещены до 1.5R"
            if inputs.max_r < inputs.be_after else
            "порог 1.5R уже достигнут: остаток вести по действующим правилам БУ/trailing"
        ),
        "next_rung_r": _rnd(next_rung, 3),
        "raw_optimizer_policy": raw_choice,
        "gate_downgrade_reasons": downgrade_reasons,
    }
    return {
        "version": "quant-policy-v1",
        "inputs": inputs.as_dict(),
        "recommendation": recommendation,
        "policies": metrics,
        "selection_rule": selection_rule,
        "selected_advantage": advantage,
        "stability": selected_stability,
        "raw_optimizer_stability": stability,
        "evidence": evidence,
        "metric_coverage": metric_coverage(evidence),
        "counterfactual_attribution": attribution,
        "metric_changes": metric_changes,
        "cancellation_boundary": cancellation,
        "first_touch_clock": first_touch_clock(sim, inputs),
        "assumptions": [
            "policies are evaluated for the currently remaining position",
            "future ladder rungs close the configured fraction; already crossed rungs are not paid twice",
            "5m/15m trailing is not simulated until candle-level exit logic is available",
            "OI/GEX has context-only authority because dealer position sign is not observed",
            "local 1-24h IV is a total-variance projection of real expiries, not a live option quote",
        ],
    }
