import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import seiltanzer.g1_research_worker as worker


def test_research_worker_exposes_and_honors_startup_grace(monkeypatch):
    @asynccontextmanager
    async def original_lifespan(_app):
        yield

    app = SimpleNamespace(
        state=SimpleNamespace(engine=SimpleNamespace(
            short_horizon=object(), management_local=object())),
        router=SimpleNamespace(lifespan_context=original_lifespan),
    )
    monkeypatch.setattr(worker, "RESEARCH_STARTUP_GRACE_SEC", 0.01)
    worker.install_research_worker(app)

    async def exercise():
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.02)

    asyncio.run(exercise())

    state = app.state.g1_research_worker
    assert state["running"] is False
    assert state["startup_grace_sec"] == 0.01
    assert state["first_cycle_not_before_ts"] is not None
