import pytest

from seiltanzer.ai_verdict import (
    SYSTEM_PROMPT, SETUP_PLAYBOOKS, build_snapshot, request_verdict,
)
from seiltanzer.config import Settings
from seiltanzer.engine import Engine


def test_prompt_is_compact_scenario_manager_not_stop_repeater():
    assert len(SETUP_PLAYBOOKS) == 16
    assert "<=220 слов" in SYSTEM_PROMPT
    assert "Не пиши очевидное" in SYSTEM_PROMPT
    assert "БУ только" in SYSTEM_PROMPT


def test_snapshot_covers_visual_models_and_trade_memory(tmp_path):
    engine = Engine(Settings(demo=True, data_dir=str(tmp_path)))
    try:
        trade = engine.journal.open_trade(
            3, "NAS100", "long", 21500, 21450, 21625)
        engine.on_trade_opened(trade)
        engine.market.refresh_price()
        engine.market.refresh_vols()
        engine.market.refresh_correlation()
        snapshot = build_snapshot(engine)
        assert snapshot["trade_id"] == trade["id"]
        assert set(snapshot["observation"]) >= {
            "position", "option_probability", "probability_cone", "lattice",
            "strike_landscape", "iv_surface", "gamma", "levels",
            "volatility", "correlation", "filters", "execution", "feed_quality",
        }
        assert snapshot["metric_history"]["samples"] >= 1
    finally:
        engine.close()


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
        def post(self, *_args, **kwargs):
            seen["body"] = kwargs["json"]
            return Response()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_PROXY", "socks5://user:pass@proxy:1080")
    monkeypatch.setattr("seiltanzer.ai_verdict.httpx.Client", Client)
    assert request_verdict({"captured_tick": {"ts": 1}})["verdict"] == "ok"
    assert seen["proxy"].startswith("socks5://")
    assert seen["trust_env"] is False
    assert seen["body"]["max_tokens"] == 700
    assert seen["body"]["temperature"] == 0.1
