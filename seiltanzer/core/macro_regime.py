from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


_VOL_MAP = {
    "NAS100": ("vxn", 24.0, 6.0),
    "SP500": ("vix", 20.0, 5.0),
    "US30": ("vix", 20.0, 5.0),
    "XAU": ("gvz", 20.0, 5.0),
    "XAG": ("gvz", 20.0, 5.0),
    "OIL": ("ovx", 35.0, 10.0),
    "EURUSD": ("evz", 10.0, 3.0),
    "GER40": ("dv1x", 20.0, 5.0),
    "UK100": ("vix", 20.0, 5.0),
}


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _feed_value(vol_data: dict | None, key: str) -> float | None:
    if not isinstance(vol_data, dict):
        return None
    raw = vol_data.get(key)
    if isinstance(raw, dict):
        raw = raw.get("value")
    return float(raw) if _finite(raw) else None


def _corr_stress(correlation_data: dict | None) -> float:
    """RMS dislocation of rolling correlations versus their baseline, 0..3."""
    if not isinstance(correlation_data, dict):
        return 0.0
    delta = correlation_data.get("matrix_delta") or []
    vals: list[float] = []
    for i, row in enumerate(delta):
        if not isinstance(row, list):
            continue
        for j, raw in enumerate(row):
            if i == j or not _finite(raw):
                continue
            vals.append(float(raw))
    if not vals:
        return 0.0
    rms = math.sqrt(sum(v * v for v in vals) / len(vals))
    return _clip(rms * 4.0, 0.0, 3.0)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
    return math.sqrt(max(var, 0.0))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])


def _stress_for_timestamp(
    ts: float,
    correlation_data: dict | None,
    correlation_history: list[dict] | None,
) -> tuple[float, str]:
    """Use a real historical correlation snapshot when one is close enough.

    The runtime normally has only a rolling in-memory history since the latest
    restart.  We never fabricate old correlation states.  When no historical
    snapshot exists for a point, the current cross-asset snapshot is used only
    as a low-frequency context contribution and local fragility still supplies
    the time-varying part of Z.
    """
    hist = [h for h in (correlation_history or []) if isinstance(h, dict) and _finite(h.get("asof"))]
    if hist:
        eligible = [h for h in hist if float(h["asof"]) <= ts + 60]
        if eligible:
            best = max(eligible, key=lambda h: float(h["asof"]))
            if ts - float(best["asof"]) <= 20 * 60:
                return _corr_stress(best), "historical_cross_asset"
    if correlation_data:
        return _corr_stress(correlation_data), "current_cross_asset_context"
    return 0.0, "local_only"


def _state_at(
    prices: list[float],
    idx: int,
    vol_index_score: float,
    corr_stress: float,
) -> tuple[float, float, float, dict]:
    """Return X trend, Y volatility and a genuinely time-varying Z fragility.

    Z is not a decorative third axis.  It combines observed cross-asset regime
    dislocation with local market fragility:
      * realized-vol impulse versus its rolling baseline;
      * standardized short-horizon return shock;
      * disagreement between 1h and 4h trend states.
    """
    if idx < 6:
        return 0.0, 0.0, 0.0, {
            "realized_impulse": 0.0, "shock": 0.0,
            "trend_dislocation": 0.0, "cross_asset": corr_stress,
        }

    short_steps = min(12, idx)
    long_steps = min(48, idx)
    p = prices[idx]
    p_short = prices[idx - short_steps]
    p_long = prices[idx - long_steps]
    if min(p, p_short, p_long) <= 0:
        return 0.0, 0.0, 0.0, {
            "realized_impulse": 0.0, "shock": 0.0,
            "trend_dislocation": 0.0, "cross_asset": corr_stress,
        }

    returns = [
        math.log(prices[k] / prices[k - 1])
        for k in range(max(1, idx - 192), idx + 1)
        if prices[k] > 0 and prices[k - 1] > 0
    ]
    sigma = max(_std(returns[-96:]), 1e-7)
    r_short = math.log(p / p_short)
    r_long = math.log(p / p_long)
    z_short = r_short / (sigma * math.sqrt(max(short_steps, 1)))
    z_long = r_long / (sigma * math.sqrt(max(long_steps, 1)))
    x = _clip(0.65 * z_short + 0.35 * z_long, -3.0, 3.0)

    # Rolling realized-vol distribution using 4h windows sampled every hour.
    sigma_windows: list[float] = []
    start = max(20, idx - 192)
    for j in range(start, idx + 1, 12):
        local = [
            math.log(prices[k] / prices[k - 1])
            for k in range(max(1, j - 48), j + 1)
            if prices[k] > 0 and prices[k - 1] > 0
        ]
        if len(local) >= 12:
            sigma_windows.append(_std(local))

    recent_returns = returns[-12:] if len(returns) >= 12 else returns
    current_sigma = max(_std(recent_returns), 1e-7)
    if sigma_windows:
        centre = max(_median(sigma_windows), 1e-7)
        spread = max(_std(sigma_windows), centre * 0.18, 1e-7)
        realized_z = (current_sigma - centre) / spread
    else:
        centre = sigma
        realized_z = 0.0
    y = _clip(0.72 * realized_z + 0.28 * vol_index_score, -3.0, 3.0)

    realized_impulse = _clip(max(0.0, realized_z), 0.0, 3.0)
    shock_sigma = max(centre, sigma, 1e-7)
    shock_ratio = max((abs(v) / shock_sigma for v in returns[-4:]), default=0.0)
    shock = _clip(max(0.0, shock_ratio - 1.0) / 1.5, 0.0, 3.0)
    trend_dislocation = _clip(abs(z_short - z_long) / 1.8, 0.0, 3.0)

    local_fragility = _clip(
        0.50 * realized_impulse + 0.32 * shock + 0.18 * trend_dislocation,
        0.0, 3.0,
    )
    # Cross-asset stress is the structural component; local fragility makes the
    # history genuinely 3D even before enough correlation snapshots accumulate.
    if corr_stress > 0:
        z = _clip(0.62 * corr_stress + 0.38 * local_fragility, 0.0, 3.0)
    else:
        z = local_fragility

    components = {
        "realized_impulse": round(realized_impulse, 3),
        "shock": round(shock, 3),
        "trend_dislocation": round(trend_dislocation, 3),
        "cross_asset": round(corr_stress, 3),
        "local_fragility": round(local_fragility, 3),
    }
    return round(x, 3), round(y, 3), round(z, 3), components


def _classify(x: float, y: float, z: float) -> str:
    if y > 1.6 or z > 1.6:
        return "VOL SHOCK"
    if y > 0.55 and abs(x) > 0.9:
        return "TREND EXPANSION"
    if y < -0.65 and abs(x) < 0.55 and z < 0.7:
        return "COMPRESSION"
    if y < 0.55 and abs(x) > 0.75 and z < 0.9:
        return "CALM TREND"
    if y > 0.8 and abs(x) < 0.65 and z < 1.4:
        return "RECOVERY"
    return "CHOP"


def _apply_hysteresis(raw: str, previous: str | None, x: float, y: float, z: float) -> str:
    if not previous or previous == raw:
        return raw
    if previous == "VOL SHOCK" and raw != "VOL SHOCK" and (y > 1.25 or z > 1.25):
        return "VOL SHOCK"
    if previous == "CALM TREND" and raw == "CHOP" and abs(x) > 0.6 and y < 0.7 and z < 1.0:
        return "CALM TREND"
    if previous == "TREND EXPANSION" and raw == "CALM TREND" and y > 0.35 and abs(x) > 0.8:
        return "TREND EXPANSION"
    return raw


def _boundary_distance(x: float, y: float, z: float) -> float:
    candidates = [
        abs(abs(x) - 0.55), abs(abs(x) - 0.75), abs(abs(x) - 0.9),
        abs(y + 0.65), abs(y - 0.55), abs(y - 0.8), abs(y - 1.6),
        abs(z - 0.7), abs(z - 0.9), abs(z - 1.4), abs(z - 1.6),
    ]
    return round(min(candidates), 3)


def _velocity(a: dict, b: dict) -> dict:
    hours = max((float(b["ts"]) - float(a["ts"])) / 3600.0, 1 / 12)
    vx = (float(b["x"]) - float(a["x"])) / hours
    vy = (float(b["y"]) - float(a["y"])) / hours
    vz = (float(b["z"]) - float(a["z"])) / hours
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    return {"x": vx, "y": vy, "z": vz, "speed": speed, "hours": hours}


def compute_macro_regime(
    price_points: List[Dict[str, Any]],
    vol_data: Optional[Dict[str, Any]] = None,
    correlation_data: Optional[Dict[str, Any]] = None,
    previous_regime: Optional[str] = None,
    *,
    instrument_code: str | None = None,
    source_meta: Optional[Dict[str, Any]] = None,
    correlation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Data-driven market phase space.

    X = multi-horizon trend/momentum z-score.
    Y = realized-vol regime plus the relevant volatility-index context.
    Z = rolling market fragility/stress: local vol shock/dislocation + observed
        cross-asset correlation regime stress.  Z is intentionally dynamic.
    """
    source_meta = dict(source_meta or {})
    pts = []
    for p in price_points or []:
        if _finite(p.get("ts")) and _finite(p.get("price")) and float(p["price"]) > 0:
            pts.append({"ts": float(p["ts"]), "price": float(p["price"])})
    pts.sort(key=lambda p: p["ts"])
    unique: dict[float, float] = {p["ts"]: p["price"] for p in pts}
    pts = [{"ts": ts, "price": unique[ts]} for ts in sorted(unique)]
    if len(pts) < 24:
        return {
            "available": False,
            "reason": f"Недостаточно реальной 5m истории: {len(pts)} точек (нужно ≥24)",
            "trajectory_6h": [], "trajectory_24h": [], "trajectory_3d": [],
            "summary": {
                "available": False, "authority": "strategy_context",
                "independent_vote": False, "source": source_meta,
            },
        }

    prices = [p["price"] for p in pts]
    timestamps = [p["ts"] for p in pts]
    vol_key, anchor, scale = _VOL_MAP.get(instrument_code or "", ("vix", 20.0, 5.0))
    vol_value = _feed_value(vol_data, vol_key)
    vol_index_score = ((vol_value - anchor) / scale) if vol_value is not None else 0.0
    vol_index_score = _clip(vol_index_score, -3.0, 3.0)

    states: list[dict] = []
    stride = max(1, len(pts) // 220)
    for idx in range(6, len(pts), stride):
        corr_stress, corr_source = _stress_for_timestamp(
            timestamps[idx], correlation_data, correlation_history)
        x, y, z, components = _state_at(prices, idx, vol_index_score, corr_stress)
        states.append({
            "ts": timestamps[idx], "x": x, "y": y, "z": z,
            "regime": _classify(x, y, z),
            "stress_components": components,
            "stress_source": corr_source,
        })
    if not states or states[-1]["ts"] != timestamps[-1]:
        corr_stress, corr_source = _stress_for_timestamp(
            timestamps[-1], correlation_data, correlation_history)
        x, y, z, components = _state_at(prices, len(prices) - 1, vol_index_score, corr_stress)
        states.append({
            "ts": timestamps[-1], "x": x, "y": y, "z": z,
            "regime": _classify(x, y, z),
            "stress_components": components,
            "stress_source": corr_source,
        })

    current = states[-1]
    regime = _apply_hysteresis(current["regime"], previous_regime,
                               current["x"], current["y"], current["z"])
    current["regime"] = regime
    now_ts = current["ts"]

    def subset(hours: float) -> list[dict]:
        cutoff = now_ts - hours * 3600
        return [dict(p) for p in states if p["ts"] >= cutoff][-150:]

    traj6, traj24, traj72 = subset(6), subset(24), subset(72)

    age_start = now_ts
    for point in reversed(states[:-1]):
        if point["regime"] != regime:
            break
        age_start = point["ts"]
    regime_age = max(0.0, now_ts - age_start)

    v_now = {"x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0, "hours": 0.0}
    acceleration = 0.0
    if len(states) >= 2:
        v_now = _velocity(states[-2], states[-1])
    if len(states) >= 3:
        v_prev = _velocity(states[-3], states[-2])
        dt_h = max(v_now.get("hours", 0.0), 1 / 12)
        acceleration = math.sqrt(
            (v_now["x"] - v_prev["x"]) ** 2 +
            (v_now["y"] - v_prev["y"]) ** 2 +
            (v_now["z"] - v_prev["z"]) ** 2
        ) / dt_h

    boundary = _boundary_distance(current["x"], current["y"], current["z"])
    coverage = min(1.0, len(pts) / 240.0)
    confidence = int(max(35, min(95, 45 + 28 * min(boundary, 1.0) + 22 * coverage)))

    result_current = {
        "x_trend": current["x"], "y_vol": current["y"], "z_stress": current["z"],
        "regime": regime, "confidence": confidence,
        "velocity_vector": {
            "x": round(v_now["x"], 3), "y": round(v_now["y"], 3),
            "z": round(v_now["z"], 3), "speed": round(v_now["speed"], 3),
        },
        "stress_components": current.get("stress_components") or {},
    }
    summary = {
        "regime": regime,
        "trend_score": current["x"], "vol_score": current["y"], "stress_score": current["z"],
        "regime_age_seconds": round(regime_age, 1),
        "boundary_distance": boundary,
        "transition_velocity": round(v_now["speed"], 3),
        "transition_acceleration": round(acceleration, 3),
        "velocity_vector": result_current["velocity_vector"],
        "stress_components": current.get("stress_components") or {},
        "stress_source": current.get("stress_source"),
        "confidence": confidence,
        "vol_index": {"key": vol_key, "value": vol_value},
        "points": len(pts),
        "history_hours_trading": source_meta.get("history_hours_trading"),
        "correlation_history_samples": len(correlation_history or []),
        "source": source_meta,
        "authority": "strategy_context", "independent_vote": False,
    }
    return {
        "version": "macro-regime-v3-fragility-3d",
        "available": True,
        "current": result_current,
        "trajectory_6h": traj6, "trajectory_24h": traj24, "trajectory_3d": traj72,
        "summary": summary,
    }
