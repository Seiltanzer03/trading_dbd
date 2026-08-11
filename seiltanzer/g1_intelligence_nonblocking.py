"""Non-blocking lifecycle/materialization for the G.1E Intelligence Cockpit.

The underlying G.1A/B/B.1/C research scans are intentionally conservative and can
be expensive on a growing prospective dataset. They must never delay terminal
startup or monopolise an HTTP request. This refinement keeps those authoritative
calculations unchanged, materializes them in a worker thread after the server is
ready, and serves persisted/zero-safe state while that worker is busy.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import time
import types
from typing import Any

from . import g1_intelligence_performance as _perf
from . import g1_intelligence_runtime as _g1e


NONBLOCKING_CONTRACT_VERSION = "g1e-nonblocking-materialization-v3"
WARM_INTERVAL_SEC = 60.0


def _authority() -> dict[str, bool]:
    return {
        "research_only": True,
        "production_authority": False,
        "production_replacement_allowed": False,
        "promotion_allowed": False,
        "physical_probability_published": False,
        "shadow_p_used_for_trading": False,
    }


def _cached_value(runtime, key: str):
    """Read presentation cache without ever waiting for a background builder."""
    lock, values = _perf._state(runtime)
    if not lock.acquire(blocking=False):
        return None
    try:
        entry = values.get(key)
        return None if entry is None else copy.deepcopy(entry[1])
    finally:
        lock.release()


def _store_value(runtime, key: str, value: Any) -> None:
    lock, values = _perf._state(runtime)
    with lock:
        values[key] = (time.monotonic(), copy.deepcopy(value))


def _latest_persisted(runtime) -> dict:
    """Read latest immutable snapshot only when the DB lock is immediately free."""
    lock = runtime.passive._lock
    if not lock.acquire(blocking=False):
        return {}
    try:
        row = runtime.passive._conn.execute(
            "SELECT captured_ts,snapshot_json FROM g1e_intelligence_snapshots "
            "ORDER BY bucket_ts DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return {}
    finally:
        lock.release()
    if row is None:
        return {}
    try:
        payload = json.loads(row["snapshot_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return {
        "captured_ts": float(row["captured_ts"]),
        "snapshot": payload if isinstance(payload, dict) else {},
    }


def _fallback_status(runtime) -> dict:
    persisted = _latest_persisted(runtime)
    snap = persisted.get("snapshot") or {}
    exp = snap.get("experience") or {}
    models = snap.get("models") or {}
    evidence = snap.get("evidence") or {}
    maturity = snap.get("maturity_state") or "COLLECTING"
    headline = (
        "Система запускается; live research-метрики прогреваются в фоне."
        if maturity == "COLLECTING"
        else "Показан последний сохранённый intelligence snapshot; live-метрики прогреваются."
    )
    empty_readiness = {
        "status": "WARMING", "ready": False, "required": {}, "observed": {},
        "deficits": {}, "blockers": [],
        "explanations": ["Live research-агрегация ещё прогревается после запуска."],
        "semantic_pooling": False,
    }
    age = None
    if persisted.get("captured_ts") is not None:
        age = max(0.0, time.time() - float(persisted["captured_ts"]))
    return {
        "g1_stage": _g1e.G1E_STAGE,
        "intelligence_contract_version": _g1e.INTELLIGENCE_CONTRACT_VERSION,
        "maturity_state": maturity,
        "headline": headline,
        "experience": {
            "forecast_eval_n": int(exp.get("forecast_eval_n") or 0),
            "forecast_effective_n": int(exp.get("forecast_effective_n") or 0),
            "q_attempts": int(exp.get("q_attempts") or 0),
            "q_captured": int(exp.get("q_captured") or 0),
            "q_resolved": int(exp.get("q_resolved") or 0),
            "q_clean_eligible": int(exp.get("q_clean_eligible") or 0),
            "q_effective_n": int(exp.get("q_effective_n") or 0),
        },
        "models": {
            "platt": copy.deepcopy(empty_readiness),
            "beta": copy.deepcopy(empty_readiness),
            "isotonic": copy.deepcopy(empty_readiness),
            "frozen_model_n": int(models.get("frozen_model_n") or 0),
            "prospective_prediction_n": int(models.get("prospective_prediction_n") or 0),
        },
        "evidence": {
            "dataset_status": evidence.get("dataset_status") or "WARMING",
            "baseline_status": evidence.get("baseline_status") or "WARMING",
            "q_status": evidence.get("q_status") or "WARMING",
            "ready_for_g1d": bool(evidence.get("ready_for_g1d", False)),
            "g1d": evidence.get("g1d") or {},
        },
        "data_quality": {"excluded_n": 0, "primary_reasons": {}, "top_q_blockers": {}},
        "storage": None,
        "authority": _authority(),
        "presentation_state": "WARMING",
        "last_persisted_snapshot_age_sec": age,
        "nonblocking_contract_version": NONBLOCKING_CONTRACT_VERSION,
    }


def status_nonblocking(runtime) -> dict:
    live = _cached_value(runtime, "panel_status")
    if live is None:
        return _fallback_status(runtime)
    live["presentation_state"] = "LIVE_CACHE"
    live["nonblocking_contract_version"] = NONBLOCKING_CONTRACT_VERSION
    return live


def _fallback_pipeline(runtime) -> dict:
    status = status_nonblocking(runtime)
    exp = status.get("experience") or {}
    return {
        "contract_version": _g1e.INTELLIGENCE_CONTRACT_VERSION,
        "nonblocking_contract_version": NONBLOCKING_CONTRACT_VERSION,
        "presentation_state": "WARMING",
        "funnel": [
            {"name": "ATTEMPTS", "n": int(exp.get("q_attempts") or 0)},
            {"name": "CAPTURED", "n": int(exp.get("q_captured") or 0)},
            {"name": "RESOLVED", "n": int(exp.get("q_resolved") or 0)},
            {"name": "Q→P ELIGIBLE", "n": int(exp.get("q_clean_eligible") or 0)},
            {"name": "EFFECTIVE Q N", "n": int(exp.get("q_effective_n") or 0)},
        ],
        "instruments": {"items": []}, "q_blockers": {"items": []},
        "dataset_exclusions": {"primary_reason_counts": {}},
        "forecast_eval_eligible_n": int(exp.get("forecast_eval_n") or 0),
        "explanations": {},
    }


def pipeline_nonblocking(runtime) -> dict:
    result = _cached_value(runtime, "panel_pipeline")
    if result is None:
        return _fallback_pipeline(runtime)
    result["presentation_state"] = "LIVE_CACHE"
    result["nonblocking_contract_version"] = NONBLOCKING_CONTRACT_VERSION
    return result


def forecast_quality_nonblocking(runtime) -> dict:
    result = _cached_value(runtime, "panel_quality")
    if result is None:
        return {
            "contract_version": _g1e.INTELLIGENCE_CONTRACT_VERSION,
            "nonblocking_contract_version": NONBLOCKING_CONTRACT_VERSION,
            "presentation_state": "WARMING", "status": {}, "cohorts": {"items": []},
            "presentation_note": "Live G.1B metrics are warming in the background.",
        }
    result["presentation_state"] = "LIVE_CACHE"
    result["nonblocking_contract_version"] = NONBLOCKING_CONTRACT_VERSION
    return result


def calibration_nonblocking(runtime) -> dict:
    result = _cached_value(runtime, "panel_calibration")
    if result is None:
        return {
            "contract_version": _g1e.INTELLIGENCE_CONTRACT_VERSION,
            "nonblocking_contract_version": NONBLOCKING_CONTRACT_VERSION,
            "presentation_state": "WARMING", "status": {},
            "models": {"items": []}, "cohorts": {"items": []},
            "predictions": {"items": []}, "research_only": True,
            "production_used": False,
        }
    result["presentation_state"] = "LIVE_CACHE"
    result["nonblocking_contract_version"] = NONBLOCKING_CONTRACT_VERSION
    return result


def _warm_live(runtime) -> None:
    """Materialize every heavy panel outside request threads, then publish atomically."""
    live = _perf._ORIGINAL_STATUS(runtime)
    live["presentation_cache"] = {
        "contract_version": _perf.PERFORMANCE_CONTRACT_VERSION,
        "ttl_sec": _perf.SOURCE_TTL_SEC,
        "authoritative_math_cached": False,
        "presentation_aggregation_cached": True,
    }
    pipeline = _perf.cached_pipeline(runtime)
    quality = _perf.cached_forecast_quality(runtime)
    calibration = _perf.cached_calibration(runtime)

    # Publish complete panels only after all builders return. HTTP handlers never
    # execute these builders and never wait on their lock.
    _store_value(runtime, "panel_status", live)
    _store_value(runtime, "panel_pipeline", pipeline)
    _store_value(runtime, "panel_quality", quality)
    _store_value(runtime, "panel_calibration", calibration)
    runtime._g1e_last_warm_ts = time.time()


def install_nonblocking_runtime(runtime) -> None:
    """Patch one app runtime instance before FastAPI lifespan begins."""
    if getattr(runtime, "_g1e_nonblocking_contract", None) == NONBLOCKING_CONTRACT_VERSION:
        return

    heavy_snapshot = runtime.snapshot_if_due
    runtime._g1e_heavy_snapshot_if_due = heavy_snapshot
    runtime._g1e_startup_snapshot_skipped = False

    def snapshot_wrapper(self, *, force: bool = False):
        if not force and not self._g1e_startup_snapshot_skipped:
            self._g1e_startup_snapshot_skipped = True
            return False
        return self._g1e_heavy_snapshot_if_due(force=force)

    async def background_wrapper(self):
        self._background_running = True
        try:
            while True:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(_warm_live, self)
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self._g1e_heavy_snapshot_if_due)
                await asyncio.sleep(WARM_INTERVAL_SEC)
        finally:
            self._background_running = False

    runtime.status = types.MethodType(status_nonblocking, runtime)
    runtime.pipeline = types.MethodType(pipeline_nonblocking, runtime)
    runtime.forecast_quality = types.MethodType(forecast_quality_nonblocking, runtime)
    runtime.calibration = types.MethodType(calibration_nonblocking, runtime)
    runtime.snapshot_if_due = types.MethodType(snapshot_wrapper, runtime)
    runtime.background_loop = types.MethodType(background_wrapper, runtime)
    runtime._g1e_nonblocking_contract = NONBLOCKING_CONTRACT_VERSION
