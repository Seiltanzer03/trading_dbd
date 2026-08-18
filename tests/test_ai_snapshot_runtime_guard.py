from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.ai_snapshot_runtime_guard import install_ai_snapshot_runtime_guard


class FakeMaterializer:
    def __init__(self, trade_id="1", fail=False):
        self.trade_id = trade_id
        self.fail = fail
        self.calls = 0
        self.last_error = None

    def current_trade_id(self):
        return self.trade_id

    def _build_once(self):
        self.calls += 1
        self.last_error = "RuntimeError:boom" if self.fail else None

    def status(self):
        return {"last_error": self.last_error}


def test_failed_heavy_build_is_not_retried_in_hot_loop():
    app = FastAPI()
    mat = FakeMaterializer(fail=True)
    install_ai_snapshot_runtime_guard(app, mat, failure_backoff_sec=20)

    mat._build_once()
    mat._build_once()

    assert mat.calls == 1
    status = mat.status()
    assert status["failure_retry_in_sec"] > 0
    assert status["failure_backoff_sec"] == 20


def test_successful_build_has_no_failure_backoff():
    app = FastAPI()
    mat = FakeMaterializer(fail=False)
    install_ai_snapshot_runtime_guard(app, mat, failure_backoff_sec=20)

    mat._build_once()
    mat._build_once()

    assert mat.calls == 2
    assert mat.status()["failure_retry_in_sec"] == 0


def test_no_active_trade_preserves_fast_400_contract():
    app = FastAPI()

    @app.post("/api/ai/verdict")
    def route_that_must_not_run():
        return {"ok": True}

    mat = FakeMaterializer(trade_id=None)
    install_ai_snapshot_runtime_guard(app, mat)
    response = TestClient(app).post("/api/ai/verdict")

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "no_active_trade"
    assert body["error"]["retriable"] is False
