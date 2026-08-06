"""Persist full Probability Lattice shapes on one stable actionable R grid.

The existing revaluation tracker stores scalar diagnostics for AI authority.  This
companion keeps the actual terminal distribution required by the canvas:
entry, time-average and current shape, plus true mass outside the visible window.
"""
from __future__ import annotations

import json
import math
import time
from copy import deepcopy
from typing import Any, Callable

_STATE_VERSION = 1
_BINS = 24
_SAMPLE_INTERVAL_SEC = 1.0
_PERSIST_INTERVAL_SEC = 10.0


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _normalise(probs: Any, edges: Any) -> tuple[list[float], list[float]] | None:
    if not isinstance(probs, list) or not isinstance(edges, list):
        return None
    if not probs or len(edges) != len(probs) + 1:
        return None
    p = [max(0.0, _number(value) or 0.0) for value in probs]
    e = [_number(value) for value in edges]
    if any(value is None for value in e):
        return None
    e = [float(value) for value in e]
    if any(b <= a for a, b in zip(e, e[1:])):
        return None
    total = sum(p)
    if total <= 0:
        return None
    return [value / total for value in p], e


def _grid(take_r: float) -> list[float]:
    lo = -2.0
    hi = max(1.25, math.ceil((max(0.25, take_r) + 1.0) * 4.0) / 4.0)
    return [lo + (hi - lo) * index / _BINS for index in range(_BINS + 1)]


def rebin_visual_distribution(probs: Any, edges: Any, target_edges: list[float]) -> dict | None:
    """Rebin without folding outer support into the first/last visible bin."""
    source = _normalise(probs, edges)
    if source is None or len(target_edges) < 2:
        return None
    src_probs, src_edges = source
    out = [0.0] * (len(target_edges) - 1)
    lo, hi = target_edges[0], target_edges[-1]
    left_tail = 0.0
    right_tail = 0.0
    for p, a, b in zip(src_probs, src_edges, src_edges[1:]):
        width = b - a
        if p <= 0 or width <= 0:
            continue
        if b <= lo:
            left_tail += p
            continue
        if a >= hi:
            right_tail += p
            continue
        if a < lo:
            left_tail += p * (lo - a) / width
        if b > hi:
            right_tail += p * (b - hi) / width
        clipped_a, clipped_b = max(a, lo), min(b, hi)
        if clipped_b <= clipped_a:
            continue
        for index, (ta, tb) in enumerate(zip(target_edges, target_edges[1:])):
            overlap = max(0.0, min(clipped_b, tb) - max(clipped_a, ta))
            if overlap:
                out[index] += p * overlap / width
    visible_mass = sum(out)
    conditional = [value / visible_mass for value in out] if visible_mass > 0 else out
    return {
        "edges": [round(value, 6) for value in target_edges],
        "probs": [round(value, 8) for value in conditional],
        "absolute_probs": [round(value, 8) for value in out],
        "visible_mass": round(visible_mass, 8),
        "left_tail": round(left_tail, 8),
        "right_tail": round(right_tail, 8),
    }


def _signature(trade: dict, edges: list[float]) -> str:
    return json.dumps([
        trade.get("id"), trade.get("direction"), trade.get("entry"),
        trade.get("stop"), trade.get("take"), edges,
    ], separators=(",", ":"), ensure_ascii=False)


def _snapshot(payload: dict, target_edges: list[float]) -> dict | None:
    market = payload.get("market") or {}
    distribution = rebin_visual_distribution(
        market.get("scenario_probs"), market.get("scenario_edges"), target_edges,
    )
    if distribution is None or not distribution["visible_mass"]:
        return None
    return {
        "ts": round(float(payload.get("ts") or time.time()), 3),
        **distribution,
    }


class LatticeVisualHistoryTracker:
    def __init__(self, cache=None):
        self.cache = cache
        self.states: dict[str, dict] = {}

    @staticmethod
    def _cache_key(trade_id: Any) -> str:
        return f"lattice_visual_history:{trade_id}"

    def _load(self, trade_id: Any, signature: str) -> dict | None:
        key = str(trade_id)
        state = self.states.get(key)
        if state is None and self.cache is not None:
            try:
                cached = self.cache.get(self._cache_key(trade_id))
                state = deepcopy(cached[0]) if cached else None
            except Exception:
                state = None
        if (not isinstance(state, dict)
                or state.get("version") != _STATE_VERSION
                or state.get("signature") != signature):
            return None
        self.states[key] = state
        return state

    def _persist(self, state: dict, now: float) -> None:
        if self.cache is None:
            return
        if now - float(state.get("last_persist_ts") or 0.0) < _PERSIST_INTERVAL_SEC:
            return
        state["last_persist_ts"] = now
        try:
            self.cache.put(self._cache_key(state["trade_id"]), state, ts=now)
        except Exception:
            pass

    def update(self, payload: dict) -> dict:
        trade = payload.get("trade") or {}
        if trade.get("id") is None:
            return {"available": False, "reason": "no active trade"}
        take_r = _number((payload.get("prob") or {}).get("T"))
        if take_r is None:
            entry = _number(trade.get("entry"))
            stop = _number(trade.get("stop"))
            take = _number(trade.get("take"))
            if None not in (entry, stop, take) and entry != stop:
                take_r = abs((take - entry) / (entry - stop))
        take_r = take_r if take_r is not None else 2.5
        edges = _grid(take_r)
        snap = _snapshot(payload, edges)
        if snap is None:
            return {
                "available": False,
                "trade_id": trade.get("id"),
                "reason": "terminal distribution unavailable",
            }
        signature = _signature(trade, edges)
        state = self._load(trade["id"], signature)
        if state is None:
            state = {
                "version": _STATE_VERSION,
                "trade_id": str(trade["id"]),
                "signature": signature,
                "started_ts": snap["ts"],
                "last_sample_ts": 0.0,
                "last_persist_ts": 0.0,
                "sample_count": 0,
                "entry": deepcopy(snap),
                "current": deepcopy(snap),
                "sum_probs": [0.0] * len(snap["probs"]),
                "sum_visible_mass": 0.0,
                "sum_left_tail": 0.0,
                "sum_right_tail": 0.0,
            }
            self.states[str(trade["id"])] = state
        now = float(snap["ts"])
        should_sample = (
            state["sample_count"] == 0
            or now - float(state.get("last_sample_ts") or 0.0) >= _SAMPLE_INTERVAL_SEC
        )
        state["current"] = deepcopy(snap)
        if should_sample:
            state["last_sample_ts"] = now
            state["sample_count"] += 1
            for index, value in enumerate(snap["probs"]):
                state["sum_probs"][index] += value
            state["sum_visible_mass"] += snap["visible_mass"]
            state["sum_left_tail"] += snap["left_tail"]
            state["sum_right_tail"] += snap["right_tail"]
            self._persist(state, now)
        count = max(1, int(state["sample_count"]))
        average_probs = [value / count for value in state["sum_probs"]]
        avg_total = sum(average_probs)
        if avg_total > 0:
            average_probs = [value / avg_total for value in average_probs]
        average = {
            "edges": snap["edges"],
            "probs": [round(value, 8) for value in average_probs],
            "visible_mass": round(state["sum_visible_mass"] / count, 8),
            "left_tail": round(state["sum_left_tail"] / count, 8),
            "right_tail": round(state["sum_right_tail"] / count, 8),
        }
        entry = deepcopy(state["entry"])
        current = deepcopy(state["current"])
        return {
            "available": True,
            "version": "lattice-visual-history-v1",
            "trade_id": trade["id"],
            "sample_count": count,
            "age_sec": round(max(0.0, now - float(state["started_ts"])), 1),
            "entry": entry,
            "average": average,
            "current": current,
            "delta_probs_from_entry": [
                round(cur - old, 8)
                for cur, old in zip(current["probs"], entry["probs"])
            ],
        }


def wrap_engine_visual_history(engine) -> LatticeVisualHistoryTracker:
    existing = getattr(engine, "_lattice_visual_history_tracker", None)
    if existing is not None:
        return existing
    tracker = LatticeVisualHistoryTracker(getattr(engine, "cache", None))
    base_tick_payload: Callable[[], dict] = engine.tick_payload

    def tick_payload_with_visual_history() -> dict:
        payload = base_tick_payload()
        payload["lattice_visual_history"] = tracker.update(payload)
        return payload

    engine.tick_payload = tick_payload_with_visual_history
    engine._lattice_visual_history_tracker = tracker
    return tracker


def install_lattice_visual_history(app) -> None:
    if getattr(app.state, "lattice_visual_history_installed", False):
        return
    app.state.lattice_visual_history_installed = True
    app.state.lattice_visual_history_tracker = wrap_engine_visual_history(app.state.engine)
