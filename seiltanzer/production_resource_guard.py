"""Production resource guard for the small shared VPS.

This module changes no market/research mathematics.  It only bounds concurrent
memory-heavy network/dataframe refreshes and the passive collector's retained
MarketData objects.  The production host has limited RAM, so several yfinance /
pandas jobs running in parallel can create a transient RSS peak large enough for
Linux OOM to kill the long-lived web process.
"""
from __future__ import annotations

import ctypes
import gc
import threading
import time
from functools import wraps
from typing import Any, Callable


RESOURCE_GUARD_VERSION = "production-resource-guard-v1"
_HEAVY_FEED_METHODS = (
    "refresh_intraday",
    "refresh_vols",
    "refresh_daily",
    "refresh_chain",
    "refresh_iv_surface",
    "refresh_correlation",
)
_HEAVY_LOCK = threading.RLock()
_LAST_TRIM_TS = 0.0
_TRIM_LOCK = threading.Lock()


def _rss_bytes() -> int | None:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _trim_allocator(*, min_interval_sec: float = 15.0) -> None:
    """Return free glibc arenas to the OS when available.

    ``malloc_trim`` is an allocator housekeeping operation; it does not change
    Python objects or numerical results.  It is intentionally throttled because
    feed refreshes can finish several times per minute.
    """
    global _LAST_TRIM_TS
    now = time.monotonic()
    if now - _LAST_TRIM_TS < min_interval_sec:
        return
    if not _TRIM_LOCK.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if now - _LAST_TRIM_TS < min_interval_sec:
            return
        gc.collect()
        try:
            libc = ctypes.CDLL("libc.so.6")
            trim = getattr(libc, "malloc_trim", None)
            if trim is not None:
                trim.argtypes = [ctypes.c_size_t]
                trim.restype = ctypes.c_int
                trim(0)
        except (OSError, AttributeError):
            pass
        _LAST_TRIM_TS = now
    finally:
        _TRIM_LOCK.release()


def _wrap_heavy_refresh(method: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(method, "_production_resource_guard_version", None) == RESOURCE_GUARD_VERSION:
        return method

    @wraps(method)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        # One dataframe/network-heavy refresh at a time across the main market
        # object and all passive/research MarketData instances.
        with _HEAVY_LOCK:
            try:
                return method(*args, **kwargs)
            finally:
                _trim_allocator()

    guarded._production_resource_guard_version = RESOURCE_GUARD_VERSION  # type: ignore[attr-defined]
    return guarded


def install_production_resource_guard() -> None:
    from .data.feeds import MarketData
    from .passive_learning import PassiveLearningEngine

    if getattr(MarketData, "_production_resource_guard_version", None) == RESOURCE_GUARD_VERSION:
        return

    for name in _HEAVY_FEED_METHODS:
        method = getattr(MarketData, name, None)
        if callable(method):
            setattr(MarketData, name, _wrap_heavy_refresh(method))

    original_feed = PassiveLearningEngine._feed

    @wraps(original_feed)
    def bounded_passive_feed(self: Any, instrument: str):
        current = self._feeds.get(instrument)
        if current is not None:
            return current
        # The collector's declared budget has always been one instrument at a
        # time.  Retaining a fully-populated MarketData object for every symbol
        # violated that budget and kept old daily/intraday/option structures in
        # the long-lived web process.  Persisted evidence lives in SQLite, not
        # in these feed objects, so eviction is lossless.
        if self._feeds:
            self._feeds.clear()
            _trim_allocator(min_interval_sec=0.0)
        return original_feed(self, instrument)

    bounded_passive_feed._production_resource_guard_version = RESOURCE_GUARD_VERSION  # type: ignore[attr-defined]
    PassiveLearningEngine._feed = bounded_passive_feed
    MarketData._production_resource_guard_version = RESOURCE_GUARD_VERSION


def resource_guard_status() -> dict[str, Any]:
    rss = _rss_bytes()
    return {
        "contract_version": RESOURCE_GUARD_VERSION,
        "heavy_feed_parallelism": 1,
        "passive_feed_cache_max": 1,
        "allocator_trim_supported": True,
        "rss_bytes": rss,
        "rss_mib": (round(rss / (1024 * 1024), 2) if rss is not None else None),
        "mathematics_changed": False,
    }
