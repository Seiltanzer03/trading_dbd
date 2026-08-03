import json

import pytest

from seiltanzer.ai_verdict import (
    SYSTEM_PROMPT, SETUP_PLAYBOOKS, build_snapshot, request_verdict,
)
from seiltanzer.config import Settings
from seiltanzer.engine import Engine


def test_prompt_is_compact_scenario_manager_not_stop_repeater():
    assert len(SETUP_PLAYBOOKS) == 16
    assert "180–260 слов" in SYSTEM_PROMPT
    assert "НЕ пересказывай FVG/AMD" in SYSTEM_PROMPT
    assert "БУ/trailing только после" in SYSTEM_PROMPT
    assert "manager_frame" in SYSTEM_PROMPT
    assert SETUP_PLAYBOOKS[11]["entry"].startswith("long after VIX")


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
        assert set(snapshot["evidence_matrix"]) == {
            "options_primary", "live_price", "levels_structure", "cross_asset",
            "oi_gamma_context", "execution_time", "data_quality",
        }
        assert set(snapshot["scenario_frame"]) >= {
            "A_continuation", "B_stall", "C_deterioration", "next_review_events",
        }
        assert "setup_guard" not in snapshot["scenario_frame"]
        assert "рынок переходит" in snapshot["scenario_frame"]["A_continuation"]["meaning"]
        assert snapshot["decision_frame"]["option_regime"]
        assert snapshot["manager_frame"]["state_name"]
        assert "текущ" in snapshot["manager_frame"]["reasons_plain"][0]
        assert "следующий плановый рубеж" in snapshot["manager_frame"]["action_now_plain"]
        assert snapshot["strategy"]["playbook"]["timeframes"] == "12H/4H/15m"
        assert snapshot["time_context"]["timezone"] == "Europe/Athens"
        assert set(snapshot["observation"]["exact_levels"]) == {
            "entry", "stop", "take", "current",
        }
        serialized = str(snapshot["metric_history"])
        assert "option_edge" not in serialized and "p_ev0" not in serialized
        assert len(json.dumps(snapshot, ensure_ascii=False)) < 25000
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
    assert seen["body"]["max_tokens"] == 650
    assert seen["body"]["temperature"] == 0.1


def test_ai_rewrites_legacy_metric_answer_once(monkeypatch):
    calls = []
    class Response:
        def __init__(self, content): self.content = content
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": self.content}}]}
    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def post(self, *_args, **kwargs):
            calls.append(kwargs["json"])
            return Response("edge null" if len(calls) == 1 else "СТАТУС — нейтрален")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("seiltanzer.ai_verdict.httpx.Client", Client)
    assert request_verdict({"captured_ts": 1})["verdict"] == "СТАТУС — нейтрален"
    assert len(calls) == 2


def test_ai_accepts_human_manager_report_without_setup_repetition(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": (
                "СОСТОЯНИЕ — зависание.\nИЗМЕНИЛОСЬ — без изменений.\n"
                "ВРЕМЯ — окно модели.\nЧТО ДЕЛАТЬ — удерживать.\nПОЧЕМУ — рынок ждёт паузу.\n"
                "ПЛАН — Продолжение → держать; Зависание → ждать; Ухудшение → защищать.\n"
                "СЛЕДУЮЩАЯ ПРОВЕРКА — новая цепочка.")}}]}
    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def post(self, *_args, **_kwargs): return Response()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("seiltanzer.ai_verdict.httpx.Client", Client)
    out = request_verdict({"manager_frame": {"state_name": "ЗАВИСАНИЕ"}})
    assert "СОСТОЯНИЕ — зависание" in out["verdict"]
    assert "setup" not in out["verdict"].lower()
