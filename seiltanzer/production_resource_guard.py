"""Production resource guard for the small shared VPS.

This module changes no market/research mathematics. It serializes large refreshes,
trims released allocator arenas and sheds low-priority data/research work when the
long-lived web process approaches the host RAM limit. Missing/stale data remains
explicit; preserving terminal availability outranks optional background refreshes.
"""
from __future__ import annotations

import ctypes
import gc
import os
import threading
import time
from functools import wraps
from typing import Any, Callable


RESOURCE_GUARD_VERSION = "production-resource-guard-v2-memory-pressure"
_HEAVY_FEED_METHODS = (
    "refresh_intraday",
    "refresh_vols",
    "refresh_daily",
    "refresh_chain",
    "refresh_iv_surface",
    "refresh_correlation",
)
_OPTIONAL_AT_HARD_PRESSURE = {
    "refresh_daily",
    "refresh_chain",
    "refresh_iv_surface",
    "refresh_correlation",
}
_HEAVY_LOCK = threading.RLock()
_LAST_TRIM_TS = 0.0
_TRIM_LOCK = threading.Lock()


def _env_mib(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(128, value)


MEMORY_SOFT_MIB = _env_mib("SEILTZANZER_MEMORY_SOFT_MIB", 850)
MEMORY_HARD_MIB = max(MEMORY_SOFT_MIB + 128, _env_mib("SEILTZANZER_MEMORY_HARD_MIB", 1200))
MEMORY_CRITICAL_MIB = max(MEMORY_HARD_MIB + 128, _env_mib("SEILTZANZER_MEMORY_CRITICAL_MIB", 1450))


def _rss_bytes() -> int | None:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def memory_pressure_state(rss_bytes: int | None = None) -> dict[str, Any]:
    rss = _rss_bytes() if rss_bytes is None else rss_bytes
    rss_mib = (rss / (1024 * 1024)) if rss is not None else None
    if rss_mib is None:
        level = "unknown"
    elif rss_mib >= MEMORY_CRITICAL_MIB:
        level = "critical"
    elif rss_mib >= MEMORY_HARD_MIB:
        level = "hard"
    elif rss_mib >= MEMORY_SOFT_MIB:
        level = "soft"
    else:
        level = "normal"
    return {
        "level": level,
        "rss_bytes": rss,
        "rss_mib": round(rss_mib, 2) if rss_mib is not None else None,
        "soft_mib": MEMORY_SOFT_MIB,
        "hard_mib": MEMORY_HARD_MIB,
        "critical_mib": MEMORY_CRITICAL_MIB,
        "pause_background": level in {"soft", "hard", "critical"},
        "shed_optional_feeds": level in {"hard", "critical"},
        "shed_all_heavy_feeds": level == "critical",
    }


def _trim_allocator(*, min_interval_sec: float = 15.0) -> None:
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


def trim_memory_for_pressure() -> None:
    _trim_allocator(min_interval_sec=0.0)


def _mark_pressure_degraded(owner: Any, method_name: str, pressure: dict[str, Any]) -> None:
    message = f"refresh skipped under {pressure['level']} memory pressure ({pressure.get('rss_mib')} MiB RSS)"
    attr_by_method = {
        "refresh_daily": "daily",
        "refresh_chain": "chain",
        "refresh_iv_surface": "iv_surface",
        "refresh_correlation": "correlation",
    }
    attr = attr_by_method.get(method_name)
    if attr:
        state = getattr(owner, attr, None)
        if isinstance(state, dict):
            state["status"] = "delayed" if state.get("ts") is not None else "no_data"
            state["fresh"] = False
            state["error"] = message[:200]
    if method_name == "refresh_vols":
        vols = getattr(owner, "vols", None)
        if isinstance(vols, dict):
            for state in vols.values():
                if isinstance(state, dict):
                    state["status"] = "delayed" if state.get("ts") is not None else "no_data"
                    state["fresh"] = False
                    state["error"] = message[:200]


def _production_shedding_enabled(owner: Any) -> bool:
    settings = getattr(owner, "settings", None)
    return settings is not None and getattr(settings, "demo", None) is False


def _wrap_heavy_refresh(method: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(method, "_production_resource_guard_version", None) == RESOURCE_GUARD_VERSION:
        return method
    method_name = getattr(method, "__name__", "")

    @wraps(method)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        with _HEAVY_LOCK:
            owner = args[0] if args else None
            pressure = memory_pressure_state()
            should_shed = _production_shedding_enabled(owner) and (
                pressure["shed_all_heavy_feeds"] or (
                    pressure["shed_optional_feeds"] and method_name in _OPTIONAL_AT_HARD_PRESSURE
                )
            )
            if should_shed:
                _mark_pressure_degraded(owner, method_name, pressure)
                _trim_allocator(min_interval_sec=0.0)
                return None
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
        if self._feeds:
            self._feeds.clear()
            _trim_allocator(min_interval_sec=0.0)
        return original_feed(self, instrument)

    bounded_passive_feed._production_resource_guard_version = RESOURCE_GUARD_VERSION  # type: ignore[attr-defined]
    PassiveLearningEngine._feed = bounded_passive_feed
    MarketData._production_resource_guard_version = RESOURCE_GUARD_VERSION


def resource_guard_status() -> dict[str, Any]:
    pressure = memory_pressure_state()
    return {
        "contract_version": RESOURCE_GUARD_VERSION,
        "heavy_feed_parallelism": 1,
        "passive_feed_cache_max": 1,
        "allocator_trim_supported": True,
        "memory_pressure": pressure,
        "rss_bytes": pressure["rss_bytes"],
        "rss_mib": pressure["rss_mib"],
        "mathematics_changed": False,
    }
