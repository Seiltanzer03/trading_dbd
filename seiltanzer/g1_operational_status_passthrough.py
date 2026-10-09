"""Expose P0 collector health through the bounded passive status endpoint.

G.1E.2 replaces ``PassiveLearningEngine.status`` with a light/materialized view.
The P0 operational-integrity wrapper is installed on the class earlier, so that
replacement intentionally bypasses its richer status method.  This adapter is
installed *after* G.1E.2 and materializes its exact report plus persisted health
before background workers start. Refreshes may wait for SQLite; HTTP readers
consume only the last good copy with explicit freshness and errors.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import types
from typing import Any

from . import passive_learning as _pl
from .g1_operational_integrity import (
    OPERATIONAL_INTEGRITY_VERSION,
    _load_health,
)
from .passive_calibration_nonblocking import PassiveCalibrationCache


STATUS_PASSTHROUGH_VERSION = "g1-operational-status-passthrough-v2"


class PassiveStatusCache(PassiveCalibrationCache):
    """Reuse the existing single-flight materializer; date every reader view."""

    def get(self) -> dict[str, Any]:
        # Payload and its timestamp must belong to the same refresh generation.
        with self._snapshot_lock:
            body = super().get()
            meta = self.status()
        now = time.time()
        age = max(0.0, now - meta["refreshed_at"])
        stale = bool(meta["last_error"] or age > 2.0 * self.refresh_sec)
        meta.update(age_sec=round(age, 1), telemetry_status="STALE" if stale else "FRESH")
        body["status_cache"] = meta
        health = body["collector_health"]
        health["telemetry_status"] = meta["telemetry_status"]
        latest = _finite_ts(health.get("last_successful_eligible_capture_ts"))
        health["eligible_capture_age_sec"] = (
            round(max(0.0, now - latest), 1) if latest is not None else None
        )
        health["last_known_operational_status"] = health["operational_status"]
        if stale:
            health["operational_status"] = "UNKNOWN"
            body["operational_collector_status"] = "UNKNOWN"
            body["collector_status"] = "unknown"
        return body


def _finite_ts(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or abs(out) == float("inf"):
        return None
    return out


def _health_payload(self, base: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    health = _load_health(self)

    persisted_latest = _finite_ts(health.get("last_successful_eligible_capture_ts"))
    latest_recent: float | None = None
    # This time predicate bounds the result, not physical table work. Run it
    # only in startup/background materialization, never on the HTTP path.
    # A failed read must preserve the prior snapshot, not publish false zeros.
    with self._lock:
        row = self._conn.execute(
            "SELECT MAX(captured_ts) latest,"
            "SUM(CASE WHEN captured_ts>=? THEN 1 ELSE 0 END) eligible_1h,"
            "COUNT(*) eligible_24h "
            "FROM passive_market_observations "
            "WHERE evidence_eligible=1 AND captured_ts>=?",
            (now - 3600.0, now - 86400.0),
        ).fetchone()
    latest_recent = _finite_ts(row[0])
    eligible_1h = int(row[1] or 0)
    eligible_24h = int(row[2] or 0)

    candidates = [x for x in (persisted_latest, latest_recent) if x is not None]
    latest_eligible = max(candidates) if candidates else None
    if latest_eligible is not None:
        health["last_successful_eligible_capture_ts"] = latest_eligible
    age = max(0.0, now - latest_eligible) if latest_eligible is not None else None

    budget = base.get("budget") or getattr(self, "budget", {}) or {}
    cadence = float(budget.get(
        "base_observation_cadence_sec", _pl.OBSERVATION_CADENCE_SEC
    ))
    market_open = False
    if not getattr(getattr(self, "settings", None), "demo", False):
        try:
            market_open = any(
                _pl._session_state(code, now).get("is_open", False)
                for code in tuple(_pl.INSTRUMENTS)
            )
        except Exception:
            market_open = False

    failed_cycles = int(health.get("consecutive_failed_capture_cycles") or 0)
    stalled = bool(
        market_open
        and (latest_eligible is None or (age is not None and age > 2.0 * cadence))
    )
    if stalled:
        operational_status = "STALLED"
    elif failed_cycles > 0:
        operational_status = "DEGRADED"
    else:
        operational_status = "RUNNING"

    return {
        **health,
        "version": OPERATIONAL_INTEGRITY_VERSION,
        "status_passthrough_version": STATUS_PASSTHROUGH_VERSION,
        "operational_status": operational_status,
        "market_open_any_supported_instrument": market_open,
        "expected_capture_cadence_sec": cadence,
        "stall_threshold_sec": 2.0 * cadence,
        "last_successful_eligible_capture_ts": latest_eligible,
        "eligible_capture_age_sec": None if age is None else round(age, 1),
        "eligible_captures_1h": eligible_1h,
        "eligible_captures_24h": eligible_24h,
        "bounded_recent_query_hours": 24,
        "request_time_full_history_scan": False,
        "meaningful_error_persists_across_market_closed_cycle": True,
        "optional_feature_failure_cancels_core_capture": False,
        "production_authority": False,
    }


def install_operational_status_passthrough(app) -> None:
    """Wrap the already-installed G.1E.2 light status exactly once."""
    if getattr(app.state, "g1_operational_status_passthrough_installed", False):
        return
    passive = app.state.engine.passive
    bounded_status = passive.status

    def materialize():
        body = bounded_status()
        if not isinstance(body, dict):
            body = {}
        health = _health_payload(passive, body)
        body["collector_health"] = health
        body["operational_collector_status"] = health["operational_status"]
        body["collector_health_bounded"] = True
        return body

    cache = PassiveStatusCache(materialize, refresh_sec=60.0)
    cache.refresh_sync(required=True)

    def status(self):
        return cache.get()

    passive.status = types.MethodType(status, passive)
    app.state.passive_status_cache = cache
    original_lifespan = app.router.lifespan_context

    async def refresh_loop():
        while True:
            await asyncio.sleep(cache.refresh_sec)
            await asyncio.to_thread(cache.refresh_sync)

    @contextlib.asynccontextmanager
    async def cached_status_lifespan(inner_app):
        async with original_lifespan(inner_app):
            task = asyncio.create_task(refresh_loop())
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app.router.lifespan_context = cached_status_lifespan
    app.state.g1_operational_status_passthrough_installed = True
