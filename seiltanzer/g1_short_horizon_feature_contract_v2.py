"""Strict pre-T0 feature contract for future G.1S observations.

V1 feature/model artifacts remain byte/dimension compatible.  V2 is additive:
future passive captures receive a frozen `g1s_evidence_v2` block built only from
sources whose timestamps are at or before T0.  Option context is selected from
the latest cached chain snapshot <= T0, never the chain request made after the
price quote.  Cross-asset context is rejected when its explicit `asof` is after
T0.  Wavelet and intraday returns use only recorded bars ending <= T0.

Option Greeks are Black-Scholes/OI-weighted context.  They are not dealer
inventory, not physical probabilities, and never grant production authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from typing import Any

import numpy as np

from . import g1_short_horizon_runtime as _runtime_module
from .core.wavelet import compute_wavelet_analysis
from .data.feeds import MarketData
from .g1_short_horizon_runtime import (
    MODEL_REFIT_INTERVAL_SEC,
    MODEL_REFIT_MIN_EFFECTIVE_DELTA,
    ShortHorizonRuntime,
    _finite,
    _fit_logistic,
    _json,
    _loads,
    _sha_text,
    _sigmoid,
)
from .passive_learning import PassiveLearningEngine


FEATURE_CONTRACT_V2 = "g1s-t0-evidence-v2"
GREEK_CONTEXT_VERSION = "g1s-bs-oi-greek-context-v1"
V2_MODEL_VERSION = "g1s-v2-dependency-weighted-logistic-v1"

V2_FEATURE_SETS = {
    "MARKET_V2": (
        "ret_5m", "ret_15m", "ret_60m", "realized_vol_15m", "realized_vol_60m",
        "wavelet_low_pct", "wavelet_high_pct", "wavelet_resonance",
        "market_intraday_available", "wavelet_available", "price_quality",
    ),
    "MARKET_OPTION_V2": (
        "ret_5m", "ret_15m", "ret_60m", "realized_vol_15m", "realized_vol_60m",
        "wavelet_low_pct", "wavelet_high_pct", "wavelet_resonance",
        "iv_rv_ratio", "option_skew", "term_slope",
        "gex_zero_flip_log_moneyness", "gex_net_balance",
        "greek_net_delta", "greek_vega_per_spot", "greek_vanna", "greek_charm_per_day",
        "market_intraday_available", "wavelet_available", "option_context_available",
        "greek_context_available", "price_quality", "option_quality",
    ),
    "FULL_V2": (
        "ret_5m", "ret_15m", "ret_60m", "realized_vol_15m", "realized_vol_60m",
        "wavelet_low_pct", "wavelet_high_pct", "wavelet_resonance",
        "iv_rv_ratio", "option_skew", "term_slope",
        "gex_zero_flip_log_moneyness", "gex_net_balance",
        "greek_net_delta", "greek_vega_per_spot", "greek_vanna", "greek_charm_per_day",
        "cross_primary_corr", "cross_primary_delta", "cross_risk_corr", "cross_risk_delta",
        "market_intraday_available", "wavelet_available", "option_context_available",
        "greek_context_available", "cross_asset_available", "price_quality", "option_quality",
    ),
}


def _plain_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _status_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nested = value.get("value")
    return nested if isinstance(nested, dict) else value


def _numeric_by_keys(value: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                candidate = _finite(value.get(key))
                if candidate is not None:
                    return candidate
        for nested in value.values():
            found = _numeric_by_keys(nested, keys)
            if found is not None:
                return found
    return None


def _bs_greek_context(raw: dict[str, Any], spot: float, r: float = 0.05) -> dict[str, Any]:
    """OI-weighted BS Greeks normalized for cross-instrument research features."""
    try:
        strikes = np.asarray(raw["strikes"], dtype=float)
        call_iv = np.asarray(raw["call_iv"], dtype=float)
        put_iv = np.asarray(raw["put_iv"], dtype=float)
        call_oi = np.asarray(raw["call_oi"], dtype=float)
        put_oi = np.asarray(raw["put_oi"], dtype=float)
        t_years = float(raw["t_years"])
    except (KeyError, TypeError, ValueError):
        return {"available": False, "reason": "missing_raw_chain",
                "contract_version": GREEK_CONTEXT_VERSION}
    if not (math.isfinite(spot) and spot > 0 and math.isfinite(t_years) and t_years > 0):
        return {"available": False, "reason": "invalid_spot_or_ttm",
                "contract_version": GREEK_CONTEXT_VERSION}
    n = min(len(strikes), len(call_iv), len(put_iv), len(call_oi), len(put_oi))
    if n < 3:
        return {"available": False, "reason": "insufficient_chain",
                "contract_version": GREEK_CONTEXT_VERSION}
    strikes, call_iv, put_iv = strikes[:n], call_iv[:n], put_iv[:n]
    call_oi, put_oi = np.maximum(call_oi[:n], 0.0), np.maximum(put_oi[:n], 0.0)
    root_t = math.sqrt(t_years)

    def side(iv: np.ndarray, is_call: bool):
        valid = np.isfinite(strikes) & (strikes > 0) & np.isfinite(iv) & (iv > 1e-6) & (iv < 5.0)
        sigma = np.where(valid, iv, np.nan)
        d1 = (np.log(spot/strikes) + (r + 0.5*sigma*sigma)*t_years)/(sigma*root_t)
        d2 = d1-sigma*root_t
        phi = np.exp(-0.5*d1*d1)/math.sqrt(2.0*math.pi)
        # erf is scalar in stdlib; vectorize is fine for small option chains.
        cdf = np.vectorize(lambda x: 0.5*(1.0+math.erf(float(x)/math.sqrt(2.0))))(d1)
        delta = cdf if is_call else cdf-1.0
        vega_per_spot = phi*root_t
        vanna = phi*(root_t-d1/sigma)
        # Calendar-time charm = -dDelta/dT, q=0.  Same local derivative for call/put.
        denom = np.maximum(2.0*t_years*sigma*root_t, 1e-12)
        charm_per_year = -phi*((2.0*r*t_years) - d2*sigma*root_t)/denom
        charm_per_day = charm_per_year/365.0
        return valid, delta, vega_per_spot, vanna, charm_per_day

    call_valid, c_delta, c_vega, c_vanna, c_charm = side(call_iv, True)
    put_valid, p_delta, p_vega, p_vanna, p_charm = side(put_iv, False)
    cw = np.where(call_valid, call_oi, 0.0)
    pw = np.where(put_valid, put_oi, 0.0)
    total = float(cw.sum()+pw.sum())
    if total <= 0:
        return {"available": False, "reason": "no_valid_open_interest",
                "contract_version": GREEK_CONTEXT_VERSION}

    def combined(c: np.ndarray, p: np.ndarray) -> float:
        return float((np.nansum(cw*c)+np.nansum(pw*p))/total)

    return {
        "available": True,
        "contract_version": GREEK_CONTEXT_VERSION,
        "model": "Black-Scholes q=0 contextual sensitivities",
        "net_delta_oi_weighted": combined(c_delta, p_delta),
        "vega_per_spot_oi_weighted": combined(c_vega, p_vega),
        "vanna_oi_weighted": combined(c_vanna, p_vanna),
        "charm_per_day_oi_weighted": combined(c_charm, p_charm),
        "call_oi_share": float(cw.sum()/total),
        "valid_oi_total": total,
        "dealer_inventory_assumption": False,
        "dealer_positioning_claim": False,
        "physical_probability_semantics": False,
        "risk_neutral_context_only": True,
    }


def _window_stats(rows: list[dict[str, Any]], captured_ts: float) -> dict[str, Any]:
    usable = [row for row in rows if float(row["bar_end_ts"]) <= captured_ts+1e-6]
    usable.sort(key=lambda row: float(row["bar_end_ts"]))
    if len(usable) < 2:
        return {"available": False, "reason": "insufficient_pre_t0_bars"}
    end = usable[-1]
    end_ts = float(end["bar_end_ts"])
    end_close = float(end["close"])

    def anchor(seconds: float) -> dict[str, Any] | None:
        cutoff = end_ts-seconds
        candidates = [row for row in usable if float(row["bar_end_ts"]) <= cutoff+1e-6]
        return candidates[-1] if candidates else None

    def ret(seconds: float) -> float | None:
        start = anchor(seconds)
        if not start:
            return None
        start_close = _finite(start.get("close"))
        if start_close is None or start_close <= 0 or end_close <= 0:
            return None
        return math.log(end_close/start_close)

    log_steps: list[tuple[float, float]] = []
    previous = None
    for row in usable:
        close = _finite(row.get("close"))
        if close is not None and close > 0 and previous is not None and previous > 0:
            log_steps.append((float(row["bar_end_ts"]), math.log(close/previous)))
        if close is not None and close > 0:
            previous = close

    def rv(seconds: float) -> float | None:
        vals = [value for ts, value in log_steps if ts > end_ts-seconds-1e-6]
        if len(vals) < 2:
            return None
        return math.sqrt(sum(value*value for value in vals))

    return {
        "available": True,
        "source": "passive_market_bars_pre_t0",
        "source_last_bar_end_ts": end_ts,
        "bar_count": len(usable),
        "ret_5m": ret(5*60.0),
        "ret_15m": ret(15*60.0),
        "ret_60m": ret(60*60.0),
        "realized_vol_15m": rv(15*60.0),
        "realized_vol_60m": rv(60*60.0),
    }


def _wavelet(rows: list[dict[str, Any]], captured_ts: float, price: float) -> dict[str, Any]:
    closes = [float(row["close"]) for row in rows
              if float(row["bar_end_ts"]) <= captured_ts+1e-6 and _finite(row.get("close"))]
    if len(closes) < 20:
        return {"available": False, "reason": "insufficient_pre_t0_bars",
                "source_bar_count": len(closes)}
    result = compute_wavelet_analysis(closes[-256:], price, scale=32)
    if not isinstance(result, dict) or not result.get("available"):
        return {"available": False, "reason": "wavelet_unavailable",
                "source_bar_count": len(closes)}
    return {
        "available": True,
        "source": "passive_market_bars_pre_t0",
        "source_bar_count": len(closes[-256:]),
        "low_pct": _finite(result.get("low_pct")),
        "high_pct": _finite(result.get("high_pct")),
        "resonance": _finite(result.get("resonance")),
        "energy_low": _finite(result.get("energy_low")),
        "energy_high": _finite(result.get("energy_high")),
        "regime": result.get("regime"),
    }


def _latest_option_snapshot(engine: PassiveLearningEngine, features: dict[str, Any],
                            captured_ts: float) -> dict[str, Any]:
    current = features.get("option_distribution") if isinstance(features, dict) else None
    proxy = str((current or {}).get("proxy") or "") if isinstance(current, dict) else ""
    if not proxy:
        return {"available": False, "reason": "no_option_proxy"}
    try:
        snapshots = engine.cache.chain_snapshots(proxy, limit=60)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"cache_error:{type(exc).__name__}"}
    valid = [snap for snap in snapshots
             if _finite(snap.get("ts")) is not None and float(snap["ts"]) <= captured_ts+1e-6]
    if not valid:
        return {"available": False, "reason": "no_chain_snapshot_at_or_before_t0",
                "proxy": proxy}
    snap = dict(valid[-1])
    source_ts = float(snap.pop("ts"))
    return {"available": True, "source_ts": source_ts, "proxy": proxy, "metrics": snap}


def _cross_asset(features: dict[str, Any], captured_ts: float, instrument: str) -> dict[str, Any]:
    wrapper = features.get("cross_asset") if isinstance(features, dict) else None
    raw = (wrapper or {}).get("data") if isinstance(wrapper, dict) else None
    status = _status_value(raw)
    asof = _finite(status.get("asof"))
    if asof is None:
        # Some status wrappers carry ts next to value. It is still explicit source time.
        asof = _finite((raw or {}).get("ts")) if isinstance(raw, dict) else None
    if asof is None:
        return {"available": False, "reason": "missing_cross_asset_asof"}
    if asof > captured_ts+1e-6:
        return {"available": False, "reason": "cross_asset_after_t0", "source_ts": asof}
    assets = status.get("assets") or []
    short = status.get("matrix_short") or status.get("matrix") or []
    delta = status.get("matrix_delta") or []
    if not isinstance(assets, list) or not isinstance(short, list):
        return {"available": False, "reason": "invalid_cross_asset_matrix", "source_ts": asof}
    index = {str(name): i for i, name in enumerate(assets)}

    def pair(a: str, b: str, matrix: Any) -> float | None:
        try:
            return _finite(matrix[index[a]][index[b]])
        except (KeyError, IndexError, TypeError):
            return None

    if instrument == "NAS100":
        primary, risk = ("NAS", "SP500"), ("NAS", "VXN")
    elif instrument == "SP500":
        primary, risk = ("SP500", "NAS"), ("SP500", "VIX")
    elif instrument == "XAUUSD":
        primary, risk = ("GOLD", "SP500"), ("GOLD", "GVZ")
    else:
        primary, risk = ("NAS", "SP500"), ("SP500", "VIX")
    return {
        "available": True,
        "source_ts": asof,
        "primary_pair": list(primary), "risk_pair": list(risk),
        "primary_corr": pair(*primary, short), "primary_delta": pair(*primary, delta),
        "risk_corr": pair(*risk, short), "risk_delta": pair(*risk, delta),
        "dynamic_pairs": status.get("dynamic_pairs"),
    }


def _option_scalars(option: dict[str, Any], annual_vol: float | None,
                    market_price: float) -> dict[str, Any]:
    if not option.get("available"):
        return {"available": False, "reason": option.get("reason")}
    metrics = option.get("metrics") or {}
    implied = metrics.get("implied_move") or {}
    implied_vol = _finite(implied.get("sigma_annual"))
    skew = _numeric_by_keys(metrics.get("skew"), ("rr_25", "risk_reversal", "skew", "value"))
    term_slope = _numeric_by_keys(metrics.get("term"), ("slope", "slope_per_day", "annualized_slope"))
    gex = metrics.get("gex") or {}
    strikes = gex.get("strikes") or []
    net = gex.get("net") or []
    zero_flip = _finite(gex.get("zero_flip"))
    gex_balance = None
    try:
        arr = np.asarray(net, dtype=float)
        den = float(np.nansum(np.abs(arr)))
        if den > 0:
            gex_balance = float(np.nansum(arr)/den)
    except (TypeError, ValueError):
        pass
    proxy_spot = _finite(metrics.get("proxy_spot")) or _finite(metrics.get("spot"))
    zero_log = None
    if zero_flip is not None and proxy_spot is not None and zero_flip > 0 and proxy_spot > 0:
        zero_log = math.log(zero_flip/proxy_spot)
    greeks = metrics.get("greek_context") or {}
    return {
        "available": True,
        "source_ts": option.get("source_ts"),
        "proxy": option.get("proxy"),
        "implied_vol_annual": implied_vol,
        "iv_rv_ratio": (implied_vol/annual_vol
                        if implied_vol is not None and annual_vol is not None and annual_vol > 0 else None),
        "skew": skew,
        "term_slope": term_slope,
        "gex_zero_flip_log_moneyness": zero_log,
        "gex_net_balance": gex_balance,
        "greek_context": greeks if isinstance(greeks, dict) else {"available": False},
        "option_spot_is_proxy_not_target": True,
        "physical_probability_semantics": False,
        "dealer_positioning_claim": False,
    }


def _build_v2(engine: PassiveLearningEngine, instrument: str, captured_ts: float,
              market_price: float, features: dict[str, Any]) -> dict[str, Any]:
    with engine._lock:
        rows = [dict(row) for row in engine._conn.execute(
            "SELECT bar_start_ts,bar_end_ts,close,high,low,source,quality,kind "
            "FROM passive_market_bars WHERE instrument=? AND bar_end_ts<=? "
            "AND bar_end_ts>=? ORDER BY bar_end_ts",
            (instrument, float(captured_ts)+1e-6, float(captured_ts)-6*3600.0)).fetchall()]
    intraday = _window_stats(rows, captured_ts)
    wavelet = _wavelet(rows, captured_ts, market_price)
    option_snapshot = _latest_option_snapshot(engine, features, captured_ts)
    vol = features.get("volatility") or {}
    annual_vol = _finite(vol.get("reference_volatility_annual")) or _finite(vol.get("reference_annual"))
    option = _option_scalars(option_snapshot, annual_vol, market_price)
    cross = _cross_asset(features, captured_ts, instrument)
    return {
        "contract_version": FEATURE_CONTRACT_V2,
        "captured_ts": float(captured_ts),
        "instrument": instrument,
        "intraday": intraday,
        "wavelet": wavelet,
        "option_context": option,
        "cross_asset": cross,
        "semantics": {
            "continuous_market_outcomes_are_physical_observed_returns": True,
            "option_inputs_are_risk_neutral_context_only": True,
            "option_inputs_are_not_physical_probability": True,
            "dealer_inventory_assumption": False,
            "no_future_feature_backfill": True,
        },
        "missing_is_not_zero": True,
    }


def _v2_values(row: dict[str, Any]) -> dict[str, float | None]:
    features = _loads(row.get("frozen_features_json"), {})
    block = features.get("g1s_evidence_v2") or {}
    intraday = block.get("intraday") or {}
    wavelet = block.get("wavelet") or {}
    option = block.get("option_context") or {}
    greeks = option.get("greek_context") or {}
    cross = block.get("cross_asset") or {}
    return {
        "ret_5m": _finite(intraday.get("ret_5m")),
        "ret_15m": _finite(intraday.get("ret_15m")),
        "ret_60m": _finite(intraday.get("ret_60m")),
        "realized_vol_15m": _finite(intraday.get("realized_vol_15m")),
        "realized_vol_60m": _finite(intraday.get("realized_vol_60m")),
        "wavelet_low_pct": _finite(wavelet.get("low_pct")),
        "wavelet_high_pct": _finite(wavelet.get("high_pct")),
        "wavelet_resonance": _finite(wavelet.get("resonance")),
        "iv_rv_ratio": _finite(option.get("iv_rv_ratio")),
        "option_skew": _finite(option.get("skew")),
        "term_slope": _finite(option.get("term_slope")),
        "gex_zero_flip_log_moneyness": _finite(option.get("gex_zero_flip_log_moneyness")),
        "gex_net_balance": _finite(option.get("gex_net_balance")),
        "greek_net_delta": _finite(greeks.get("net_delta_oi_weighted")),
        "greek_vega_per_spot": _finite(greeks.get("vega_per_spot_oi_weighted")),
        "greek_vanna": _finite(greeks.get("vanna_oi_weighted")),
        "greek_charm_per_day": _finite(greeks.get("charm_per_day_oi_weighted")),
        "cross_primary_corr": _finite(cross.get("primary_corr")),
        "cross_primary_delta": _finite(cross.get("primary_delta")),
        "cross_risk_corr": _finite(cross.get("risk_corr")),
        "cross_risk_delta": _finite(cross.get("risk_delta")),
        "market_intraday_available": 1.0 if intraday.get("available") else 0.0,
        "wavelet_available": 1.0 if wavelet.get("available") else 0.0,
        "option_context_available": 1.0 if option.get("available") else 0.0,
        "greek_context_available": 1.0 if greeks.get("available") else 0.0,
        "cross_asset_available": 1.0 if cross.get("available") else 0.0,
        "price_quality": _finite(row.get("price_quality")),
        "option_quality": _finite(row.get("option_quality")),
    }


def _has_v2(row: dict[str, Any]) -> bool:
    features = _loads(row.get("frozen_features_json"), {})
    block = features.get("g1s_evidence_v2") or {}
    return block.get("contract_version") == FEATURE_CONTRACT_V2


def _dependency_weights(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]]) -> np.ndarray:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[runtime._dependency_key(row)].append(index)
    weights = np.zeros(len(rows), dtype=float)
    for members in groups.values():
        per = 1.0/len(members)
        for index in members:
            weights[index] = per
    return weights


def _fit_weighted_logistic(x: np.ndarray, y: np.ndarray, weights: np.ndarray,
                           l2: float = 0.25) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    beta = np.zeros(design.shape[1], dtype=float)
    reg = np.eye(design.shape[1], dtype=float)*l2
    reg[0, 0] = 0.0
    for _ in range(80):
        p = 1.0/(1.0+np.exp(-np.clip(design@beta, -35.0, 35.0)))
        variance = np.maximum(p*(1.0-p), 1e-6)
        grad = design.T@(weights*(p-y))+reg@beta
        hess = design.T@((weights*variance)[:, None]*design)+reg
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hess)@grad
        beta -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return beta


def _fit_v2_models(runtime: ShortHorizonRuntime, *, force: bool = False) -> int:
    created = 0
    now = time.time()
    for horizon in _runtime_module.HORIZONS:
        rows = [row for row in runtime._resolved_eligible(horizon) if _has_v2(row)]
        evidence = runtime._evidence(rows)
        if not evidence.get("fit_allowed"):
            continue
        for feature_set, names in V2_FEATURE_SETS.items():
            with runtime._lock:
                latest = runtime._conn.execute(
                    "SELECT created_ts,effective_n FROM g1s_models WHERE horizon_minutes=? "
                    "AND feature_set=? ORDER BY created_ts DESC LIMIT 1",
                    (horizon, feature_set)).fetchone()
            if latest and not force:
                if now-float(latest["created_ts"]) < MODEL_REFIT_INTERVAL_SEC:
                    continue
                if evidence["effective_n"]-float(latest["effective_n"]) < MODEL_REFIT_MIN_EFFECTIVE_DELTA:
                    continue
            xs, ys = [], []
            for row in rows:
                vector, _ = runtime._feature_vector(row, feature_set)
                xs.append(vector); ys.append(1 if row["direction_label"] == "UP" else 0)
            x = np.asarray(xs, dtype=float); y = np.asarray(ys, dtype=float)
            weights = _dependency_weights(runtime, rows)
            den = max(float(weights.sum()), 1e-12)
            mean = (weights[:, None]*x).sum(axis=0)/den
            variance = (weights[:, None]*(x-mean)**2).sum(axis=0)/den
            std = np.sqrt(np.maximum(variance, 0.0)); std[std < 1e-12] = 1.0
            beta = _fit_weighted_logistic((x-mean)/std, y, weights)
            cutoff = max(float(row["resolved_ts"]) for row in rows)
            feature_names = list(names)+[f"instrument:{code}" for code in tuple(_runtime_module.INSTRUMENTS)[1:]]
            params = {
                "intercept_and_coefficients": [float(v) for v in beta],
                "feature_mean": [float(v) for v in mean],
                "feature_std": [float(v) for v in std],
                "feature_names": feature_names,
                "l2": 0.25,
                "dependency_group_total_weight_one": True,
            }
            artifact = {
                "contract_version": V2_MODEL_VERSION,
                "model_family": "DEPENDENCY_WEIGHTED_LOGISTIC_V2",
                "feature_contract_version": FEATURE_CONTRACT_V2,
                "horizon_minutes": horizon, "feature_set": feature_set,
                "training_cutoff_ts": cutoff,
                "source_observation_ids": [row["observation_id"] for row in rows],
                "parameters": params,
            }
            artifact_sha = _sha_text(_plain_json(artifact))
            model_id = "g1s-v2-model-"+artifact_sha[:25]
            diagnostics = {
                "status": "RESEARCH_ONLY_V2",
                "prospective_oos": False,
                "oos_validated": False,
                "dependency_weighted_fit": True,
                "source_contract": FEATURE_CONTRACT_V2,
            }
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_models(model_id,model_family,horizon_minutes,"
                    "feature_set,training_cutoff_ts,raw_n,effective_n,positive_n,negative_n,"
                    "training_days,parameters_json,artifact_sha256,diagnostics_json,authority,created_ts)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                    (model_id, "DEPENDENCY_WEIGHTED_LOGISTIC_V2", horizon, feature_set, cutoff,
                     evidence["raw_resolved"], float(evidence["effective_n"]),
                     evidence["positive_n"], evidence["negative_n"], evidence["trading_days"],
                     _json(params), artifact_sha, _json(diagnostics), now))
                created += int(cur.rowcount > 0)
    return created


def _predict_v2(runtime: ShortHorizonRuntime, observation_id: str,
                captured_ts: float, horizon: int) -> int:
    with runtime._lock:
        obs = runtime._conn.execute(
            "SELECT * FROM g1s_observations WHERE observation_id=?", (observation_id,)).fetchone()
        models = runtime._conn.execute(
            "SELECT * FROM g1s_models WHERE horizon_minutes=? AND created_ts<=? "
            "AND training_cutoff_ts<? AND model_family='DEPENDENCY_WEIGHTED_LOGISTIC_V2' "
            "ORDER BY created_ts DESC", (horizon, captured_ts, captured_ts)).fetchall()
    if obs is None or not _has_v2(dict(obs)):
        return 0
    chosen: dict[str, Any] = {}
    for model in models:
        chosen.setdefault(str(model["feature_set"]), model)
    written = 0
    for model in chosen.values():
        feature_set = str(model["feature_set"])
        if feature_set not in V2_FEATURE_SETS:
            continue
        vector, _ = runtime._feature_vector(dict(obs), feature_set)
        params = _loads(model["parameters_json"], {})
        mean = np.asarray(params.get("feature_mean") or [], dtype=float)
        std = np.asarray(params.get("feature_std") or [], dtype=float)
        beta = np.asarray(params.get("intercept_and_coefficients") or [], dtype=float)
        x = np.asarray(vector, dtype=float)
        if len(mean) != len(x) or len(std) != len(x) or len(beta) != len(x)+1:
            runtime._error("V2_MODEL_ARTIFACT_SHAPE_MISMATCH", str(model["model_id"]),
                           observation_id=observation_id, critical=True)
            continue
        z = (x-mean)/np.where(std < 1e-12, 1.0, std)
        p_up = float(_sigmoid(np.asarray([beta[0]+z@beta[1:]]))[0])
        payload = {
            "contract_version": V2_MODEL_VERSION,
            "feature_contract_version": FEATURE_CONTRACT_V2,
            "observation_id": observation_id, "model_id": str(model["model_id"]),
            "model_created_ts": float(model["created_ts"]),
            "training_cutoff_ts": float(model["training_cutoff_ts"]),
            "captured_ts": captured_ts, "p_up": p_up,
            "option_context_is_not_physical_probability": True,
            "research_only": True, "production_used": False,
        }
        raw = _plain_json(payload)
        pred_id = "g1s-v2-pred-"+hashlib.sha256(raw.encode()).hexdigest()[:27]
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_shadow_predictions(prediction_id,observation_id,"
                "model_id,created_ts,p_up,prediction_json,prediction_sha256,production_used)"
                " VALUES(?,?,?,?,?,?,?,0)",
                (pred_id, observation_id, model["model_id"], time.time(), p_up, raw,
                 hashlib.sha256(raw.encode()).hexdigest()))
            written += int(cur.rowcount > 0)
    return written


def install_g1_short_horizon_feature_contract_v2() -> None:
    if getattr(ShortHorizonRuntime, "_feature_contract_v2", None) == FEATURE_CONTRACT_V2:
        return

    previous_compute = MarketData._compute_chain_metrics
    previous_capture = PassiveLearningEngine.capture_observation
    previous_vector = ShortHorizonRuntime._feature_vector
    previous_fit = ShortHorizonRuntime.fit_if_ready
    previous_predict = ShortHorizonRuntime._create_prospective_predictions
    previous_status = ShortHorizonRuntime.status

    def compute_chain_metrics(self, raw, spot, proxy, demo, experimental=False, term=None):
        metrics = previous_compute(self, raw, spot, proxy, demo, experimental=experimental, term=term)
        metrics["greek_context"] = _bs_greek_context(raw, float(spot))
        metrics["greek_context_source"] = "raw_chain_same_snapshot"
        return metrics

    def capture_observation(self, *, instrument: str, captured_ts: float,
                            market_price: float, features: dict, forecast: dict,
                            provenance: dict, trigger_reason: str = "cadence",
                            evidence_eligible: bool = True,
                            observation_origin: str = "background_collector"):
        frozen_features = dict(features)
        # Never retrofit existing rows: augmentation occurs only before an INSERT.
        frozen_features["g1s_evidence_v2"] = _build_v2(
            self, instrument, float(captured_ts), float(market_price), frozen_features)
        return previous_capture(
            self, instrument=instrument, captured_ts=captured_ts, market_price=market_price,
            features=frozen_features, forecast=forecast, provenance=provenance,
            trigger_reason=trigger_reason, evidence_eligible=evidence_eligible,
            observation_origin=observation_origin)

    def feature_vector(row: dict, feature_set: str):
        if feature_set not in V2_FEATURE_SETS:
            return previous_vector(row, feature_set)
        values = _v2_values(row)
        vector = [0.0 if values.get(name) is None else float(values[name])
                  for name in V2_FEATURE_SETS[feature_set]]
        vector.extend(1.0 if row["instrument"] == code else 0.0
                      for code in tuple(_runtime_module.INSTRUMENTS)[1:])
        return vector, values

    def fit_if_ready(self, *, force: bool = False):
        created = int(previous_fit(self, force=force) or 0)
        return created+_fit_v2_models(self, force=force)

    def create_predictions(self, observation_id: str, captured_ts: float, horizon: int):
        created = int(previous_predict(self, observation_id, captured_ts, horizon) or 0)
        return created+_predict_v2(self, observation_id, captured_ts, horizon)

    def status(self):
        report = previous_status(self)
        with self._lock:
            v2_obs = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1s_observations WHERE frozen_features_json LIKE ?",
                (f'%"contract_version":"{FEATURE_CONTRACT_V2}"%',)).fetchone()[0])
            v2_models = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1s_models WHERE model_family='DEPENDENCY_WEIGHTED_LOGISTIC_V2'").fetchone()[0])
        report["t0_feature_contract_v2"] = {
            "contract_version": FEATURE_CONTRACT_V2,
            "future_captures_only": True,
            "v1_artifact_dimensions_unchanged": True,
            "v2_observations": v2_obs,
            "v2_models": v2_models,
            "feature_sets": list(V2_FEATURE_SETS),
            "chain_snapshot_must_be_at_or_before_t0": True,
            "cross_asset_asof_must_be_at_or_before_t0": True,
            "wavelet_pre_t0_bars_only": True,
            "greek_context_version": GREEK_CONTEXT_VERSION,
            "dealer_inventory_assumption": False,
            "option_physical_probability_semantics": False,
            "production_authority": False,
        }
        return report

    MarketData._compute_chain_metrics = compute_chain_metrics
    PassiveLearningEngine.capture_observation = capture_observation
    ShortHorizonRuntime._feature_vector = staticmethod(feature_vector)
    ShortHorizonRuntime.fit_if_ready = fit_if_ready
    ShortHorizonRuntime._create_prospective_predictions = create_predictions
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._feature_contract_v2 = FEATURE_CONTRACT_V2
