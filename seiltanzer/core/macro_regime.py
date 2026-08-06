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


def _feed_value(vol_data: dict | None, key: str) -> float | None:
    if not isinstance(vol_data, dict):
        return None
    raw = vol_data.get(key)
    if isinstance(raw, dict):
        raw = raw.get("value")
    return float(raw) if _finite(raw) else None


def _corr_stress(correlation_data: dict | None) -> float:
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
    return max(0.0, min(3.0, rms * 4.0))


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
    return math.sqrt(max(var, 0.0))


def _state_at(prices: list[float], idx: int, vol_index_score: float, stress: float) -> tuple[float, float]:
    # 5m bars: 12 ~= 1h, 48 ~= 4h.  The long leg gracefully shortens
    # near the beginning of the history instead of fabricating earlier prices.
    if idx < 6:
        return 0.0, 0.0

    short_steps = min(12, idx)
    long_steps = min(48, idx)
    p = prices[idx]
    p_short = prices[idx - short_steps]
    p_long = prices[idx - long_steps]
    if min(p, p_short, p_long) <= 0:
        return 0.0, 0.0

    returns = [
        math.log(prices[k] / prices[k - 1])
        for k in range(max(1, idx - 96), idx + 1)
        if prices[k] > 0 and prices[k - 1] > 0
    ]
    sigma = max(_std(returns), 1e-6)
    r_short = math.log(p / p_short)
    r_long = math.log(p / p_long)
    z_short = r_short / (sigma * math.sqrt(max(short_steps, 1)))
    z_long = r_long / (sigma * math.sqrt(max(long_steps, 1)))
    x = max(-3.0, min(3.0, 0.65 * z_short + 0.35 * z_long))

    # Realized-vol score is relative to the recent history of 5m return
    # dispersion, not a fixed 0.5% magic constant.  Current relevant vol-index
    # adds a smaller macro context contribution.
    current_sigma = sigma
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
    if sigma_windows:
        centre = sorted(sigma_windows)[len(sigma_windows) // 2]
        spread = max(_std(sigma_windows), centre * 0.15, 1e-6)
        realized_z = (current_sigma - centre) / spread
    else:
        realized_z = 0.0
    y = 0.75 * realized_z + 0.25 * vol_index_score
    return round(max(-3.0, min(3.0, x)), 3), round(max(-3.0, min(3.0, y)), 3)


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
    if previous == "CALM TREND" and raw == "CHOP" and abs(x) > 0.6 and y < 0.7:
        return "CALM TREND"
    if previous == "TREND EXPANSION" and raw == "CALM TREND" and y > 0.35 and abs(x) > 0.8:
        return "TREND EXPANSION"
    return raw


def _boundary_distance(x: float, y: float, z: float) -> float:
    # Distance to the nearest meaningful decision surface used above.
    candidates = [
        abs(abs(x) - 0.55), abs(abs(x) - 0.75), abs(abs(x) - 0.9),
        abs(y + 0.65), abs(y - 0.55), abs(y - 0.8), abs(y - 1.6),
        abs(z - 0.7), abs(z - 0.9), abs(z - 1.4), abs(z - 1.6),
    ]
    return round(min(candidates), 3)


def compute_macro_regime(
    price_points: List[Dict[str, Any]],
    vol_data: Optional[Dict[str, Any]] = None,
    correlation_data: Optional[Dict[str, Any]] = None,
    previous_regime: Optional[str] = None,
    *,
    instrument_code: str | None = None,
    source_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Data-driven phase space from observed 5m market history.

    X = rolling multi-horizon trend / momentum z-score
    Y = rolling realized-vol regime + relevant volatility-index context
    Z = current cross-asset correlation-regime stress
    """
    source_meta = dict(source_meta or {})
    pts = []
    for p in price_points or []:
        if _finite(p.get("ts")) and _finite(p.get("price")) and float(p["price"]) > 0:
            pts.append({"ts": float(p["ts"]), "price": float(p["price"])})
    pts.sort(key=lambda p: p["ts"])

    # Deduplicate timestamps.
    unique: dict[float, float] = {p["ts"]: p["price"] for p in pts}
    pts = [{"ts": ts, "price": unique[ts]} for ts in sorted(unique)]
    if len(pts) < 24:
        return {
            "available": False,
            "reason": f"Недостаточно реальной 5m истории: {len(pts)} точек (нужно ≥24)",
            "trajectory_6h": [], "trajectory_24h": [], "trajectory_3d": [],
            "summary": {
                "available": False,
                "authority": "strategy_context",
                "independent_vote": False,
                "source": source_meta,
            },
        }

    prices = [p["price"] for p in pts]
    timestamps = [p["ts"] for p in pts]

    vol_key, anchor, scale = _VOL_MAP.get(instrument_code or "", ("vix", 20.0, 5.0))
    vol_value = _feed_value(vol_data, vol_key)
    vol_index_score = ((vol_value - anchor) / scale) if vol_value is not None else 0.0
    vol_index_score = max(-3.0, min(3.0, vol_index_score))
    stress = round(_corr_stress(correlation_data), 3)

    states = []
    stride = max(1, len(pts) // 180)
    for idx in range(6, len(pts), stride):
        x, y = _state_at(prices, idx, vol_index_score, stress)
        states.append({
            "ts": timestamps[idx],
            "x": x, "y": y, "z": stress,
            "regime": _classify(x, y, stress),
        })
    if states[-1]["ts"] != timestamps[-1]:
        x, y = _state_at(prices, len(prices) - 1, vol_index_score, stress)
        states.append({
            "ts": timestamps[-1], "x": x, "y": y, "z": stress,
            "regime": _classify(x, y, stress),
        })

    current = states[-1]
    regime = _apply_hysteresis(current["regime"], previous_regime,
                               current["x"], current["y"], current["z"])
    current["regime"] = regime

    now_ts = current["ts"]
    def subset(hours: float) -> list[dict]:
        cutoff = now_ts - hours * 3600
        out = [dict(p) for p in states if p["ts"] >= cutoff]
        return out[-120:]

    traj6 = subset(6)
    traj24 = subset(24)
    traj72 = subset(72)

    # Regime age from the actual rolling-state path, not a fixed placeholder.
    age_start = now_ts
    for point in reversed(states[:-1]):
        if point["regime"] != regime:
            break
        age_start = point["ts"]
    regime_age = max(0.0, now_ts - age_start)

    velocity = 0.0
    if len(states) >= 2:
        a, b = states[-2], states[-1]
        hours = max((b["ts"] - a["ts"]) / 3600.0, 1 / 12)
        velocity = math.sqrt((b["x"] - a["x"]) ** 2 +
                             (b["y"] - a["y"]) ** 2 +
                             (b["z"] - a["z"]) ** 2) / hours

    boundary = _boundary_distance(current["x"], current["y"], current["z"])
    coverage = min(1.0, len(pts) / 240.0)
    confidence = int(max(35, min(95, 45 + 28 * min(boundary, 1.0) + 22 * coverage)))

    result_current = {
        "x_trend": current["x"],
        "y_vol": current["y"],
        "z_stress": current["z"],
        "regime": regime,
        "confidence": confidence,
    }
    summary = {
        "regime": regime,
        "trend_score": current["x"],
        "vol_score": current["y"],
        "stress_score": current["z"],
        "regime_age_seconds": round(regime_age, 1),
        "boundary_distance": boundary,
        "transition_velocity": round(velocity, 3),
        "confidence": confidence,
        "vol_index": {"key": vol_key, "value": vol_value},
        "points": len(pts),
        "history_hours_trading": source_meta.get("history_hours_trading"),
        "source": source_meta,
        "authority": "strategy_context",
        "independent_vote": False,
    }
    return {
        "version": "macro-regime-v2-real-history",
        "available": True,
        "current": result_current,
        "trajectory_6h": traj6,
        "trajectory_24h": traj24,
        "trajectory_3d": traj72,
        "summary": summary,
    }
