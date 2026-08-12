"""Low-priority research worker for G.1S and G.1-M.1.

Market collection is owned by the existing passive loop. This worker only consumes
already-frozen source rows and may lag/fail without delaying quotes, trade writes or
AI Verdict. It shares SQLite durability but has no production decision authority.

Work is deliberately split into bounded batches. The research worker shares the
SQLite source of truth with the market collector, so one iteration must not hold the
process around a multi-thousand-row research burst merely because backlog exists.
"""
from __future__ import annotations

import asyncio
import contextlib
import time


RESEARCH_WORKER_VERSION = "g1-research-worker-v1"
RESEARCH_WORKER_SCALABILITY_VERSION = "g1-research-worker-bounded-v5"
RESEARCH_INTERVAL_SEC = 10.0
G1S_BATCH = 500
G1M_LOCAL_BATCH = 100
FIT_GATE_INTERVAL_SEC = 15 * 60.0
TRADE_LINK_INTERVAL_SEC = 60.0


def _run_g1s_bounded(runtime) -> dict:
    materialized = runtime.materialize_new(limit=G1S_BATCH)
    resolved = runtime.resolve_new(limit=G1S_BATCH)

    status_refresh = runtime.refresh_materialized_status(limit=10000)
    now = time.time()

    links = 0
    last_links = float(getattr(runtime, "_g1s_worker_last_trade_links_ts", 0.0) or 0.0)
    if now-last_links >= TRADE_LINK_INTERVAL_SEC:
        links = runtime.materialize_trade_links()
        runtime._g1s_worker_last_trade_links_ts = now

    models = 0
    last_fit = float(getattr(runtime, "_g1s_worker_last_fit_gate_ts", 0.0) or 0.0)
    fit_due = now-last_fit >= FIT_GATE_INTERVAL_SEC
    fit_ready = any(bool(item.get("fit_allowed"))
                    for item in runtime.status().get("horizons", []))
    if fit_due:
        runtime._g1s_worker_last_fit_gate_ts = now
        if fit_ready:
            models = runtime.fit_if_ready()

    from .g1_short_horizon_refinement import _materialize_barriers
    from .g1_short_horizon_metrics_refinement import _materialize_path_metrics
    barrier_rows = _materialize_barriers(runtime, limit=G1S_BATCH)
    path_metric_rows = _materialize_path_metrics(runtime, limit=G1S_BATCH)

    # Production ShortHorizonRuntime always has this method after package install.
    # Minimal test doubles and compatibility callers are allowed to omit it; the
    # evidence cache is presentation-only and must never make core resolution fail.
    evidence_fn = getattr(runtime, "materialize_evidence_reports", None)
    evidence_reports = (
        evidence_fn() if callable(evidence_fn)
        else {"refreshed": False, "reason": "MATERIALIZER_UNAVAILABLE"}
    )
    return {
        "materialized": materialized,
        "resolved": resolved,
        "status_refresh": status_refresh,
        "trade_links": links,
        "models_created": models,
        "fit_gate_due": fit_due,
        "fit_gate_ready": fit_ready,
        "barrier_rows_created": barrier_rows,
        "path_metrics_created": path_metric_rows,
        "evidence_reports": evidence_reports,
        "batch_limit": G1S_BATCH,
    }


def _run_g1m_local_bounded(runtime) -> dict:
    windows = runtime.materialize_windows(limit=G1M_LOCAL_BATCH)
    outcomes = runtime.resolve_due(limit=G1M_LOCAL_BATCH)
    return {
        "windows_created": windows,
        "outcomes_resolved": outcomes,
        "batch_limit": G1M_LOCAL_BATCH,
    }


def install_research_worker(app) -> None:
    if getattr(app.state, "g1_research_worker_installed", False):
        return
    engine = app.state.engine
    app.state.g1_research_worker = {
        "contract_version": RESEARCH_WORKER_VERSION,
        "scalability_refinement_version": RESEARCH_WORKER_SCALABILITY_VERSION,
        "running": False,
        "last_started_ts": None,
        "last_finished_ts": None,
        "last_duration_ms": None,
        "last_result": None,
        "last_error": None,
        "g1s_batch_limit": G1S_BATCH,
        "g1m_local_batch_limit": G1M_LOCAL_BATCH,
        "fit_gate_interval_sec": FIT_GATE_INTERVAL_SEC,
        "trade_link_interval_sec": TRADE_LINK_INTERVAL_SEC,
        "evidence_reports_request_time_scan": False,
    }
    original_lifespan = app.router.lifespan_context

    async def loop():
        state = app.state.g1_research_worker
        state["running"] = True
        try:
            while True:
                started = time.time()
                state["last_started_ts"] = started
                try:
                    g1s_result = await asyncio.to_thread(
                        _run_g1s_bounded, engine.short_horizon)
                    await asyncio.sleep(0)
                    g1m_result = await asyncio.to_thread(
                        _run_g1m_local_bounded, engine.management_local)
                    state["last_result"] = {
                        "g1s": g1s_result,
                        "g1m_local": g1m_result,
                    }
                    state["last_error"] = None
                except Exception as exc:
                    state["last_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
                state["last_finished_ts"] = time.time()
                state["last_duration_ms"] = (
                    state["last_finished_ts"] - started) * 1000.0
                await asyncio.sleep(RESEARCH_INTERVAL_SEC)
        finally:
            state["running"] = False

    @contextlib.asynccontextmanager
    async def research_lifespan(inner_app):
        task = None
        async with original_lifespan(inner_app):
            task = asyncio.create_task(loop())
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app.router.lifespan_context = research_lifespan
    app.state.g1_research_worker_installed = True
