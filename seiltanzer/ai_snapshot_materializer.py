"""Materialize the expensive deterministic AI snapshot outside the HTTP path.

The Position Manager already tells the trader when a new review is useful:
new option chain, roughly +/-0.15R from the reviewed state, a strategy/risk
boundary, cancellation boundary, or a trade edit.  Re-running the full 6,500-path
policy stack every few seconds would only burn the 2 GB VPS.  This module therefore
keeps one full snapshot and rebuilds it only when one of those review events occurs.

The heavy policy/CVaR math is unchanged and remains authoritative.  The HTTP route
only reads a same-trade, non-invalidated snapshot and then optionally asks the LLM
to explain it.  While an event-triggered rebuild is in progress callers receive a
fast retryable 503 rather than sitting behind the reverse proxy until HTTP 504.
"""
from __future__ import annotations

import copy
import gc
import math
import threading
import time
from typing import Any, Callable


MATERIALIZER_VERSION = "ai-snapshot-materializer-v2-event-driven"
REVIEW_DELTA_R = 0.15
DEFAULT_WATCH_INTERVAL_SEC = 2.0
DEFAULT_STARTUP_DELAY_SEC = 8.0


class SnapshotNotReady(RuntimeError):
    def __init__(self, status: dict[str, Any]):
        super().__init__("AI_SNAPSHOT_WARMING")
        self.status = status


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _trade_id(trade: Any) -> str | None:
    if not isinstance(trade, dict):
        return None
    value = trade.get("id")
    return str(value) if value not in (None, "") else None


def _price_from_r(trade: dict[str, Any], r_value: float | None) -> float | None:
    r_value = _finite(r_value)
    entry = _finite(trade.get("entry"))
    stop = _finite(trade.get("stop"))
    if r_value is None or entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    sign = 1.0 if str(trade.get("direction") or "long").lower() == "long" else -1.0
    return entry + sign * r_value * risk


def _r_from_price(trade: dict[str, Any], price: float | None) -> float | None:
    price = _finite(price)
    entry = _finite(trade.get("entry"))
    stop = _finite(trade.get("stop"))
    if price is None or entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    sign = 1.0 if str(trade.get("direction") or "long").lower() == "long" else -1.0
    return sign * (price - entry) / risk


def _crossed(left: float | None, right: float | None, boundary: float | None) -> bool:
    left, right, boundary = _finite(left), _finite(right), _finite(boundary)
    if left is None or right is None or boundary is None:
        return False
    return (left < boundary <= right) or (right <= boundary < left)


class AISnapshotMaterializer:
    def __init__(
        self,
        engine: Any,
        builder: Callable[[Any], dict[str, Any]],
        *,
        watch_interval_sec: float = DEFAULT_WATCH_INTERVAL_SEC,
        startup_delay_sec: float = DEFAULT_STARTUP_DELAY_SEC,
        review_delta_r: float = REVIEW_DELTA_R,
    ) -> None:
        self.engine = engine
        self.builder = builder
        self.watch_interval_sec = max(0.5, float(watch_interval_sec))
        self.startup_delay_sec = max(0.0, float(startup_delay_sec))
        self.review_delta_r = max(0.05, float(review_delta_r))
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_trade_id: str | None = None
        self._built_at: float | None = None
        self._build_started_at: float | None = None
        self._build_ms: float | None = None
        self._building = False
        self._last_error: str | None = None
        self._build_n = 0
        self._invalidated_reason: str | None = "INITIAL_BUILD"
        self._chain_marker: str | None = None
        self._baseline_r: float | None = None
        self._boundaries_r: tuple[float, ...] = ()

    def current_trade(self) -> dict[str, Any] | None:
        try:
            trade = self.engine.journal.active_trade()
            return dict(trade) if isinstance(trade, dict) else None
        except Exception:
            return None

    def current_trade_id(self) -> str | None:
        return _trade_id(self.current_trade())

    def _current_price(self, trade: dict[str, Any] | None) -> float | None:
        if not trade:
            return None
        try:
            value = self.engine._current_instrument_price(trade)
            value = _finite(value)
            if value is not None:
                return value
        except Exception:
            pass
        market = getattr(self.engine, "market", None)
        row = getattr(market, "price", None)
        return _finite(row.get("value")) if isinstance(row, dict) else None

    def _current_r(self, trade: dict[str, Any] | None) -> float | None:
        return _r_from_price(trade or {}, self._current_price(trade))

    def _current_chain_marker(self) -> str | None:
        """Return a cheap identity for the already-fetched option chain.

        No network call is made here.  The market collector owns refresh cadence;
        we only notice when its current in-memory chain changes.
        """
        market = getattr(self.engine, "market", None)
        chain = getattr(market, "chain", None)
        if not isinstance(chain, dict):
            return None
        for key in ("asof", "as_of", "asof_ts", "ts", "fetched_at", "updated_at", "expiry"):
            value = chain.get(key)
            if value not in (None, ""):
                return f"{key}:{value}"
        source = chain.get("source")
        age = _finite(chain.get("age_sec"))
        if source not in (None, "") and age is not None:
            # Coarse fallback: 60-second bucket prevents age ticking itself from
            # generating a rebuild every watcher iteration.
            bucket = int(max(0.0, time.time() - age) // 60)
            return f"source:{source}|minute:{bucket}"
        return None

    @staticmethod
    def _snapshot_boundaries(snapshot: dict[str, Any], trade: dict[str, Any]) -> tuple[float, ...]:
        manager = snapshot.get("policy_manager") or {}
        recommendation = manager.get("recommendation") or {}
        cancellation = manager.get("cancellation_boundary") or {}
        switch = cancellation.get("hold_switch") if cancellation.get("available") else None
        clock = manager.get("first_touch_clock") or {}
        values = [
            recommendation.get("next_rung_r"),
            (switch or {}).get("r") if isinstance(switch, dict) else None,
            clock.get("risk_barrier_r"),
        ]
        take = _finite(trade.get("take"))
        if take is not None:
            values.append(_r_from_price(trade, take))
        output = []
        for value in values:
            number = _finite(value)
            if number is not None and all(abs(number - old) > 1e-9 for old in output):
                output.append(number)
        return tuple(sorted(output))

    def _annotate_review_trigger(self, snapshot: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
        geometry = snapshot.get("trade_geometry") or {}
        baseline_r = _finite(geometry.get("current_r"))
        if baseline_r is None:
            baseline_r = self._current_r(trade)
        lower_r = baseline_r - self.review_delta_r if baseline_r is not None else None
        upper_r = baseline_r + self.review_delta_r if baseline_r is not None else None
        boundaries = self._snapshot_boundaries(snapshot, trade)
        return {
            "contract_version": "ai-next-review-trigger-v1",
            "baseline_r": round(baseline_r, 6) if baseline_r is not None else None,
            "movement_delta_r": self.review_delta_r,
            "lower_r": round(lower_r, 6) if lower_r is not None else None,
            "upper_r": round(upper_r, 6) if upper_r is not None else None,
            "lower_price": round(_price_from_r(trade, lower_r), 6) if lower_r is not None and _price_from_r(trade, lower_r) is not None else None,
            "upper_price": round(_price_from_r(trade, upper_r), 6) if upper_r is not None and _price_from_r(trade, upper_r) is not None else None,
            "boundary_r": [round(x, 6) for x in boundaries],
            "also_on": [
                "NEW_OPTION_CHAIN", "STRATEGY_OR_RISK_BOUNDARY",
                "CANCELLATION_BOUNDARY", "TRADE_EDIT_OR_REPLACEMENT",
            ],
            "periodic_heavy_recompute": False,
        }

    def _event_reason(self) -> str | None:
        trade = self.current_trade()
        current_trade = _trade_id(trade)
        with self._lock:
            snapshot = self._snapshot
            snapshot_trade = self._snapshot_trade_id
            baseline_r = self._baseline_r
            boundaries = self._boundaries_r
            chain_marker = self._chain_marker
            invalidated = self._invalidated_reason
        if invalidated:
            return invalidated
        if current_trade is None:
            return "NO_ACTIVE_TRADE" if snapshot is not None else None
        if snapshot is None:
            return "INITIAL_BUILD"
        if snapshot_trade != current_trade:
            return "TRADE_CHANGED"
        live_r = self._current_r(trade)
        if baseline_r is not None and live_r is not None:
            if abs(live_r - baseline_r) >= self.review_delta_r - 1e-9:
                return "PRICE_MOVED_0_15R"
            if any(_crossed(baseline_r, live_r, boundary) for boundary in boundaries):
                return "STRATEGY_OR_RISK_BOUNDARY_CROSSED"
        live_chain = self._current_chain_marker()
        if chain_marker is not None and live_chain is not None and live_chain != chain_marker:
            return "NEW_OPTION_CHAIN"
        return None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="ai-snapshot-materializer",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_refresh(self, reason: str = "EXPLICIT_REFRESH") -> None:
        with self._lock:
            self._invalidated_reason = str(reason)
        self._wake.set()

    def _run(self) -> None:
        if self.startup_delay_sec and self._stop.wait(self.startup_delay_sec):
            return
        while not self._stop.is_set():
            reason = self._event_reason()
            if reason and reason != "NO_ACTIVE_TRADE":
                self.request_refresh(reason)
                self._build_once()
            elif reason == "NO_ACTIVE_TRADE":
                with self._lock:
                    self._snapshot = None
                    self._snapshot_trade_id = None
                    self._baseline_r = None
                    self._boundaries_r = ()
                    self._chain_marker = None
                    self._invalidated_reason = None
            self._wake.wait(self.watch_interval_sec)
            self._wake.clear()

    def _build_once(self) -> None:
        current_trade_row = self.current_trade()
        current_trade = _trade_id(current_trade_row)
        if current_trade is None:
            with self._lock:
                self._snapshot = None
                self._snapshot_trade_id = None
                self._built_at = time.time()
                self._build_ms = 0.0
                self._last_error = None
                self._invalidated_reason = None
            return
        started_wall = time.time()
        started_mono = time.monotonic()
        with self._lock:
            if self._building:
                return
            self._building = True
            self._build_started_at = started_wall
        try:
            snapshot = self.builder(self.engine)
            snapshot_trade = str(snapshot.get("trade_id")) if isinstance(snapshot, dict) and snapshot.get("trade_id") is not None else None
            if not isinstance(snapshot, dict) or snapshot_trade != current_trade or not isinstance(snapshot.get("policy_manager"), dict):
                raise RuntimeError("BUILT_SNAPSHOT_TRADE_MISMATCH_OR_UNAVAILABLE")
            # Re-read trade because policy construction may synchronize BE/state.
            final_trade = self.current_trade() or current_trade_row or {}
            trigger = self._annotate_review_trigger(snapshot, final_trade)
            finished = time.time()
            build_ms = (time.monotonic() - started_mono) * 1000.0
            annotated = copy.deepcopy(snapshot)
            annotated["next_review_trigger"] = trigger
            annotated["materialization"] = {
                "version": MATERIALIZER_VERSION,
                "built_at": finished,
                "build_ms": round(build_ms, 1),
                "trade_id": snapshot_trade,
                "request_path_recomputed": False,
                "deterministic_snapshot": True,
                "event_driven": True,
                "periodic_heavy_recompute": False,
            }
            boundaries = self._snapshot_boundaries(snapshot, final_trade)
            baseline_r = _finite(trigger.get("baseline_r"))
            chain_marker = self._current_chain_marker()
            with self._lock:
                self._snapshot = annotated
                self._snapshot_trade_id = snapshot_trade
                self._built_at = finished
                self._build_ms = build_ms
                self._last_error = None
                self._build_n += 1
                self._baseline_r = baseline_r
                self._boundaries_r = boundaries
                self._chain_marker = chain_marker
                self._invalidated_reason = None
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}:{str(exc)[:220]}"
                # Keep invalidated=true so the worker retries; never silently
                # mark an old crossed-threshold snapshot current again.
                if self._invalidated_reason is None:
                    self._invalidated_reason = "BUILD_FAILED_RETRYING"
        finally:
            with self._lock:
                self._building = False
            gc.collect()

    def status(self) -> dict[str, Any]:
        now = time.time()
        current_trade = self.current_trade_id()
        live_r = self._current_r(self.current_trade())
        with self._lock:
            age = max(0.0, now - self._built_at) if self._built_at is not None else None
            ready = bool(
                self._snapshot is not None
                and self._snapshot_trade_id is not None
                and self._snapshot_trade_id == current_trade
                and self._invalidated_reason is None
            )
            return {
                "version": MATERIALIZER_VERSION,
                "ready": ready,
                "building": self._building,
                "current_trade_id": current_trade,
                "snapshot_trade_id": self._snapshot_trade_id,
                "age_sec": round(age, 3) if age is not None else None,
                "watch_interval_sec": self.watch_interval_sec,
                "review_delta_r": self.review_delta_r,
                "baseline_r": round(self._baseline_r, 6) if self._baseline_r is not None else None,
                "live_r": round(live_r, 6) if live_r is not None else None,
                "boundary_r": [round(x, 6) for x in self._boundaries_r],
                "invalidated_reason": self._invalidated_reason,
                "build_ms": round(self._build_ms, 1) if self._build_ms is not None else None,
                "build_started_at": self._build_started_at,
                "build_n": self._build_n,
                "last_error": self._last_error,
                "request_path_heavy_build": False,
                "periodic_heavy_recompute": False,
            }

    def cached_build_snapshot(self, engine: Any) -> dict[str, Any]:
        """Drop-in cache-only replacement for app.build_snapshot."""
        if engine is not self.engine:
            raise SnapshotNotReady({**self.status(), "reason": "ENGINE_MISMATCH"})
        reason = self._event_reason()
        if reason:
            if reason != "NO_ACTIVE_TRADE":
                self.request_refresh(reason)
            raise SnapshotNotReady({**self.status(), "reason": reason})
        with self._lock:
            if self._snapshot is not None:
                return copy.deepcopy(self._snapshot)
        self.request_refresh("INITIAL_BUILD")
        raise SnapshotNotReady({**self.status(), "reason": "SNAPSHOT_WARMING"})


def _engine_from_ai_route(app: Any) -> Any:
    for route in getattr(app, "routes", []):
        if getattr(route, "path", None) != "/api/ai/verdict":
            continue
        endpoint = getattr(route, "endpoint", None)
        freevars = getattr(getattr(endpoint, "__code__", None), "co_freevars", ())
        closure = getattr(endpoint, "__closure__", None) or ()
        values = {name: cell.cell_contents for name, cell in zip(freevars, closure)}
        engine = values.get("engine")
        if engine is not None:
            return engine
    raise RuntimeError("AI_VERDICT_ENGINE_NOT_FOUND")


def install_ai_snapshot_materializer(app: Any) -> AISnapshotMaterializer:
    """Install one production materializer without changing deterministic math."""
    existing = getattr(app.state, "ai_snapshot_materializer", None)
    if existing is not None:
        return existing
    from . import app as app_module

    engine = _engine_from_ai_route(app)
    original_builder = app_module.build_snapshot
    materializer = AISnapshotMaterializer(engine, original_builder)
    app.state.ai_snapshot_materializer = materializer
    app_module.build_snapshot = materializer.cached_build_snapshot

    @app.get("/api/ai/snapshot/status")
    def ai_snapshot_status():
        return materializer.status()

    materializer.start()
    return materializer
