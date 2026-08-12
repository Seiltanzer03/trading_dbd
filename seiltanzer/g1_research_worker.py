"""Low-priority research worker for G.1S and G.1-M.1.

Market collection is owned by the existing passive loop. This worker only consumes
already-frozen source rows and may lag/fail without delaying quotes, trade writes or
AI Verdict. It shares SQLite durability but has no production decision authority.
"""
from __future__ import annotations

import asyncio
import contextlib
import time


RESEARCH_WORKER_VERSION = "g1-research-worker-v1"
RESEARCH_INTERVAL_SEC = 10.0


def install_research_worker(app) -> None:
    if getattr(app.state, "g1_research_worker_installed", False):
        return
    engine = app.state.engine
    app.state.g1_research_worker = {
        "contract_version": RESEARCH_WORKER_VERSION,
        "running": False,
        "last_started_ts": None,
        "last_finished_ts": None,
        "last_duration_ms": None,
        "last_result": None,
        "last_error": None,
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
                    def run_once():
                        return {
                            "g1s": engine.short_horizon.step(),
                            "g1m_local": engine.management_local.step(),
                        }
                    result = await asyncio.to_thread(run_once)
                    state["last_result"] = result
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
