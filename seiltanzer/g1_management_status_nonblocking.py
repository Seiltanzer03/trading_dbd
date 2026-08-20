"""Process-local nonblocking facade for G.1-M management status.

The G.1-M SQLite tables remain the durable source of truth.  This facade moves
status materialization out of the latency-sensitive HTTP request: the snapshot
is prewarmed during Engine construction and refreshed by the existing passive
research step.  Request-time ``status()`` never acquires the shared research
lock or touches SQLite.

While the research step is mutating/materializing G.1-M state, the previous
durable snapshot is served with an explicit BUILDING/dirty marker rather than
blocking the terminal or pretending the snapshot is current.
"""
from __future__ import annotations

import json
import time
import types
from typing import Any, Callable

from .g1_management_runtime import G1M_CONTRACT_VERSION, G1M_STAGE


NONBLOCKING_STATUS_VERSION = "g1m-nonblocking-status-v1"
LOCAL_NONBLOCKING_STATUS_VERSION = "g1m-local-nonblocking-status-v1"


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
    runtime._g1m_status_snapshot_json = _json(body)
    runtime._g1m_status_snapshot_ts = time.time()
    runtime._g1m_status_cache_dirty = False


def install_g1_management_status_nonblocking(runtime: Any) -> None:
    """Install an instance-local status cache without changing G.1-M authority."""
    if getattr(runtime, "_g1m_nonblocking_status_version", None) == NONBLOCKING_STATUS_VERSION:
        return

    original_status: Callable[..., dict[str, Any]] = runtime.status
    original_step = runtime.step

    # Prewarm from durable truth before uvicorn serves traffic and before the
    # passive research worker can contend for the shared SQLite/research lock.
    _cache_snapshot(runtime, original_status())

    def status(self) -> dict[str, Any]:
        raw = getattr(self, "_g1m_status_snapshot_json", "")
        if not raw:
            # Never fall back to SQLite from the HTTP path.  If the in-process
            # cache is unexpectedly missing, fail closed and remain research-only.
            return {
                "g1_stage": G1M_STAGE,
                "g1m_contract_version": G1M_CONTRACT_VERSION,
                "evidence_status": "UNAVAILABLE",
                "reason": "NONBLOCKING_STATUS_CACHE_MISSING",
                "request_time_sqlite_access": False,
                "status_snapshot_cached": False,
                "authority": {
                    "research_only": True,
                    "production_authority": False,
                    "auto_execution_allowed": False,
                    "policy_promotion_allowed": False,
                    "oos_validated": False,
                    "edge_claim_allowed": False,
                },
            }

        body = json.loads(raw)
        dirty = bool(getattr(self, "_g1m_status_cache_dirty", False))
        snapshot_ts = float(getattr(self, "_g1m_status_snapshot_ts", 0.0) or 0.0)
        materialization = dict(body.get("status_materialization") or {})
        materialization["request_time_sqlite_access"] = False
        materialization["cached_snapshot"] = True
        materialization["cache_dirty"] = dirty
        materialization["snapshot_age_sec"] = max(0.0, time.time() - snapshot_ts)
        if dirty:
            materialization["presentation_state"] = "BUILDING"
        body["status_materialization"] = materialization
        body["request_time_sqlite_access"] = False
        body["status_snapshot_cached"] = True
        return body

    def step(self, *args, **kwargs):
        # Mark first so concurrent HTTP reads never claim that a pre-step
        # snapshot is current while durable G.1-M state is being advanced.
        self._g1m_status_cache_dirty = True
        try:
            result = original_step(*args, **kwargs)
        except Exception:
            # Preserve the last known durable snapshot as BUILDING/dirty.  The
            # existing research scheduler/error handling owns retry semantics.
            raise
        _cache_snapshot(self, original_status())
        return result

    runtime.status = types.MethodType(status, runtime)
    runtime.step = types.MethodType(step, runtime)
    runtime._g1m_nonblocking_status_version = NONBLOCKING_STATUS_VERSION


def _cache_local_snapshot(runtime: Any, snapshot: dict[str, Any]) -> None:
    body = dict(snapshot)
    body["status_materialization"] = {
        "nonblocking_status_version": LOCAL_NONBLOCKING_STATUS_VERSION,
        "request_time_sqlite_access": False,
        "cached_snapshot": True,
        "cache_dirty": False,
    }
    body["request_time_sqlite_access"] = False
    body["status_snapshot_cached"] = True
    runtime._g1m_local_status_snapshot_json = _json(body)
    runtime._g1m_local_status_snapshot_ts = time.time()
    runtime._g1m_local_status_cache_dirty = False


def install_g1_management_local_status_nonblocking(runtime: Any) -> None:
    """Keep the G.1-M.1 status route off the shared research SQLite lock."""
    if (
        getattr(runtime, "_g1m_local_nonblocking_status_version", None)
        == LOCAL_NONBLOCKING_STATUS_VERSION
    ):
        return

    original_status: Callable[..., dict[str, Any]] = runtime.status
    original_materialize = runtime.materialize_windows
    original_resolve = runtime.resolve_due
    _cache_local_snapshot(runtime, original_status())

    def status(self) -> dict[str, Any]:
        raw = getattr(self, "_g1m_local_status_snapshot_json", "")
        if not raw:
            return {
                "contract_version": "g1m-local-feedback-v1",
                "evidence_status": "UNAVAILABLE",
                "reason": "NONBLOCKING_STATUS_CACHE_MISSING",
                "request_time_sqlite_access": False,
                "status_snapshot_cached": False,
                "authority": {
                    "research_only": True,
                    "production_authority": False,
                    "auto_execution_allowed": False,
                    "policy_promotion_allowed": False,
                    "edge_claim_allowed": False,
                },
            }

        body = json.loads(raw)
        dirty = bool(getattr(self, "_g1m_local_status_cache_dirty", False))
        snapshot_ts = float(
            getattr(self, "_g1m_local_status_snapshot_ts", 0.0) or 0.0
        )
        materialization = dict(body.get("status_materialization") or {})
        materialization.update({
            "request_time_sqlite_access": False,
            "cached_snapshot": True,
            "cache_dirty": dirty,
            "snapshot_age_sec": max(0.0, time.time() - snapshot_ts),
        })
        if dirty:
            materialization["presentation_state"] = "BUILDING"
        body["status_materialization"] = materialization
        body["request_time_sqlite_access"] = False
        body["status_snapshot_cached"] = True
        return body

    def refresh_after(self, operation, *args, **kwargs):
        self._g1m_local_status_cache_dirty = True
        try:
            result = operation(*args, **kwargs)
        except Exception:
            raise
        _cache_local_snapshot(self, original_status())
        return result

    def materialize_windows(self, *args, **kwargs):
        return refresh_after(self, original_materialize, *args, **kwargs)

    def resolve_due(self, *args, **kwargs):
        return refresh_after(self, original_resolve, *args, **kwargs)

    runtime.status = types.MethodType(status, runtime)
    runtime.materialize_windows = types.MethodType(materialize_windows, runtime)
    runtime.resolve_due = types.MethodType(resolve_due, runtime)
    runtime._g1m_local_nonblocking_status_version = (
        LOCAL_NONBLOCKING_STATUS_VERSION
    )
