"""Bound the optional LLM provider without weakening deterministic AI policy.

The deterministic policy snapshot/report is authoritative. OpenRouter is an
explanation layer and must not keep `/api/ai/verdict` waiting indefinitely. A
single dedicated worker prevents repeated timeouts from accumulating provider
threads on the small production VPS.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable


# Production requests with the current rich snapshot regularly need more than
# the old 10-second wall-clock budget. Keep the call bounded, but give the LLM
# enough time to answer instead of falling back deterministically on normal
# provider latency.
DEFAULT_PROVIDER_TIMEOUT_SEC = 25.0
MIN_PROVIDER_TIMEOUT_SEC = 5.0
MAX_PROVIDER_TIMEOUT_SEC = 45.0

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seiltanzer-ai-provider")
_INSTALLED = False


def provider_timeout_sec() -> float:
    raw = os.environ.get("AI_PROVIDER_TIMEOUT_SEC", str(DEFAULT_PROVIDER_TIMEOUT_SEC))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_PROVIDER_TIMEOUT_SEC
    return min(MAX_PROVIDER_TIMEOUT_SEC, max(MIN_PROVIDER_TIMEOUT_SEC, value))


def bounded_provider_call(
    fn: Callable[[dict[str, Any]], dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    timeout_sec: float | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> dict[str, Any]:
    """Call one provider with a hard wall-clock budget.

    A timed-out running HTTP call may finish later inside the single provider
    worker, but it cannot multiply into many concurrent provider calls. The
    caller receives RuntimeError so the established deterministic fallback path
    remains the only authority.
    """
    timeout = provider_timeout_sec() if timeout_sec is None else float(timeout_sec)
    pool = executor or _EXECUTOR
    future = pool.submit(fn, snapshot)
    try:
        return future.result(timeout=max(0.001, timeout))
    except FutureTimeout as exc:
        future.cancel()
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
        "Bounded OpenRouter explanation call; deterministic fallback remains authoritative."
    )
    app_module.request_verdict = guarded_request_verdict
    _INSTALLED = True
