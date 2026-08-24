import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException, WebSocketDisconnect
from fastapi.testclient import TestClient

from seiltanzer.app import JournalAdd, create_app
from seiltanzer.config import SETUPS, Settings
from seiltanzer.g1_short_horizon_routes import install_g1_short_horizon_routes


def _direct_state_response(app):
    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/state"
    )
    response = route.endpoint()
    if asyncio.iscoroutine(response):
        response = asyncio.run(response)
    return response


def _direct_state_payload(app):
    response = _direct_state_response(app)
    if hasattr(response, "body"):
        return json.loads(response.body)
    return response


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
            current = app.state.live_state_snapshot["current"]
            assert current is not None
            assert current["build_n"] >= 1
            release.set()
    finally:
        release.set()


def test_live_state_and_initial_websocket_do_not_recompute_tick(tmp_path, monkeypatch):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    current = app.state.live_state_snapshot["current"]
    assert current is not None
    cached = current["payload"]["tick"]

    calls = []

    def forbidden_recompute():
        calls.append("tick_payload")
        raise AssertionError("request-time tick recomputation is forbidden")

    monkeypatch.setattr(app.state.engine, "tick_payload", forbidden_recompute)

    assert _direct_state_payload(app)["tick"] == cached

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


def test_live_state_does_not_recompute_any_request_time_dependency(
    tmp_path, monkeypatch,
):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    state_route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/state"
    )
    assert asyncio.iscoroutinefunction(state_route.endpoint)
    current = app.state.live_state_snapshot["current"]
    assert current is not None
    assert json.loads(current["encoded"]) == current["payload"]
    expected = _direct_state_payload(app)
    calls = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"request-time state recomputation: {name}")
        return fail

    engine = app.state.engine
    monkeypatch.setattr(engine, "tick_payload", forbidden("tick_payload"))
    monkeypatch.setattr(engine, "ridge_payload", forbidden("ridge_payload"))
    for name in (
        "active_trade",
        "list_trades",
        "edge_track",
        "recent_ai_verdicts",
        "setup_stats",
        "journal_counts",
    ):
        monkeypatch.setattr(engine.journal, name, forbidden(name))

    response = _direct_state_response(app)
    actual = json.loads(response.body)

    assert response.body is current["encoded"]
    assert actual == expected
    assert calls == []


def test_fresh_live_state_rebuilds_after_journal_mutation(tmp_path):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    before = _direct_state_payload(app)
    setup = next(iter(SETUPS))
    mutation_route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/journal"
        and "POST" in getattr(route, "methods", set())
    )
    trade = mutation_route.endpoint(JournalAdd(
        setup=setup, direction="long", entry=100.0, stop=99.0,
        take=102.0, result_r=1.0,
    ))
    assert all(item["id"] != trade["id"] for item in before["journal"])
    with pytest.raises(HTTPException) as invalidated:
        _direct_state_response(app)
    assert invalidated.value.status_code == 503

    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/state"
    )
    response = asyncio.run(route.endpoint(fresh=True))
    refreshed = json.loads(response.body)

    assert any(item["id"] == trade["id"] for item in refreshed["journal"])
    assert response.body is app.state.live_state_snapshot["current"]["encoded"]
    source = Path("seiltanzer/web/js/app.js").read_text(encoding="utf-8")
    assert "fetch('/api/state?fresh=true')" in source
    refresh_source = source[
        source.index("async function refreshJournalAndSetups()"):
        source.index("async function maybeRefreshRidge()")
    ]
    assert "S.tick =" not in refresh_source


def test_failed_live_state_refresh_fails_closed_and_recovers(
    tmp_path, monkeypatch,
):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    original = engine.journal.list_trades
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("state build failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(engine.journal, "list_trades", fail_once)
    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/state"
    )

    with pytest.raises(HTTPException) as failed:
        asyncio.run(route.endpoint(fresh=True))
    assert failed.value.status_code == 503
    assert app.state.live_state_snapshot["error"] == "RuntimeError"
    with pytest.raises(HTTPException) as stale:
        _direct_state_response(app)
    assert stale.value.status_code == 503

    recovered = asyncio.run(route.endpoint(fresh=True))
    assert recovered.status_code == 200
    assert app.state.live_state_snapshot["error"] is None
    assert attempts == 2


def test_cancelled_live_state_refresh_keeps_single_flight_until_worker_exits(
    tmp_path, monkeypatch,
):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    original = engine.journal.list_trades
    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/state"
    )
    first_entered = threading.Event()
    release_first = threading.Event()
    guard = threading.Lock()
    calls = 0
    active = 0
    max_active = 0
    before_build_n = app.state.live_state_snapshot["build_n"]

    def controlled_list_trades(*args, **kwargs):
        nonlocal calls, active, max_active
        with guard:
            calls += 1
            ordinal = calls
            active += 1
            max_active = max(max_active, active)
        try:
            if ordinal == 1:
                first_entered.set()
                assert release_first.wait(2.0)
            return original(*args, **kwargs)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(
        engine.journal, "list_trades", controlled_list_trades,
    )

    async def scenario():
        first = asyncio.create_task(route.endpoint(fresh=True))
        assert await asyncio.to_thread(first_entered.wait, 1.0)
        first.cancel()
        try:
            # A bare cancelled ``to_thread`` await completes immediately and
            # releases the lock. The safe path remains pending on its real
            # worker until that worker exits. Check before a second task can
            # acquire the lock, so the lock owner is unambiguous.
            await asyncio.sleep(0)
            assert not first.done()
            assert app.state.live_state_build_lock.locked()
            second = asyncio.create_task(route.endpoint(fresh=True))
            await asyncio.sleep(0)
            assert not second.done()
        finally:
            release_first.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        response = await asyncio.wait_for(second, 2.0)
        return response

    try:
        response = asyncio.run(scenario())
    finally:
        release_first.set()

    assert max_active == 1
    assert response.status_code == 200
    assert app.state.live_state_snapshot["build_n"] == before_build_n + 1


def test_superseded_live_state_revision_cannot_publish_stale_generation(
    tmp_path, monkeypatch,
):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    engine = app.state.engine
    original = engine.journal.list_trades
    state_route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/state"
    )
    mutation_route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/journal"
        and "POST" in getattr(route, "methods", set())
    )
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    snapshot = app.state.live_state_snapshot
    old_revision = snapshot["revision"]
    before_build_n = snapshot["build_n"]

    def controlled_list_trades(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(2.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        engine.journal, "list_trades", controlled_list_trades,
    )

    async def scenario():
        stale = asyncio.create_task(state_route.endpoint(fresh=True))
        assert await asyncio.to_thread(entered.wait, 1.0)
        trade = mutation_route.endpoint(JournalAdd(
            setup=next(iter(SETUPS)), direction="long", entry=100.0,
            stop=99.0, take=102.0, result_r=1.0,
        ))
        release.set()
        with pytest.raises(HTTPException) as superseded:
            await stale
        assert superseded.value.status_code == 503
        assert snapshot["revision"] == old_revision + 1
        assert snapshot["current"] is None
        assert snapshot["error"] is None
        assert snapshot["build_n"] == before_build_n
        return trade, await state_route.endpoint(fresh=True)

    try:
        trade, response = asyncio.run(scenario())
    finally:
        release.set()

    refreshed = json.loads(response.body)
    assert any(item["id"] == trade["id"] for item in refreshed["journal"])
    assert snapshot["build_n"] == before_build_n + 1
    assert snapshot["current"]["revision"] == old_revision + 1
    assert response.body is snapshot["current"]["encoded"]
