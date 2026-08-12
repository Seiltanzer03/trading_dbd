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


# Keep the externally asserted worker API contract stable; the bounded scheduling
# behaviour is an additive scalability refinement, exposed separately below.
RESEARCH_WORKER_VERSION = "g1-research-worker-v1"
RESEARCH_WORKER_SCALABILITY_VERSION = "g1-research-worker-bounded-v2"
RESEARCH_INTERVAL_SEC = 10.0
G1S_BATCH = 500
G1M_LOCAL_BATCH = 100


def _run_g1s_bounded(runtime) -> dict:
    materialized = runtime.materialize_new(limit=G1S_BATCH)
    resolved = runtime.resolve_new(limit=G1S_BATCH)
    links = runtime.materialize_trade_links()
    models = runtime.fit_if_ready()
    # Barrier outcomes are a separate research materialization. Invoke them
    # directly with the same bounded batch instead of calling runtime.step(),
    # which would repeat the default 2,500-row materialize/resolve burst.
    from .g1_short_horizon_refinement import _materialize_barriers
    barrier_rows = _materialize_barriers(runtime, limit=G1S_BATCH)
    return {
        "materialized": materialized,
        "resolved": resolved,
        "trade_links": links,
        "models_created": models,
        "barrier_rows_created": barrier_rows,
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
                    # Separate thread turns make the G.1S and G.1-M.1 workloads
                    # independently yielding. A slow local replay cannot extend
                    # the same worker turn as the short-horizon materializer.
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
                except Exception as exc:  # fail visible; production service stays alive
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
