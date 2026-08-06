from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _iso(ts: float) -> str:
    if not _finite(ts) or ts <= 0:
        return ""
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).isoformat()


def compute_gex_migration(
    mapped_snapshots: List[Dict[str, Any]],
    current_price: float,
    active_trade: Optional[Dict[str, Any]] = None,
    scale: float = 1.0,
) -> Dict[str, Any]:
    """Build an actionable GEX history view from mapped chain snapshots.

    The first prototype used the union of every historical strike as the Y-axis;
    one stale/extreme strike could stretch the chart from ~2k to ~10k and flatten
    all useful structure around price.  v2 tracks walls first and then constructs
    a robust price window around the live trade/current price.
    """
    if not mapped_snapshots or not _finite(current_price) or float(current_price) <= 0:
        return {
            "available": False,
            "reason": "Недостаточно данных снимков цепочки или отсутствует текущая цена",
            "timestamps": [], "price_grid": [], "heatmap": [], "trajectories": {},
            "summary": {
                "gamma_regime": "NO DATA", "take_path": "NO DATA",
                "path_pressure": 0.0, "authority": "context_only",
                "independent_vote": False,
            },
        }

    current_price = float(current_price)
    valid = []
    for s in mapped_snapshots:
        if not isinstance(s, dict):
            continue
        gx = s.get("gex") or {}
        if gx.get("available") is False:
            continue
        strikes, net = gx.get("strikes") or [], gx.get("net") or []
        if not strikes or len(strikes) != len(net):
            continue
        ts = s.get("ts") or s.get("timestamp") or 0
        if not _finite(ts):
            continue
        valid.append(s)
    valid.sort(key=lambda s: float(s.get("ts") or s.get("timestamp") or 0))
    if len(valid) < 1:
        return {"available": False, "reason": "Нет валидных GEX-снимков", "snapshots": []}

    timestamps = [float(s.get("ts") or s.get("timestamp") or 0) for s in valid]
    times_iso = [_iso(ts) for ts in timestamps]

    flip_traj: list[dict] = []
    call_traj: list[dict] = []
    put_traj: list[dict] = []
    last_call = last_put = None

    for s, ts in zip(valid, timestamps):
        gx = s.get("gex") or {}
        strikes = [float(v) for v in gx.get("strikes") or []]
        net = [float(v) for v in gx.get("net") or []]
        flip = gx.get("zero_flip")
        flip_traj.append({"ts": ts, "price": float(flip) if _finite(flip) else None})

        positive = sorted([(k, g) for k, g in zip(strikes, net) if _finite(k) and _finite(g) and g > 0],
                          key=lambda p: p[1], reverse=True)
        negative = sorted([(k, g) for k, g in zip(strikes, net) if _finite(k) and _finite(g) and g < 0],
                          key=lambda p: abs(p[1]), reverse=True)

        def track(candidates, previous):
            if not candidates:
                return None, 0.0
            strongest = candidates[0]
            if previous is None:
                return float(strongest[0]), float(strongest[1])
            nearby = [p for p in candidates if abs(p[0] - previous) <= max(current_price * 0.012, 1e-6)]
            if nearby:
                near = max(nearby, key=lambda p: abs(p[1]))
                if abs(strongest[1]) < abs(near[1]) * 1.45:
                    return float(near[0]), float(near[1])
            return float(strongest[0]), float(strongest[1])

        call_p, call_g = track(positive, last_call)
        put_p, put_g = track(negative, last_put)
        if call_p is not None:
            last_call = call_p
        if put_p is not None:
            last_put = put_p
        call_traj.append({"ts": ts, "price": call_p, "gex": call_g})
        put_traj.append({"ts": ts, "price": put_p, "gex": put_g})

    def migration(traj: list[dict]) -> dict:
        pts = [p for p in traj if _finite(p.get("price")) and _finite(p.get("ts"))]
        if not pts:
            return {"latest": None, "delta_1h": None, "delta_6h": None,
                    "delta_24h": None, "r_per_hour": None, "persistence": 0.0}
        latest = pts[-1]

        def past(hours: float):
            target = latest["ts"] - hours * 3600
            tolerance = max(1800, hours * 3600 * 0.35)
            older = [p for p in pts[:-1] if p["ts"] <= latest["ts"]]
            if not older:
                return None
            best = min(older, key=lambda p: abs(p["ts"] - target))
            return best if abs(best["ts"] - target) <= tolerance else None

        values = {}
        for hours, key in ((1.0, "delta_1h"), (6.0, "delta_6h"), (24.0, "delta_24h")):
            p = past(hours)
            values[key] = round(latest["price"] - p["price"], 2) if p else None

        recent = pts[-min(8, len(pts)):]
        centre = latest["price"]
        tolerance = current_price * 0.01
        persistence = sum(abs(p["price"] - centre) <= tolerance for p in recent) / len(recent)
        rph = None
        if values["delta_6h"] is not None and active_trade:
            entry, stop = active_trade.get("entry"), active_trade.get("stop")
            if _finite(entry) and _finite(stop) and abs(float(entry) - float(stop)) > 1e-9:
                rph = (values["delta_6h"] / 6) / abs(float(entry) - float(stop))
        return {
            "latest": latest["price"], **values,
            "r_per_hour": round(rph, 3) if rph is not None else None,
            "persistence": round(persistence, 2),
        }

    call_mig, put_mig, flip_mig = migration(call_traj), migration(put_traj), migration(flip_traj)

    # Robust price window around the actual decision geometry.  Old/extreme
    # strikes remain excluded from the plot but still exist in the raw snapshots.
    trade_levels = []
    if active_trade:
        for key in ("entry", "stop", "take"):
            if _finite(active_trade.get(key)):
                trade_levels.append(float(active_trade[key]))
    latest_levels = [current_price]
    for m in (call_mig, put_mig, flip_mig):
        if _finite(m.get("latest")):
            latest_levels.append(float(m["latest"]))
    latest_levels.extend(trade_levels)

    base_span = current_price * 0.12
    if len(trade_levels) >= 2:
        base_span = max(base_span, (max(trade_levels) - min(trade_levels)) * 1.35)
    lo = current_price - base_span
    hi = current_price + base_span
    # Include actionable current walls if they are not absurdly remote.
    for value in latest_levels:
        if current_price * 0.65 <= value <= current_price * 1.35:
            lo = min(lo, value - current_price * 0.02)
            hi = max(hi, value + current_price * 0.02)
    lo = max(1e-9, lo)

    bins = 96
    step = (hi - lo) / max(1, bins - 1)
    price_grid = [lo + i * step for i in range(bins)]
    heatmap = [[0.0 for _s in valid] for _ in range(bins)]
    for c, s in enumerate(valid):
        gx = s.get("gex") or {}
        pairs = [(float(k), float(g)) for k, g in zip(gx.get("strikes") or [], gx.get("net") or [])
                 if _finite(k) and _finite(g) and lo <= float(k) <= hi]
        for strike, gex in pairs:
            row = int(round((strike - lo) / max(step, 1e-12)))
            if 0 <= row < bins:
                # Keep the strongest observation when multiple strikes land in a bin.
                if abs(gex) > abs(heatmap[row][c]):
                    heatmap[row][c] = gex

    latest_flip = flip_mig["latest"]
    latest_call = call_mig["latest"]
    latest_put = put_mig["latest"]
    gamma_regime = "MIXED / UNKNOWN"
    path_pressure = 0.0
    if latest_flip is not None:
        if current_price > latest_flip:
            gamma_regime = "POSITIVE / PINNING CONTEXT"
            path_pressure += 0.15
        else:
            gamma_regime = "NEGATIVE / MOMENTUM CONTEXT"
            path_pressure -= 0.15

    take_path = "NO ACTIVE TRADE"
    if active_trade and _finite(active_trade.get("take")):
        take = float(active_trade["take"])
        direction = str(active_trade.get("direction") or "long").lower()
        take_path = "CLEAR"
        if direction == "long":
            if latest_call is not None and current_price < latest_call <= take:
                take_path = "OBSTRUCTED · CALL WALL IN PATH"
                path_pressure -= 0.4
            elif latest_flip is not None and current_price < latest_flip <= take:
                take_path = "MIXED · FLIP IN PATH"
                path_pressure -= 0.15
            else:
                path_pressure += 0.15
        else:
            if latest_put is not None and take <= latest_put < current_price:
                take_path = "OBSTRUCTED · PUT WALL IN PATH"
                path_pressure -= 0.4
            elif latest_flip is not None and take <= latest_flip < current_price:
                take_path = "MIXED · FLIP IN PATH"
                path_pressure -= 0.15
            else:
                path_pressure += 0.15

    history_hours = max(0.0, timestamps[-1] - timestamps[0]) / 3600 if len(timestamps) > 1 else 0.0
    return {
        "version": "gex-migration-v2-focused",
        "available": True,
        "timestamps": timestamps,
        "times_iso": times_iso,
        "price_grid": [round(v, 4) for v in price_grid],
        "plot_range": [round(lo, 4), round(hi, 4)],
        "heatmap": heatmap,
        "trajectories": {"flip": flip_traj, "call_wall": call_traj, "put_wall": put_traj},
        "summary": {
            "gamma_regime": gamma_regime,
            "current_price": current_price,
            "snapshot_count": len(valid),
            "history_hours": round(history_hours, 1),
            "flip": {
                "price": latest_flip,
                "dist": (current_price - latest_flip) if latest_flip is not None else None,
                "migration_1h": flip_mig["delta_1h"], "migration_6h": flip_mig["delta_6h"],
                "migration_24h": flip_mig["delta_24h"], "persistence": flip_mig["persistence"],
            },
            "call_wall": {
                "price": latest_call,
                "dist": (latest_call - current_price) if latest_call is not None else None,
                "migration_1h": call_mig["delta_1h"], "migration_6h": call_mig["delta_6h"],
                "migration_24h": call_mig["delta_24h"], "r_per_hour": call_mig["r_per_hour"],
                "persistence": call_mig["persistence"],
            },
            "put_wall": {
                "price": latest_put,
                "dist": (current_price - latest_put) if latest_put is not None else None,
                "migration_1h": put_mig["delta_1h"], "migration_6h": put_mig["delta_6h"],
                "migration_24h": put_mig["delta_24h"], "r_per_hour": put_mig["r_per_hour"],
                "persistence": put_mig["persistence"],
            },
            "take_path": take_path,
            "path_pressure": round(max(-1.0, min(1.0, path_pressure)), 2),
            "authority": "context_only",
            "independent_vote": False,
        },
    }
