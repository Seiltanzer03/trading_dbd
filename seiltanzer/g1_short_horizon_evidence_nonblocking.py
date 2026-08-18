"""Process-local nonblocking facade for materialized G.1S evidence reports.

The durable ``g1s_evidence_materializations`` rows remain the source of truth and
are still produced only by the low-priority research worker.  This facade moves
only their presentation read off the shared passive/G1S SQLite lock.

At startup the latest durable snapshots are prewarmed before the worker starts.
After each worker refresh the process-local snapshots are replaced atomically.
HTTP reads never touch SQLite and never trigger a full-history scan.  Missing or
corrupt in-process cache entries fail closed as BUILDING rather than falling back
to request-time database work.
"""
from __future__ import annotations

import time
import types
from typing import Any, Callable

from .g1_short_horizon_evidence_materialization import REPORT_NAMES


NONBLOCKING_EVIDENCE_VERSION = "g1s-evidence-nonblocking-v1"


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


def _cache_status(runtime: Any, body: dict[str, Any]) -> None:
    snapshot = dict(body) if isinstance(body, dict) else {
        "reports": [], "production_authority": False,
    }
    reports = []
    for row in snapshot.get("reports") or []:
        if isinstance(row, dict):
            reports.append(dict(row))
    snapshot["reports"] = reports
    snapshot["request_time_sqlite_access"] = False
    snapshot["cached_snapshot"] = True
    snapshot["nonblocking_evidence_version"] = NONBLOCKING_EVIDENCE_VERSION
    runtime._g1s_evidence_status_cache = snapshot


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


def install_g1_short_horizon_evidence_nonblocking(runtime: Any) -> None:
    """Prewarm durable evidence and replace request-time readers with memory reads."""
    if getattr(runtime, "_g1s_evidence_nonblocking_version", None) == NONBLOCKING_EVIDENCE_VERSION:
        return

    original_report: Callable[..., dict[str, Any]] = runtime.materialized_evidence_report
    original_status: Callable[..., dict[str, Any]] = runtime.evidence_materialization_status
    original_refresh = runtime.materialize_evidence_reports

    # Prewarm before uvicorn/research-worker startup. SQLite access is acceptable
    # here because no latency-sensitive HTTP request exists yet.
    for name in REPORT_NAMES:
        _cache_report(runtime, name, original_report(name))
    _cache_status(runtime, original_status())

    def report(self, name: str) -> dict[str, Any]:
        if str(name) not in REPORT_NAMES:
            raise ValueError(f"unknown G1S materialized report: {name}")
        return _present_report(self, str(name))

    def status(self) -> dict[str, Any]:
        return _present_status(self)

    def refresh(self, *args, **kwargs):
        result = original_refresh(*args, **kwargs)
        # Refresh cache only on the worker path after durable writes/skip checks.
        # These reads may use SQLite because they are not on the HTTP path.
        for name in REPORT_NAMES:
            _cache_report(self, name, original_report(name))
        _cache_status(self, original_status())
        return result

    runtime.materialized_evidence_report = types.MethodType(report, runtime)
    runtime.evidence_materialization_status = types.MethodType(status, runtime)
    runtime.materialize_evidence_reports = types.MethodType(refresh, runtime)
    runtime._g1s_evidence_nonblocking_version = NONBLOCKING_EVIDENCE_VERSION
