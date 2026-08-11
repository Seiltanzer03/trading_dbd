"""Low-frequency presentation cache for the Intelligence Cockpit.

Authoritative G.1A/B/B.1/C runtimes remain the source of truth. This layer only
coalesces repeated read-only aggregation so opening one page does not launch the
same membership/calibration scans from several concurrent HTTP requests.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

from . import g1_intelligence_runtime as _g1e


PERFORMANCE_CONTRACT_VERSION = "g1e-presentation-cache-v1"
SOURCE_TTL_SEC = 20.0
EXTRAS_TTL_SEC = 20.0

_ORIGINAL_SOURCES = _g1e.IntelligenceRuntime._sources
_ORIGINAL_STATUS = _g1e.IntelligenceRuntime.status


def _state(self) -> tuple[threading.RLock, dict[str, tuple[float, Any]]]:
    lock = getattr(self, "_g1e_cache_lock", None)
    if lock is None:
        lock = threading.RLock()
        self._g1e_cache_lock = lock
        self._g1e_cache_values = {}
    return lock, self._g1e_cache_values


def _cached(self, key: str, ttl: float, builder: Callable[[], Any]) -> Any:
    lock, values = _state(self)
    now = time.monotonic()
    with lock:
        entry = values.get(key)
        if entry is not None and now - entry[0] <= ttl:
            return copy.deepcopy(entry[1])
        value = builder()
        values[key] = (time.monotonic(), value)
        return copy.deepcopy(value)


def cached_sources(self):
    return _cached(self, "sources", SOURCE_TTL_SEC, lambda: _ORIGINAL_SOURCES(self))


def cached_status(self) -> dict:
    result = _cached(self, "status", 10.0, lambda: _ORIGINAL_STATUS(self))
    result["presentation_cache"] = {
        "contract_version": PERFORMANCE_CONTRACT_VERSION,
        "ttl_sec": SOURCE_TTL_SEC,
        "authoritative_math_cached": False,
        "presentation_aggregation_cached": True,
    }
    return result


def cached_pipeline(self) -> dict:
    dataset, exclusions, _baseline, q, _g1c_status = self._sources()
    instruments, blockers = _cached(
        self,
        "pipeline_extras",
        EXTRAS_TTL_SEC,
        lambda: (self.passive.g1_q_instruments(), self.passive.g1_q_blockers()),
    )
    return {
        "contract_version": _g1e.INTELLIGENCE_CONTRACT_VERSION,
        "presentation_cache_contract_version": PERFORMANCE_CONTRACT_VERSION,
        "funnel": [
            {"name": "ATTEMPTS", "n": int(q.get("capture_attempt_n") or 0)},
            {"name": "CAPTURED", "n": int(q.get("successful_q_capture_n") or 0)},
            {"name": "RESOLVED", "n": int(q.get("resolved_q_observation_n") or 0)},
            {"name": "Q→P ELIGIBLE", "n": int(q.get("q_to_p_eligible_n") or 0)},
            {"name": "EFFECTIVE Q N", "n": int(q.get("effective_q_n") or 0)},
        ],
        "instruments": instruments,
        "q_blockers": blockers,
        "dataset_exclusions": exclusions,
        "forecast_eval_eligible_n": int(dataset.get("forecast_eval_eligible_n") or 0),
        "explanations": {
            code: _g1e.HUMAN_EXPLANATIONS.get(code, code)
            for code in set((q.get("top_blockers") or {}).keys())
            | set((exclusions.get("primary_reason_counts") or {}).keys())
        },
    }


def cached_forecast_quality(self) -> dict:
    _dataset, _exclusions, baseline, _q, _g1c_status = self._sources()
    cohorts = _cached(
        self, "baseline_cohorts", EXTRAS_TTL_SEC,
        lambda: self.passive.g1_baseline_cohorts(),
    )
    return {
        "contract_version": _g1e.INTELLIGENCE_CONTRACT_VERSION,
        "presentation_cache_contract_version": PERFORMANCE_CONTRACT_VERSION,
        "status": baseline,
        "cohorts": cohorts,
        "presentation_note": (
            "Все числа рассчитаны authoritative G.1B backend; cockpit не пересчитывает Brier/PIT в браузере."
        ),
    }


def cached_calibration(self) -> dict:
    _dataset, _exclusions, _baseline, _q, g1c = self._sources()
    models, cohorts, predictions = _cached(
        self,
        "calibration_extras",
        EXTRAS_TTL_SEC,
        lambda: (
            self.passive.g1c_models(limit=200),
            self.passive.g1c_cohorts(),
            self.passive.g1c_predictions(limit=100),
        ),
    )
    return {
        "contract_version": _g1e.INTELLIGENCE_CONTRACT_VERSION,
        "presentation_cache_contract_version": PERFORMANCE_CONTRACT_VERSION,
        "status": g1c,
        "models": models,
        "cohorts": cohorts,
        "predictions": predictions,
        "research_only": True,
        "production_used": False,
    }


def install_g1_intelligence_performance() -> None:
    if getattr(_g1e.IntelligenceRuntime, "_g1e_performance_contract", None) == PERFORMANCE_CONTRACT_VERSION:
        return
    _g1e.IntelligenceRuntime._sources = cached_sources
    _g1e.IntelligenceRuntime.status = cached_status
    _g1e.IntelligenceRuntime.pipeline = cached_pipeline
    _g1e.IntelligenceRuntime.forecast_quality = cached_forecast_quality
    _g1e.IntelligenceRuntime.calibration = cached_calibration
    _g1e.IntelligenceRuntime._g1e_performance_contract = PERFORMANCE_CONTRACT_VERSION
