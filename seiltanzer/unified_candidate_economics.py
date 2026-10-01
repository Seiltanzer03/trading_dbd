"""Price every management candidate on one frozen, weighted scenario bank.

The old authoritative simulator retains absorbed outcomes rather than complete
market paths. Its bank cannot be reconstructed from a policy snapshot. Unless
an explicit full-path bank is supplied, this module therefore creates one
bounded *comparison* bank from the same option-driver parameters. It labels the
piecewise-linear execution assumption and never changes original admission or
claims to reproduce the authoritative simulator's Brownian-bridge events.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import math

import numpy as np

from .ai_policy_base import PolicyInputs, _centered_skew_noise
from .execution_simulator import ExecutionSpec, replay_execution_path


VERSION = "unified-candidate-economics-v1"
BASE_FRACTIONS = {"HOLD": 0., "CLOSE_10": .1, "CLOSE_25": .25,
                  "CLOSE_50": .5, "EXIT": 1.}
STOP_POLICIES = {"MOVE_TO_BE", "TIGHTEN_STOP", "TRAIL_GAMMA_FLIP"}
TAKE_POLICIES = {"REDUCE_TAKE", "EXTEND_TAKE"}
SEEDS = (0xA17E, 0xB17E)
PATHS_PER_SEED = 600
STEPS = 160


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _unavailable(reason):
    return {"version": VERSION, "available": False, "reason": reason,
            "candidates": {}, "shared_scenario_bank": False,
            "authoritative_bank_reused": False,
            "changes_original_admission": False}


def _option_driver_bank(inputs):
    """Draw once by stable path ID; policies never influence random draws."""
    mid = (np.arange(STEPS, dtype=float) + .5) / STEPS
    shape = np.exp(np.clip(inputs.term_slope, -.8, .8) * (mid - .5))
    variance = inputs.sigma_R ** 2 * shape ** 2 / np.sum(shape ** 2)
    batches = []
    for seed in SEEDS:
        rng = np.random.default_rng(seed)
        # Draw in time-major order, matching the indexed stable option stream.
        noise = _centered_skew_noise(
            rng.standard_normal((STEPS, PATHS_PER_SEED)).T, inputs.skew_R)
        increments = inputs.drift_R / STEPS + noise * np.sqrt(variance)
        paths = np.column_stack((np.full(PATHS_PER_SEED, inputs.r0),
                                  inputs.r0 + np.cumsum(increments, axis=1)))
        batches.append(paths)
    return (np.concatenate(batches), np.full(PATHS_PER_SEED * len(SEEDS),
                                            1. / (PATHS_PER_SEED * len(SEEDS))),
            np.repeat(np.asarray(SEEDS), PATHS_PER_SEED))


def _bank(snapshot, inputs):
    manager = snapshot.get("policy_manager") or {}
    supplied = manager.get("unified_scenario_bank", snapshot.get("unified_scenario_bank"))
    if supplied is None:
        # A weighted/empirical distribution is not recoverable from scalar
        # sigma/skew/drift. Never replace a declared such bank with new noise.
        declaration = str(manager.get("scenario_distribution_kind") or "").lower()
        if declaration in {"empirical", "weighted_empirical", "historical", "weighted_paths"}:
            raise ValueError("DECLARED_PATH_DISTRIBUTION_REQUIRES_FULL_FROZEN_BANK")
        paths, weights, groups = _option_driver_bank(inputs)
        metadata = {"source": "frozen_option_driver_comparison",
                    "distribution_kind": "option_parameter_diffusion",
                    "exact_authoritative_bank": False,
                    "bridge_events_reproduced": False,
                    "measure": "option_driver_model_not_empirical_return_probability",
                    "seeds": list(SEEDS)}
    else:
        if not isinstance(supplied, dict):
            raise ValueError("INVALID_FROZEN_SCENARIO_BANK")
        horizon = _number(supplied.get("horizon_minutes"))
        if horizon is None or abs(horizon - inputs.horizon_minutes) > 1e-8:
            raise ValueError("FROZEN_BANK_HORIZON_MISMATCH")
        if supplied.get("state_space") != "R_multiple_of_initial_risk":
            raise ValueError("FROZEN_BANK_STATE_SPACE_UNAVAILABLE")
        captured, bank_captured = (_number(snapshot.get("captured_ts")),
                                   _number(supplied.get("captured_ts")))
        if captured is not None and bank_captured is None:
            raise ValueError("FROZEN_BANK_SNAPSHOT_TIMESTAMP_UNAVAILABLE")
        if bank_captured is not None and (captured is None or abs(captured - bank_captured) > 1e-6):
            raise ValueError("FROZEN_BANK_SNAPSHOT_TIMESTAMP_MISMATCH")
        try:
            paths = np.array(supplied.get("r_paths"), dtype=float, copy=True)
            weights = np.asarray(supplied.get("weights"), dtype=float)
            groups = np.asarray(supplied.get("seed_ids", [0] * len(paths)))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("INVALID_FROZEN_SCENARIO_BANK") from None
        metadata = {"source": supplied.get("source") or "explicit_frozen_path_bank",
                    "distribution_kind": supplied.get("distribution_kind") or "supplied_weighted_paths",
                    "exact_authoritative_bank": supplied.get("exact_authoritative_bank") is True,
                    "bridge_events_reproduced": False,
                    "measure": supplied.get("measure") or "supplied_measure_not_reinterpreted",
                    "seeds": np.unique(groups).tolist()}
    if (paths.ndim != 2 or not 1 <= paths.shape[0] <= 10000
            or not 2 <= paths.shape[1] <= 2001 or not np.isfinite(paths).all()):
        raise ValueError("INVALID_FROZEN_SCENARIO_PATHS")
    if (weights.ndim != 1 or len(weights) != len(paths)
            or not np.isfinite(weights).all() or np.any(weights < 0)
            or not float(weights.sum()) > 0):
        raise ValueError("INVALID_FROZEN_SCENARIO_WEIGHTS")
    if groups.ndim != 1 or len(groups) != len(paths):
        raise ValueError("INVALID_FROZEN_SCENARIO_GROUPS")
    if not np.allclose(paths[:, 0], inputs.r0, rtol=0, atol=1e-7):
        raise ValueError("FROZEN_BANK_START_R_MISMATCH")
    weights = weights / weights.sum()
    paths.setflags(write=False)
    weights.setflags(write=False)
    fingerprint = hashlib.sha256()
    fingerprint.update(paths.tobytes())
    fingerprint.update(weights.tobytes())
    metadata.update({"bank_id": fingerprint.hexdigest()[:24],
                     "path_count": len(paths), "step_count": paths.shape[1] - 1,
                     "horizon_minutes": inputs.horizon_minutes,
                     "captured_ts": _number(snapshot.get("captured_ts")),
                     "weights_preserved": True,
                     "effective_path_count": float(1. / np.sum(weights ** 2)),
                     "execution_assumption": "piecewise_linear_barrier_fill_no_slippage",
                     "changes_original_admission": False})
    return paths, weights, groups, metadata


def _weighted_cvar(values, weights, alpha=.10):
    """Mean of the worst alpha probability mass, including partial boundary."""
    order = np.argsort(values, kind="stable")
    w = weights[order]
    mass = np.minimum(w, np.maximum(alpha - (np.cumsum(w) - w), 0.))
    return float(np.sum(values[order] * mass) / alpha)


def _variant(base, row, snapshot, inputs):
    policy, params = row.get("policy"), row.get("parameters") or {}
    if policy in BASE_FRACTIONS:
        return base
    if policy == "TIME_STOP":
        captured, deadline = _number(snapshot.get("captured_ts")), _number(params.get("deadline_ts"))
        if captured is None or deadline is None:
            raise ValueError("TIME_STOP_DEADLINE_UNAVAILABLE")
        fraction = (deadline - captured) / (60. * inputs.horizon_minutes)
        if not 0 < fraction <= 1:
            raise ValueError("INVALID_TIME_STOP_DEADLINE")
        return replace(base, time_stop_fraction=fraction)
    geometry = snapshot.get("trade_geometry") or {}
    entry, original_stop = _number(geometry.get("entry")), _number(geometry.get("original_stop"))
    if entry is None or original_stop is None or entry == original_stop:
        raise ValueError("TRADE_GEOMETRY_UNAVAILABLE")
    sign = 1. if original_stop < entry else -1.
    risk = abs(entry - original_stop)
    if policy in STOP_POLICIES:
        target = _number(params.get("stop_price"))
        if target is None:
            raise ValueError("EXACT_STOP_PRICE_UNAVAILABLE")
        target_r = sign * (target - entry) / risk
        if not inputs.stop_r + 1e-8 < target_r < inputs.r0 - 1e-8:
            raise ValueError("STOP_MUST_TIGHTEN_BELOW_CURRENT_PRICE")
        return replace(base, stop_r=target_r)
    if policy in TAKE_POLICIES:
        target = _number(params.get("take_price"))
        if target is None:
            raise ValueError("EXACT_TAKE_PRICE_UNAVAILABLE")
        target_r = sign * (target - entry) / risk
        if target_r <= max(inputs.r0, inputs.max_r) + 1e-8:
            raise ValueError("TAKE_ALREADY_CROSSED")
        if policy == "EXTEND_TAKE" and target_r <= inputs.T + 1e-8:
            raise ValueError("TAKE_NOT_EXTENDED")
        if policy == "REDUCE_TAKE" and target_r >= inputs.T - 1e-8:
            raise ValueError("TAKE_NOT_REDUCED")
        return replace(base, take_r=target_r)
    if policy == "SCALE_OUT_ON_SPIKE":
        trigger, fraction = _number(params.get("trigger_price")), _number(params.get("close_fraction"))
        if trigger is None or fraction is None or not 0 < fraction < 1:
            raise ValueError("INVALID_SPIKE_PARAMETERS")
        target_r = sign * (trigger - entry) / risk
        if not max(inputs.r0, inputs.max_r) < target_r < inputs.T:
            raise ValueError("SPIKE_TRIGGER_ALREADY_CROSSED_OR_OUTSIDE_TAKE")
        if any(abs(target_r - rung) < .05 for rung in inputs.rungs):
            raise ValueError("SPIKE_DUPLICATES_STRATEGY_RUNG")
        return replace(base, spike_r=target_r, spike_fraction=fraction)
    raise ValueError("UNSUPPORTED_CANDIDATE_POLICY")


def price_unified_candidates(snapshot: dict, candidates: list[dict]) -> dict:
    """Return candidate-ID keyed comparable net economics, without admission.

    Original source/risk/confirmation gates remain compulsory. A computed
    shared-bank CVaR pass is only additional evidence, never permission to
    restore a candidate that failed its original gate.
    """
    manager = snapshot.get("policy_manager") or {}
    data = manager.get("inputs") or {}
    if data.get("option_available") is not True:
        return _unavailable("OPTION_PATH_DISTRIBUTION_UNAVAILABLE")
    try:
        inputs = PolicyInputs(**{**data, "rungs": tuple(data["rungs"])})
    except (TypeError, ValueError, KeyError):
        return _unavailable("POLICY_INPUTS_INCOMPLETE")
    numerics = (inputs.r0, inputs.T, inputs.sigma_R, inputs.drift_R,
                inputs.skew_R, inputs.term_slope, inputs.horizon_minutes,
                inputs.max_r, inputs.rung_fraction, inputs.be_after, inputs.stop_r,
                *inputs.rungs)
    if any(_number(x) is None or not isinstance(x, (int, float, np.integer, np.floating))
           for x in numerics) or not (
            inputs.sigma_R > 0 and inputs.horizon_minutes > 0
            and inputs.stop_r < inputs.r0 < inputs.T and 0 <= inputs.rung_fraction <= 1):
        return _unavailable("INVALID_POLICY_INPUTS")
    geometry = snapshot.get("trade_geometry") or {}
    entry, stop, current = (_number(geometry.get("entry")),
                            _number(geometry.get("original_stop")),
                            _number(geometry.get("current")))
    if None not in (entry, stop, current) and entry != stop:
        geometric_r = (current - entry) / (entry - stop)
        # Serialized PolicyInputs uses four decimal places.
        if abs(geometric_r - inputs.r0) > 1e-4:
            return _unavailable("FROZEN_INPUTS_TRADE_GEOMETRY_MISMATCH")
    rule = manager.get("selection_rule") or {}
    costs = manager.get("execution_cost_model") or rule.get("execution_cost_model") or {}
    immediate, deferred = (_number(costs.get("immediate_full_close_r")),
                           _number(costs.get("deferred_full_close_r")))
    if immediate is None or deferred is None or min(immediate, deferred) < 0:
        return _unavailable("EXECUTION_COST_MODEL_UNAVAILABLE")
    try:
        paths, weights, groups, bank = _bank(snapshot, inputs)
    except (ValueError, TypeError, OverflowError) as exc:
        return _unavailable(str(exc))
    base = ExecutionSpec.from_values(current_r=inputs.r0, max_r=inputs.max_r,
        take_r=inputs.T, rungs=inputs.rungs, rung_fraction_original=inputs.rung_fraction,
        be_after_r=inputs.be_after, stop_r=inputs.stop_r)
    # Identical execution geometry is replayed once even if several policies
    # express it (for example MOVE_TO_BE and TIGHTEN_STOP at the same price).
    cached = {}
    def replay(spec):
        if spec not in cached:
            cached[spec] = np.asarray([replay_execution_path(path, spec).outcome_r
                                       for path in paths], dtype=float)
        return cached[spec]
    hold_gross = replay(base)
    hold_net = hold_gross - deferred
    hold_e, hold_c = float(np.sum(weights * hold_net)), _weighted_cvar(hold_net, weights)
    floor = _number((manager.get("risk_constraint") or {}).get("net_cvar_floor_r"))
    if floor is None:
        floor = _number(rule.get("cvar_floor_r"))
    result = {}
    for row in candidates:
        identity = str(row.get("candidate_id") or row.get("policy") or "")
        try:
            spec = _variant(base, row, snapshot, inputs)
        except (ValueError, TypeError, OverflowError) as exc:
            result[identity] = {"available": False, "reason": str(exc)}
            continue
        policy = row.get("policy")
        fraction = BASE_FRACTIONS.get(policy, 0.)
        gross = fraction * inputs.r0 + (1. - fraction) * replay(spec)
        cost = fraction * immediate + (1. - fraction) * deferred
        net = gross - cost
        expected, cvar = float(np.sum(weights * net)), _weighted_cvar(net, weights)
        paired = net - hold_net
        delta = float(np.sum(weights * paired))
        effective_n = bank["effective_path_count"]
        variance = float(np.sum(weights * (paired - delta) ** 2))
        se = math.sqrt(variance / max(effective_n - 1., 1.))
        group_cvars, group_lowers = [], []
        for group in np.unique(groups):
            mask = groups == group
            if weights[mask].sum() <= 0:
                continue
            group_weights = weights[mask] / weights[mask].sum()
            group_delta = float(np.sum(group_weights * paired[mask]))
            group_n = 1. / np.sum(group_weights ** 2)
            group_se = math.sqrt(float(np.sum(group_weights * (paired[mask] - group_delta) ** 2))
                                 / max(group_n - 1., 1.))
            group_lowers.append(group_delta - 1.96 * group_se)
            group_cvars.append(_weighted_cvar(net[mask], group_weights))
        worst_cvar = min(group_cvars) if group_cvars else cvar
        result[identity] = {
            "available": True, "reason": "COMMON_FROZEN_SCENARIO_NET_ECONOMICS",
            "expected_net_r": expected, "cvar10_net_r": cvar,
            "expected_gross_r": float(np.sum(weights * gross)),
            "delta_expected_r": delta, "delta_cvar_r": cvar - hold_c,
            "expected_hold_net_r": hold_e, "hold_cvar10_net_r": hold_c,
            "execution_cost_r": cost, "cost_source": costs.get("source") or "UNAVAILABLE",
            "cost_assumed": costs.get("assumed") is not False,
            "paired_delta_ci95_lower_r": min(delta - 1.96 * se, *group_lowers),
            "ci_semantics": "model_monte_carlo_approximation_not_validated_market_edge",
            "worst_seed_cvar10_net_r": worst_cvar,
            "hard_risk_pass": worst_cvar >= floor - 1e-8 if floor is not None else None,
            "hard_net_floor_r": floor,
            "original_candidate_eligible": row.get("eligible") is True,
            "economics_basis": "common_frozen_weighted_option_driver_paths",
            "bank_id": bank["bank_id"], "path_count": bank["path_count"],
            "horizon_minutes": inputs.horizon_minutes,
        }
    return {"version": VERSION, "available": True,
            "reason": "COMMON_FROZEN_SCENARIO_NET_ECONOMICS", "candidates": result,
            "shared_scenario_bank": True, "method": "single_bank_execution_simulator_replay",
            "authoritative_bank_reused": False,
            "all_candidates_priced": all(row.get("available") for row in result.values()),
            "priced_candidate_ids": [key for key, row in result.items() if row.get("available")],
            "unpriced_candidate_ids": [key for key, row in result.items() if not row.get("available")],
            "bank": bank, "unique_execution_replays": len(cached),
            "changes_original_admission": False,
            "statistically_validated_advantage": False}
