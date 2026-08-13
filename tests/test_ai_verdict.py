import json

import pytest

from seiltanzer.ai_verdict import (
    SYSTEM_PROMPT, SETUP_PLAYBOOKS, build_snapshot, render_policy_report,
    request_verdict,
)
from seiltanzer.config import Settings
from seiltanzer.engine import Engine
from seiltanzer.decision_research import canonical_snapshot
from seiltanzer import ai_verdict_v18


def _minimal_policy_snapshot():
    policies = {}
    for index, name in enumerate(("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")):
        policies[name] = {
            "expected_final_r": round(.10 + index * .01, 3),
            "median_final_r": round(.05 + index * .01, 3),
            "cvar10_r": round(-.60 + index * .10, 3),
            "p_next_rung_before_stop": .30,
            "p_stop_before_next_rung": .40,
            "no_event_probability": {"60m": .70},
        }
    return {
        "captured_ts": 1,
        "strategy": {"direction": "long"},
        "observation": {"exact_levels": {"entry": 100, "stop": 90, "take": 125,
                                            "current": 102}},
        "policy_manager": {
            "recommendation": {
                "policy": "CLOSE_25", "action_ru": "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС",
                "remaining_fraction": .75,
                "remaining_management": "остаток вести по исходному стопу; БУ/trailing запрещены до 1.5R",
                "next_rung_r": 1.0, "raw_optimizer_policy": "CLOSE_25",
                "gate_downgrade_reasons": [],
            },
            "policies": policies,
            "selection_rule": {"cvar_floor_r": -.60},
            "stability": {"selected_count": 9, "checks": 11, "selected_share": 9 / 11},
            "inputs": {"r0": .2, "chain_age_sec": 120, "chain_status": "ok",
                       "proxy_quality": "reference_proxy"},
            "evidence": {"adverse_confirmations": [], "supportive_contradictions": [],
                         "uncertainty_flags": []},
            "metric_coverage": {"summary": {"available_groups": 12,
                                                 "total_groups": 12,
                                                 "coverage_ratio": 1}},
            "counterfactual_attribution": {"available": False},
            "metric_changes": {"available": False},
            "cancellation_boundary": {"available": False,
                                      "reason": "На проверенной сетке переход не найден"},
        },
    }


def test_prompt_makes_quant_engine_authoritative_and_bans_vague_language():
    assert len(SETUP_PLAYBOOKS) == 16
    assert "policy_manager" in SYSTEM_PROMPT
    assert "Нельзя менять" in SYSTEM_PROMPT
    assert "локальная проекция 1–24h" in SYSTEM_PROMPT
    assert "полная корреляционная матрица" in SYSTEM_PROMPT
    assert "слишком раннее действие" in SYSTEM_PROMPT
    assert "РАСЧЁТ ПОЛИТИК" in SYSTEM_PROMPT


def test_snapshot_contains_quant_policy_and_all_recent_metric_families(tmp_path):
    engine = Engine(Settings(demo=True, data_dir=str(tmp_path)))
    try:
        trade = engine.journal.open_trade(3, "NAS100", "long", 21500, 21450, 21625)
        engine.on_trade_opened(trade)
        engine.market.refresh_price()
        engine.market.refresh_vols()
        engine.market.refresh_correlation()
        snapshot = build_snapshot(engine)
        manager = snapshot["policy_manager"]
        assert snapshot["trade_id"] == trade["id"]
        assert set(manager["policies"]) == {"HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"}
        for policy in manager["policies"].values():
            assert set(policy) >= {
                "expected_final_r", "median_final_r", "cvar10_r",
                "p_next_rung_before_stop", "p_stop_before_next_rung",
                "no_event_probability",
            }
        evidence = manager["evidence"]
        assert evidence["cone_rnd"]["center_path"]
        assert evidence["iv_surface"]["frontend_formula_match"] is True
        assert len(evidence["iv_surface"]["local_24h"]) == 7
        assert "all_pairs" in evidence["correlation"]
        assert evidence["decision_roles"]["context_only"]
        coverage = snapshot["metric_coverage"]["summary"]
        assert coverage["total_groups"] == 12
        assert coverage["all_groups_have_explicit_role"] is True
        assert snapshot["metric_history"]["samples"] >= 1
        size = len(json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8"))
        assert size <= ai_verdict_v18.SNAPSHOT_TARGET_BYTES
        assert snapshot["snapshot_budget"]["final_bytes"] == size
        chain_trigger = (manager.get("recalculation_triggers") or {}).get("chain_refresh") or {}
        assert "next_attempt_ts" not in chain_trigger
        assert canonical_snapshot(snapshot)["trade_id"] == trade["id"]
    finally:
        engine.close()


def test_saturated_metric_payload_is_deterministically_below_snapshot_limit():
    huge_rows = [{
        "symbol": f"METRIC_{index}", "available": True, "status": "available",
        "source": "x" * 500, "value": index, "detail": "y" * 2000,
    } for index in range(400)]
    snapshot = _minimal_policy_snapshot()
    snapshot.update({
        "trade_id": "trade-1", "metric_history": {"samples": 9999, "rows": huge_rows},
        "metric_coverage": {"summary": {"total_groups": 12}},
        "ede_causal_context": {"families": {"OPTIONS": {"metrics": huge_rows}}},
    })
    manager = snapshot["policy_manager"]
    manager["management_decision"] = {
        "policy": "CLOSE_25", "action": "CLOSE_25", "remaining_fraction": .75}
    manager["input_audit"] = {"rows": {"options": {"items": huge_rows}}}
    manager["option_derivative_state"] = {"metrics": {str(i): row for i, row in enumerate(huge_rows)}}
    manager["evidence"].update({
        "iv_surface": {"available": True, "frontend_formula_match": True,
                       "local_24h": huge_rows, "real_expiries": huge_rows},
        "correlation": {"available": True, "all_pairs": huge_rows},
        "unknown_saturated_research_payload": huge_rows,
    })
    decision_before = dict(manager["management_decision"])
    ai_verdict_v18._enforce_snapshot_budget(snapshot)
    first = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(first.encode("utf-8")) <= ai_verdict_v18.SNAPSHOT_TARGET_BYTES
    assert manager["management_decision"] == decision_before
    copy = json.loads(first)
    ai_verdict_v18._enforce_snapshot_budget(copy)
    second = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(second.encode("utf-8")) < ai_verdict_v18.SNAPSHOT_LIMIT_BYTES


def test_deterministic_report_is_concrete_and_contains_every_policy():
    report = render_policy_report(_minimal_policy_snapshot())
    for header in ("ДЕЙСТВИЕ", "РАСЧЁТ ПОЛИТИК", "ПОЧЕМУ ВЫБРАНО",
                   "ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ", "РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ",
                   "ПОСЛЕ ИСПОЛНЕНИЯ", "ГРАНИЦА ОТМЕНЫ", "СЛЕДУЮЩИЙ ПЕРЕСЧЁТ",
                   "КАЧЕСТВО ДАННЫХ"):
        assert header in report
    for policy in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        assert policy in report
    assert "слишком раннее действие" not in report.lower()
    assert "выше потенциальная прибыль" not in report.lower()
    assert "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС" in report
    assert "CVaR10" in report


def test_ai_key_is_server_side_and_required(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="не настроен"):
        request_verdict({"captured_ts": 1})


def test_ai_proxy_is_scoped_and_quant_settings_are_deterministic(monkeypatch):
    seen = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "ok"}}], "model": "test"}
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
    out = request_verdict({"captured_ts": 1})
    assert out["verdict"] == "ok"
    assert seen["proxy"].startswith("socks5://")
    assert seen["trust_env"] is False
    assert seen["body"]["max_tokens"] == 1100
    assert seen["body"]["temperature"] == 0.0


def test_model_cannot_change_action_or_policy_numbers(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": (
                "ДЕЙСТВИЕ — НЕ СОКРАЩАТЬ ПОЗИЦИЮ.\n"
                "РАСЧЁТ ПОЛИТИК — HOLD CLOSE_10 CLOSE_25 CLOSE_50 EXIT.\n"
                "ПОЧЕМУ ВЫБРАНО — всё хорошо.\n"
                "ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ — нет.\n"
                "РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ — нет.\nПОСЛЕ ИСПОЛНЕНИЯ — держать.\n"
                "ГРАНИЦА ОТМЕНЫ — нет.\nСЛЕДУЮЩИЙ ПЕРЕСЧЁТ — потом.\n"
                "КАЧЕСТВО ДАННЫХ — хорошее.")}}], "model": "test"}
    class Client:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def post(self, *_args, **_kwargs): return Response()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("seiltanzer.ai_verdict.httpx.Client", Client)
    snapshot = _minimal_policy_snapshot()
    out = request_verdict(snapshot)
    assert out["model"] == "deterministic-policy-fallback"
    assert out["verdict"] == render_policy_report(snapshot)
    assert "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС" in out["verdict"]
