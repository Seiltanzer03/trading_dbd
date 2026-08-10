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

# Materiality is a hard-feasibility contract, not another score.  The weight
# floor equals the existing 0.05 deterministic sensitivity-grid resolution;
# smaller weights cannot be distinguished by that analysis and therefore must
# not veto a policy.  Confidence 0.30 is the existing derived-history authority
# threshold.  Quality 0.48 is the lowest option-anchored snapshot weight in the
# source-quality contract; scenario-only (0.25) stays diagnostic.
MATERIAL_WEIGHT_MIN = 0.05
MATERIAL_CONFIDENCE_MIN = 0.30
MATERIAL_SOURCE_QUALITY_MIN = 0.48
MATERIAL_SAMPLE_SPAN_MINUTES = 5.0


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

    interaction_rows = {
        row.get("name"): row for row in interaction.get("items") or []
        if isinstance(row, dict)
    }
    gamma_row = interaction_rows.get("gex_stiffness_x_live_price_impulse") or {}
    gamma_quality = _clip(_number(gamma_row.get("source_quality")) or 0.0)
    gamma = max(0.0, _number(gamma_row.get("score")) or 0.0) * gamma_quality

    cross = ((tick.get("analytics") or {}).get("cross_asset") or {})
    observed = max(int(cross.get("observed_pairs") or 0), 1)
    correlation = max(
        _clip(_number(cross.get("network_tension")) or 0.0),
        _clip(_number(cross.get("fragmentation")) or 0.0),
        math.tanh(abs(_number(cross.get("max_break_velocity")) or 0.0)),
        _clip((int(cross.get("active_breaks_count") or 0)) / observed),
    )

    def metric_meta(name: str) -> dict:
        metric = (state.get("metrics") or {}).get(name) or {}
        return {
            "metric": name,
            "available": bool(metric.get("available")),
            "driver_confidence": _clip(_number(metric.get("confidence")) or 0.0),
            "source_quality": _clip(_number(metric.get("source_quality")) or 0.0),
            "sample_span_minutes": max(
                0.0, _number(metric.get("time_span_minutes")) or 0.0),
        }

    vol_candidates = {
        "width": max(0.0, width_z or 0.0),
        "iv": max(0.0, iv_z or 0.0),
        "rv": max(0.0, rv_z or 0.0),
    }
    vol_metric = max(vol_candidates, key=vol_candidates.get)
    option_quality = _clip(_number((state.get("source_quality") or {}).get(
        "weight")) or 0.0)
    r_meta = metric_meta("r")
    gex_meta = metric_meta("gex_stiffness")
    gamma_meta = {
        "metric": "gex_stiffness_x_live_price_impulse",
        "available": bool(gamma_row.get("available", gamma_row))
        and r_meta["available"] and gamma > 0.0,
        "driver_confidence": min(
            r_meta["driver_confidence"],
            gex_meta["driver_confidence"] if gex_meta["available"] else r_meta["driver_confidence"],
        ),
        "source_quality": gamma_quality,
        "sample_span_minutes": r_meta["sample_span_minutes"],
    }
    cross_source = cross.get("source") or {}
    cross_status = str(cross_source.get("status") or "no_data")
    cross_quality = {
        "live": 0.85, "ok": 0.62, "delayed": 0.62,
        "indicative": 0.62,
    }.get(cross_status, 0.0)
    cross_samples = int(cross.get("history_samples") or 0)
    cross_span = max(0.0, _number(cross.get("history_span_minutes")) or 0.0)
    cross_confidence = min(1.0, max(0.0, (cross_samples - 1) / 5.0))
    if not cross.get("velocity_ready"):
        cross_confidence = 0.0

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
    edge_meta = metric_meta("barrier_ev")
    skew_meta = metric_meta("skew")
    vol_meta = metric_meta(vol_metric)
    for meta in (edge_meta, skew_meta, vol_meta):
        # Preserve the separately auditable source value but use the canonical
        # option-source contract when older persisted metrics lack the field.
        if meta["source_quality"] <= 0.0:
            meta["source_quality"] = option_quality
    driver_metadata = {
        "BASE": {
            "metric": "authoritative_base", "available": True,
            "driver_confidence": 1.0, "source_quality": 1.0,
            "sample_span_minutes": 0.0,
        },
        "EDGE_CONTINUATION": dict(edge_meta),
        "EDGE_MEAN_REVERSION": dict(edge_meta),
        "VOL_EXPANSION": dict(vol_meta),
        "SKEW_ADVERSE": dict(skew_meta),
        "GAMMA_STRESS": gamma_meta,
        "CORRELATION_STRESS": {
            "metric": "cross_asset_regime_instability",
            "available": bool(cross.get("available", cross)) and correlation > 0.0,
            "driver_confidence": cross_confidence,
            "source_quality": cross_quality,
            "sample_span_minutes": cross_span,
        },
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
        "driver_metadata": driver_metadata,
        "metric_scales": metric_scales,
        "vol_driver_metric": vol_metric,
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
    gamma_magnitude = _clip(_number((drivers.get("signals") or {}).get(
        "gamma_stress")) or 0.0)
    correlation_magnitude = _clip(_number((drivers.get("signals") or {}).get(
        "correlation_stress")) or 0.0)
    return {
        "BASE": inputs,
        "EDGE_CONTINUATION": replace(inputs, drift_R=inputs.drift_R + sign * 0.04),
        "EDGE_MEAN_REVERSION": replace(inputs, drift_R=inputs.drift_R - sign * 0.04),
        "VOL_EXPANSION": replace(inputs, sigma_R=max(0.08, inputs.sigma_R * 1.15)),
        "SKEW_ADVERSE": replace(
            inputs, skew_R=min(max(inputs.skew_R + 0.05, -0.45), 0.45)),
        "GAMMA_STRESS": replace(
            inputs,
            # OI×gamma reveals concentration/curvature, not dealer direction.
            # It may widen local variance but never invents adverse drift.
            sigma_R=max(0.08, inputs.sigma_R * (1.0 + 0.15 * gamma_magnitude))),
        "CORRELATION_STRESS": replace(
            inputs,
            # Regime breakdown raises broad uncertainty and shrinks directional
            # confidence toward zero; it does not prove adverse direction.
            sigma_R=max(0.08, inputs.sigma_R * (1.0 + 0.20 * correlation_magnitude)),
            drift_R=inputs.drift_R * (1.0 - 0.50 * correlation_magnitude)),
    }


def scenario_materiality(name: str, weight: float, metadata: dict | None) -> dict:
    """Deterministic hard-veto eligibility with a fully published reason."""
    metadata = metadata or {}
    failures = []
    if name == "BASE":
        return {"material": True, "materiality_reason": "BASE is always required"}
    if weight < MATERIAL_WEIGHT_MIN - 1e-12:
        failures.append("weight below 0.05 sensitivity-grid resolution")
    if not metadata.get("available"):
        failures.append("driver unavailable")
    if float(metadata.get("driver_confidence") or 0.0) < MATERIAL_CONFIDENCE_MIN:
        failures.append("driver confidence below existing 0.30 threshold")
    if float(metadata.get("source_quality") or 0.0) < MATERIAL_SOURCE_QUALITY_MIN:
        failures.append("source quality below option-anchored snapshot floor 0.48")
    if float(metadata.get("sample_span_minutes") or 0.0) < MATERIAL_SAMPLE_SPAN_MINUTES:
        failures.append("sample span below derivative minimum 5 minutes")
    return {
        "material": not failures,
        "materiality_reason": (
            "meets published weight/confidence/quality/span contract"
            if not failures else "; ".join(failures)
        ),
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
        material = [row for row in scenario_rows if scenario_materiality(
            row["name"], weights[row["name"]], row.get("driver_metadata"))["material"]]
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
        material = [row for row in scenario_rows if scenario_materiality(
            row["name"], weights[row["name"]], row.get("driver_metadata"))["material"]]
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
        metadata = (drivers.get("driver_metadata") or {}).get(name) or {}
        materiality = scenario_materiality(name, weights[name], metadata)
        scenario_rows.append({
            "name": name,
            "source_family": (
                "correlation" if name == "CORRELATION_STRESS"
                else "option_distribution"),
            "weight": round(weights[name], 7),
            "raw_weight": round(float(drivers["raw_weights"][name]), 7),
            "material": materiality["material"],
            "materiality_reason": materiality["materiality_reason"],
            "driver_confidence": round(float(metadata.get("driver_confidence") or 0.0), 3),
            "source_quality": round(float(metadata.get("source_quality") or 0.0), 3),
            "metric_available": bool(metadata.get("available")),
            "sample_span_minutes": (
                None if not math.isfinite(float(metadata.get("sample_span_minutes") or 0.0))
                else round(float(metadata.get("sample_span_minutes") or 0.0), 3)),
            "driver_metadata": metadata,
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
        "version": "derived-scenario-ensemble-v2-material-stress",
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
        "materiality_contract": {
            "weight_min": MATERIAL_WEIGHT_MIN,
            "driver_confidence_min": MATERIAL_CONFIDENCE_MIN,
            "source_quality_min": MATERIAL_SOURCE_QUALITY_MIN,
            "sample_span_minutes_min": MATERIAL_SAMPLE_SPAN_MINUTES,
            "hard_feasibility_scope": "BASE plus material stresses only",
            "non_material_role": "weighted diagnostics and attribution; never hard veto",
        },
        "drivers": drivers,
        "scenarios": scenario_rows,
        "policies": policy_rows,
        "candidate_policy": candidate,
        "old_policy": old_policy,
        "candidate_differs": candidate != old_policy,
        "selection_rule": (
            "net hard-CVaR feasible in BASE and every published material stress; "
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
