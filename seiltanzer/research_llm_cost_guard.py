"""Tiny process-local guards for optional research LLM/write paths.

Caching remains the primary cost control. These gates are a second line of
defence for public POST routes: macro ingestion is bounded before arbitrary text
can be persisted, and uncached provider calls have their own burst gates. They
are intentionally separate from the latency-critical AI verdict circuit.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Any

from .g1_historical_analog_analyst import _provider as _analog_provider
from . import macro_data_factory as _macro_data_factory


COST_GUARD_VERSION = "research-llm-cost-guard-v2-dynamic-provider"
DEFAULT_MACRO_INTERVAL_SEC = 300.0
DEFAULT_ANALOG_INTERVAL_SEC = 15.0


def _interval(env_name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(env_name, "") or default)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


class ProviderRateGate:
    def __init__(self, interval_sec: float):
        self.interval_sec = float(interval_sec)
        self._lock = threading.Lock()
        self._last_reserved_mono: float | None = None

    def reserve(self, *, now_mono: float | None = None) -> float:
        """Reserve one guarded action or raise with a bounded retry-after value."""
        now = float(time.monotonic() if now_mono is None else now_mono)
        with self._lock:
            if self._last_reserved_mono is not None:
                elapsed = now - self._last_reserved_mono
                if elapsed < self.interval_sec:
                    retry = max(0.0, self.interval_sec - elapsed)
                    raise RuntimeError(
                        f"RESEARCH_LLM_RATE_LIMITED:retry_after_sec={retry:.1f}"
                    )
            self._last_reserved_mono = now
        return self.interval_sec


_MACRO_INTERVAL = _interval(
    "DATA_FACTORY_MIN_PROVIDER_INTERVAL_SEC",
    DEFAULT_MACRO_INTERVAL_SEC, 30.0, 3600.0,
)
# Separate gates deliberately share the same interval but not state: one bounds
# incoming DB writes, the other bounds the actual provider call after validation/cache.
_MACRO_INGEST_GATE = ProviderRateGate(_MACRO_INTERVAL)
_MACRO_PROVIDER_GATE = ProviderRateGate(_MACRO_INTERVAL)
_ANALOG_GATE = ProviderRateGate(_interval(
    "ANALOG_LLM_MIN_PROVIDER_INTERVAL_SEC",
    DEFAULT_ANALOG_INTERVAL_SEC, 5.0, 300.0,
))


def reserve_macro_ingest_request() -> float:
    return _MACRO_INGEST_GATE.reserve()


def guarded_macro_extractor(current_text: str, previous_text: str | None,
                            model: str) -> dict[str, Any]:
    _MACRO_PROVIDER_GATE.reserve()
    # Resolve at call time so startup refinements can replace the extractor while
    # preserving the same rate gate. A module-imported function reference would
    # keep calling the rejected v1 prompt even after PROMPT_VERSION moved to v2.
    return _macro_data_factory._openrouter_extract(current_text, previous_text, model)


def guarded_analog_provider(summary: dict[str, Any], model: str) -> str:
    _ANALOG_GATE.reserve()
    return _analog_provider(summary, model)


def cost_guard_status() -> dict[str, Any]:
    return {
        "contract_version": COST_GUARD_VERSION,
        "macro_min_ingest_interval_sec": _MACRO_INGEST_GATE.interval_sec,
        "macro_min_provider_interval_sec": _MACRO_PROVIDER_GATE.interval_sec,
        "analog_min_provider_interval_sec": _ANALOG_GATE.interval_sec,
        "macro_write_burst_protection": True,
        "cache_checked_before_provider_gate": True,
        "macro_provider_resolved_at_call_time": True,
        "separate_from_ai_verdict_provider_guard": True,
    }
