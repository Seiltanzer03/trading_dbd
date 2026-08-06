"""Stateful distribution revaluation for Probability Lattice and AI evidence.

The browser animation is intentionally not used as evidence.  This module wraps
one Engine instance and derives a compact, persisted history from the same
option/scenario payload that already drives the lattice:

* entry snapshot;
* arithmetic average over the active trade;
* current snapshot;
* probability-mass migration between stop/red/green/take zones;
* source-quality and history-stability weights.

The result is attached to every tick as ``lattice_revaluation``.  It is useful
both for the UI and for the quantitative AI policy manager.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from copy import deepcopy
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import Response

_STATE_VERSION = 1
_RECENT_LIMIT = 180
_SAMPLE_INTERVAL_SEC = 1.0
_PERSIST_INTERVAL_SEC = 10.0
_SCRIPT_TAG = (
    '<script type="module" '
    'src="/static/js/lattice_revaluation_ui.js"></script>'
)


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _round(value: Any, digits: int = 5) -> float | None:
    out = _number(value)
    return None if out is None else round(out, digits)


def _normalised_distribution(probs: Any, edges: Any) -> tuple[list[float], list[float]] | None:
    if not isinstance(probs, list) or not isinstance(edges, list):
        return None
    if len(edges) != len(probs) + 1 or not probs:
        return None
    p = [max(0.0, _number(x) or 0.0) for x in probs]
    e = [_number(x) for x in edges]
    if any(x is None for x in e):
        return None
    e = [float(x) for x in e]
    if any(e[i + 1] <= e[i] for i in range(len(e) - 1)):
        return None
    total = sum(p)
    if total <= 0:
        return None
    return [x / total for x in p], e


def _range_mass(probs: list[float], edges: list[float], lo: float, hi: float) -> float:
    """Integrate piecewise-uniform bin mass over [lo, hi)."""
    out = 0.0
    for p, a, b in zip(probs, edges, edges[1:]):
        width = b - a
        if width <= 0 or p <= 0:
            continue
        overlap = max(0.0, min(b, hi) - max(a, lo))
        if overlap:
            out += p * overlap / width
    return out


def distribution_buckets(probs: Any, edges: Any, take_r: float) -> dict[str, float] | None:
    """Return four mutually exclusive terminal-R masses that sum to one."""
    dist = _normalised_distribution(probs, edges)
    if dist is None:
        return None
    p, e = dist
    inf = float("inf")
    take = max(0.0, float(take_r))
    buckets = {
        "stop_tail": _range_mass(p, e, -inf, -1.0),
        "red_zone": _range_mass(p, e, -1.0, 0.0),
        "green_zone": _range_mass(p, e, 0.0, take),
        "take_tail": _range_mass(p, e, take, inf),
    }
    total = sum(buckets.values())
    if total > 0:
        buckets = {k: v / total for k, v in buckets.items()}
    return {k: round(v, 7) for k, v in buckets.items()}


def _source_quality(payload: dict) -> dict:
    market = payload.get("market") or {}
    feeds = payload.get("feeds") or {}
    proxy = feeds.get("proxy_price") or {}
    chain = feeds.get("chain") or {}
    summary = payload.get("options_summary") or {}

    if not market.get("available"):
        return {
            "mode": "scenario_only",
            "label": "SCENARIO ONLY",
            "weight": 0.25,
            "reason": market.get("anchor_reason") or "option anchor unavailable",
            "context_only": True,
        }

    proxy_status = str(proxy.get("status") or "no_data")
    if proxy_status == "live":
        mode, label, weight = "live_mapping", "LIVE MAPPING", 0.85
    elif proxy_status in {"delayed", "ok", "indicative"}:
        mode, label, weight = "indicative_mapping", "INDICATIVE MAPPING", 0.62
    else:
        mode, label, weight = "snapshot_mapping", "SNAPSHOT MAPPING", 0.48

    if summary.get("experimental"):
        weight *= 0.72
        label += " · EXP PROXY"

    age = _number(chain.get("age_sec"))
    if age is None and chain.get("ts") is not None:
        age = max(0.0, time.time() - float(chain["ts"]))
    if age is not None:
        if age > 1800:
            weight *= 0.65
        elif age > 900:
            weight *= 0.80

    weight = _clip(weight, 0.20, 0.90)
    return {
        "mode": mode,
        "label": label,
        "weight": round(weight, 3),
        "reason": (
            "weight, not veto: current option shape remains useful but delayed/proxy "
            "mapping must not have the same authority as a fresh direct observation"
        ),
        "proxy_status": proxy_status,
        "chain_status": chain.get("status"),
        "chain_age_sec": _round(age, 1),
        "experimental_proxy": bool(summary.get("experimental")),
        "context_only": False,
    }


def _snapshot(payload: dict) -> dict | None:
    trade = payload.get("trade") or {}
    market = payload.get("market") or {}
    prob = payload.get("prob") or {}
    if not trade or not market:
        return None

    take_r = _number(prob.get("T"))
    if take_r is None:
        entry = _number(trade.get("entry"))
        stop = _number(trade.get("stop"))
        take = _number(trade.get("take"))
        if None not in (entry, stop, take) and entry != stop:
            take_r = abs((take - entry) / (entry - stop))
    take_r = take_r if take_r is not None else 2.5

    buckets = distribution_buckets(
        market.get("scenario_probs"), market.get("scenario_edges"), take_r
    )
    if buckets is None:
        return None

    p_take = _number(market.get("p_take_horizon"))
    if p_take is None:
        p_take = _number(market.get("p_take"))
    p_stop = _number(market.get("p_stop_horizon"))
    if p_stop is None:
        p_stop = _number(market.get("p_stop"))
    no_touch = _number(market.get("p_unresolved_horizon"))
    barrier_ev = _number(market.get("horizon_barrier_ev"))
    if barrier_ev is None:
        barrier_ev = _number(market.get("barrier_ev"))

    q10 = _number(market.get("scenario_p10_r"))
    q50 = _number(market.get("scenario_median_r"))
    q90 = _number(market.get("scenario_p90_r"))
    width = q90 - q10 if q10 is not None and q90 is not None else None

    return {
        "ts": _round(payload.get("ts") or time.time(), 3),
        "r": _round(prob.get("r")),
        "take_r": _round(take_r),
        "p_take": _round(p_take),
        "p_stop": _round(p_stop),
        "no_touch": _round(no_touch),
        "barrier_ev_r": _round(barrier_ev),
        "q10_r": _round(q10),
        "q50_r": _round(q50),
        "q90_r": _round(q90),
        "width_r": _round(width),
        "buckets": buckets,
        "source": _source_quality(payload),
    }


def _signature(trade: dict) -> str:
    values = [
        trade.get("id"), trade.get("direction"), trade.get("entry"),
        trade.get("stop"), trade.get("take"),
    ]
    return json.dumps(values, separators=(",", ":"), ensure_ascii=False)


def _empty_sums() -> dict:
    return {
        "p_take": 0.0, "p_stop": 0.0, "no_touch": 0.0,
        "barrier_ev_r": 0.0, "q10_r": 0.0, "q50_r": 0.0,
        "q90_r": 0.0, "width_r": 0.0,
        "stop_tail": 0.0, "red_zone": 0.0,
        "green_zone": 0.0, "take_tail": 0.0,
    }


def _add_to_sums(state: dict, snap: dict) -> None:
    sums = state["sums"]
    counts = state["value_counts"]
    for key in ("p_take", "p_stop", "no_touch", "barrier_ev_r",
                "q10_r", "q50_r", "q90_r", "width_r"):
        value = _number(snap.get(key))
        if value is not None:
            sums[key] += value
            counts[key] += 1
    for key, value in (snap.get("buckets") or {}).items():
        if key in sums:
            sums[key] += float(value)
            counts[key] += 1


def _average(state: dict) -> dict:
    sums = state["sums"]
    counts = state["value_counts"]
    out = {}
    for key in ("p_take", "p_stop", "no_touch", "barrier_ev_r",
                "q10_r", "q50_r", "q90_r", "width_r"):
        out[key] = _round(sums[key] / counts[key]) if counts[key] else None
    out["buckets"] = {
        key: _round(sums[key] / counts[key], 7) if counts[key] else None
        for key in ("stop_tail", "red_zone", "green_zone", "take_tail")
    }
    return out


def _delta(current: dict, reference: dict) -> dict:
    out = {}
    for key in ("p_take", "p_stop", "no_touch", "barrier_ev_r",
                "q10_r", "q50_r", "q90_r", "width_r"):
        a, b = _number(current.get(key)), _number(reference.get(key))
        out[key] = _round(a - b) if a is not None and b is not None else None
    current_b = current.get("buckets") or {}
    reference_b = reference.get("buckets") or {}
    out["buckets"] = {
        key: _round((_number(current_b.get(key)) or 0.0)
                    - (_number(reference_b.get(key)) or 0.0), 7)
        for key in ("stop_tail", "red_zone", "green_zone", "take_tail")
    }
    return out


def _slope(rows: list[dict], key: str) -> float | None:
    points = [
        (float(row["ts"]), float(row[key]))
        for row in rows
        if _number(row.get("ts")) is not None and _number(row.get(key)) is not None
    ]
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(t - t0) / 60.0 for t, _ in points]
    ys = [y for _, y in points]
    xbar, ybar = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((x - xbar) ** 2 for x in xs)
    if denom <= 1e-12:
        return None
    return sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / denom


def _recent_noise(rows: list[dict], key: str) -> float | None:
    values = [float(row[key]) for row in rows if _number(row.get(key)) is not None]
    return statistics.pstdev(values) if len(values) >= 3 else None


def _direction_consistency(rows: list[dict], key: str) -> float | None:
    values = [float(row[key]) for row in rows if _number(row.get(key)) is not None]
    if len(values) < 4:
        return None
    changes = [b - a for a, b in zip(values, values[1:]) if abs(b - a) > 1e-9]
    if not changes:
        return 0.5
    net = values[-1] - values[0]
    direction = 1 if net >= 0 else -1
    return sum(1 for change in changes if change * direction > 0) / len(changes)


def _score(current: dict, entry: dict, average: dict,
           source_weight: float, sample_count: int,
           p_noise: float | None) -> dict:
    de = _delta(current, entry)
    da = _delta(current, average)
    p_delta = _number(de.get("p_take")) or 0.0
    ev_delta = _number(de.get("barrier_ev_r")) or 0.0
    center_delta = _number(de.get("q50_r")) or 0.0
    width_delta = _number(de.get("width_r")) or 0.0

    # Positive = improving long-trade distribution; negative = deteriorating.
    raw = (
        0.42 * _clip(p_delta / 0.10, -1.5, 1.5)
        + 0.33 * _clip(ev_delta / 0.25, -1.5, 1.5)
        + 0.18 * _clip(center_delta / 0.50, -1.5, 1.5)
        - 0.07 * _clip(width_delta / 0.75, -1.0, 1.0)
    )
    raw = _clip(raw, -1.0, 1.0)
    sample_weight = _clip(math.sqrt(max(sample_count, 1) / 30.0), 0.20, 1.0)
    noise_weight = 1.0
    if p_noise is not None:
        noise_weight = _clip(1.0 - p_noise / 0.12, 0.45, 1.0)
    confidence = _clip(source_weight * sample_weight * noise_weight, 0.10, 0.95)
    weighted = raw * confidence
    if weighted <= -0.20:
        direction = "deteriorating"
    elif weighted >= 0.20:
        direction = "improving"
    else:
        direction = "neutral"
    return {
        "raw": round(raw, 4),
        "weighted": round(weighted, 4),
        "direction": direction,
        "confidence_weight": round(confidence, 3),
        "source_weight": round(source_weight, 3),
        "sample_weight": round(sample_weight, 3),
        "noise_weight": round(noise_weight, 3),
        "components": {
            "p_take_entry_delta": de.get("p_take"),
            "p_take_average_delta": da.get("p_take"),
            "barrier_ev_entry_delta_r": de.get("barrier_ev_r"),
            "center_entry_delta_r": de.get("q50_r"),
            "width_entry_delta_r": de.get("width_r"),
        },
    }


class LatticeRevaluationTracker:
    """Small persisted per-trade online accumulator."""

    def __init__(self, cache=None):
        self.cache = cache
        self.states: dict[str, dict] = {}

    def _cache_key(self, trade_id: Any) -> str:
        return f"lattice_revaluation:{trade_id}"

    def _load(self, trade_id: Any, signature: str) -> dict | None:
        key = str(trade_id)
        if key in self.states:
            state = self.states[key]
            return state if state.get("signature") == signature else None
        if self.cache is None:
            return None
        try:
            cached = self.cache.get(self._cache_key(trade_id))
            state = deepcopy(cached[0]) if cached else None
        except Exception:
            state = None
        if (not isinstance(state, dict)
                or state.get("version") != _STATE_VERSION
                or state.get("signature") != signature):
            return None
        state["recent"] = list(state.get("recent") or [])[-_RECENT_LIMIT:]
        self.states[key] = state
        return state

    def _new(self, trade: dict, snap: dict, signature: str) -> dict:
        state = {
            "version": _STATE_VERSION,
            "trade_id": str(trade.get("id")),
            "signature": signature,
            "started_ts": snap["ts"],
            "last_sample_ts": 0.0,
            "last_persist_ts": 0.0,
            "sample_count": 0,
            "entry": deepcopy(snap),
            "current": deepcopy(snap),
            "sums": _empty_sums(),
            "value_counts": {key: 0 for key in _empty_sums()},
            "recent": [],
        }
        self.states[str(trade.get("id"))] = state
        return state

    def _persist(self, state: dict, now: float) -> None:
        if self.cache is None or now - float(state.get("last_persist_ts") or 0) < _PERSIST_INTERVAL_SEC:
            return
        state["last_persist_ts"] = now
        try:
            self.cache.put(self._cache_key(state["trade_id"]), state, ts=now)
        except Exception:
            pass

    def update(self, payload: dict) -> dict:
        trade = payload.get("trade") or {}
        if not trade or trade.get("id") is None:
            return {"available": False, "reason": "no active trade"}
        snap = _snapshot(payload)
        if snap is None:
            return {
                "available": False,
                "trade_id": trade.get("id"),
                "reason": "terminal distribution unavailable",
            }

        signature = _signature(trade)
        state = self._load(trade["id"], signature)
        if state is None:
            state = self._new(trade, snap, signature)

        now = float(snap["ts"] or time.time())
        should_sample = (
            state["sample_count"] == 0
            or now - float(state.get("last_sample_ts") or 0) >= _SAMPLE_INTERVAL_SEC
        )
        if should_sample:
            state["last_sample_ts"] = now
            state["sample_count"] += 1
            state["current"] = deepcopy(snap)
            _add_to_sums(state, snap)
            recent_row = {
                "ts": snap["ts"],
                "p_take": snap.get("p_take"),
                "barrier_ev_r": snap.get("barrier_ev_r"),
                "q50_r": snap.get("q50_r"),
                "width_r": snap.get("width_r"),
            }
            state["recent"] = (state.get("recent") or [])[-(_RECENT_LIMIT - 1):] + [recent_row]
            self._persist(state, now)
        else:
            # The latest price-sensitive snapshot is still shown, but duplicate
            # API/AI calls inside one second do not bias the average.
            state["current"] = deepcopy(snap)

        entry = deepcopy(state["entry"])
        average = _average(state)
        current = deepcopy(state["current"])
        recent = list(state.get("recent") or [])[-60:]
        p_noise = _recent_noise(recent, "p_take")
        source = current.get("source") or {}
        score = _score(
            current, entry, average,
            float(source.get("weight") or 0.25),
            int(state["sample_count"]),
            p_noise,
        )
        return {
            "available": True,
            "version": "lattice-revaluation-v1",
            "trade_id": trade.get("id"),
            "sample_count": int(state["sample_count"]),
            "age_sec": round(max(0.0, now - float(state["started_ts"])), 1),
            "entry": entry,
            "average": average,
            "current": current,
            "change_from_entry": _delta(current, entry),
            "change_from_average": _delta(current, average),
            "momentum": {
                "p_take_pp_per_min": _round(
                    (_slope(recent, "p_take") or 0.0) * 100.0, 4
                ) if len(recent) >= 3 else None,
                "barrier_ev_r_per_min": _round(
                    _slope(recent, "barrier_ev_r"), 5
                ),
                "center_r_per_min": _round(_slope(recent, "q50_r"), 5),
                "p_take_noise_pp": _round(
                    p_noise * 100.0 if p_noise is not None else None, 4
                ),
                "direction_consistency": _round(
                    _direction_consistency(recent, "p_take"), 4
                ),
            },
            "score": score,
            "source_quality": source,
            "interpretation": (
                "Derived metrics are weighted evidence from the same option-distribution "
                "family. They can alter the family direction/confidence but never count "
                "as several independent votes."
            ),
        }


def wrap_engine(engine) -> LatticeRevaluationTracker:
    """Attach the tracker to one Engine instance, idempotently."""
    existing = getattr(engine, "_lattice_revaluation_tracker", None)
    if existing is not None:
        return existing
    tracker = LatticeRevaluationTracker(getattr(engine, "cache", None))
    base_tick_payload: Callable[[], dict] = engine.tick_payload

    def tick_payload_with_revaluation() -> dict:
        payload = base_tick_payload()
        payload["lattice_revaluation"] = tracker.update(payload)
        return payload

    engine.tick_payload = tick_payload_with_revaluation
    engine._lattice_revaluation_tracker = tracker
    engine._base_tick_payload_without_revaluation = base_tick_payload
    return tracker


def install_lattice_revaluation(app) -> None:
    """Install engine tracking and inject the standalone lattice UI module."""
    if getattr(app.state, "lattice_revaluation_installed", False):
        return
    app.state.lattice_revaluation_installed = True
    app.state.lattice_revaluation_tracker = wrap_engine(app.state.engine)

    @app.middleware("http")
    async def inject_lattice_revaluation_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/" or response.status_code != 200:
            return response
        media = str(response.headers.get("content-type") or "")
        if "text/html" not in media:
            return response
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
        body = b"".join(chunks).decode("utf-8")
        if _SCRIPT_TAG not in body:
            body = body.replace("</body>", f"{_SCRIPT_TAG}\n</body>")
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("etag", None)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
        )
