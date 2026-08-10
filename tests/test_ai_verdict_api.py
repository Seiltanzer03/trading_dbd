from fastapi.testclient import TestClient

import seiltanzer.app as app_module
from seiltanzer.app import create_app
from seiltanzer.config import Settings


def _snapshot():
    return {
        "captured_ts": 1_700_000_000.0,
        "trade_id": 1,
        "policy_manager": {
            "version": "test",
            "recommendation": {"policy": "HOLD"},
            "policies": {},
        },
        "previous_reviews": [],
    }


def _client(tmp_path, monkeypatch, verdict):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    monkeypatch.setattr(app_module, "build_snapshot", lambda _engine: _snapshot())
    monkeypatch.setattr(app_module, "render_policy_report", lambda _snapshot: "DETERMINISTIC")
    monkeypatch.setattr(app_module, "request_verdict", verdict)
    return app, TestClient(app, raise_server_exceptions=False)


def test_llm_success_has_stable_contract(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch, lambda _snapshot: {
        "verdict": "LLM", "model": "test-model", "captured_ts": 1_700_000_000.0})
    try:
        response = client.post("/api/ai/verdict")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body["ok"] is True
        assert body["verdict"] == "LLM"
        assert body["model"] == "test-model"
        assert body["mode"] == "llm"
        assert body["degraded"] is False
        assert body["request_id"].startswith("ai-")
    finally:
        app.state.engine.close()


def test_provider_timeout_returns_deterministic_fallback(tmp_path, monkeypatch):
    def timeout(_snapshot):
        raise RuntimeError("OpenRouter connection failed: ReadTimeout")
    app, client = _client(tmp_path, monkeypatch, timeout)
    try:
        response = client.post("/api/ai/verdict")
        body = response.json()
        assert response.status_code == 200
        assert body["verdict"] == "DETERMINISTIC"
        assert body["mode"] == "deterministic_fallback"
        assert body["provider_error"] == {"code": "provider_timeout", "retriable": True}
    finally:
        app.state.engine.close()


def test_malformed_provider_payload_returns_structured_fallback(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch, lambda _snapshot: {})
    try:
        response = client.post("/api/ai/verdict")
        body = response.json()
        assert response.status_code == 200
        assert body["mode"] == "deterministic_fallback"
        assert body["provider_error"]["code"] == "provider_invalid_payload"
    finally:
        app.state.engine.close()


def test_unexpected_application_error_is_safe_json_500(tmp_path, monkeypatch):
    def broken(_snapshot):
        raise ValueError("secret internal detail")
    app, client = _client(tmp_path, monkeypatch, broken)
    try:
        response = client.post("/api/ai/verdict")
        body = response.json()
        assert response.status_code == 500
        assert body["error"]["code"] == "ai_internal_error"
        assert "secret" not in body["error"]["message"]
    finally:
        app.state.engine.close()


def test_snapshot_and_journal_failures_are_distinct_json(tmp_path, monkeypatch):
    app = create_app(Settings(demo=True, data_dir=str(tmp_path)))
    monkeypatch.setattr(app_module, "build_snapshot", lambda _engine: (_ for _ in ()).throw(
        TypeError("snapshot")))
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/ai/verdict")
            assert response.status_code == 500
            assert response.json()["error"]["code"] == "snapshot_error"
    finally:
        app.state.engine.close()


def test_journal_failure_and_rate_limit_are_structured(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch, lambda _snapshot: {
        "verdict": "LLM", "model": "test"})
    monkeypatch.setattr(app.state.engine.journal, "record_ai_verdict",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")))
    try:
        first = client.post("/api/ai/verdict")
        second = client.post("/api/ai/verdict")
        assert first.status_code == 500
        assert first.json()["error"]["code"] == "journal_error"
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "ai_rate_limited"
    finally:
        app.state.engine.close()
