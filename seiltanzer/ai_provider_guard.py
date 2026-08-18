"""Bound the optional LLM provider without weakening deterministic AI policy.

The deterministic policy snapshot/report is authoritative. OpenRouter is only an
explanation layer, so a slow provider must never hold the public HTTP request long
enough for an upstream gateway to return HTML 504. A timeout opens a short circuit:
while the still-running HTTP call drains inside the single provider worker, new AI
reviews immediately use the established deterministic fallback instead of queueing
behind stale provider work.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable


# The public route has a materially tighter SLA than the provider's own HTTP
# timeout. Keep enough time for a fast explanation, but fail over well before a
# gateway can terminate the browser request. The full deterministic policy report
# is already available and remains authoritative on every timeout.
DEFAULT_PROVIDER_TIMEOUT_SEC = 6.0
MIN_PROVIDER_TIMEOUT_SEC = 3.0
MAX_PROVIDER_TIMEOUT_SEC = 15.0
DEFAULT_PROVIDER_CIRCUIT_SEC = 50.0

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seiltanzer-ai-provider")
_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT_OPEN_UNTIL = 0.0
_INSTALLED = False


def provider_timeout_sec() -> float:
    raw = os.environ.get("AI_PROVIDER_TIMEOUT_SEC", str(DEFAULT_PROVIDER_TIMEOUT_SEC))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_PROVIDER_TIMEOUT_SEC
    return min(MAX_PROVIDER_TIMEOUT_SEC, max(MIN_PROVIDER_TIMEOUT_SEC, value))


def provider_circuit_sec() -> float:
    raw = os.environ.get("AI_PROVIDER_CIRCUIT_SEC", str(DEFAULT_PROVIDER_CIRCUIT_SEC))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_PROVIDER_CIRCUIT_SEC
    return min(120.0, max(15.0, value))


def _circuit_remaining(now: float | None = None) -> float:
    now = time.monotonic() if now is None else float(now)
    with _CIRCUIT_LOCK:
        return max(0.0, float(_CIRCUIT_OPEN_UNTIL) - now)


def _open_circuit() -> None:
    global _CIRCUIT_OPEN_UNTIL
    with _CIRCUIT_LOCK:
        _CIRCUIT_OPEN_UNTIL = max(
            float(_CIRCUIT_OPEN_UNTIL), time.monotonic() + provider_circuit_sec())


def _close_circuit() -> None:
    global _CIRCUIT_OPEN_UNTIL
    with _CIRCUIT_LOCK:
        _CIRCUIT_OPEN_UNTIL = 0.0


def bounded_provider_call(
    fn: Callable[[dict[str, Any]], dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    timeout_sec: float | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> dict[str, Any]:
    """Call one provider with a hard wall-clock budget and stale-work circuit.

    The shared production executor is deliberately single-threaded. A timed-out
    running HTTP call cannot be killed safely by ``Future.cancel()``; therefore we
    open a circuit for longer than the normal drain interval. During that window
    callers receive RuntimeError immediately and FastAPI renders the deterministic
    policy report. Supplying a private executor (unit tests) bypasses the global
    circuit so tests remain isolated.
    """
    timeout = provider_timeout_sec() if timeout_sec is None else float(timeout_sec)
    production_pool = executor is None
    if production_pool:
        remaining = _circuit_remaining()
        if remaining > 0:
            raise RuntimeError(f"provider_circuit_open_{remaining:.1f}s")
    pool = executor or _EXECUTOR
    future = pool.submit(fn, snapshot)
    try:
        result = future.result(timeout=max(0.001, timeout))
        if production_pool:
            _close_circuit()
        return result
    except FutureTimeout as exc:
        future.cancel()
        if production_pool:
            _open_circuit()
        raise RuntimeError(f"provider_timeout_after_{timeout:.1f}s") from exc


def install_ai_provider_guard() -> None:
    """Patch the FastAPI module's imported provider call before app creation."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import app as app_module

    original = app_module.request_verdict

    def guarded_request_verdict(snapshot: dict[str, Any]) -> dict[str, Any]:
        return bounded_provider_call(original, snapshot)

    guarded_request_verdict.__name__ = getattr(original, "__name__", "request_verdict")
    guarded_request_verdict.__doc__ = (
        "Gateway-safe OpenRouter explanation call; deterministic fallback remains authoritative."
    )
    app_module.request_verdict = guarded_request_verdict
    _INSTALLED = True
