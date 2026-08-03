import pytest

from seiltanzer.ai_verdict import _compact, request_verdict


def test_compact_summarizes_render_arrays_but_keeps_metrics():
    out = _compact({"edge": 0.12, "strikes": list(range(100)), "hist": [1, 2]})
    assert out["edge"] == 0.12
    assert out["strikes_summary"]["count"] == 100
    assert out["hist_count"] == 2


def test_ai_key_is_server_side_and_required(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="не настроен"):
        request_verdict({"captured_tick": {"ts": 1}})


def test_ai_proxy_is_scoped_to_openrouter_client(monkeypatch):
    seen = {}
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "model": "test"}
    class Client:
        def __init__(self, **kwargs): seen.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def post(self, *_args, **_kwargs): return Response()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_PROXY", "socks5://user:pass@proxy:1080")
    monkeypatch.setattr("seiltanzer.ai_verdict.httpx.Client", Client)
    assert request_verdict({"captured_tick": {"ts": 1}})["verdict"] == "ok"
    assert seen["proxy"].startswith("socks5://")
    assert seen["trust_env"] is False
