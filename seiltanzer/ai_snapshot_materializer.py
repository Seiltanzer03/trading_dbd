"""Bound the AI Verdict HTTP path by materializing the full deterministic snapshot.

The expensive policy/CVaR/derivative calculation is unchanged.  It is executed by
one daemon worker outside the HTTP request, never concurrently.  `/api/ai/verdict`
keeps calling ``seiltanzer.app.build_snapshot``; this installer replaces that
module-global with a cache-only proxy after retaining the original builder for the
worker.  A stale/wrong-trade cache is never used: callers get a fast structured
503 while the worker refreshes instead of waiting long enough for a gateway 504.
"""
from __future__ import annotations

import copy
import gc
import threading
import time
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse


MATERIALIZER_VERSION = "ai-snapshot-materializer-v1"
DEFAULT_REFRESH_INTERVAL_SEC = 45.0
DEFAULT_MAX_AGE_SEC = 150.0
DEFAULT_STARTUP_DELAY_SEC = 12.0


class SnapshotNotReady(RuntimeError):
    def __init__(self, status: dict[str, Any]):
        super().__init__("AI_SNAPSHOT_WARMING")
        self.status = status


def _trade_id(trade: Any) -> str | None:
    if not isinstance(trade, dict):
        return None
    value = trade.get("id")
    return str(value) if value not in (None, "") else None


class AISnapshotMaterializer:
    def __init__(
        self,
        engine: Any,
        builder: Callable[[Any], dict[str, Any]],
        *,
        refresh_interval_sec: float = DEFAULT_REFRESH_INTERVAL_SEC,
        max_age_sec: float = DEFAULT_MAX_AGE_SEC,
        startup_delay_sec: float = DEFAULT_STARTUP_DELAY_SEC,
    ) -> None:
        self.engine = engine
        self.builder = builder
        self.refresh_interval_sec = max(10.0, float(refresh_interval_sec))
        self.max_age_sec = max(self.refresh_interval_sec + 10.0, float(max_age_sec))
        self.startup_delay_sec = max(0.0, float(startup_delay_sec))
        self._lock = threading.RLock()
        self._refresh = threading.Event()
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

    def current_trade_id(self) -> str | None:
        try:
            return _trade_id(self.engine.journal.active_trade())
        except Exception:
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
        self._refresh.set()

    def request_refresh(self) -> None:
        self._refresh.set()

    def _run(self) -> None:
        if self.startup_delay_sec and self._stop.wait(self.startup_delay_sec):
            return
        while not self._stop.is_set():
            self._build_once()
            self._refresh.wait(self.refresh_interval_sec)
            self._refresh.clear()

    def _build_once(self) -> None:
        current_trade = self.current_trade_id()
        if current_trade is None:
            with self._lock:
                self._snapshot = None
                self._snapshot_trade_id = None
                self._built_at = time.time()
                self._build_ms = 0.0
                self._last_error = None
            return
        started_wall = time.time()
        started_mono = time.monotonic()
        with self._lock:
            self._building = True
            self._build_started_at = started_wall
        try:
            snapshot = self.builder(self.engine)
            snapshot_trade = _trade_id((snapshot or {}).get("trade"))
            if not isinstance(snapshot, dict) or not snapshot.get("available") or snapshot_trade != current_trade:
                raise RuntimeError("BUILT_SNAPSHOT_TRADE_MISMATCH_OR_UNAVAILABLE")
            finished = time.time()
            build_ms = (time.monotonic() - started_mono) * 1000.0
            annotated = copy.deepcopy(snapshot)
            annotated["materialization"] = {
                "version": MATERIALIZER_VERSION,
                "built_at": finished,
                "build_ms": round(build_ms, 1),
                "trade_id": snapshot_trade,
                "request_path_recomputed": False,
                "deterministic_snapshot": True,
            }
            with self._lock:
                self._snapshot = annotated
                self._snapshot_trade_id = snapshot_trade
                self._built_at = finished
                self._build_ms = build_ms
                self._last_error = None
                self._build_n += 1
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}:{str(exc)[:220]}"
        finally:
            with self._lock:
                self._building = False
            # The 2 GB host must not retain temporary policy/simulation objects.
            gc.collect()

    def status(self) -> dict[str, Any]:
        now = time.time()
        current_trade = self.current_trade_id()
        with self._lock:
            age = (max(0.0, now - self._built_at) if self._built_at is not None else None)
            ready = bool(
                self._snapshot is not None
                and self._snapshot_trade_id is not None
                and self._snapshot_trade_id == current_trade
                and age is not None
                and age <= self.max_age_sec
            )
            return {
                "version": MATERIALIZER_VERSION,
                "ready": ready,
                "building": self._building,
                "current_trade_id": current_trade,
                "snapshot_trade_id": self._snapshot_trade_id,
                "age_sec": round(age, 3) if age is not None else None,
                "max_age_sec": self.max_age_sec,
                "refresh_interval_sec": self.refresh_interval_sec,
                "build_ms": round(self._build_ms, 1) if self._build_ms is not None else None,
                "build_started_at": self._build_started_at,
                "build_n": self._build_n,
                "last_error": self._last_error,
                "request_path_heavy_build": False,
            }

    def cached_build_snapshot(self, engine: Any) -> dict[str, Any]:
        """Drop-in cache-only replacement for app.build_snapshot."""
        if engine is not self.engine:
            # Do not silently serve a snapshot across independent app instances.
            raise SnapshotNotReady({**self.status(), "reason": "ENGINE_MISMATCH"})
        current_trade = self.current_trade_id()
        now = time.time()
        with self._lock:
            snapshot = self._snapshot
            snapshot_trade = self._snapshot_trade_id
            built_at = self._built_at
            age = max(0.0, now - built_at) if built_at is not None else None
            valid = bool(
                snapshot is not None
                and current_trade is not None
                and snapshot_trade == current_trade
                and age is not None
                and age <= self.max_age_sec
            )
            if valid:
                return copy.deepcopy(snapshot)
        self.request_refresh()
        reason = "NO_ACTIVE_TRADE" if current_trade is None else (
            "TRADE_CHANGED" if snapshot_trade and snapshot_trade != current_trade else
            "SNAPSHOT_STALE" if age is not None else "SNAPSHOT_WARMING"
        )
        raise SnapshotNotReady({**self.status(), "reason": reason})


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

    # Import after create_app so we capture the authoritative full builder before
    # replacing the module-global referenced by the already-created endpoint.
    from . import app as app_module

    engine = _engine_from_ai_route(app)
    original_builder = app_module.build_snapshot
    materializer = AISnapshotMaterializer(engine, original_builder)
    app.state.ai_snapshot_materializer = materializer
    app_module.build_snapshot = materializer.cached_build_snapshot

    @app.exception_handler(SnapshotNotReady)
    async def _snapshot_not_ready(_request: Request, exc: SnapshotNotReady):
        return JSONResponse(
            {
                "error": "ai_snapshot_warming",
                "message": "Deterministic trade analysis is being refreshed; no stale/wrong-trade snapshot was used.",
                "retryable": True,
                "retry_after_sec": 3,
                "snapshot": exc.status,
            },
            status_code=503,
            headers={"Retry-After": "3"},
        )

    @app.get("/api/ai/snapshot/status")
    def ai_snapshot_status():
        return materializer.status()

    materializer.start()
    return materializer
