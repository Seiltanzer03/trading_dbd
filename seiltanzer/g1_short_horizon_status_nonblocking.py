"""Process-local nonblocking facade for materialized G.1S presentation reads.

The durable G.1S tables remain the source of truth. This layer separates their
SQLite reads from latency-sensitive HTTP requests: status plus the bounded
``cuts``, ``barriers`` and ``path_metrics`` lists are prewarmed before uvicorn
starts and refreshed by the existing low-priority status materializer.
Request-time readers never acquire the shared passive/G1S SQLite lock.

If new observations/resolutions/models arrive before the next materialized
refresh, the cached status is deliberately marked BUILDING and lag is reported
as unknown rather than pretending the snapshot is current. Operational list
snapshots may be briefly stale, but preserve the exact durable row order and
limit semantics; no research value, label or authority is recomputed here.
"""
from __future__ import annotations

import json
import time
import types
from typing import Any, Callable


NONBLOCKING_STATUS_VERSION = "g1s-nonblocking-status-v2-operational-cache"
_OPERATIONAL_LIMITS = {
    "cuts": 500,
    "barriers": 5000,
    "path_metrics": 5000,
}


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _detached(value: Any) -> Any:
    """Detach SQLite-derived objects into immutable process-local JSON values."""
    return json.loads(_json(value))


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


def _cache_operational(runtime: Any, name: str, payload: dict[str, Any],
                       max_limit: int) -> None:
    """Cache one max-limit ordered list so smaller LIMITs are exact prefixes."""
    detached = _detached(payload if isinstance(payload, dict) else {})
    items = detached.pop("items", [])
    if not isinstance(items, list):
        items = []
    cache = getattr(runtime, "_g1s_operational_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        runtime._g1s_operational_cache = cache
    cache[name] = {
        "root": detached,
        "items": items[:max_limit],
        "max_limit": int(max_limit),
        "snapshot_ts": time.time(),
    }
    errors = getattr(runtime, "_g1s_operational_cache_errors", None)
    if isinstance(errors, dict):
        errors.pop(name, None)


def _refresh_operational(runtime: Any) -> None:
    """Refresh off the request path; retain the last good snapshot on failure."""
    originals = getattr(runtime, "_g1s_operational_originals", {})
    errors = getattr(runtime, "_g1s_operational_cache_errors", None)
    if not isinstance(errors, dict):
        errors = {}
        runtime._g1s_operational_cache_errors = errors
    for name, max_limit in _OPERATIONAL_LIMITS.items():
        fn = originals.get(name) if isinstance(originals, dict) else None
        if not callable(fn):
            continue
        try:
            _cache_operational(runtime, name, fn(limit=max_limit), max_limit)
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {str(exc)[:240]}"


def _cached_operational(runtime: Any, name: str, limit: int) -> dict[str, Any]:
    cache = getattr(runtime, "_g1s_operational_cache", {})
    row = cache.get(name) if isinstance(cache, dict) else None
    if not isinstance(row, dict):
        # Installation prewarms all three snapshots. Fail closed in memory if an
        # unexpected later corruption removes one; never fall back to SQLite on
        # a latency-sensitive request.
        return {
            "items": [],
            "status": "UNAVAILABLE",
            "reason": "NONBLOCKING_OPERATIONAL_CACHE_MISSING",
            "request_time_sqlite_access": False,
        }
    max_limit = int(row.get("max_limit") or _OPERATIONAL_LIMITS[name])
    normalized = max(1, min(int(limit), max_limit))
    root = _detached(row.get("root") or {})
    items = row.get("items") or []
    root["items"] = _detached(items[:normalized])
    return root


def install_g1_short_horizon_status_nonblocking(runtime: Any) -> None:
    """Install instance-local caches after all G.1S class refinements exist."""
    if getattr(runtime, "_g1s_nonblocking_status_version", None) == NONBLOCKING_STATUS_VERSION:
        return

    original_status: Callable[..., dict[str, Any]] = runtime.status
    original_refresh = runtime.refresh_materialized_status
    original_materialize = runtime.materialize_new
    original_resolve = runtime.resolve_new
    original_fit = runtime.fit_if_ready
    original_error = runtime._error
    original_operational = {
        "cuts": runtime.cuts,
        "barriers": runtime.barriers,
        "path_metrics": runtime.path_metrics,
    }
    runtime._g1s_operational_originals = original_operational
    runtime._g1s_operational_cache = {}
    runtime._g1s_operational_cache_errors = {}

    # Prewarm from durable truth before uvicorn starts and before the research
    # worker can contend for the shared passive/G1S SQLite lock.
    _cache_snapshot(runtime, original_status())
    for name, max_limit in _OPERATIONAL_LIMITS.items():
        _cache_operational(runtime, name,
                           original_operational[name](limit=max_limit), max_limit)

    def status(self) -> dict[str, Any]:
        raw = getattr(self, "_g1s_status_snapshot_json", "")
        if not raw:
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
            materialization["presentation_state"] = "BUILDING"
            materialization["lag_rows"] = None
        body["status_materialization"] = materialization
        body["request_time_sqlite_access"] = False
        body["status_snapshot_cached"] = True
        return body

    def refresh(self, *args, **kwargs):
        result = original_refresh(*args, **kwargs)
        _cache_snapshot(self, original_status())
        _refresh_operational(self)
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

    def cuts(self, limit: int = 100):
        return _cached_operational(self, "cuts", limit)

    def barriers(self, limit: int = 500):
        return _cached_operational(self, "barriers", limit)

    def path_metrics(self, limit: int = 500):
        return _cached_operational(self, "path_metrics", limit)

    runtime.status = types.MethodType(status, runtime)
    runtime.refresh_materialized_status = types.MethodType(refresh, runtime)
    runtime.materialize_new = types.MethodType(materialize, runtime)
    runtime.resolve_new = types.MethodType(resolve, runtime)
    runtime.fit_if_ready = types.MethodType(fit, runtime)
    runtime._error = types.MethodType(record_error, runtime)
    runtime.cuts = types.MethodType(cuts, runtime)
    runtime.barriers = types.MethodType(barriers, runtime)
    runtime.path_metrics = types.MethodType(path_metrics, runtime)
    runtime._g1s_nonblocking_status_version = NONBLOCKING_STATUS_VERSION
