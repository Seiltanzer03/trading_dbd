import time

from fastapi.testclient import TestClient

from seiltanzer.app import create_app
from seiltanzer.config import Settings


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
