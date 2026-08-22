from __future__ import annotations

import json

import pytest

from seiltanzer import ai_provider_explanation as explanation
from seiltanzer import ai_verdict


class _Response:
    def __init__(self, content: str, model: str = "openai/test-model"):
        self._content = content
        self._model = model
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "model": self._model,
            "choices": [{"message": {"content": self._content}}],
        }


class _Client:
    def __init__(self, capture: dict, content: str):
        self.capture = capture
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, *, json=None, headers=None):
        self.capture["url"] = url
        self.capture["body"] = json
        self.capture["headers"] = headers
        return _Response(self.content)


def _snapshot() -> dict:
    return {
        "captured_ts": 1234.0,
        "trade_id": 7,
        "provider_projection": {"authority": "EXPLANATION_ONLY"},
        "policy_manager": {
            "recommendation": {
                "policy": "HOLD",
                "action_ru": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
                "automatic_execution_allowed": False,
            },
            "policies": {
                name: {
                    "expected_final_r": value,
                    "cvar10_r": value - 0.4,
                    "eligible": True,
                }
                for name, value in {
                    "HOLD": 0.10,
                    "CLOSE_10": 0.08,
                    "CLOSE_25": 0.05,
                    "CLOSE_50": 0.02,
                    "EXIT": -0.01,
                }.items()
            },
            "gate": {"status": "confirmed_hold"},
            "stability": {"stable": True},
            "input_audit": {"available_count": 8, "total_count": 12},
            "scenario_geometry": {"scenario_count": 6500},
        },
    }


def test_fast_explanation_keeps_deterministic_report_and_real_model(monkeypatch):
    snapshot = _snapshot()
    capture = {}
    deterministic = "DETERMINISTIC POLICY REPORT WITH ALL AUTHORITATIVE NUMBERS"
    llm_text = (
        "Модельный выбор устойчив по сравнению с альтернативами. Основное ограничение — "
        "неполное покрытие части входов, поэтому контекст следует читать с пониженной "
        "уверенностью. Следующий пересчёт нужен после обновления цепочки или существенного "
        "движения цены; исследовательский контекст не имеет самостоятельного торгового веса."
    )

    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-key")
    monkeypatch.setattr(ai_verdict, "render_policy_report", lambda value: deterministic)
    monkeypatch.setattr(ai_verdict, "_validate_model_report", lambda content, value: [])
    monkeypatch.setattr(
        explanation.httpx,
        "Client",
        lambda **kwargs: _Client(capture, llm_text),
    )

    result = explanation.request_explanation(snapshot, authoritative_snapshot=snapshot)

    assert result["model"] == "openai/test-model"
    assert result["provider_mode"] == "llm_explanation_over_deterministic_policy"
    assert deterministic in result["verdict"]
    assert llm_text in result["verdict"]
    assert "LLM EXPLANATION · OPENROUTER" in result["verdict"]
    assert capture["body"]["max_tokens"] == explanation.FAST_EXPLANATION_MAX_TOKENS
    assert capture["body"]["max_tokens"] < 1100
    user_prompt = capture["body"]["messages"][1]["content"]
    assert deterministic not in user_prompt
    assert "EXPLANATION_ONLY quantitative snapshot" in user_prompt
    assert "Authoritative control facts" in user_prompt
    # Provider receives the supplied bounded projection, not a regenerated copy
    # of the server-side deterministic report.
    assert json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) in user_prompt


def test_explanation_validator_treats_formatting_as_soft_but_numbers_as_hard(monkeypatch):
    snapshot = _snapshot()
    capture = {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-key")
    monkeypatch.setattr(ai_verdict, "render_policy_report", lambda value: "AUTHORITATIVE")
    monkeypatch.setattr(
        explanation.httpx,
        "Client",
        lambda **kwargs: _Client(capture, "Краткое объяснение качества входов и пересчёта."),
    )

    monkeypatch.setattr(
        ai_verdict,
        "_validate_model_report",
        lambda content, value: ["нет раздела КАЧЕСТВО ДАННЫХ"],
    )
    result = explanation.request_explanation(snapshot)
    assert result["model"] == "openai/test-model"
    assert result["validation_warnings"] == ["нет раздела КАЧЕСТВО ДАННЫХ"]

    monkeypatch.setattr(
        ai_verdict,
        "_validate_model_report",
        lambda content, value: ["изменено или пропущено HOLD.expected_final_r"],
    )
    with pytest.raises(RuntimeError, match="hard_integrity_failure"):
        explanation.request_explanation(snapshot)


def test_explanation_rejects_new_imperative_but_allows_describing_rejected_alternative():
    with pytest.raises(RuntimeError, match="attempted_trading_instruction"):
        explanation._sanitize_explanation("Сейчас нужно закрыть 50% позиции из-за риска.")

    text = explanation._sanitize_explanation(
        "CLOSE_50 был отклонён gate, поэтому эта альтернатива остаётся только сравнением."
    )
    assert "CLOSE_50" in text
