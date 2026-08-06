import math
from typing import Any, Dict, List, Optional


def compute_gex_migration(
    mapped_snapshots: List[Dict[str, Any]],
    current_price: float,
    active_trade: Optional[Dict[str, Any]] = None,
    scale: float = 1.0,
) -> Dict[str, Any]:
    """
    Вычисляет историческую матрицу GEX миграции, трекинг условных уровней (Walls/Flip)
    и оценку проходимости траектории сделки (Take Path Obstruction).
    """
    if not mapped_snapshots or current_price is None or math.isnan(current_price):
        return {
            "available": False,
            "reason": "Недостаточно данных снимков цепочки или отсутствует текущая цена",
            "timestamps": [],
            "price_grid": [],
            "heatmap": [],
            "trajectories": {},
            "summary": {
                "regime": "NO DATA",
                "flip": None,
                "call_wall": None,
                "put_wall": None,
                "take_path": "NO DATA",
                "path_pressure": 0.0,
                "authority": "context_only",
                "independent_vote": False,
            },
        }

    valid_snaps = [s for s in mapped_snapshots if s and isinstance(s, dict)]
    if not valid_snaps:
        return {"available": False, "reason": "Нет валидных снимков", "snapshots": []}

    timestamps = []
    times_iso = []
    for s in valid_snaps:
        ts = s.get("ts") or s.get("timestamp") or 0
        timestamps.append(float(ts) if isinstance(ts, (int, float)) else 0.0)
        times_iso.append(s.get("ts_iso") or str(ts))

    all_strikes_set = set()
    for s in valid_snaps:
        gx = s.get("gex") or {}
        stk = gx.get("strikes") or []
        for k in stk:
            if k is not None and math.isfinite(k):
                all_strikes_set.add(round(float(k), 2))

    if not all_strikes_set:
        for s in valid_snaps:
            dens = s.get("density") or {}
            stk = dens.get("strikes") or []
            for k in stk:
                if k is not None and math.isfinite(k):
                    all_strikes_set.add(round(float(k), 2))

    price_grid = sorted(list(all_strikes_set))

    heatmap = []
    for price_level in price_grid:
        row = []
        for s in valid_snaps:
            gx = s.get("gex") or {}
            stk = gx.get("strikes") or []
            net = gx.get("net") or []
            val = 0.0
            if stk and net and len(stk) == len(net):
                min_dist = float("inf")
                closest_idx = -1
                for idx, k in enumerate(stk):
                    dist = abs(k - price_level)
                    if dist < min_dist:
                        min_dist = dist
                        closest_idx = idx
                if closest_idx >= 0 and min_dist <= (price_level * 0.005):
                    val = float(net[closest_idx])
            row.append(round(val, 4))
        heatmap.append(row)

    flip_traj = []
    call_wall_traj = []
    put_wall_traj = []

    last_call_wall_price = None
    last_put_wall_price = None

    for idx, s in enumerate(valid_snaps):
        ts = timestamps[idx]
        gx = s.get("gex") or {}
        stk = gx.get("strikes") or []
        net = gx.get("net") or []
        z_flip = gx.get("zero_flip")

        if z_flip is not None and math.isfinite(z_flip):
            flip_traj.append({"ts": ts, "price": float(z_flip)})
        else:
            flip_traj.append({"ts": ts, "price": None})

        cand_call_p = None
        cand_call_gex = 0.0
        cand_put_p = None
        cand_put_gex = 0.0

        if stk and net and len(stk) == len(net):
            pos_candidates = [(k, n) for k, n in zip(stk, net) if n > 0]
            neg_candidates = [(k, n) for k, n in zip(stk, net) if n < 0]

            if pos_candidates:
                pos_candidates.sort(key=lambda x: x[1], reverse=True)
                top_p, top_gex = pos_candidates[0]

                if last_call_wall_price is not None and len(pos_candidates) > 1:
                    near_cands = [c for c in pos_candidates if abs(c[0] - last_call_wall_price) <= (last_call_wall_price * 0.015)]
                    if near_cands:
                        near_p, near_gex = near_cands[0]
                        if top_gex < near_gex * 1.4:
                            top_p, top_gex = near_p, near_gex

                cand_call_p, cand_call_gex = float(top_p), float(top_gex)
                last_call_wall_price = cand_call_p

            if neg_candidates:
                neg_candidates.sort(key=lambda x: x[1])
                top_p, top_gex = neg_candidates[0]

                if last_put_wall_price is not None and len(neg_candidates) > 1:
                    near_cands = [c for c in neg_candidates if abs(c[0] - last_put_wall_price) <= (last_put_wall_price * 0.015)]
                    if near_cands:
                        near_p, near_gex = near_cands[0]
                        if abs(top_gex) < abs(near_gex) * 1.4:
                            top_p, top_gex = near_p, near_gex

                cand_put_p, cand_put_gex = float(top_p), float(top_gex)
                last_put_wall_price = cand_put_p

        call_wall_traj.append({"ts": ts, "price": cand_call_p, "gex": cand_call_gex})
        put_wall_traj.append({"ts": ts, "price": cand_put_p, "gex": cand_put_gex})

    def _calc_migration(traj: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid_points = [p for p in traj if p.get("price") is not None]
        if not valid_points:
            return {"latest": None, "delta_1h": 0.0, "delta_6h": 0.0, "delta_24h": 0.0, "r_per_hour": 0.0}

        latest_p = valid_points[-1]["price"]
        latest_ts = valid_points[-1]["ts"]

        def _get_past_price(hours_ago: float) -> Optional[float]:
            target_ts = latest_ts - (hours_ago * 3600.0)
            best_p = None
            min_diff = float("inf")
            for pt in valid_points:
                diff = abs(pt["ts"] - target_ts)
                if diff < min_diff and diff <= (hours_ago * 3600.0 * 0.5 + 1800.0):
                    min_diff = diff
                    best_p = pt["price"]
            return best_p

        p_1h = _get_past_price(1.0)
        p_6h = _get_past_price(6.0)
        p_24h = _get_past_price(24.0)

        d_1h = (latest_p - p_1h) if p_1h is not None else 0.0
        d_6h = (latest_p - p_6h) if p_6h is not None else 0.0
        d_24h = (latest_p - p_24h) if p_24h is not None else 0.0

        r_per_h = 0.0
        if active_trade and active_trade.get("entry") and active_trade.get("stop"):
            r_dist = abs(active_trade["entry"] - active_trade["stop"])
            if r_dist > 1e-6:
                r_per_h = (d_6h / 6.0) / r_dist

        return {
            "latest": latest_p,
            "delta_1h": round(d_1h, 2),
            "delta_6h": round(d_6h, 2),
            "delta_24h": round(d_24h, 2),
            "r_per_hour": round(r_per_h, 3),
        }

    call_migration = _calc_migration(call_wall_traj)
    put_migration = _calc_migration(put_wall_traj)
    flip_migration = _calc_migration(flip_traj)

    take_path_status = "CLEAR"
    path_pressure = 0.0

    latest_flip = flip_migration["latest"]
    latest_call = call_migration["latest"]
    latest_put = put_migration["latest"]

    if latest_flip is not None:
        if current_price > latest_flip:
            gamma_regime = "POSITIVE (PINNING / MEAN REVERSION)"
            path_pressure += 0.3
        else:
            gamma_regime = "NEGATIVE (MOMENTUM / SQUEEZE)"
            path_pressure -= 0.3
    else:
        gamma_regime = "MIXED / UNKNOWN"

    if active_trade:
        entry = float(active_trade.get("entry", current_price))
        take = float(active_trade.get("take", current_price))
        direction = active_trade.get("direction", "long").lower()

        if direction == "long":
            if latest_call is not None and current_price < latest_call <= take:
                take_path_status = "OBSTRUCTED (CALL WALL IN PATH)"
                path_pressure -= 0.4
            elif latest_flip is not None and current_price < latest_flip <= take:
                take_path_status = "OBSTRUCTED (FLIP LEVEL AHEAD)"
                path_pressure -= 0.2
            else:
                take_path_status = "CLEAR"
                path_pressure += 0.2
        else:
            if latest_put is not None and take <= latest_put < current_price:
                take_path_status = "OBSTRUCTED (PUT WALL IN PATH)"
                path_pressure -= 0.4
            elif latest_flip is not None and take <= latest_flip < current_price:
                take_path_status = "OBSTRUCTED (FLIP LEVEL AHEAD)"
                path_pressure -= 0.2
            else:
                take_path_status = "CLEAR"
                path_pressure += 0.2

    path_pressure = max(-1.0, min(1.0, path_pressure))

    return {
        "available": True,
        "timestamps": timestamps,
        "times_iso": times_iso,
        "price_grid": price_grid,
        "heatmap": heatmap,
        "trajectories": {
            "flip": flip_traj,
            "call_wall": call_wall_traj,
            "put_wall": put_wall_traj,
        },
        "summary": {
            "gamma_regime": gamma_regime,
            "flip": {
                "price": latest_flip,
                "dist": (current_price - latest_flip) if latest_flip else 0.0,
                "migration_6h": flip_migration["delta_6h"],
            },
            "call_wall": {
                "price": latest_call,
                "dist": (latest_call - current_price) if latest_call else 0.0,
                "migration_6h": call_migration["delta_6h"],
                "r_per_hour": call_migration["r_per_hour"],
            },
            "put_wall": {
                "price": latest_put,
                "dist": (current_price - latest_put) if latest_put else 0.0,
                "migration_6h": put_migration["delta_6h"],
                "r_per_hour": put_migration["r_per_hour"],
            },
            "take_path": take_path_status,
            "path_pressure": round(path_pressure, 2),
            "authority": "context_only",
            "independent_vote": False,
        },
    }
