import asyncio
import threading
import time

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from seiltanzer.app import create_app
from seiltanzer.config import Settings
from seiltanzer.g1_short_horizon_routes import install_g1_short_horizon_routes


def test_slow_passive_collector_does_not_block_live_http_or_websocket(tmp_path):
    app=create_app(Settings(demo=True,data_dir=str(tmp_path)))
    original=app.state.engine.passive.step

    def slow_step(*args,**kwargs):
        time.sleep(.35)
        return original(*args,**kwargs)

    app.state.engine.passive.step=slow_step
    with TestClient(app) as client:
        started=time.perf_counter()
        response=client.get("/api/state")
        state_elapsed=time.perf_counter()-started
        assert response.status_code == 200
        assert state_elapsed < .75

        started=time.perf_counter()
        ai=client.post("/api/ai/verdict")
        ai_elapsed=time.perf_counter()-started
        assert ai.status_code == 400
        assert ai.json()["error"]["code"] == "no_active_trade"
        assert ai_elapsed < .75

        started=time.perf_counter()
        with client.websocket_connect("/ws") as websocket:
            payload=websocket.receive_json()
        ws_elapsed=time.perf_counter()-started
        assert "ts" in payload and "feeds" in payload
        assert ws_elapsed < 2.5


def test_expensive_validation_is_not_on_live_state_path(tmp_path, monkeypatch):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    install_g1_short_horizon_routes(app)
    calls = []

    def validation_report():
        calls.append("validation")
        time.sleep(0.5)
        return {"n": 7, "production_authority": False}

    monkeypatch.setattr(app.state.engine.journal, "validation_report", validation_report)
    with TestClient(app) as client:
        started = time.perf_counter()
        state = client.get("/api/state")
        state_elapsed = time.perf_counter() - started
        assert state.status_code == 200
        assert state_elapsed < 0.4
        assert calls == []
        assert state.json()["validation"]["summary_endpoint"] == "/api/validation/summary"

        worker = client.get("/api/research/runtime/worker-status").json()["worker"]
        assert "current_phase" in worker
        assert "maintenance_running" in worker
        assert "acceptance_pause_active" in worker
        assert "acceptance_gate_run_id" in worker

        summary = client.get("/api/validation/summary")
        assert summary.status_code == 200
        assert summary.json()["n"] == 7
        assert calls == ["validation"]


def test_slow_tick_materialization_does_not_block_event_loop(tmp_path, monkeypatch):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    install_g1_short_horizon_routes(app)
    engine = app.state.engine
    started = threading.Event()
    release = threading.Event()

    for name in (
        "refresh_price", "refresh_proxy_price", "refresh_intraday",
        "refresh_vols", "refresh_daily", "refresh_chain",
        "refresh_iv_surface", "refresh_correlation",
    ):
        monkeypatch.setattr(engine.market, name, lambda: None)
    monkeypatch.setattr(engine.passive, "step", lambda: None)

    def slow_tick_payload():
        started.set()
        assert release.wait(2.0)
        return {"ts": time.time()}

    monkeypatch.setattr(engine, "tick_payload", slow_tick_payload)

    try:
        with TestClient(app) as client:
            assert started.wait(1.0)
            requested = time.perf_counter()
            response = client.get("/api/research/runtime/worker-status")
            elapsed = time.perf_counter() - requested
            assert response.status_code == 200
            assert response.json()["sqlite_access"] is False
            assert elapsed < 0.25

            requested = time.perf_counter()
            state = client.get("/api/state")
            state_elapsed = time.perf_counter() - requested
            assert state.status_code == 200
            assert "ts" in state.json()["tick"]
            assert state_elapsed < 0.25
            assert app.state.live_tick_snapshot["build_n"] >= 1
            release.set()
    finally:
        release.set()


def test_live_state_and_initial_websocket_do_not_recompute_tick(tmp_path, monkeypatch):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    cached = app.state.live_tick_snapshot["payload"]
    assert cached is not None

    calls = []

    def forbidden_recompute():
        calls.append("tick_payload")
        raise AssertionError("request-time tick recomputation is forbidden")

    monkeypatch.setattr(app.state.engine, "tick_payload", forbidden_recompute)

    state_route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/state"
    )
    assert state_route.endpoint()["tick"] == cached

    sent = []

    class Socket:
        headers = {}

        async def accept(self):
            return None

        async def send_json(self, payload):
            sent.append(payload)

        async def receive_text(self):
            raise WebSocketDisconnect()

    websocket_route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/ws"
    )
    asyncio.run(websocket_route.endpoint(Socket()))
    assert sent == [cached]

    assert calls == []
