from __future__ import annotations

from seiltanzer import ai_verdict
from seiltanzer.ai_api import success_body
from seiltanzer.llm_decision_shadow import (
    _extract_json_object,
    _hard_guard,
    _shadow_projection,
    _validate_model_payload,
    append_shadow_section,
    unavailable_shadow,
)


def _snapshot() -> dict:
    return {
        "captured_ts": 123.0,
        "trade_id": 7,
        "trade_geometry": {
            "current": 100.2,
            "entry": 100.0,
            "original_stop": 99.0,
            "current_r": 0.2,
        },
        "policy_manager": {
            "management_decision": {
                "policy": "HOLD",
                "authority": "STRATEGY",
                "execution_status": "not_required",
            },
            "recommendation": {
                "policy": "HOLD",
                "raw_optimizer_policy": "HOLD",
            },
            "selection_rule": {
                "eligible": ["HOLD", "CLOSE_10", "CLOSE_25"],
                "cvar_floor_r": -0.80,
            },
            "policies": {
                "HOLD": {"expected_final_r": 0.12, "cvar10_r": -0.70},
                "CLOSE_10": {"expected_final_r": 0.11, "cvar10_r": -0.62},
                "CLOSE_25": {"expected_final_r": 0.09, "cvar10_r": -0.50},
                "CLOSE_50": {"expected_final_r": 0.04, "cvar10_r": -0.35},
                "EXIT": {"expected_final_r": -0.01, "cvar10_r": -0.01},
            },
            "evidence": {
                "adverse_confirmations": [
                    {"metric": "tape_delta", "value": -0.42, "family": "live_tape"},
                ]
            },
        },
        "previous_reviews": [{"huge": "must not be duplicated into shadow prompt"}],
    }


def test_shadow_payload_is_strictly_parsed_and_bounded():
    payload = _extract_json_object(
        "```json\n"
        '{"policy":"CLOSE_25","confidence":0.73,"reason_ru":"tail risk",'
        '"key_evidence":["CVaR better"],"counter_evidence":["Expected lower"]}'
        "\n```"
    )
    parsed = _validate_model_payload(payload)
    assert parsed["policy"] == "CLOSE_25"
    assert parsed["confidence"] == 0.73
    assert parsed["key_evidence"] == ["CVaR better"]


def test_shadow_projection_is_bounded_and_marks_zero_authority():
    projection = _shadow_projection(_snapshot())
    assert "previous_reviews" not in projection
    assert projection["shadow_contract"]["production_authority"] is False
    assert projection["shadow_contract"]["automatic_execution_allowed"] is False
    assert projection["shadow_contract"]["quant_management_decision"]["policy"] == "HOLD"


def test_shadow_hard_guard_blocks_policy_outside_published_feasible_set():
    ok, reasons = _hard_guard(_snapshot(), "CLOSE_50")
    assert ok is False
    assert "POLICY_OUTSIDE_PUBLISHED_CVAR_FEASIBLE_SET" in reasons


def test_shadow_hard_guard_allows_published_feasible_policy():
    ok, reasons = _hard_guard(_snapshot(), "CLOSE_25")
    assert ok is True
    assert reasons == []


def test_shadow_report_is_explicitly_non_authoritative():
    report = append_shadow_section(
        "**ДЕЙСТВИЕ СЕЙЧАС** — HOLD.",
        {
            "status": "ok",
            "quant_policy": "HOLD",
            "policy": "CLOSE_25",
            "confidence": 0.81,
            "agreement": False,
            "blocked_by_hard_guard": False,
            "hard_guard_reasons": [],
            "reason_ru": "Хвостовой риск ухудшился.",
            "key_evidence": ["CVaR CLOSE_25 лучше HOLD"],
            "counter_evidence": ["Expected HOLD выше"],
        },
    )
    assert "LLM SHADOW DECISION · БЕЗ PRODUCTION AUTHORITY" in report
    assert "Quant: HOLD" in report
    assert "Независимый LLM: CLOSE_25" in report
    assert "не меняет management_decision" in report


def test_public_verdict_wrapper_preserves_quant_and_adds_shadow(monkeypatch):
    snapshot = _snapshot()
    original_decision = dict(snapshot["policy_manager"]["management_decision"])

    monkeypatch.setattr(
        ai_verdict,
        "_BASE_REQUEST_VERDICT_WITHOUT_SHADOW",
        lambda _snapshot: {
            "verdict": "**ДЕЙСТВИЕ СЕЙЧАС** — HOLD.",
            "model": "openai/test-model",
            "captured_ts": 123.0,
        },
    )
    monkeypatch.setattr(
        ai_verdict,
        "_request_llm_shadow_decision",
        lambda _snapshot: {
            "version": "llm-decision-shadow-v1",
            "status": "ok",
            "production_authority": False,
            "automatic_execution_allowed": False,
            "quant_policy": "HOLD",
            "policy": "CLOSE_25",
            "confidence": 0.77,
            "agreement": False,
            "blocked_by_hard_guard": False,
            "hard_guard_reasons": [],
            "reason_ru": "Независимый shadow вывод.",
            "key_evidence": [],
            "counter_evidence": [],
        },
    )

    result = ai_verdict.request_verdict(snapshot)
    assert snapshot["policy_manager"]["management_decision"] == original_decision
    assert result["llm_shadow_decision"]["policy"] == "CLOSE_25"
    assert "Независимый LLM: CLOSE_25" in result["verdict"]


def test_shadow_failure_never_breaks_primary_verdict(monkeypatch):
    monkeypatch.setattr(
        ai_verdict,
        "_BASE_REQUEST_VERDICT_WITHOUT_SHADOW",
        lambda _snapshot: {
            "verdict": "PRIMARY REPORT",
            "model": "openai/test-model",
            "captured_ts": 123.0,
        },
    )

    def fail(_snapshot):
        raise RuntimeError("shadow_provider_connection_failed")

    monkeypatch.setattr(ai_verdict, "_request_llm_shadow_decision", fail)
    result = ai_verdict.request_verdict(_snapshot())
    assert result["verdict"].startswith("PRIMARY REPORT")
    assert result["llm_shadow_decision"]["status"] == "unavailable"
    assert "shadow_provider_connection_failed" in result["verdict"]


def test_primary_deterministic_fallback_does_not_make_second_provider_call(monkeypatch):
    monkeypatch.setattr(
        ai_verdict,
        "_BASE_REQUEST_VERDICT_WITHOUT_SHADOW",
        lambda _snapshot: {
            "verdict": "DETERMINISTIC REPORT",
            "model": "deterministic-policy-fallback",
            "captured_ts": 123.0,
        },
    )

    def must_not_call(_snapshot):
        raise AssertionError("shadow provider should not be called after primary fallback")

    monkeypatch.setattr(ai_verdict, "_request_llm_shadow_decision", must_not_call)
    result = ai_verdict.request_verdict(_snapshot())
    assert result["llm_shadow_decision"]["status"] == "unavailable"
    assert result["llm_shadow_decision"]["reason_code"] == "PRIMARY_LLM_REPORT_FALLBACK"


def test_success_body_exposes_shadow_without_replacing_management_decision():
    shadow = unavailable_shadow(_snapshot(), "test")
    result = {
        "verdict": "REPORT",
        "model": "model",
        "management_decision": {"policy": "HOLD"},
        "llm_shadow_decision": shadow,
    }
    body = success_body(result, "req-1")
    assert body["management_decision"]["policy"] == "HOLD"
    assert body["llm_shadow_decision"]["production_authority"] is False
