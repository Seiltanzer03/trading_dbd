"""Process-local nonblocking facade for the already materialized G.1S status.

The durable G.1S status tables remain the source of truth.  This layer only
separates their SQLite read from the latency-sensitive HTTP request: a snapshot
is read before the server starts and refreshed by the existing low-priority
status materializer.  Request-time ``status()`` never acquires the shared
research lock or touches SQLite.

If new observations/resolutions/models arrive before the next materialized
refresh, the cached counts are deliberately marked BUILDING and lag is reported
as unknown rather than pretending the snapshot is current.
"""
from __future__ import annotations

import json
import time
import types
from typing import Any, Callable


NONBLOCKING_STATUS_VERSION = "g1s-nonblocking-status-v1"


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _cache_snapshot(runtime: Any, snapshot: dict[str, Any]) -> None:
    body = dict(snapshot)
    materialization = dict(body.get("status_materialization") or {})
    materialization.update({
        "nonblocking_status_version": NONBLOCKING_STATUS_VERSION,
        "request_time_sqlite_access": False,
        "cached_snapshot": True,
        "cache_dirty": False,
    })
    body["status_materialization"] = materialization
    body["request_time_sqlite_access"] = False
    body["status_snapshot_cached"] = True
    runtime._g1s_status_snapshot_json = _json(body)
    runtime._g1s_status_snapshot_ts = time.time()
    runtime._g1s_status_cache_dirty = False


def _mark_dirty(runtime: Any) -> None:
    runtime._g1s_status_cache_dirty = True


def install_g1_short_horizon_status_nonblocking(runtime: Any) -> None:
    """Install an instance-local cache after all G.1S class refinements exist."""
    if getattr(runtime, "_g1s_nonblocking_status_version", None) == NONBLOCKING_STATUS_VERSION:
        return

    original_status: Callable[..., dict[str, Any]] = runtime.status
    original_refresh = runtime.refresh_materialized_status
    original_materialize = runtime.materialize_new
    original_resolve = runtime.resolve_new
    original_fit = runtime.fit_if_ready
    original_error = runtime._error

    # Prewarm from the durable materialized truth before uvicorn starts and before
    # the research worker can contend for the shared passive/G1S SQLite lock.
    _cache_snapshot(runtime, original_status())

    def status(self) -> dict[str, Any]:
        raw = getattr(self, "_g1s_status_snapshot_json", "")
        if not raw:
            # Installation prewarms this cache.  Fail closed without falling back
            # to SQLite if an unexpected in-process corruption removes it later.
            return {
                "g1_stage": "G.1S",
                "contract_version": "g1s-short-horizon-v1",
                "status": "UNAVAILABLE",
                "reason": "NONBLOCKING_STATUS_CACHE_MISSING",
                "request_time_sqlite_access": False,
                "status_snapshot_cached": False,
                "authority": {
                    "research_only": True,
                    "production_authority": False,
                    "auto_execution_allowed": False,
                    "policy_promotion_allowed": False,
                    "edge_claim_allowed": False,
                    "oos_validated": False,
                },
            }
        body = json.loads(raw)
        dirty = bool(getattr(self, "_g1s_status_cache_dirty", False))
        snapshot_ts = float(getattr(self, "_g1s_status_snapshot_ts", 0.0) or 0.0)
        materialization = dict(body.get("status_materialization") or {})
        materialization["request_time_sqlite_access"] = False
        materialization["cached_snapshot"] = True
        materialization["cache_dirty"] = dirty
        materialization["snapshot_age_sec"] = max(0.0, time.time() - snapshot_ts)
        if dirty:
            # Counts are still the latest durable materialized snapshot, but exact
            # row lag cannot be known without querying SQLite.  Never fabricate 0.
            materialization["presentation_state"] = "BUILDING"
            materialization["lag_rows"] = None
        body["status_materialization"] = materialization
        body["request_time_sqlite_access"] = False
        body["status_snapshot_cached"] = True
        return body

    def refresh(self, *args, **kwargs):
        result = original_refresh(*args, **kwargs)
        _cache_snapshot(self, original_status())
        return result

    def materialize(self, *args, **kwargs):
        result = original_materialize(*args, **kwargs)
        if int(result or 0) > 0:
            _mark_dirty(self)
        return result

    def resolve(self, *args, **kwargs):
        result = original_resolve(*args, **kwargs)
        if int(result or 0) > 0:
            _mark_dirty(self)
        return result

    def fit(self, *args, **kwargs):
        result = original_fit(*args, **kwargs)
        if int(result or 0) > 0:
            _mark_dirty(self)
        return result

    def record_error(self, *args, **kwargs):
        result = original_error(*args, **kwargs)
        _mark_dirty(self)
        return result

    runtime.status = types.MethodType(status, runtime)
    runtime.refresh_materialized_status = types.MethodType(refresh, runtime)
    runtime.materialize_new = types.MethodType(materialize, runtime)
    runtime.resolve_new = types.MethodType(resolve, runtime)
    runtime.fit_if_ready = types.MethodType(fit, runtime)
    runtime._error = types.MethodType(record_error, runtime)
    runtime._g1s_nonblocking_status_version = NONBLOCKING_STATUS_VERSION
