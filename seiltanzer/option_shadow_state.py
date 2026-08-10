"""Robust option-distribution derivatives and transparent shadow interactions.

The browser never supplies observations here.  State is derived from successive
server-side option/barrier snapshots and is deliberately non-authoritative until
out-of-sample shadow validation supports policy integration.
"""
from __future__ import annotations

import math
import threading
import time
from copy import deepcopy
from typing import Any, Callable

import numpy as np

from .lattice_revaluation import _source_quality

STATE_VERSION = 1
FAMILY = "option_distribution"
MIN_SAMPLES = 6
MIN_SPAN_MINUTES = 5.0
RECENT_LIMIT = 360
SAMPLE_INTERVAL_SEC = 30.0
PERSIST_INTERVAL_SEC = 10.0
NORMALIZATION_HORIZON_MINUTES = 20.0
NUMERICAL_EFFECT_FLOOR_FRACTION = 0.005


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _rounded(value: Any, digits: int = 7) -> float | None:
    value = _number(value)
    return None if value is None else round(value, digits)


def robust_derivative(
    observations: list[dict],
    field: str,
    *,
    source_quality: float = 1.0,
    min_samples: int = MIN_SAMPLES,
    min_span_minutes: float = MIN_SPAN_MINUTES,
    reference_ts: float | None = None,
    stale_after_minutes: float | None = None,
) -> dict:
    """Huber IRLS with exponential recency weights; never a two-point slope."""
    points = [
        (float(row["ts"]), float(row[field]))
        for row in observations
        if _number(row.get("ts")) is not None and _number(row.get(field)) is not None
    ]
    points.sort()
    # Collapse duplicate timestamps, which otherwise manufacture sample count.
    unique: dict[float, float] = {}
    for ts, value in points:
        unique[ts] = value
    points = sorted(unique.items())
    count = len(points)
    span_minutes = (points[-1][0] - points[0][0]) / 60.0 if count > 1 else 0.0
    current = points[-1][1] if points else None
    base = {
        "value": _rounded(current), "slope": None, "acceleration": None,
        "noise": None, "sample_count": count,
        "time_span_minutes": round(span_minutes, 3), "confidence": 0.0,
        "source_quality": round(_clip(source_quality, 0.0, 1.0), 3),
        "estimator": "ewls_huber_irls", "available": False,
    }
    if (reference_ts is not None and points and stale_after_minutes is not None):
        stale_minutes = max(0.0, (float(reference_ts) - points[-1][0]) / 60.0)
        base["staleness_minutes"] = round(stale_minutes, 3)
        if stale_minutes > float(stale_after_minutes):
            base["reason"] = f"last observation stale by {stale_minutes:.1f} minutes"
            return base
    if count < min_samples or span_minutes < min_span_minutes:
        base["reason"] = (
            f"requires >= {min_samples} unique observations over >= "
            f"{min_span_minutes:g} minutes")
        return base

    ts = np.asarray([p[0] for p in points], dtype=float)
    y = np.asarray([p[1] for p in points], dtype=float)
    x = (ts - ts[-1]) / 60.0
    half_life = max(10.0, span_minutes / 2.0)
    recency = np.exp(math.log(0.5) * (-x) / half_life)

    def fit(degree: int) -> tuple[np.ndarray, np.ndarray, float]:
        design = np.column_stack([x ** power for power in range(degree + 1)])
        weights = recency.copy()
        coef = np.zeros(degree + 1)
        residual = y.copy()
        scale = 0.0
        for _ in range(5):
            root_w = np.sqrt(np.maximum(weights, 1e-12))
            coef, *_ = np.linalg.lstsq(design * root_w[:, None], y * root_w, rcond=None)
            residual = y - design @ coef
            median = float(np.median(residual))
            scale = 1.4826 * float(np.median(np.abs(residual - median)))
            if scale <= 1e-12:
                break
            u = np.abs(residual) / (1.345 * scale)
            huber = np.ones_like(u)
            mask = u > 1.0
            huber[mask] = 1.0 / u[mask]
            weights = recency * huber
        return coef, residual, scale

    linear, _, noise = fit(1)
    slope = float(linear[1])
    acceleration = None
    if count >= max(8, min_samples) and span_minutes >= max(10.0, min_span_minutes):
        quadratic, _, _ = fit(2)
        acceleration = float(2.0 * quadratic[2])

    canonical_scale = max(1.0, float(np.median(np.abs(y))))
    numerical_floor = NUMERICAL_EFFECT_FLOOR_FRACTION * canonical_scale
    normalization_noise = max(noise, numerical_floor)
    effective_span = min(span_minutes, NORMALIZATION_HORIZON_MINUTES)
    signal_change = abs(slope) * effective_span
    snr = signal_change / normalization_noise
    sample_weight = min(1.0, (count - min_samples + 1) / 12.0)
    span_weight = min(1.0, span_minutes / 20.0)
    noise_weight = snr / (1.0 + snr)
    confidence = source_quality * math.sqrt(sample_weight * span_weight) * noise_weight
    base.update({
        "slope": _rounded(slope),
        "acceleration": _rounded(acceleration),
        "noise": _rounded(noise),
        "normalization_noise": _rounded(normalization_noise),
        "numerical_effect_floor": _rounded(numerical_floor),
        "normalization_horizon_minutes": NORMALIZATION_HORIZON_MINUTES,
        "confidence": round(_clip(confidence, 0.0, 1.0), 3),
        "available": True,
    })
    return base


def _observation(payload: dict) -> dict | None:
    trade = payload.get("trade") or {}
    market = payload.get("market") or {}
    cone = payload.get("cone") or {}
    if not trade or not market.get("available"):
        return None
    p_take = _number(market.get("p_take_horizon"))
    p_stop = _number(market.get("p_stop_horizon"))
    p_no_touch = _number(market.get("p_unresolved_horizon"))
    q10 = _number(market.get("scenario_p10_r"))
    q50 = _number(market.get("scenario_median_r"))
    q90 = _number(market.get("scenario_p90_r"))
    if None in (p_take, p_stop, p_no_touch, q10, q50, q90):
        return None
    eps = 1e-6
    up_tail = max(float(q90) - float(q50), eps)
    down_tail = max(float(q50) - float(q10), eps)
    hazard = ((cone.get("first_touch_hazard") or {}).get("next_window") or {})
    opts = payload.get("options_summary") or {}
    vrp = payload.get("vrp") or {}
    gamma = ((payload.get("gamma") or {}).get("field_geometry") or {})
    obs = {
        "ts": float(payload.get("ts") or time.time()),
        "price": _number(((payload.get("levels") or {}).get("price"))),
        "r": _number(((payload.get("prob") or {}).get("r"))),
        "p_take": p_take,
        "p_stop": p_stop,
        "p_no_touch": p_no_touch,
        "barrier_ev": _number(market.get("horizon_barrier_ev")),
        "bop": math.log((float(p_take) + eps) / (float(p_stop) + eps)),
        "q10": q10, "q50": q50, "q90": q90,
        "width": float(q90) - float(q10),
        "tail_ratio": up_tail / down_tail,
        "tail_log_ratio": math.log(up_tail / down_tail),
        # cone.skew is already transformed into favorable/adverse R coordinates;
        # increasing value means a thicker adverse (-R) tail.
        "skew": _number(cone.get("skew")),
        "term_slope": _number(cone.get("term_slope")),
        "iv": _number(vrp.get("iv")) or _number(opts.get("sigma_annual")),
        "rv": _number(vrp.get("rv")),
        "vrp": _number(vrp.get("vrp")),
        "h_take": _number(hazard.get("h_take")),
        "h_stop": _number(hazard.get("h_stop")),
        "hazard_log_ratio": _number(hazard.get("log_hazard_ratio")),
        "gex_field": _number(gamma.get("field")),
        "gex_force": _number(gamma.get("force_score")),
        "gex_stiffness": _number(gamma.get("stiffness_score")),
        "call_wall": _number(gamma.get("call_wall")),
        "put_wall": _number(gamma.get("put_wall")),
        "distance_to_zero_gamma": _number(gamma.get("distance_to_zero_gamma")),
        "distance_to_call_wall": _number(gamma.get("distance_to_call_wall_r")),
        "distance_to_put_wall": _number(gamma.get("distance_to_put_wall_r")),
    }
    return {key: value for key, value in obs.items() if value is not None}


_UNITS = {
    "p_take": "probability_per_minute", "p_stop": "probability_per_minute",
    "p_no_touch": "probability_per_minute", "barrier_ev": "R_per_minute",
    "bop": "log_odds_per_minute", "q10": "R_per_minute",
    "q50": "R_per_minute", "q90": "R_per_minute", "width": "R_per_minute",
    "tail_ratio": "ratio_per_minute", "tail_log_ratio": "log_ratio_per_minute",
    "skew": "skew_R_per_minute", "term_slope": "slope_per_minute",
    "iv": "annual_vol_per_minute", "rv": "annual_vol_per_minute",
    "vrp": "annual_vol_spread_per_minute", "h_take": "hazard_per_minute",
    "h_stop": "hazard_per_minute", "hazard_log_ratio": "log_ratio_per_minute",
    "r": "R_per_minute", "price": "price_units_per_minute",
    "gex_field": "normalized_field_per_minute", "gex_force": "score_per_minute",
    "gex_stiffness": "score_per_minute", "distance_to_zero_gamma": "R_per_minute",
    "call_wall": "price_units_per_minute", "put_wall": "price_units_per_minute",
    "distance_to_call_wall": "R_per_minute", "distance_to_put_wall": "R_per_minute",
}

_VALUE_UNITS = {
    "p_take": "probability", "p_stop": "probability",
    "p_no_touch": "probability", "barrier_ev": "R",
    "bop": "log_odds", "q10": "R", "q50": "R", "q90": "R",
    "width": "R", "tail_ratio": "ratio", "tail_log_ratio": "log_ratio",
    "skew": "skew_R", "term_slope": "term_structure_slope",
    "iv": "annualized_volatility", "rv": "annualized_volatility",
    "vrp": "annualized_volatility_spread",
    "h_take": "conditional_hazard_probability",
    "h_stop": "conditional_hazard_probability",
    "hazard_log_ratio": "log_hazard_ratio", "r": "R", "price": "price",
    "gex_field": "normalized_gex_field", "gex_force": "normalized_score",
    "gex_stiffness": "normalized_score", "distance_to_zero_gamma": "R",
    "call_wall": "price", "put_wall": "price",
    "distance_to_call_wall": "R", "distance_to_put_wall": "R",
}


def unit_contract(field: str) -> dict:
    value = _VALUE_UNITS.get(field, "units")
    return {
        "value_units": value,
        "slope_units": f"{value}/min",
        "acceleration_units": f"{value}/min^2",
        # Compatibility only: historically `units` described the slope while
        # being attached to the entire metric object.
        "units": _UNITS.get(field, "units_per_minute"),
        "units_compatibility": "deprecated_slope_units_alias",
    }


def standardized_derivative_signal(metric: dict | None, *, acceleration: bool = False) -> float | None:
    """Bound a derivative on a fixed decision horizon with a numerical floor.

    Longer history may improve estimator confidence, but cannot mechanically
    amplify the signal beyond the 20-minute management horizon.  Exact/near-zero
    residual noise is regularised by the published 0.5%-of-canonical-unit floor.
    """
    if not metric or not metric.get("available"):
        return None
    key = "acceleration" if acceleration else "slope"
    value = _number(metric.get(key))
    if value is None:
        return None
    span = min(
        max(_number(metric.get("time_span_minutes")) or 0.0, 0.0),
        _number(metric.get("normalization_horizon_minutes"))
        or NORMALIZATION_HORIZON_MINUTES,
    )
    noise = max(
        _number(metric.get("normalization_noise")) or 0.0,
        _number(metric.get("numerical_effect_floor")) or 0.0,
        1e-9,
    )
    effect = value * (span * span if acceleration else span)
    return math.tanh(effect / noise)


def _standardized_slope(metric: dict | None) -> float | None:
    return standardized_derivative_signal(metric)


def _interaction(name: str, formula: str, components: dict, z: float | None,
                 quality: float, explanation: str) -> dict:
    return {
        "name": name, "formula": formula, "normalization": "score=tanh(z)",
        "score": None if z is None else round(math.tanh(z), 6),
        "components": components, "source_quality": round(quality, 3),
        "available": z is not None, "explanation": explanation,
        "family": FAMILY, "independent_vote": False, "authority": "shadow_context",
    }


def build_interaction_state(payload: dict, metrics: dict, source_quality: float) -> dict:
    """Small, formula-visible interactions; all components stay in one family."""
    prob = payload.get("prob") or {}
    horizon = _number((payload.get("market") or {}).get("horizon_years"))
    ev_z = _standardized_slope(metrics.get("barrier_ev"))
    tail_z = _standardized_slope(metrics.get("tail_log_ratio"))
    price_z = _standardized_slope(metrics.get("r"))
    skew_z = _standardized_slope(metrics.get("skew"))
    vrp_z = _standardized_slope(metrics.get("vrp"))
    rv_acc = _number((metrics.get("rv") or {}).get("acceleration"))
    no_touch_z = _standardized_slope(metrics.get("p_no_touch"))
    h_stop = _number((metrics.get("h_stop") or {}).get("value"))
    stiffness = _number((metrics.get("gex_stiffness") or {}).get("value"))
    stop_distance = max(_number(prob.get("r")) + 1.0, 0.0) if _number(prob.get("r")) is not None else None

    items = []
    z = None if ev_z is None or h_stop is None else max(0.0, -ev_z) * h_stop
    items.append(_interaction(
        "ev_velocity_x_local_stop_hazard", "max(0,-z(dEV/dt))*h_stop(next_window)",
        {"ev_velocity_z": ev_z, "h_stop": h_stop}, z, source_quality,
        "Positive score means edge is deteriorating while immediate stop hazard is elevated."))

    z = None if tail_z is None or price_z is None else max(0.0, -tail_z) * max(0.0, -price_z)
    items.append(_interaction(
        "tail_expansion_x_adverse_price_velocity",
        "max(0,-z(dTailLogRatio/dt))*max(0,-z(dR/dt))",
        {"tail_geometry_z": tail_z, "price_velocity_z": price_z}, z, source_quality,
        "Positive score requires adverse-tail expansion and adverse price movement together."))

    proximity = None if stop_distance is None else math.tanh(1.0 / max(stop_distance, 1e-6))
    z = None if skew_z is None or proximity is None else max(0.0, skew_z) * proximity
    items.append(_interaction(
        "skew_deterioration_x_stop_proximity", "max(0,z(dSkew/dt))*tanh(1/d_stop_R)",
        {"skew_deterioration_z": skew_z, "stop_proximity": proximity}, z, source_quality,
        "Positive score means adverse skew is worsening while price is close to the stop."))

    z = (None if stiffness is None or price_z is None
         else abs(stiffness) * abs(price_z))
    items.append(_interaction(
        "gex_stiffness_x_live_price_impulse", "abs(stiffness_score)*abs(z(dR/dt))",
        {"gex_stiffness": stiffness, "price_impulse_z": price_z}, z,
        source_quality,
        "Magnitude-only instability context; OI×gamma does not prove dealer direction."))

    rv_metric = metrics.get("rv") or {}
    rv_acc_z = standardized_derivative_signal(rv_metric, acceleration=True)
    z = None if vrp_z is None or rv_acc_z is None else max(0.0, vrp_z) * max(0.0, rv_acc_z)
    items.append(_interaction(
        "vrp_expansion_x_realized_vol_acceleration",
        "max(0,z(dVRP/dt))*max(0,z(d2RV/dt2))",
        {"vrp_expansion_z": vrp_z, "rv_acceleration_z": rv_acc_z}, z,
        source_quality, "Positive score marks simultaneous repricing and realized-vol acceleration."))

    urgency = None if horizon is None else math.tanh(1.0 / max(horizon * 365.0 * 24.0, 1e-6))
    z = None if no_touch_z is None or urgency is None else max(0.0, -no_touch_z) * urgency
    items.append(_interaction(
        "no_touch_decay_x_remaining_horizon", "max(0,-z(dNoTouch/dt))*tanh(1/horizon_hours)",
        {"no_touch_decay_z": no_touch_z, "horizon_urgency": urgency}, z,
        source_quality, "Positive score means resolution probability is rising in a short horizon."))

    available = [item for item in items if item["available"]]
    return {
        "version": STATE_VERSION, "family": FAMILY, "independent_vote": False,
        "authority": "shadow_context", "shadow_mode": True,
        "items": items, "available_count": len(available),
        "max_risk_score": max((item["score"] for item in available), default=None),
    }


class OptionShadowTracker:
    def __init__(self, cache=None, *, sample_interval_sec: float = SAMPLE_INTERVAL_SEC):
        self.cache = cache
        self.sample_interval_sec = float(sample_interval_sec)
        self._histories: dict[int, list[dict]] = {}
        self._last_persist: dict[int, float] = {}
        self._lock = threading.RLock()

    def _cache_key(self, trade_id: int) -> str:
        return f"option_shadow_state:v{STATE_VERSION}:{trade_id}"

    def _history(self, trade_id: int) -> list[dict]:
        if trade_id not in self._histories:
            loaded = self.cache.get(self._cache_key(trade_id)) if self.cache else None
            rows = (loaded[0].get("observations") if loaded else None) or []
            self._histories[trade_id] = [row for row in rows if isinstance(row, dict)][-RECENT_LIMIT:]
        return self._histories[trade_id]

    def update(self, payload: dict) -> dict:
        with self._lock:
            return self._update_unlocked(payload)

    def _update_unlocked(self, payload: dict) -> dict:
        trade = payload.get("trade") or {}
        observation = _observation(payload)
        if not trade or observation is None:
            return {
                "available": False, "family": FAMILY, "independent_vote": False,
                "authority": "shadow_context", "shadow_mode": True,
                "reason": "active trade with an option-anchored distribution is required",
            }
        trade_id = int(trade["id"])
        history = self._history(trade_id)
        if not history or observation["ts"] - float(history[-1]["ts"]) >= self.sample_interval_sec:
            history.append(observation)
            del history[:-RECENT_LIMIT]
        quality = _source_quality(payload)
        quality_weight = float(quality.get("weight") or 0.0)
        fields = sorted({key for row in history for key in row if key != "ts"})
        metrics = {
            field: dict(
                robust_derivative(history, field, source_quality=quality_weight),
                **unit_contract(field),
            )
            for field in fields
        }
        current = history[-1]
        entry = history[0]
        averages = {
            field: round(float(np.mean([row[field] for row in history if field in row])), 7)
            for field in fields if any(field in row for row in history)
        }
        named = {
            "dP_take/dt": (metrics.get("p_take") or {}).get("slope"),
            "dP_stop/dt": (metrics.get("p_stop") or {}).get("slope"),
            "dP_no_touch/dt": (metrics.get("p_no_touch") or {}).get("slope"),
            "dBarrierEV/dt": (metrics.get("barrier_ev") or {}).get("slope"),
            "d2BarrierEV/dt2": (metrics.get("barrier_ev") or {}).get("acceleration"),
            "dBOP/dt": (metrics.get("bop") or {}).get("slope"),
            "dq10/dt": (metrics.get("q10") or {}).get("slope"),
            "dq50/dt": (metrics.get("q50") or {}).get("slope"),
            "dq90/dt": (metrics.get("q90") or {}).get("slope"),
            "dWidth/dt": (metrics.get("width") or {}).get("slope"),
            "dTailRatio/dt": (metrics.get("tail_ratio") or {}).get("slope"),
            "dSkew/dt": (metrics.get("skew") or {}).get("slope"),
            "dTermSlope/dt": (metrics.get("term_slope") or {}).get("slope"),
            "dIV/dt": (metrics.get("iv") or {}).get("slope"),
            "dRV/dt": (metrics.get("rv") or {}).get("slope"),
            "dVRP/dt": (metrics.get("vrp") or {}).get("slope"),
            "hazard_delta": (metrics.get("hazard_log_ratio") or {}).get("slope"),
            "wall_velocity": {
                "call": (metrics.get("call_wall") or {}).get("slope"),
                "put": (metrics.get("put_wall") or {}).get("slope"),
            },
            "flip_velocity": (metrics.get("distance_to_zero_gamma") or {}).get("slope"),
        }
        descriptive_components = {
            "edge_velocity": _standardized_slope(metrics.get("barrier_ev")),
            "barrier_odds_velocity": _standardized_slope(metrics.get("bop")),
            "tail_geometry_velocity": _standardized_slope(metrics.get("tail_log_ratio")),
            "hazard_balance": (
                None if current.get("hazard_log_ratio") is None
                else math.tanh(float(current["hazard_log_ratio"]))
            ),
            "width_velocity": _standardized_slope(metrics.get("width")),
            "iv_velocity": _standardized_slope(metrics.get("iv")),
            "rv_velocity": _standardized_slope(metrics.get("rv")),
        }

        def mean_available(names: tuple[str, ...], *, sign: float = 1.0) -> float | None:
            values = [descriptive_components[name] for name in names
                      if descriptive_components.get(name) is not None]
            return sign * sum(values) / len(values) if values else None

        # Correlated transforms are first collapsed into conceptual subfamilies.
        # Each subfamily contributes at most once to the single option-family
        # context score; GEX remains magnitude-only context outside this score.
        score_components = {
            "EDGE": mean_available(("edge_velocity", "barrier_odds_velocity")),
            "TAIL": mean_available(("tail_geometry_velocity",)),
            "LOCAL_HAZARD": mean_available(("hazard_balance",)),
            "VOLATILITY": mean_available(
                ("width_velocity", "iv_velocity", "rv_velocity"), sign=-1.0),
            "GEX_CONTEXT": None,
        }
        score_values = [value for name, value in score_components.items()
                        if name != "GEX_CONTEXT" and value is not None]
        option_score = sum(score_values) / len(score_values) if score_values else None

        confidence_groups = []
        for names in (
            ("barrier_ev", "bop"), ("tail_log_ratio",),
            ("hazard_log_ratio",), ("width", "iv", "rv"),
        ):
            values = [float(metrics[name]["confidence"]) for name in names
                      if name in metrics and metrics[name].get("available")]
            if values:
                confidence_groups.append(sum(values) / len(values))
        option_confidence = (
            sum(confidence_groups) / len(confidence_groups)
            if confidence_groups else 0.0)
        state = {
            "available": True, "version": STATE_VERSION, "family": FAMILY,
            "independent_vote": False, "authority": "shadow_context",
            "shadow_mode": True, "policy_influence": "none",
            "sample_count": len(history), "source_quality": quality,
            "metrics": metrics, "named_derivatives": named,
            "option_state_score": None if option_score is None else round(option_score, 6),
            "option_state_confidence": round(option_confidence, 3),
            "option_state_attribution": score_components,
            "option_state_descriptive_components": descriptive_components,
            "option_state_aggregation": (
                "EDGE/TAIL/LOCAL_HAZARD/VOLATILITY subfamily mean; each subfamily "
                "contributes once; GEX_CONTEXT excluded; family authority remains one"),
            "option_state_redundancy_contract": {
                "EDGE": ["barrier_ev_velocity", "barrier_odds_velocity"],
                "TAIL": ["tail_geometry_velocity"],
                "LOCAL_HAZARD": ["hazard_balance"],
                "VOLATILITY": ["width_velocity", "iv_velocity", "rv_velocity"],
                "GEX_CONTEXT": {
                    "included_in_score": False,
                    "reason": "OI×gamma dealer sign is unobserved",
                },
            },
            "barrier_odds_pressure": metrics.get("bop"),
            "edge_velocity": metrics.get("barrier_ev"),
            "tail_geometry": {
                "up_tail": _rounded(current.get("q90", 0) - current.get("q50", 0)),
                "down_tail": _rounded(current.get("q50", 0) - current.get("q10", 0)),
                "tail_ratio": _rounded(current.get("tail_ratio")),
                "tail_log_ratio": _rounded(current.get("tail_log_ratio")),
            },
            "distribution_width": metrics.get("width"),
            "first_touch_hazard": {
                "h_take": _rounded(current.get("h_take")),
                "h_stop": _rounded(current.get("h_stop")),
                "hazard_ratio_log": _rounded(current.get("hazard_log_ratio")),
                "hazard_delta": named["hazard_delta"],
                "window": deepcopy(
                    ((payload.get("cone") or {}).get("first_touch_hazard") or {}).get(
                        "next_window") or {}),
                "derivative_confidence": (
                    metrics.get("hazard_log_ratio") or {}).get("confidence"),
                "source_quality": quality,
            },
            "gex_geometry": deepcopy((payload.get("gamma") or {}).get("field_geometry") or {}),
            "comparison": {"entry": entry, "trade_life_average": averages, "now": current},
        }
        state["interaction_state"] = build_interaction_state(payload, metrics, quality_weight)
        now = time.time()
        if self.cache and now - self._last_persist.get(trade_id, 0.0) >= PERSIST_INTERVAL_SEC:
            self.cache.put(self._cache_key(trade_id), {"observations": history})
            self._last_persist[trade_id] = now
        return state


def wrap_engine(engine) -> OptionShadowTracker:
    existing = getattr(engine, "_option_shadow_tracker", None)
    if existing is not None:
        return existing
    tracker = OptionShadowTracker(getattr(engine, "cache", None))
    base_tick_payload: Callable[[], dict] = engine.tick_payload

    def tick_payload_with_option_shadow() -> dict:
        payload = base_tick_payload()
        state = tracker.update(payload)
        payload["option_derivative_state"] = state
        payload["interaction_state"] = deepcopy(state.get("interaction_state") or {
            "available": False, "family": FAMILY, "independent_vote": False,
            "authority": "shadow_context", "shadow_mode": True,
        })
        return payload

    engine.tick_payload = tick_payload_with_option_shadow
    engine._option_shadow_tracker = tracker
    return tracker


def install_option_shadow_state(app) -> None:
    if getattr(app.state, "option_shadow_state_installed", False):
        return
    app.state.option_shadow_state_installed = True
    app.state.option_shadow_tracker = wrap_engine(app.state.engine)
