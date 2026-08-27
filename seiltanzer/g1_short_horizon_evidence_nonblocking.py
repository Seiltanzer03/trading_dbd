"""Process-local nonblocking facade for materialized G.1S research reports.

Durable SQLite rows remain the source of truth and are still produced only by
existing research materializers. This facade moves presentation reads off the
shared passive/G1S SQLite lock: evidence snapshots and historical walk-forward
status are prewarmed before uvicorn/research-worker startup, then refreshed only
from the worker path after durable writes.

HTTP reads never touch SQLite and never trigger full-history/network work.
Missing or corrupt process-local cache fails closed instead of falling back to a
request-time database scan. Large evidence payloads are also pre-encoded on the
worker/startup path so latency-sensitive HTTP routes do not recursively JSON-
encode the same immutable report on every request.
"""
from __future__ import annotations

import json
import time
import types
from typing import Any, Callable

from .g1_short_horizon_evidence_materialization import REPORT_NAMES


NONBLOCKING_EVIDENCE_VERSION = "g1s-evidence-nonblocking-v3-preencoded"
_AGE_PLACEHOLDER = "__G1S_MATERIALIZATION_AGE_SEC__"
_AGE_MARKER = json.dumps(_AGE_PLACEHOLDER).encode("utf-8")


def _building(name: str) -> dict[str, Any]:
    return {
        "status": "BUILDING",
        "evidence_status": "INSUFFICIENT",
        "report_name": str(name),
        "materialized": False,
        "request_time_full_history_scan": False,
        "request_time_sqlite_access": False,
        "materialized_snapshot_cached": False,
        "production_authority": False,
        "edge_claim_allowed": False,
    }


def _historical_building() -> dict[str, Any]:
    """Fail-closed contract-compatible status without touching SQLite."""
    return {
        "contract_version": "g1s-historical-wf-real-bars-v1",
        "evidence_label": "HISTORICAL_WALK_FORWARD",
        "live_validation_label": "LIVE_PROSPECTIVE_OOS",
        "state": "PENDING",
        "source_count": 0,
        "run_count": 0,
        "provisional_count": 0,
        "interval": "5m",
        "requested_period": "60d",
        "sources": [],
        "runs": [],
        "historical_option_features": "UNAVAILABLE_NOT_SYNTHESIZED",
        "synthetic_option_history": False,
        "expanding_chronological_walk_forward": True,
        "purge_embargo": True,
        "shuffle": False,
        "dependency_group_total_weight_one": True,
        "historical_fold_outcomes_count_as_live_oos": False,
        "provisional_artifact_starts_separate_live_oos": True,
        "request_time_network_fetch": False,
        "request_time_full_history_scan": False,
        "request_time_sqlite_access": False,
        "cached_snapshot": False,
        "auto_promotion": False,
        "production_authority": False,
    }


def _encode_report_template(snapshot: dict[str, Any]) -> tuple[bytes, float | None]:
    """Pre-encode immutable evidence while keeping age_sec request-current.

    The durable/materialized report itself is unchanged. Only the presentation
    representation is cached. A unique JSON string token stands in for the one
    dynamic field and is replaced with a finite JSON number at request time.
    """
    payload = dict(snapshot)
    generated_ts: float | None = None
    materialization = payload.get("materialization")
    if isinstance(materialization, dict):
        materialization = dict(materialization)
        generated = materialization.get("generated_ts")
        try:
            generated_ts = float(generated)
        except (TypeError, ValueError):
            generated_ts = None
        if generated_ts is not None:
            materialization["age_sec"] = _AGE_PLACEHOLDER
        payload["materialization"] = materialization
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return encoded, generated_ts


def _cache_report(runtime: Any, name: str, body: dict[str, Any]) -> None:
    cache = getattr(runtime, "_g1s_evidence_report_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        runtime._g1s_evidence_report_cache = cache
    snapshot = dict(body) if isinstance(body, dict) else _building(name)
    materialization = snapshot.get("materialization")
    if isinstance(materialization, dict):
        materialization = dict(materialization)
        materialization.update({
            "request_time_sqlite_access": False,
            "cached_snapshot": True,
            "nonblocking_evidence_version": NONBLOCKING_EVIDENCE_VERSION,
        })
        snapshot["materialization"] = materialization
    snapshot["request_time_sqlite_access"] = False
    snapshot["materialized_snapshot_cached"] = True
    cache[str(name)] = snapshot

    json_cache = getattr(runtime, "_g1s_evidence_json_cache", None)
    if not isinstance(json_cache, dict):
        json_cache = {}
        runtime._g1s_evidence_json_cache = json_cache
    try:
        json_cache[str(name)] = _encode_report_template(snapshot)
    except (TypeError, ValueError):
        # Fail closed at presentation time rather than falling back to SQLite or
        # expensive request-time recursive encoding of an invalid snapshot.
        fallback = _building(name)
        fallback["materialized_snapshot_cached"] = False
        json_cache[str(name)] = _encode_report_template(fallback)


def _cache_status(runtime: Any, body: dict[str, Any]) -> None:
    snapshot = dict(body) if isinstance(body, dict) else {
        "reports": [], "production_authority": False,
    }
    snapshot["reports"] = [dict(row) for row in snapshot.get("reports") or []
                           if isinstance(row, dict)]
    snapshot["request_time_sqlite_access"] = False
    snapshot["cached_snapshot"] = True
    snapshot["nonblocking_evidence_version"] = NONBLOCKING_EVIDENCE_VERSION
    runtime._g1s_evidence_status_cache = snapshot


def _cache_historical(runtime: Any, body: dict[str, Any]) -> None:
    snapshot = dict(body) if isinstance(body, dict) else _historical_building()
    snapshot["sources"] = [dict(row) for row in snapshot.get("sources") or []
                           if isinstance(row, dict)]
    snapshot["runs"] = [dict(row) for row in snapshot.get("runs") or []
                        if isinstance(row, dict)]
    snapshot["request_time_network_fetch"] = False
    snapshot["request_time_full_history_scan"] = False
    snapshot["request_time_sqlite_access"] = False
    snapshot["cached_snapshot"] = True
    snapshot["nonblocking_evidence_version"] = NONBLOCKING_EVIDENCE_VERSION
    snapshot["production_authority"] = False
    snapshot["auto_promotion"] = False
    runtime._g1s_historical_wf_cache = snapshot


def _present_report(runtime: Any, name: str) -> dict[str, Any]:
    cache = getattr(runtime, "_g1s_evidence_report_cache", None)
    source = cache.get(str(name)) if isinstance(cache, dict) else None
    if not isinstance(source, dict):
        return _building(name)
    body = dict(source)
    materialization = body.get("materialization")
    if isinstance(materialization, dict):
        materialization = dict(materialization)
        generated = materialization.get("generated_ts")
        try:
            generated_ts = float(generated)
        except (TypeError, ValueError):
            generated_ts = None
        if generated_ts is not None:
            materialization["age_sec"] = max(0.0, time.time()-generated_ts)
        materialization["request_time_sqlite_access"] = False
        materialization["cached_snapshot"] = True
        materialization["nonblocking_evidence_version"] = NONBLOCKING_EVIDENCE_VERSION
        body["materialization"] = materialization
    body["request_time_sqlite_access"] = False
    body["materialized_snapshot_cached"] = True
    return body


def _present_report_json(runtime: Any, name: str) -> bytes:
    """Return already encoded JSON without SQLite or recursive request encoding."""
    cache = getattr(runtime, "_g1s_evidence_json_cache", None)
    entry = cache.get(str(name)) if isinstance(cache, dict) else None
    if not (isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], bytes)):
        body = _building(name)
        body["materialized_snapshot_cached"] = False
        return json.dumps(
            body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    template, generated_ts = entry
    if generated_ts is None or _AGE_MARKER not in template:
        return template
    age = max(0.0, time.time()-float(generated_ts))
    return template.replace(_AGE_MARKER, repr(float(age)).encode("ascii"), 1)


def _present_status(runtime: Any) -> dict[str, Any]:
    source = getattr(runtime, "_g1s_evidence_status_cache", None)
    if not isinstance(source, dict):
        return {
            "contract_version": "g1s-evidence-materialization-v1",
            "reports": [],
            "request_time_full_history_scan": False,
            "request_time_sqlite_access": False,
            "cached_snapshot": False,
            "nonblocking_evidence_version": NONBLOCKING_EVIDENCE_VERSION,
            "production_authority": False,
        }
    body = dict(source)
    reports = []
    now = time.time()
    for source_row in source.get("reports") or []:
        if not isinstance(source_row, dict):
            continue
        row = dict(source_row)
        try:
            generated_ts = float(row.get("generated_ts"))
        except (TypeError, ValueError):
            generated_ts = None
        if generated_ts is not None:
            row["age_sec"] = max(0.0, now-generated_ts)
        reports.append(row)
    body["reports"] = reports
    body["request_time_full_history_scan"] = False
    body["request_time_sqlite_access"] = False
    body["cached_snapshot"] = True
    body["nonblocking_evidence_version"] = NONBLOCKING_EVIDENCE_VERSION
    body["production_authority"] = False
    return body


def _present_historical(runtime: Any) -> dict[str, Any]:
    source = getattr(runtime, "_g1s_historical_wf_cache", None)
    if not isinstance(source, dict):
        return _historical_building()
    body = dict(source)
    body["sources"] = [dict(row) for row in source.get("sources") or []
                       if isinstance(row, dict)]
    body["runs"] = [dict(row) for row in source.get("runs") or []
                    if isinstance(row, dict)]
    body["request_time_network_fetch"] = False
    body["request_time_full_history_scan"] = False
    body["request_time_sqlite_access"] = False
    body["cached_snapshot"] = True
    body["nonblocking_evidence_version"] = NONBLOCKING_EVIDENCE_VERSION
    body["production_authority"] = False
    body["auto_promotion"] = False
    return body


def prewarm_g1_short_horizon_evidence(runtime: Any) -> None:
    """Fill materialized evidence caches away from HTTP request threads."""
    original_report = getattr(runtime, "_g1s_evidence_original_report", None)
    original_status = getattr(runtime, "_g1s_evidence_original_status", None)
    original_historical_status = getattr(
        runtime, "_g1s_evidence_original_historical_status", None,
    )
    if not callable(original_report) or not callable(original_status):
        raise RuntimeError("G1S_EVIDENCE_PREWARM_NOT_INSTALLED")
    for name in REPORT_NAMES:
        _cache_report(runtime, name, original_report(name))
    _cache_status(runtime, original_status())
    if callable(original_historical_status):
        _cache_historical(runtime, original_historical_status())


def install_g1_short_horizon_evidence_nonblocking(
    runtime: Any, *, prewarm: bool = True,
) -> None:
    """Prewarm durable research views and replace HTTP readers with memory reads."""
    if getattr(runtime, "_g1s_evidence_nonblocking_version", None) == NONBLOCKING_EVIDENCE_VERSION:
        return

    original_report: Callable[..., dict[str, Any]] = runtime.materialized_evidence_report
    original_status: Callable[..., dict[str, Any]] = runtime.evidence_materialization_status
    original_refresh = runtime.materialize_evidence_reports
    original_historical_status = getattr(runtime, "historical_walkforward_status", None)
    original_historical_refresh = getattr(runtime, "materialize_historical_walkforward", None)
    runtime._g1s_evidence_original_report = original_report
    runtime._g1s_evidence_original_status = original_status
    runtime._g1s_evidence_original_historical_status = original_historical_status

    def report(self, name: str) -> dict[str, Any]:
        if str(name) not in REPORT_NAMES:
            raise ValueError(f"unknown G1S materialized report: {name}")
        return _present_report(self, str(name))

    def report_json(self, name: str) -> bytes:
        if str(name) not in REPORT_NAMES:
            raise ValueError(f"unknown G1S materialized report: {name}")
        return _present_report_json(self, str(name))

    def status(self) -> dict[str, Any]:
        return _present_status(self)

    def refresh(self, *args, **kwargs):
        result = original_refresh(*args, **kwargs)
        # Refresh cache only on the worker path after durable writes/skip checks.
        for name in REPORT_NAMES:
            _cache_report(self, name, original_report(name))
        _cache_status(self, original_status())
        return result

    def historical_status(self) -> dict[str, Any]:
        return _present_historical(self)

    def historical_refresh(self, *args, **kwargs):
        if not callable(original_historical_refresh):
            return {"refreshed": False, "reason": "HISTORICAL_WF_UNAVAILABLE"}
        result = original_historical_refresh(*args, **kwargs)
        if callable(original_historical_status):
            _cache_historical(self, original_historical_status())
        return result

    runtime.materialized_evidence_report = types.MethodType(report, runtime)
    runtime.materialized_evidence_json = types.MethodType(report_json, runtime)
    runtime.evidence_materialization_status = types.MethodType(status, runtime)
    runtime.materialize_evidence_reports = types.MethodType(refresh, runtime)
    if callable(original_historical_status):
        runtime.historical_walkforward_status = types.MethodType(historical_status, runtime)
    if callable(original_historical_refresh):
        runtime.materialize_historical_walkforward = types.MethodType(historical_refresh, runtime)
    runtime._g1s_evidence_nonblocking_version = NONBLOCKING_EVIDENCE_VERSION
    if prewarm:
        prewarm_g1_short_horizon_evidence(runtime)
