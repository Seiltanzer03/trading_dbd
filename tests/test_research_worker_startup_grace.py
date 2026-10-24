import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import seiltanzer.g1_research_worker as worker
from seiltanzer import production_resource_guard as resource_guard
from seiltanzer.g1_short_horizon_routes import install_g1_short_horizon_routes


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


def test_worker_status_route_is_lock_free_and_bounded(monkeypatch):
    class Routes:
        def __init__(self):
            self.routes = {}

        def add_api_route(self, path, endpoint, **_kwargs):
            self.routes[path] = endpoint

    class BombRuntime:
        def __getattr__(self, name):
            if name in {"materializer_status", "evidence_materialization_status",
                        "historical_walkforward_status"}:
                def bomb(*_args, **_kwargs):
                    raise AssertionError("worker status must not touch SQLite runtime")
                return bomb
            return lambda *args, **kwargs: {}

    app = Routes()
    app.state = SimpleNamespace(
        engine=SimpleNamespace(short_horizon=BombRuntime(), management_local=BombRuntime()),
        g1_research_worker={
            "contract_version": "g1-research-worker-v1",
            "running": True,
            "last_started_ts": 10.0,
            "last_finished_ts": 12.0,
            "last_error": None,
            "core_stage": "g1s:resolve",
            "core_stage_started_ts": 11.0,
            "memory_pressure": {"rss_mib": 64.0, "level": "normal"},
            "last_result": {
                "g1s": {"batch_limit": 500, "large_payload": list(range(1000))},
                "g1m_local": {"batch_limit": 100},
            },
        },
    )
    monkeypatch.setattr(
        "seiltanzer.g1_short_horizon_routes.install_g1_short_horizon_final_report",
        lambda: None)
    monkeypatch.setattr(
        "seiltanzer.g1_short_horizon_routes.install_g1_short_horizon_historical_wf_integrity",
        lambda: None)
    install_g1_short_horizon_routes(app)
    readings = iter((128 * 1024 ** 2, 1536 * 1024 ** 2))
    monkeypatch.setattr(resource_guard, '_rss_bytes', lambda: next(readings))
    monkeypatch.setattr(resource_guard, 'MEMORY_SOFT_MIB', 512)
    monkeypatch.setattr(resource_guard, 'MEMORY_HARD_MIB', 768)
    monkeypatch.setattr(resource_guard, 'MEMORY_CRITICAL_MIB', 1024)

    endpoint = app.routes["/api/research/runtime/worker-status"]
    assert asyncio.iscoroutinefunction(endpoint)
    body = asyncio.run(endpoint())
    assert body["sqlite_access"] is False
    assert body["production_authority"] is False
    assert body['worker']['core_stage'] == 'g1s:resolve'
    assert body['worker']['core_stage_started_ts'] == 11.0
    assert body['worker']['memory_pressure']['rss_mib'] == 128.0
    second = asyncio.run(endpoint())
    assert second['worker']['memory_pressure']['rss_mib'] == 1536.0
    assert second['worker']['memory_pressure']['level'] == 'critical'
    assert app.state.g1_research_worker['memory_pressure']['rss_mib'] == 64.0
    assert body["last_result"] == {
        "g1s": {"batch_limit": 500}, "g1m_local": {"batch_limit": 100}}
