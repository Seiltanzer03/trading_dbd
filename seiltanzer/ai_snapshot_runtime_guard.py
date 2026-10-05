"""Production safety around the event-driven AI snapshot materializer."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from fastapi.responses import JSONResponse


RUNTIME_GUARD_VERSION = "ai-snapshot-runtime-guard-v1"
DEFAULT_FAILURE_BACKOFF_SEC = 20.0


def install_ai_snapshot_runtime_guard(app: Any, materializer: Any,
                                      *, failure_backoff_sec: float = DEFAULT_FAILURE_BACKOFF_SEC) -> None:
    """Prevent failed heavy builds from hot-looping and preserve no-trade semantics."""
    if getattr(app.state, "ai_snapshot_runtime_guard_installed", False):
        return
    backoff = max(5.0, float(failure_backoff_sec))
    lock = threading.RLock()
    retry_not_before = {"mono": 0.0, "wall": None}

    original_build_once = materializer._build_once

    def guarded_build_once() -> None:
        with lock:
            if time.monotonic() < retry_not_before["mono"]:
                return
        original_build_once()
        status = materializer.status()
        with lock:
            if status.get("last_error"):
                retry_not_before["mono"] = time.monotonic() + backoff
                retry_not_before["wall"] = time.time() + backoff
            else:
                retry_not_before["mono"] = 0.0
                retry_not_before["wall"] = None

    # The materializer thread has an 8s startup delay, so this instance-level
    # replacement is installed before its first heavy build during normal startup.
    materializer._build_once = guarded_build_once

    original_status = materializer.status

    def guarded_status() -> dict[str, Any]:
        row = dict(original_status())
        with lock:
            remaining = max(0.0, retry_not_before["mono"] - time.monotonic())
            row.update({
                "runtime_guard_version": RUNTIME_GUARD_VERSION,
                "failure_backoff_sec": backoff,
                "failure_retry_in_sec": round(remaining, 3),
                "failure_retry_not_before": retry_not_before["wall"],
            })
        return row

    materializer.status = guarded_status

    @app.middleware("http")
    async def _no_active_trade_fast_path(request, call_next):
        if request.url.path != "/api/ai/verdict" or request.method.upper() != "POST":
            return await call_next(request)
        from .ai_request_trace import AIRequestTrace
        inherited = getattr(request.state, 'ai_request_trace', None)
        trace = inherited or AIRequestTrace()
        request.state.ai_request_trace = trace
        try:
            # Production installs this middleware outside the materializer.
            # Start timing before the synchronous journal probe can block.
            with trace.span('runtime_guard'):
                no_active_trade = materializer.current_trade_id() is None
            if no_active_trade:
                response = JSONResponse(
                    status_code=400,
                    content={
                        "ok": False,
                        "error": {
                            "code": "no_active_trade",
                            "message": "Нет активной сделки для ИИ-разбора",
                            "retriable": False,
                        },
                    },
                )
            else:
                response = await call_next(request)
        except BaseException as exc:
            trace.error('internal_error', exc)
            if inherited is None:
                trace.finish(499 if isinstance(exc, asyncio.CancelledError) else 500)
            raise
        if inherited is None:
            trace.finish(response.status_code)
        return response

    app.state.ai_snapshot_runtime_guard_installed = True
