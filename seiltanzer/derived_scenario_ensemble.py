"""Scenario-weighted policy robustness driven by observed derivative state.

This module does not promote its candidate.  It evaluates how the existing net
policy distributions behave under the already-calibrated local stress scales
used by policy v5 (drift/skew) and authority stability (volatility).
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Callable


SCENARIO_ORDER = (
    "BASE",
    "EDGE_CONTINUATION",
    "EDGE_MEAN_REVERSION",
    "VOL_EXPANSION",
    "SKEW_ADVERSE",
    "GAMMA_STRESS",
    "CORRELATION_STRESS",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _metric_z(state: dict, name: str, *, acceleration: bool = False) -> float | None:
    metric = (state.get("metrics") or {}).get(name) or {}
    key = "acceleration" if acceleration else "slope"
    value = _number(metric.get(key))
    if not metric.get("available") or value is None:
        return None
    span = max(_number(metric.get("time_span_minutes")) or 0.0, 1.0)
    noise = max(_number(metric.get("noise")) or 0.0, 1e-9)
    scaled = value * (span * span if acceleration else span) / noise
    return math.tanh(scaled)


def derivative_drivers(tick: dict) -> dict:
    state = tick.get("option_derivative_state") or {}
    interaction = tick.get("interaction_state") or {}
    confidence = _clip(_number(state.get("option_state_confidence")) or 0.0)
    ev_z = _metric_z(state, "barrier_ev")
    ev_acc_z = _metric_z(state, "barrier_ev", acceleration=True)
    width_z = _metric_z(state, "width")
    iv_z = _metric_z(state, "iv")
    rv_z = _metric_z(state, "rv")
    skew_z = _metric_z(state, "skew")

    if ev_z is None:
        continuation = mean_reversion = 0.0
    elif ev_acc_z is None:
        continuation, mean_reversion = abs(ev_z), 0.0
    else:
        alignment = ev_z * ev_acc_z
        continuation = abs(ev_z) * max(0.0, alignment)
        mean_reversion = abs(ev_z) * max(0.0, -alignment)

    interaction_scores = {
        row.get("name"): max(0.0, _number(row.get("score")) or 0.0)
        * _clip(_number(row.get("source_quality")) or 0.0)
        for row in interaction.get("items") or [] if isinstance(row, dict)
    }
    gamma = interaction_scores.get("gex_stiffness_x_live_price_impulse", 0.0)

    cross = ((tick.get("analytics") or {}).get("cross_asset") or {})
    observed = max(int(cross.get("observed_pairs") or 0), 1)
    correlation = max(
        _clip(_number(cross.get("network_tension")) or 0.0),
        _clip(_number(cross.get("fragmentation")) or 0.0),
        math.tanh(abs(_number(cross.get("max_break_velocity")) or 0.0)),
        _clip((int(cross.get("active_breaks_count") or 0)) / observed),
    )

    direction = 1.0 if (ev_z or 0.0) >= 0.0 else -1.0
    raw = {
        "BASE": 1.0,
        "EDGE_CONTINUATION": confidence * continuation,
        "EDGE_MEAN_REVERSION": confidence * mean_reversion,
        "VOL_EXPANSION": confidence * max(0.0, width_z or 0.0, iv_z or 0.0, rv_z or 0.0),
        "SKEW_ADVERSE": confidence * max(0.0, skew_z or 0.0),
        "GAMMA_STRESS": gamma,
        "CORRELATION_STRESS": correlation,
    }
    total = sum(raw.values()) or 1.0
    metric_scales = {}
    for name in ("barrier_ev", "width", "iv", "rv", "skew"):
        metric = (state.get("metrics") or {}).get(name) or {}
        metric_scales[name] = {
            "slope": _number(metric.get("slope")),
            "noise": _number(metric.get("noise")),
            "time_span_minutes": _number(metric.get("time_span_minutes")),
            "confidence": _number(metric.get("confidence")),
        }
    vol_signals = {
        "width": max(0.0, width_z or 0.0),
        "iv": max(0.0, iv_z or 0.0),
        "rv": max(0.0, rv_z or 0.0),
    }
    return {
        "raw_weights": raw,
        "weights": {name: raw[name] / total for name in SCENARIO_ORDER},
        "edge_direction": direction,
        "signals": {
            "edge_velocity_z": ev_z,
            "edge_acceleration_z": ev_acc_z,
            "width_velocity_z": width_z,
            "iv_velocity_z": iv_z,
            "rv_velocity_z": rv_z,
            "skew_velocity_z": skew_z,
            "gamma_stress": gamma,
            "correlation_stress": correlation,
            "option_state_confidence": confidence,
        },
        "derived_weight": sum(raw.values()) - raw["BASE"],
        "metric_scales": metric_scales,
        "vol_driver_metric": max(vol_signals, key=vol_signals.get),
        "edge_continuation_factor": (
            confidence if ev_acc_z is None else confidence * max(0.0, ev_z * ev_acc_z)
        ),
        "edge_mean_reversion_factor": (
            0.0 if ev_z is None or ev_acc_z is None
            else confidence * max(0.0, -(ev_z * ev_acc_z))
        ),
    }


def _scenario_inputs(inputs, drivers: dict) -> dict:
    sign = float(drivers.get("edge_direction") or 1.0)
    # Scales are inherited from existing policy robustness contracts:
    # drift ±0.04R, skew ±0.05, sigma ±15%.
    return {
        "BASE": inputs,
        "EDGE_CONTINUATION": replace(inputs, drift_R=inputs.drift_R + sign * 0.04),
        "EDGE_MEAN_REVERSION": replace(inputs, drift_R=inputs.drift_R - sign * 0.04),
        "VOL_EXPANSION": replace(inputs, sigma_R=max(0.08, inputs.sigma_R * 1.15)),
        "SKEW_ADVERSE": replace(
            inputs, skew_R=min(max(inputs.skew_R + 0.05, -0.45), 0.45)),
        "GAMMA_STRESS": replace(
            inputs, sigma_R=max(0.08, inputs.sigma_R * 1.15),
            drift_R=inputs.drift_R - 0.04),
        "CORRELATION_STRESS": replace(
            inputs, sigma_R=max(0.08, inputs.sigma_R * 1.15),
            drift_R=inputs.drift_R - 0.04),
    }


def _normalise_weights(raw: dict) -> dict:
    total = sum(max(0.0, float(raw.get(name) or 0.0)) for name in SCENARIO_ORDER) or 1.0
    return {name: max(0.0, float(raw.get(name) or 0.0)) / total for name in SCENARIO_ORDER}


def _choose_candidate(policy_rows: dict, scenario_rows: list[dict], weights: dict,
                      fractions: dict, old_policy: str, derived_weight: float) -> str:
    if derived_weight <= 1e-12:
        return old_policy
    base = next(row for row in scenario_rows if row["name"] == "BASE")
    floor = float(base["cvar_floor_r"])
    eligible = []
    for name, metrics in policy_rows.items():
        base_cvar = float(base["policies"][name]["cvar10_r"])
        material = [row for row in scenario_rows if weights[row["name"]] > 1e-12]
        survives = all(float(row["policies"][name]["cvar10_r"]) >= float(row["cvar_floor_r"]) - 1e-12
                       for row in material)
        if base_cvar >= floor - 1e-12 and survives:
            eligible.append((name, metrics))
    if not eligible:
        # No hard-feasible policy across all material stresses: choose the best
        # worst-stress CVaR for diagnosis, never for automatic promotion.
        return max(policy_rows, key=lambda name: float(policy_rows[name]["worst_stress_cvar_r"]))
    best_expected = max(float(row["expected_net_r"]) for _, row in eligible)
    near = [(name, row) for name, row in eligible
            if best_expected - float(row["expected_net_r"]) <= 0.03 + 1e-12]
    return min(near, key=lambda item: fractions[item[0]])[0]


def _aggregate_policy_rows(scenario_rows: list[dict], weights: dict,
                           policy_fractions: dict, source_shares: dict | None = None) -> dict:
    winners = {name: 0.0 for name in policy_fractions}
    for row in scenario_rows:
        winners[row["winner"]] += weights[row["name"]]
    output = {}
    for policy in policy_fractions:
        rows = [(weights[row["name"]], row["policies"][policy]) for row in scenario_rows]
        material = [row for row in scenario_rows if weights[row["name"]] > 1e-12]
        output[policy] = {
            "expected_net_r": round(sum(
                weight * float(metric["expected_final_r"]) for weight, metric in rows), 4),
            "median_net_r": round(sum(
                weight * float(metric["median_final_r"]) for weight, metric in rows), 4),
            "cvar10_net_r": round(sum(
                weight * float(metric["cvar10_r"]) for weight, metric in rows), 4),
            "p_loss": round(sum(
                weight * float(metric["p_final_loss"]) for weight, metric in rows), 4),
            "worst_stress_r": round(min(
                float(row["policies"][policy]["expected_final_r"])
                for row in material), 4),
            "worst_stress_cvar_r": round(min(
                float(row["policies"][policy]["cvar10_r"])
                for row in material), 4),
            "stress_survival": round(sum(
                weights[row["name"]]
                for row in material
                if float(row["policies"][policy]["cvar10_r"])
                >= float(row["cvar_floor_r"]) - 1e-12), 4),
            "policy_stability": round(winners[policy], 4),
            "source_stability": _number((source_shares or {}).get(policy)),
        }
    return output


def evaluate_derived_scenarios(
    *,
    inputs,
    tick: dict,
    old_policy: str,
    run_once: Callable,
    raw_policy_choice: Callable,
    floor_for_r: Callable,
    policy_fractions: dict,
    source_stability: dict | None = None,
    n_paths: int = 900,
    n_steps: int = 160,
) -> dict:
    drivers = derivative_drivers(tick)
    weights = drivers["weights"]
    scenarios = _scenario_inputs(inputs, drivers)
    scenario_rows = []
    for index, name in enumerate(SCENARIO_ORDER):
        scenario = scenarios[name]
        metrics, _ = run_once(
            scenario, n_paths=n_paths, n_steps=n_steps, seed=0xE500 + index)
        floor = floor_for_r(scenario.r0)
        choice, rule = raw_policy_choice(metrics, scenario.r0, cvar_floor=floor)
        compact_metrics = {
            policy: {
                "expected_final_r": row.get("expected_final_r"),
                "median_final_r": row.get("median_final_r"),
                "cvar10_r": row.get("cvar10_r"),
                "p_final_loss": row.get("p_final_loss"),
            }
            for policy, row in metrics.items()
        }
        scenario_rows.append({
            "name": name,
            "source_family": (
                "correlation" if name == "CORRELATION_STRESS"
                else "option_distribution"),
            "weight": round(weights[name], 7),
            "raw_weight": round(float(drivers["raw_weights"][name]), 7),
            "winner": choice,
            "cvar_floor_r": rule.get("cvar_floor_r"),
            "eligible": list(rule.get("eligible") or []),
            "inputs": {
                "drift_R": round(float(scenario.drift_R), 5),
                "sigma_R": round(float(scenario.sigma_R), 5),
                "skew_R": round(float(scenario.skew_R), 5),
                "term_slope": round(float(scenario.term_slope), 5),
            },
            "policies": compact_metrics,
        })

    source_shares = (source_stability or {}).get("winner_shares") or {}
    policy_rows = _aggregate_policy_rows(
        scenario_rows, weights, policy_fractions, source_shares)

    candidate = _choose_candidate(
        policy_rows, scenario_rows, weights, policy_fractions, old_policy,
        float(drivers["derived_weight"]),
    )
    return {
        "version": "derived-scenario-ensemble-v1",
        "family": "option_distribution",
        "independent_vote": False,
        "authority": "shadow_robustness",
        "shadow_mode": True,
        "promotion_allowed": False,
        "calibration": {
            "drift_stress_r": 0.04,
            "skew_stress": 0.05,
            "sigma_stress_fraction": 0.15,
            "source": "existing policy-v5 and authority-stability perturbation contracts",
        },
        "drivers": drivers,
        "scenarios": scenario_rows,
        "policies": policy_rows,
        "candidate_policy": candidate,
        "old_policy": old_policy,
        "candidate_differs": candidate != old_policy,
        "selection_rule": (
            "net hard-CVaR feasible in BASE and every material weighted stress; "
            "then maximum weighted Expected within the existing 0.03R indifference band"
        ),
    }


def calibrated_switch_thresholds(ensemble: dict, policy_fractions: dict) -> list[dict]:
    """Reweight already-simulated stresses; no LLM or invented raw threshold."""
    scenarios = ensemble.get("scenarios") or []
    if not scenarios:
        return []
    old = str(ensemble.get("old_policy") or "HOLD")
    base_raw = dict((ensemble.get("drivers") or {}).get("raw_weights") or {})
    mapping = {
        "edge_continuation": "EDGE_CONTINUATION",
        "edge_mean_reversion": "EDGE_MEAN_REVERSION",
        "vol_expansion": "VOL_EXPANSION",
        "skew_adverse": "SKEW_ADVERSE",
        "gamma_stress": "GAMMA_STRESS",
        "correlation_stress": "CORRELATION_STRESS",
    }
    output = []
    drivers = ensemble.get("drivers") or {}
    scales = drivers.get("metric_scales") or {}

    def raw_equivalent(driver: str, level: float) -> dict:
        metric_name = None
        factor = _number((drivers.get("signals") or {}).get("option_state_confidence")) or 0.0
        direction = 1.0
        assumption = "all other observed scenario weights held fixed"
        if driver == "skew_adverse":
            metric_name = "skew"
        elif driver == "vol_expansion":
            metric_name = drivers.get("vol_driver_metric") or "width"
        elif driver == "edge_continuation":
            metric_name = "barrier_ev"
            factor = _number(drivers.get("edge_continuation_factor")) or 0.0
            direction = 1.0 if (_number((drivers.get("signals") or {}).get(
                "edge_velocity_z")) or 0.0) >= 0.0 else -1.0
            assumption += "; current acceleration alignment held fixed"
        elif driver == "edge_mean_reversion":
            metric_name = "barrier_ev"
            factor = _number(drivers.get("edge_mean_reversion_factor")) or 0.0
            direction = -1.0 if (_number((drivers.get("signals") or {}).get(
                "edge_velocity_z")) or 0.0) >= 0.0 else 1.0
            assumption += "; current opposing acceleration alignment held fixed"
        scale = scales.get(metric_name) or {}
        noise = _number(scale.get("noise"))
        span = _number(scale.get("time_span_minutes"))
        required_z = level / factor if factor > 0 else None
        if (required_z is None or required_z >= 1.0 or noise is None
                or span is None or span <= 0):
            return {"metric": metric_name, "raw_slope_threshold_per_minute": None,
                    "operator": None, "assumption": assumption}
        slope = direction * math.atanh(required_z) * noise / span
        return {
            "metric": metric_name,
            "raw_slope_threshold_per_minute": round(slope, 8),
            "operator": ">=" if direction > 0 else "<=",
            "assumption": assumption,
        }

    for driver, scenario_name in mapping.items():
        threshold = candidate = None
        for step in range(1, 20):
            level = step / 20.0
            raw = dict(base_raw)
            raw[scenario_name] = level
            weights = _normalise_weights(raw)
            policy_rows = _aggregate_policy_rows(
                scenarios, weights, policy_fractions)
            candidate = _choose_candidate(
                policy_rows, scenarios, weights, policy_fractions, old, 1.0)
            if candidate != old:
                threshold = level
                break
        if threshold is not None:
            output.append({
                "driver": driver,
                "bounded_weight_threshold": threshold,
                "candidate_policy": candidate,
                "derivation": "minimum 0.05 grid crossing from deterministic stress reweighting",
                "llm_generated": False,
                **raw_equivalent(driver, threshold),
            })
    return output
