"""Tiny process-local spend guard for optional research LLM calls.

Caching remains the primary cost control.  These gates are a second line of
defence for public POST routes: they reserve a slot immediately before an actual
provider call, so concurrent unique requests cannot create an LLM burst.  They
are intentionally separate from the latency-critical AI verdict circuit.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Any

from .g1_historical_analog_analyst import _provider as _analog_provider
from .macro_data_factory import _openrouter_extract as _macro_provider


COST_GUARD_VERSION = "research-llm-cost-guard-v1"
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
        """Reserve one provider call or raise with a bounded retry-after value."""
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


_MACRO_GATE = ProviderRateGate(_interval(
    "DATA_FACTORY_MIN_PROVIDER_INTERVAL_SEC",
    DEFAULT_MACRO_INTERVAL_SEC, 30.0, 3600.0,
))
_ANALOG_GATE = ProviderRateGate(_interval(
    "ANALOG_LLM_MIN_PROVIDER_INTERVAL_SEC",
    DEFAULT_ANALOG_INTERVAL_SEC, 5.0, 300.0,
))


def guarded_macro_extractor(current_text: str, previous_text: str | None,
                            model: str) -> dict[str, Any]:
    _MACRO_GATE.reserve()
    return _macro_provider(current_text, previous_text, model)


def guarded_analog_provider(summary: dict[str, Any], model: str) -> str:
    _ANALOG_GATE.reserve()
    return _analog_provider(summary, model)


def cost_guard_status() -> dict[str, Any]:
    return {
        "contract_version": COST_GUARD_VERSION,
        "macro_min_provider_interval_sec": _MACRO_GATE.interval_sec,
        "analog_min_provider_interval_sec": _ANALOG_GATE.interval_sec,
        "cache_checked_before_gate": True,
        "separate_from_ai_verdict_provider_guard": True,
    }
