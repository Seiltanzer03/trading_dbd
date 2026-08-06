from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _group(asset: str) -> str:
    a = asset.upper()
    if a in {"NAS", "SP500", "US30", "GER40", "UK100"}:
        return "equity"
    if a in {"VIX", "VXN", "GVZ", "OVX", "DV1X", "EVZ"}:
        return "volatility"
    if a in {"GOLD", "XAU", "SILVER", "XAG"}:
        return "metals"
    if a in {"OIL", "BRENT", "WTI"}:
        return "energy"
    if a in {"DXY", "JPY", "EURUSD", "GBPUSD"}:
        return "fx"
    return "other"


def _matrix_value(matrix, i: int, j: int) -> float | None:
    try:
        raw = matrix[i][j]
    except (TypeError, IndexError):
        return None
    return float(raw) if _finite(raw) else None


def _nearest_history(history: list[dict], now_ts: float, seconds_ago: float) -> dict | None:
    if not history:
        return None
    target = now_ts - seconds_ago
    tolerance = max(180.0, seconds_ago * 0.45)
    candidates = [h for h in history if _finite(h.get("asof"))]
    if not candidates:
        return None
    best = min(candidates, key=lambda h: abs(float(h["asof"]) - target))
    return best if abs(float(best["asof"]) - target) <= tolerance else None


def compute_correlation_graph(
    correlation_payload: Optional[Dict[str, Any]] = None,
    price_feeds: Optional[Dict[str, Any]] = None,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    source_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a force-graph contract only from observed correlation matrices.

    No mock links are emitted. On the first sample velocity fields are null and
    become available as the 5-minute correlation history accumulates.
    """
    source_meta = dict(source_meta or {})
    p = correlation_payload or {}
    assets = p.get("assets") or p.get("pairs") or []
    short = p.get("matrix_short") or p.get("matrix")
    baseline = p.get("matrix_baseline")
    delta = p.get("matrix_delta")
    if not assets or not isinstance(short, list) or len(short) < 2:
        return {
            "version": "cross-asset-v2-real-only",
            "available": False,
            "reason": "Нет реальной rolling cross-asset матрицы",
            "nodes": [], "links": [], "break_alerts": [],
            "summary": {
                "available": False,
                "authority": "correlation_family",
                "independent_vote": False,
                "source": source_meta,
            },
        }

    n = min(len(assets), len(short))
    assets = [str(a) for a in assets[:n]]
    now_ts = float(p.get("asof")) if _finite(p.get("asof")) else 0.0
    history = list(history or [])
    prev5 = _nearest_history(history[:-1], now_ts, 5 * 60) if now_ts else None
    prev15 = _nearest_history(history[:-1], now_ts, 15 * 60) if now_ts else None
    prev60 = _nearest_history(history[:-1], now_ts, 60 * 60) if now_ts else None

    group_angles = {
        "equity": -2.35,
        "volatility": -0.75,
        "metals": 2.55,
        "energy": 1.55,
        "fx": 0.35,
        "other": 0.0,
    }
    group_counts: dict[str, int] = {}
    nodes = []
    for asset in assets:
        g = _group(asset)
        idx = group_counts.get(g, 0)
        group_counts[g] = idx + 1
        angle = group_angles[g] + (idx - 0.5) * 0.26
        nodes.append({
            "id": asset,
            "name": asset,
            "group": g,
            "x_norm": round(0.5 + 0.33 * math.cos(angle), 4),
            "y_norm": round(0.5 + 0.33 * math.sin(angle), 4),
        })

    links = []
    alerts = []
    observed_pairs = 0
    velocity_values = []
    for i in range(n):
        for j in range(i + 1, n):
            rho = _matrix_value(short, i, j)
            if rho is None:
                continue
            observed_pairs += 1
            base = _matrix_value(baseline, i, j) if baseline else None
            d = _matrix_value(delta, i, j) if delta else (rho - base if base is not None else None)

            def velocity(prev: dict | None) -> float | None:
                if not prev:
                    return None
                old = _matrix_value(prev.get("matrix_short") or prev.get("matrix"), i, j)
                return round(rho - old, 3) if old is not None else None

            v5 = velocity(prev5)
            v15 = velocity(prev15)
            v60 = velocity(prev60)
            for v in (v5, v15, v60):
                if v is not None:
                    velocity_values.append(abs(v))

            break_now = (
                (d is not None and abs(d) >= 0.25)
                or (v15 is not None and abs(v15) >= 0.20)
                or (v60 is not None and abs(v60) >= 0.30)
            )
            link = {
                "source": assets[i],
                "target": assets[j],
                "correlation": round(rho, 3),
                "baseline": round(base, 3) if base is not None else None,
                "delta_baseline": round(d, 3) if d is not None else None,
                "delta_5m": v5,
                "delta_15m": v15,
                "delta_1h": v60,
                "status": "BREAK_ALERT" if break_now else "STABLE",
                "strength": round(abs(rho), 3),
            }
            links.append(link)
            if break_now:
                alerts.append(link)

    if observed_pairs == 0:
        return {
            "version": "cross-asset-v2-real-only",
            "available": False,
            "reason": "В rolling матрице нет валидных пар",
            "nodes": nodes, "links": [], "break_alerts": [],
            "summary": {
                "available": False,
                "authority": "correlation_family",
                "independent_vote": False,
                "source": source_meta,
            },
        }

    alerts.sort(key=lambda l: max(
        abs(l.get("delta_baseline") or 0),
        abs(l.get("delta_15m") or 0),
        abs(l.get("delta_1h") or 0)), reverse=True)
    return {
        "version": "cross-asset-v2-real-only",
        "available": True,
        "nodes": nodes,
        "links": links,
        "break_alerts": alerts[:8],
        "summary": {
            "regime": "CORRELATION BREAKDOWN" if alerts else "NORMAL CORRELATION",
            "active_breaks_count": len(alerts),
            "observed_pairs": observed_pairs,
            "history_samples": len(history),
            "velocity_ready": len(history) >= 2,
            "max_break_velocity": round(max(velocity_values), 3) if velocity_values else None,
            "source": source_meta,
            "authority": "correlation_family",
            "independent_vote": False,
        },
    }
