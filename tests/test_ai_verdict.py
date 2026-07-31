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
