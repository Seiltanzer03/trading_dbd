"""Serve full validation from a persisted single-flight last-good materialization.

Production validation rebuilds Q calibration and counterfactual history.  A client
transport timeout does not cancel a synchronous FastAPI worker, so repeated
readiness/browser requests could leave several full-history calculations alive
inside the 2 GiB web process.  This module performs that calculation only away
from the request path, persists the genuine JSON-compatible result atomically,
and keeps serving the previous good generation if a refresh is deferred/fails.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder

from .production_resource_guard import memory_pressure_state, trim_memory_for_pressure


VALIDATION_CACHE_VERSION = "validation-last-good-v1"
DEFAULT_REFRESH_SEC = 15 * 60.0
REFRESH_ENV = "SEILTANZER_VALIDATION_REFRESH_SEC"
CACHE_FILENAME = "validation_last_good.json"


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=True,
    ).encode("utf-8")


class ValidationCache:
    def __init__(
        self,
        source: Callable[[], dict[str, Any]],
        *,
        path: Path,
        refresh_sec: float,
    ) -> None:
        self._source = source
        self.path = path
        self.refresh_sec = max(60.0, float(refresh_sec))
        self._snapshot_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._refreshed_at: float | None = None
        self._last_error: str | None = None
        self._refresh_n = 0
        self._loaded_persisted = False

    def _publish(self, payload: dict[str, Any], *, refreshed_at: float) -> None:
        with self._snapshot_lock:
            self._payload = copy.deepcopy(payload)
            self._refreshed_at = float(refreshed_at)
            self._last_error = None
            self._refresh_n += 1

    def load_persisted(self) -> bool:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("contract_version") != VALIDATION_CACHE_VERSION:
                return False
            payload = raw.get("payload")
            if not isinstance(payload, dict):
                return False
            expected = str(raw.get("payload_sha256") or "").lower()
            actual = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
            if len(expected) != 64 or actual != expected:
                return False
            refreshed_at = float(raw.get("refreshed_at") or 0.0)
            if refreshed_at <= 0.0:
                return False
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        self._publish(payload, refreshed_at=refreshed_at)
        self._loaded_persisted = True
        return True

    def _persist(self, payload: dict[str, Any], *, refreshed_at: float) -> None:
        encoded = _canonical_bytes(payload)
        envelope = {
            "contract_version": VALIDATION_CACHE_VERSION,
            "refreshed_at": float(refreshed_at),
            "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            "payload": payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(
                    envelope,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=True,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()

    def refresh_sync(self, *, required: bool = False) -> bool:
        """Compute one genuine report; never overlap and preserve last-good."""
        if not required:
            pressure = memory_pressure_state()
            if pressure.get("level") != "normal":
                with self._snapshot_lock:
                    self._last_error = (
                        "refresh deferred under "
                        f"{pressure.get('level')} memory pressure"
                    )
                trim_memory_for_pressure()
                return False
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            try:
                payload = self._source()
                if not isinstance(payload, dict):
                    raise TypeError("validation source must return a dict")
                payload = jsonable_encoder(payload)
                if not isinstance(payload, dict):
                    raise TypeError("encoded validation source must return a dict")
                refreshed_at = time.time()
                self._persist(payload, refreshed_at=refreshed_at)
            except Exception as exc:
                with self._snapshot_lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                if required:
                    raise
                return False
            self._publish(payload, refreshed_at=refreshed_at)
            return True
        finally:
            self._refresh_lock.release()
            trim_memory_for_pressure()

    def get(self) -> dict[str, Any]:
        with self._snapshot_lock:
            if self._payload is None:
                raise RuntimeError("validation materialization is not ready")
            return copy.deepcopy(self._payload)

    def status(self) -> dict[str, Any]:
        with self._snapshot_lock:
            return {
                "ready": self._payload is not None,
                "refreshed_at": self._refreshed_at,
                "last_error": self._last_error,
                "refresh_n": self._refresh_n,
                "refresh_sec": self.refresh_sec,
                "loaded_persisted": self._loaded_persisted,
                "request_path_sqlite": False,
                "single_flight": True,
                "last_good_on_refresh_error": True,
            }


def _validation_route(app):
    for route in app.router.routes:
        if getattr(route, "path", None) == "/api/validation":
            return route
    raise RuntimeError("/api/validation route not found")


def install_validation_nonblocking(app) -> ValidationCache:
    existing = getattr(app.state, "validation_cache", None)
    if existing is not None:
        return existing

    route = _validation_route(app)
    source = route.dependant.call
    if not callable(source):
        raise RuntimeError("/api/validation source is not callable")

    data_dir = Path(str(app.state.settings.data_dir)).resolve()
    path = data_dir / "research" / CACHE_FILENAME
    refresh_sec = float(os.environ.get(REFRESH_ENV, str(DEFAULT_REFRESH_SEC)))
    cache = ValidationCache(source, path=path, refresh_sec=refresh_sec)

    # On the first deployment there is no last-good file yet.  Build exactly one
    # truthful generation before uvicorn/background workers start, when memory
    # contention is lowest.  Every later restart loads the verified materialized
    # generation without rescanning the journal.
    if not cache.load_persisted():
        cache.refresh_sync(required=True)

    def cached_validation():
        return cache.get()

    # FastAPI's request handler closes over the mutable Dependant. Replacing both
    # references keeps introspection and the actual request call aligned.
    route.endpoint = cached_validation
    route.dependant.call = cached_validation
    app.state.validation_cache = cache

    original_lifespan = app.router.lifespan_context

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(cache.refresh_sec)
            await asyncio.to_thread(cache.refresh_sync)

    @contextlib.asynccontextmanager
    async def validation_lifespan(inner_app):
        refresh_task: asyncio.Task | None = None
        async with original_lifespan(inner_app):
            refresh_task = asyncio.create_task(refresh_loop())
            try:
                yield
            finally:
                refresh_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await refresh_task

    app.router.lifespan_context = validation_lifespan
    return cache
