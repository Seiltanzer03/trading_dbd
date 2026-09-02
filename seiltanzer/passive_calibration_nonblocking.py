"""Serve passive calibration from a last-good process-local materialization.

``research_scalability`` already replaces the legacy full-history calibration with
bounded SQL, but those queries still share the passive writer lock. Under startup
or research contention an HTTP reader can therefore wait behind unrelated work.
This refinement seeds one exact lightweight report before uvicorn starts and
refreshes it off the request path while readers always consume the last good copy.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import os
import threading
import time
import types
from collections.abc import Callable
from typing import Any


DEFAULT_REFRESH_SEC = 60.0
REFRESH_ENV = "SEILTANZER_PASSIVE_CALIBRATION_REFRESH_SEC"


class PassiveCalibrationCache:
    """Single-flight last-good cache around the already-bounded calibration call."""

    def __init__(self, source: Callable[[], dict[str, Any]], *, refresh_sec: float):
        self._source = source
        self.refresh_sec = max(15.0, float(refresh_sec))
        self._snapshot_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._refreshed_at: float | None = None
        self._last_error: str | None = None
        self._refresh_n = 0

    def refresh_sync(self, *, required: bool = False) -> bool:
        """Refresh away from HTTP; preserve the previous good payload on error."""
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            try:
                payload = self._source()
                if not isinstance(payload, dict):
                    raise TypeError("passive calibration source must return a dict")
            except Exception as exc:
                with self._snapshot_lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                if required:
                    raise
                return False
            with self._snapshot_lock:
                self._payload = copy.deepcopy(payload)
                self._refreshed_at = time.time()
                self._last_error = None
                self._refresh_n += 1
            return True
        finally:
            self._refresh_lock.release()

    def get(self) -> dict[str, Any]:
        """Return the last exact materialized report without touching SQLite."""
        with self._snapshot_lock:
            if self._payload is None:
                raise RuntimeError("passive calibration materialization is not ready")
            return copy.deepcopy(self._payload)

    def status(self) -> dict[str, Any]:
        with self._snapshot_lock:
            return {
                "ready": self._payload is not None,
                "refreshed_at": self._refreshed_at,
                "last_error": self._last_error,
                "refresh_n": self._refresh_n,
                "refresh_sec": self.refresh_sec,
                "request_path_sqlite": False,
                "last_good_on_refresh_error": True,
            }


def install_passive_calibration_nonblocking(app) -> PassiveCalibrationCache:
    """Patch only passive calibration after the canonical scalability refinement."""
    existing = getattr(app.state, "passive_calibration_cache", None)
    if existing is not None:
        return existing

    passive = app.state.engine.passive
    source = passive.calibration_report
    refresh_sec = float(os.environ.get(REFRESH_ENV, str(DEFAULT_REFRESH_SEC)))
    cache = PassiveCalibrationCache(source, refresh_sec=refresh_sec)

    # Seed before uvicorn/lifespan background workers start. If even the bounded
    # source cannot produce one truthful report, startup remains fail-closed.
    cache.refresh_sync(required=True)

    def cached_calibration(_self):
        return cache.get()

    passive.calibration_report = types.MethodType(cached_calibration, passive)
    app.state.passive_calibration_cache = cache

    original_lifespan = app.router.lifespan_context

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(cache.refresh_sec)
            await asyncio.to_thread(cache.refresh_sync)

    @contextlib.asynccontextmanager
    async def nonblocking_calibration_lifespan(inner_app):
        refresh_task: asyncio.Task | None = None
        async with original_lifespan(inner_app):
            refresh_task = asyncio.create_task(refresh_loop())
            try:
                yield
            finally:
                refresh_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await refresh_task

    app.router.lifespan_context = nonblocking_calibration_lifespan
    return cache
