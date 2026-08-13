"""Broad future-only T0 market evidence for Phase H2.

Collect wide, train controlled: this module freezes a richer prospective state but
never adds those fields to an existing model vector. V1/V2 artifacts stay intact.
All inputs are observed at or before T0; missing data remains missing.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from .core.cross_asset import compute_correlation_graph
from .core.gex_field import analytic_gex_field
from .core.macro_regime import compute_macro_regime
from .core.wavelet import compute_wavelet_analysis
from .g1_short_horizon_runtime import ShortHorizonRuntime, _finite
from .option_shadow_state import robust_derivative
from .passive_learning import PassiveLearningEngine


MARKET_EVIDENCE_V3 = "g1s-t0-evidence-v3"
FAMILY_MANIFEST_VERSION = "g1s-feature-family-manifest-v1"

MARKET_FEATURE_FAMILIES = {
    "BASE_V3": ("price_volatility",),
    "PRICE_OPTION_STATIC": ("price_volatility", "option_static"),
    "PRICE_OPTION_DYNAMICS": ("price_volatility", "option_dynamics"),
    "PRICE_GEX": ("price_volatility", "gex"),
    "PRICE_MACRO": ("price_volatility", "macro"),
    "PRICE_WAVELET": ("price_volatility", "wavelet"),
    "PRICE_CROSS_ASSET": ("price_volatility", "cross_asset"),
    "FULL_MARKET_V3": (
        "price_volatility", "option_static", "option_dynamics", "gex",
        "cross_asset", "macro", "wavelet",
    ),
}

_INSTRUMENT_NODE_ALIASES = {
    "NAS100": ("NAS", "NASDAQ", "NAS100"),
    "SP500": ("SP500", "SPX", "S&P500"),
    "US30": ("US30", "DJI", "DOW"),
    "GER40": ("GER40", "DAX"),
    "UK100": ("UK100", "FTSE"),
    "JPY100": ("JPY100", "NIKKEI", "JP225"),
    "XAU": ("GOLD", "XAU", "XAUUSD"),
    "XAG": ("SILVER", "XAG", "XAGUSD"),
    "EURUSD": ("EURUSD", "EUR"),
    "USDCAD": ("USDCAD", "CAD"),
}


def _quality_block(*, family: str, source_ts: float | None, captured_ts: float,
                   sample_count: int, time_span_minutes: float,
                   quality: float | None = None, available: bool,
                   stale_after_minutes: float | None = None,
                   authority: str = "research_context") -> dict[str, Any]:
    age = None if source_ts is None else max(0.0, (captured_ts-source_ts)/60.0)
    return {
        "family": family,
        "source_ts": source_ts,
        "captured_ts": captured_ts,
        "source_age_minutes": None if age is None else round(age, 3),
        "source_quality": quality,
        "sample_count": int(sample_count),
        "time_span_minutes": round(max(0.0, time_span_minutes), 3),
        "available": bool(available),
        "stale": bool(age is not None and stale_after_minutes is not None
                      and age > stale_after_minutes),
        "authority": authority,
        "independent_vote": False,
    }


def _bars(engine: PassiveLearningEngine, instrument: str,
          captured_ts: float) -> list[dict]:
    # Bounded collector-side query: at most ~30h of one-minute rows, never an
    # HTTP/request-time full-history scan.
    with engine._lock:
        return [dict(row) for row in engine._conn.execute(
            "SELECT bar_start_ts,bar_end_ts,open,high,low,close,source,quality,kind "
            "FROM passive_market_bars WHERE instrument=? AND bar_end_ts<=? "
            "AND bar_end_ts>=? ORDER BY bar_end_ts",
            (instrument, captured_ts+1e-6, captured_ts-30*3600.0),
        ).fetchall()]


def _five_minute_points(rows: list[dict], captured_ts: float) -> list[dict]:
    buckets: dict[int, dict] = {}
    for row in rows:
        ts, close = _finite(row.get("bar_end_ts")), _finite(row.get("close"))
        if ts is None or close is None or close <= 0 or ts > captured_ts+1e-6:
            continue
        key = int(ts//300)
        if key not in buckets or ts > buckets[key]["ts"]:
            buckets[key] = {"ts": ts, "price": close}
    return [buckets[key] for key in sorted(buckets)]


def _price_block(rows: list[dict], captured_ts: float) -> dict:
    usable = [row for row in rows
              if _finite(row.get("bar_end_ts")) is not None
              and float(row["bar_end_ts"]) <= captured_ts+1e-6
              and (_finite(row.get("close")) or 0.0) > 0]
    usable.sort(key=lambda row: float(row["bar_end_ts"]))
    if len(usable) < 2:
        return {
            "available": False, "reason": "insufficient_pre_t0_bars",
            "quality": _quality_block(
                family="live_price", source_ts=None, captured_ts=captured_ts,
                sample_count=len(usable), time_span_minutes=0, available=False),
        }
    pairs = [(float(row["bar_end_ts"]), float(row["close"])) for row in usable]
    end_ts, end_price = pairs[-1]

    def anchor(ts: float, seconds: float):
        candidates = [item for item in pairs if item[0] <= ts-seconds+1e-6]
        return candidates[-1] if candidates else None

    def ret(ts: float, price: float, seconds: float):
        start = anchor(ts, seconds)
        return (math.log(price/start[1])
                if start is not None and start[1] > 0 and price > 0 else None)

    steps = [(pairs[i][0], math.log(pairs[i][1]/pairs[i-1][1]))
             for i in range(1, len(pairs)) if pairs[i-1][1] > 0 and pairs[i][1] > 0]

    def rv(ts: float, seconds: float):
        values = [value for step_ts, value in steps
                  if ts-seconds < step_ts <= ts+1e-6]
        return (math.sqrt(sum(value*value for value in values))
                if len(values) >= 2 else None)

    windows = (5, 15, 30, 60, 120, 240)
    returns = {f"ret_{m}m": ret(end_ts, end_price, m*60.0) for m in windows}
    realized = {f"realized_vol_{m}m": rv(end_ts, m*60.0) for m in windows}

    window_60 = [row for row in usable
                 if end_ts-60*60.0 < float(row["bar_end_ts"]) <= end_ts+1e-6]
    path_stats: dict[str, Any] = {
        "trend_efficiency_60": None, "range_60m": None,
        "drawdown_60m": None, "drawup_60m": None,
        "trend_regime": None, "volatility_regime": None,
    }
    ret60 = returns.get("ret_60m")
    rv15 = realized.get("realized_vol_15m")
    rv60 = realized.get("realized_vol_60m")
    if len(window_60) >= 10 and ret60 is not None:
        path_steps = [value for step_ts, value in steps
                      if end_ts-60*60.0 < step_ts <= end_ts+1e-6]
        path_abs = sum(abs(value) for value in path_steps)
        high = max(float(row["high"]) for row in window_60)
        low = min(float(row["low"]) for row in window_60)
        efficiency = abs(float(ret60))/path_abs if path_abs > 1e-12 else 0.0
        path_stats.update({
            "trend_efficiency_60": max(0.0, min(1.0, efficiency)),
            "range_60m": math.log(high/low) if high > 0 and low > 0 else None,
            "drawdown_60m": math.log(end_price/high) if high > 0 else None,
            "drawup_60m": math.log(end_price/low) if low > 0 else None,
            "trend_regime": (
                "TREND_UP" if ret60 > 0 and efficiency >= 0.35
                else "TREND_DOWN" if ret60 < 0 and efficiency >= 0.35
                else "CHOP"),
        })
    if rv15 is not None and rv60 is not None and rv60 > 0:
        ratio = 2.0*float(rv15)/float(rv60)
        path_stats["volatility_regime"] = (
            "EXPANDING" if ratio >= 1.15
            else "CONTRACTING" if ratio <= 0.85
            else "NORMAL")

    state_points = _five_minute_points(usable, captured_ts)[-36:]
    ret_series, rv_series = [], []
    for point in state_points:
        r15 = ret(float(point["ts"]), float(point["price"]), 15*60.0)
        rv60 = rv(float(point["ts"]), 60*60.0)
        if r15 is not None:
            ret_series.append({"ts": point["ts"], "value": r15})
        if rv60 is not None:
            rv_series.append({"ts": point["ts"], "value": rv60})
    qualities = [_finite(row.get("quality")) for row in usable[-240:]]
    qualities = [value for value in qualities if value is not None]
    source_quality = (sum(qualities)/len(qualities)) if qualities else 0.0
    span = (end_ts-pairs[0][0])/60.0
    return {
        "available": True, "price": end_price,
        "source_last_bar_end_ts": end_ts,
        **returns, **realized, **path_stats,
        "return_state_for_derivative": "rolling_15m_log_return",
        "return_dynamics": robust_derivative(
            ret_series, "value", source_quality=source_quality,
            reference_ts=captured_ts, stale_after_minutes=15.0),
        "rv_state_for_derivative": "rolling_60m_sqrt_sum_squared_log_returns",
        "rv_dynamics": robust_derivative(
            rv_series, "value", source_quality=source_quality,
            reference_ts=captured_ts, stale_after_minutes=15.0),
        "quality": _quality_block(
            family="live_price", source_ts=end_ts, captured_ts=captured_ts,
            sample_count=len(usable), time_span_minutes=span,
            quality=round(source_quality, 3), available=True,
            stale_after_minutes=15.0, authority="observed_market_context"),
    }


def _nested_number(value: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                number = _finite(value.get(key))
                if number is not None:
                    return number
        for child in value.values():
            number = _nested_number(child, keys)
            if number is not None:
                return number
    return None


def _option_history(engine: PassiveLearningEngine, features: dict,
                    captured_ts: float) -> tuple[str | None, list[dict]]:
    current = features.get("option_distribution") if isinstance(features, dict) else None
    proxy = str((current or {}).get("proxy") or "") if isinstance(current, dict) else ""
    if not proxy:
        return None, []
    try:
        rows = engine.cache.chain_snapshots(proxy, limit=120)
    except Exception:  # noqa: BLE001
        return proxy, []
    valid = [dict(row) for row in rows
             if _finite(row.get("ts")) is not None
             and float(row["ts"]) <= captured_ts+1e-6]
    valid.sort(key=lambda row: float(row["ts"]))
    return proxy, valid


def _gex_snapshot(metrics: dict) -> dict:
    gex = metrics.get("gex") or {}
    spot = (_finite(metrics.get("proxy_spot")) or _finite(metrics.get("spot"))
            or _finite(gex.get("spot")))
    if spot is None:
        return {"available": False, "reason": "missing_proxy_spot"}
    try:
        field = analytic_gex_field(gex.get("strikes") or [], gex.get("net") or [], spot)
    except (TypeError, ValueError):
        return {"available": False, "reason": "invalid_gex_profile"}
    if not field.get("available"):
        return field
    zero = _finite(gex.get("zero_flip"))
    return {
        **field,
        "zero_gamma": zero,
        "zero_gamma_log_moneyness": (
            math.log(zero/spot) if zero is not None and zero > 0 and spot > 0 else None),
        "call_wall_log_moneyness": (
            math.log(float(field["call_wall"])/spot)
            if field.get("call_wall") is not None and float(field["call_wall"]) > 0 else None),
        "put_wall_log_moneyness": (
            math.log(float(field["put_wall"])/spot)
            if field.get("put_wall") is not None and float(field["put_wall"]) > 0 else None),
        "proxy_spot": spot,
        "option_spot_is_proxy_not_target": True,
    }


def _option_blocks(engine: PassiveLearningEngine, features: dict, captured_ts: float,
                   annual_rv: float | None, source_quality: float | None):
    proxy, history = _option_history(engine, features, captured_ts)
    quality = max(0.0, min(1.0, float(source_quality))) if source_quality is not None else 0.0
    if not history:
        unavailable = {
            "available": False, "reason": "no_pre_t0_option_history", "proxy": proxy,
            "quality": _quality_block(
                family="option_distribution", source_ts=None, captured_ts=captured_ts,
                sample_count=0, time_span_minutes=0, quality=source_quality,
                available=False),
        }
        return unavailable, dict(unavailable), dict(unavailable)

    series: dict[str, list[dict]] = defaultdict(list)
    gex_rows: list[dict] = []
    latest_static: dict[str, Any] = {}
    for snapshot in history:
        ts = float(snapshot["ts"])
        metrics = {key: value for key, value in snapshot.items() if key != "ts"}
        implied, greeks = metrics.get("implied_move") or {}, metrics.get("greek_context") or {}
        static = {
            "iv": _finite(implied.get("sigma_annual")),
            "skew": _nested_number(metrics.get("skew"),
                                   ("rr", "rr_25", "risk_reversal", "skew", "value")),
            "term_slope": _nested_number(metrics.get("term"),
                                         ("slope", "slope_per_day", "annualized_slope")),
            "delta": _finite(greeks.get("net_delta_oi_weighted")),
            "vega_per_spot": _finite(greeks.get("vega_per_spot_oi_weighted")),
            "vanna": _finite(greeks.get("vanna_oi_weighted")),
            "charm_per_day": _finite(greeks.get("charm_per_day_oi_weighted")),
        }
        latest_static = static
        for name, value in static.items():
            if value is not None:
                series[name].append({"ts": ts, "value": value})
        gex = _gex_snapshot(metrics)
        if gex.get("available"):
            gex_rows.append({"ts": ts, **gex})
            for name in (
                "field_score", "force_score", "stiffness_score",
                "zero_gamma_log_moneyness", "call_wall_log_moneyness",
                "put_wall_log_moneyness",
            ):
                value = _finite(gex.get(name))
                if value is not None:
                    series[f"gex_{name}"].append({"ts": ts, "value": value})

    latest_ts, first_ts = float(history[-1]["ts"]), float(history[0]["ts"])
    span = (latest_ts-first_ts)/60.0
    derivatives = {
        name: robust_derivative(
            observations, "value", source_quality=quality,
            reference_ts=captured_ts, stale_after_minutes=30.0)
        for name, observations in series.items()
    }
    static_block = {
        "available": True, "proxy": proxy, **latest_static,
        "iv_rv_ratio": (
            latest_static.get("iv")/annual_rv
            if latest_static.get("iv") is not None and annual_rv is not None and annual_rv > 0
            else None),
        "risk_neutral_context_only": True,
        "physical_probability_semantics": False,
        "dealer_positioning_claim": False,
        "quality": _quality_block(
            family="option_distribution", source_ts=latest_ts,
            captured_ts=captured_ts, sample_count=len(history), time_span_minutes=span,
            quality=source_quality, available=True, stale_after_minutes=30.0,
            authority="risk_neutral_context"),
    }
    dynamic_block = {
        "available": any(row.get("available") for row in derivatives.values()),
        "proxy": proxy,
        "estimator": "existing_option_shadow_state.robust_derivative",
        "derivatives": derivatives,
        "vrp_derivative": {
            "available": False,
            "reason": "requires time-aligned physical RV series; not fabricated from chain snapshots",
        },
        "quality": _quality_block(
            family="option_distribution", source_ts=latest_ts,
            captured_ts=captured_ts, sample_count=len(history), time_span_minutes=span,
            quality=source_quality,
            available=any(row.get("available") for row in derivatives.values()),
            stale_after_minutes=30.0, authority="shadow_research_context"),
    }
    latest_gex = gex_rows[-1] if gex_rows else {"available": False, "reason": "gex_unavailable"}
    gex_block = {
        **latest_gex,
        "dynamics": {
            key: derivatives.get(f"gex_{key}") for key in (
                "field_score", "force_score", "stiffness_score",
                "zero_gamma_log_moneyness", "call_wall_log_moneyness",
                "put_wall_log_moneyness",
            )
        },
        "family": "option_distribution", "independent_vote": False,
        "authority": "context_only", "dealer_positioning_claim": False,
        "quality": _quality_block(
            family="option_distribution",
            source_ts=(float(latest_gex["ts"]) if gex_rows else latest_ts),
            captured_ts=captured_ts, sample_count=len(gex_rows),
            time_span_minutes=((float(gex_rows[-1]["ts"])-float(gex_rows[0]["ts"]))/60.0
                               if len(gex_rows) >= 2 else 0.0),
            quality=source_quality, available=bool(gex_rows),
            stale_after_minutes=30.0, authority="context_only"),
    }
    return static_block, dynamic_block, gex_block


def _cross_block(features: dict, captured_ts: float, instrument: str):
    wrapper = features.get("cross_asset") if isinstance(features, dict) else None
    raw = (wrapper or {}).get("data") if isinstance(wrapper, dict) else None
    if not isinstance(raw, dict):
        return {"available": False, "reason": "missing_cross_asset_payload"}, None
    status = raw.get("value") if isinstance(raw.get("value"), dict) else raw
    asof = _finite(status.get("asof"))
    if asof is None:
        asof = _finite(raw.get("ts"))
    if asof is None:
        return {"available": False, "reason": "missing_cross_asset_asof"}, None
    if asof > captured_ts+1e-6:
        return {"available": False, "reason": "cross_asset_after_t0"}, None
    graph = compute_correlation_graph(
        status, history=[], source_meta={"source": "frozen_pre_t0_cross_asset"})
    if not graph.get("available"):
        return {"available": False, "reason": graph.get("reason"), "source_ts": asof}, status
    aliases = {value.upper() for value in _INSTRUMENT_NODE_ALIASES.get(instrument, (instrument,))}
    node = next((row for row in graph.get("nodes") or []
                 if str(row.get("id") or "").upper() in aliases), None)
    summary = graph.get("summary") or {}
    return {
        "available": True, "source_ts": asof, "instrument_node": node,
        "systemic_coupling": summary.get("systemic_coupling"),
        "network_tension": summary.get("network_tension"),
        "fragmentation": summary.get("fragmentation"),
        "active_breaks_count": summary.get("active_breaks_count"),
        "material_pairs": summary.get("material_pairs"),
        "stress_pairs": summary.get("stress_pairs"),
        "relationship_states": summary.get("relationship_states"),
        "dominant_stress_node": summary.get("dominant_stress_node"),
        "dynamic_pairs": status.get("dynamic_pairs"),
        "full_observed_graph": {
            "version": graph.get("version"), "nodes": graph.get("nodes") or [],
            "links": graph.get("links") or []},
        "causal_direction_claim": False,
        "quality": _quality_block(
            family="correlation", source_ts=asof, captured_ts=captured_ts,
            sample_count=int(summary.get("observed_pairs") or 0),
            time_span_minutes=float(summary.get("history_span_minutes") or 0.0),
            available=True, stale_after_minutes=30.0,
            authority="correlation_context"),
    }, status


def _macro_block(points: list[dict], correlation: dict | None,
                 captured_ts: float, instrument: str) -> dict:
    result = compute_macro_regime(
        points, vol_data=None, correlation_data=correlation, previous_regime=None,
        instrument_code=instrument,
        source_meta={"source": "passive_5m_points_pre_t0",
                     "history_hours_trading": round(len(points)*5/60, 2),
                     "vol_index_available": False},
        correlation_history=[])
    source_ts = points[-1]["ts"] if points else None
    span = ((points[-1]["ts"]-points[0]["ts"])/60.0 if len(points) >= 2 else 0.0)
    if not result.get("available"):
        return {
            "available": False, "reason": result.get("reason"),
            "quality": _quality_block(
                family="macro_context", source_ts=source_ts, captured_ts=captured_ts,
                sample_count=len(points), time_span_minutes=span, available=False,
                authority="strategy_context")}
    current, summary = result.get("current") or {}, result.get("summary") or {}
    confidence = _finite(summary.get("confidence"))
    return {
        "available": True, "version": result.get("version"),
        "x": current.get("x_trend"), "y": current.get("y_vol"),
        "z": current.get("z_stress"), "regime": current.get("regime"),
        "boundary_distance": summary.get("boundary_distance"),
        "regime_age_seconds": summary.get("regime_age_seconds"),
        "transition_velocity": summary.get("transition_velocity"),
        "transition_acceleration": summary.get("transition_acceleration"),
        "velocity_vector": summary.get("velocity_vector"),
        "acceleration_vector": summary.get("acceleration_vector"),
        "stress_components": summary.get("stress_components"),
        "vol_index_available": False,
        "quality": _quality_block(
            family="macro_context", source_ts=source_ts, captured_ts=captured_ts,
            sample_count=len(points), time_span_minutes=span,
            quality=(confidence/100.0 if confidence is not None else None), available=True,
            stale_after_minutes=15.0, authority="strategy_context"),
    }


def _energy_transfer(flow: list[dict]) -> dict:
    if len(flow) < 2:
        return {"available": False, "reason": "insufficient_energy_flow_history"}
    latest = flow[-1]
    target = float(latest["ts"])-30*60.0
    prior = min(flow[:-1], key=lambda row: abs(float(row["ts"])-target))
    elapsed = max((float(latest["ts"])-float(prior["ts"]))/60.0, 1e-9)
    bands = ("micro", "intraday", "macro")
    delta = {band: float(latest.get(band) or 0)-float(prior.get(band) or 0) for band in bands}
    destination, source = max(delta, key=delta.get), min(delta, key=delta.get)
    magnitude = max(0.0, delta[destination], -delta[source])
    return {
        "available": magnitude > 1e-9,
        "source": source if magnitude > 1e-9 else None,
        "destination": destination if magnitude > 1e-9 else None,
        "elapsed_minutes": round(elapsed, 3),
        "rate_pp_per_30m": round(magnitude/elapsed*30.0, 4),
        "magnitude_pp": round(magnitude, 4),
        "band_deltas_pp": {key: round(value, 4) for key, value in delta.items()},
        "causal_claim": False,
    }


def _wavelet_block(points: list[dict], captured_ts: float) -> dict:
    result = compute_wavelet_analysis(
        points, sampling_minutes=5.0,
        source_meta={"source": "passive_5m_points_pre_t0"})
    source_ts = points[-1]["ts"] if points else None
    span = ((points[-1]["ts"]-points[0]["ts"])/60.0 if len(points) >= 2 else 0.0)
    if not result.get("available"):
        return {
            "available": False, "reason": result.get("reason"),
            "quality": _quality_block(
                family="derived_price_context", source_ts=source_ts,
                captured_ts=captured_ts, sample_count=len(points),
                time_span_minutes=span, available=False,
                authority="visual_research_context")}
    summary = result.get("summary") or {}
    return {
        "available": True, "version": result.get("version"),
        "dominant_period_hours": summary.get("dominant_period_hours"),
        "secondary_period_hours": summary.get("secondary_period_hours"),
        "secondary_power_ratio": summary.get("secondary_power_ratio"),
        "micro_energy_pct": summary.get("micro_energy_pct"),
        "intraday_energy_pct": summary.get("intraday_energy_pct"),
        "macro_energy_pct": summary.get("macro_energy_pct"),
        "persistence": summary.get("persistence"),
        "phase_stability": summary.get("phase_stability"),
        "spectral_concentration": summary.get("spectral_concentration"),
        "cycle_shift": summary.get("cycle_shift"),
        "ridge_velocity_log_per_hour": summary.get("ridge_velocity_log_per_hour"),
        "ridge_power_slope_log_per_hour": summary.get("ridge_power_slope_log_per_hour"),
        "decay_half_life_estimate_hours": summary.get("decay_half_life_estimate_hours"),
        "energy_transfer": _energy_transfer(result.get("energy_flow") or []),
        "quality": _quality_block(
            family="derived_price_context", source_ts=source_ts,
            captured_ts=captured_ts, sample_count=len(points),
            time_span_minutes=span, available=True, stale_after_minutes=15.0,
            authority="visual_research_context"),
    }


def build_market_evidence_v3(engine: PassiveLearningEngine, instrument: str,
                             captured_ts: float, market_price: float,
                             features: dict[str, Any],
                             provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = _bars(engine, instrument, captured_ts)
    points = _five_minute_points(rows, captured_ts)
    price = _price_block(rows, captured_ts)
    vol = features.get("volatility") or {}
    annual_rv = _finite(vol.get("reference_volatility_annual"))
    if annual_rv is None:
        annual_rv = _finite(vol.get("reference_annual"))
    option_quality = _finite(((provenance or {}).get("options") or {}).get("quality"))
    option_static, option_dynamics, gex = _option_blocks(
        engine, features, captured_ts, annual_rv, option_quality)
    cross, raw_cross = _cross_block(features, captured_ts, instrument)
    macro = _macro_block(points, raw_cross, captured_ts, instrument)
    wavelet = _wavelet_block(points, captured_ts)
    families = {
        name: {"members": list(members), "training_enabled": False,
               "auto_fit": False, "ablation_required": True,
               "independent_vote": False}
        for name, members in MARKET_FEATURE_FAMILIES.items()
    }
    return {
        "contract_version": MARKET_EVIDENCE_V3,
        "family_manifest_version": FAMILY_MANIFEST_VERSION,
        "captured_ts": float(captured_ts), "instrument": instrument,
        "market_price": float(market_price),
        "price_volatility": price, "option_static": option_static,
        "option_dynamics": option_dynamics, "gex": gex,
        "cross_asset": cross, "macro": macro, "wavelet": wavelet,
        "feature_families": families,
        "semantics": {
            "collect_wide_train_controlled": True,
            "future_captures_only": True,
            "no_historical_retrofit": True,
            "no_future_feature_backfill": True,
            "family_ablation_before_training_authority": True,
            "option_inputs_are_risk_neutral_context_only": True,
            "gex_is_not_observed_dealer_inventory": True,
            "cross_asset_has_no_causal_direction_claim": True,
            "macro_and_wavelet_are_context_not_independent_votes": True,
            "production_authority": False,
        },
        "missing_is_not_zero": True,
    }


def install_g1_broad_market_evidence_v3() -> None:
    if getattr(ShortHorizonRuntime, "_broad_market_evidence_version", None) == MARKET_EVIDENCE_V3:
        return
    previous_capture = PassiveLearningEngine.capture_observation
    previous_status = ShortHorizonRuntime.status

    def capture_observation(self, *, instrument: str, captured_ts: float,
                            market_price: float, features: dict, forecast: dict,
                            provenance: dict, trigger_reason: str = "cadence",
                            evidence_eligible: bool = True,
                            observation_origin: str = "background_collector"):
        frozen = dict(features)
        frozen["g1s_evidence_v3"] = build_market_evidence_v3(
            self, instrument, float(captured_ts), float(market_price), frozen,
            provenance=provenance)
        return previous_capture(
            self, instrument=instrument, captured_ts=captured_ts,
            market_price=market_price, features=frozen, forecast=forecast,
            provenance=provenance, trigger_reason=trigger_reason,
            evidence_eligible=evidence_eligible,
            observation_origin=observation_origin)

    def status(self):
        body = previous_status(self)
        # Request-time status must stay O(1). Do not count historical V3 JSON rows
        # here; Phase G.1S already moved history scans to the research worker.
        body["t0_feature_contract_v3"] = {
            "contract_version": MARKET_EVIDENCE_V3,
            "future_captures_only": True,
            "v1_v2_model_dimensions_unchanged": True,
            "collection_count": None,
            "collection_count_source": "not_scanned_on_request",
            "feature_families": list(MARKET_FEATURE_FAMILIES),
            "training_enabled": False,
            "ablation_required_before_training": True,
            "production_authority": False,
        }
        return body

    PassiveLearningEngine.capture_observation = capture_observation
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._broad_market_evidence_version = MARKET_EVIDENCE_V3
