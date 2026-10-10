from __future__ import annotations

import json
import pytest

from seiltanzer import llm_decision_shadow as shadow


def test_structured_preferences_cover_all_actions_and_do_not_use_confidence_as_score():
    scores = {policy: 0.0 for policy in shadow.VALID_POLICIES}
    scores["HOLD"] = .7
    parsed = shadow._validate_model_payload({"policy": "HOLD", "confidence": .1,
        "reason_ru": "Сохранён импульс", "policy_scores": scores,
        "evidence_families": ["price_bars"], "invalidation_conditions": ["Новый adverse импульс"]})
    assert parsed["policy_scores"]["HOLD"] == .7
    assert parsed["confidence"] == .1
    assert parsed["evidence_families"] == ["price_bars"]


@pytest.mark.parametrize("invalid", [{"HOLD": .2},
    {policy: float("nan") for policy in shadow.VALID_POLICIES},
    {policy: 1.1 for policy in shadow.VALID_POLICIES},
    {policy: True for policy in shadow.VALID_POLICIES}])
def test_malformed_structured_preferences_are_rejected(invalid):
    with pytest.raises(RuntimeError, match="shadow_invalid_policy_scores"):
        shadow._validate_model_payload({"policy": "HOLD", "confidence": .8,
                                       "reason_ru": "test", "policy_scores": invalid})


def test_independent_projection_masks_nested_server_selection():
    snapshot = _snapshot(eligible=["HOLD", "CLOSE_25"])
    snapshot["policy_manager"]["gate"] = {"winner": "AI", "effective_policy": "CLOSE_25",
        "degraded_authority_overlay": {"selected": {"policy": "CLOSE_25"},
                                      "candidate_summary": {"CLOSE_25": {"qualified": True}}}}
    projection = shadow._shadow_projection(snapshot)
    gate = projection["policy_manager"]["gate"]
    assert "winner" not in gate and "effective_policy" not in gate
    assert projection["policy_manager"]["policies"]["CLOSE_25"]["cvar10_r"] == -.5


def _snapshot(*, eligible=None, include_floor=True):
    rule = {}
    if eligible is not None:
        rule["eligible"] = eligible
    if include_floor:
        rule["cvar_floor_r"] = -0.80
    return {
        "trade_id": 7,
        "policy_manager": {
            "management_decision": {"policy": "HOLD"},
            "selection_rule": rule,
            "policies": {
                "HOLD": {"expected_final_r": 0.1, "cvar10_r": -0.70},
                "CLOSE_25": {"expected_final_r": 0.08, "cvar10_r": -0.50},
            },
        },
    }


def test_explicit_empty_feasible_set_blocks_every_shadow_policy():
    ok, reasons = shadow._hard_guard(_snapshot(eligible=[]), "HOLD")
    assert ok is False
    assert "POLICY_OUTSIDE_PUBLISHED_CVAR_FEASIBLE_SET" in reasons


def test_unverifiable_hard_cvar_never_reports_pass():
    snapshot = _snapshot(eligible=None, include_floor=False)
    snapshot["policy_manager"]["policies"]["HOLD"].pop("cvar10_r")
    ok, reasons = shadow._hard_guard(snapshot, "HOLD")
    assert ok is False
    assert "HARD_CVAR_GUARD_UNAVAILABLE" in reasons


def test_shadow_provider_default_timeout_is_tightly_bounded(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "test-shadow",
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "policy": "HOLD",
                            "confidence": 0.6,
                            "reason_ru": "Expected и CVaR допускают HOLD.",
                            "key_evidence": ["HOLD CVaR10=-0.70R"],
                            "counter_evidence": [],
                            "family_assessments": {
                                "macro": {"feature_names": ["macro.actual"],
                                          "policy_scores": {"HOLD": .2}, "reason_ru": "Observed macro."},
                                "event": {"bad": "format"}},
                        }, ensure_ascii=False)
                    }
                }],
            }

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.delenv("OPENROUTER_SHADOW_TIMEOUT_SEC", raising=False)
    monkeypatch.setattr(shadow.httpx, "Client", Client)

    result = shadow.request_shadow_decision(
        _snapshot(eligible=["HOLD", "CLOSE_25"]))

    assert seen["timeout"] == 10.0
    assert result["status"] == "ok"
    assert result["production_authority"] is False
    assert result['family_assessments']['macro']['policy_scores'] == {'HOLD': .2}
    assert result['family_assessment_rejections'] == {'event': 'INVALID_WORKING_FAMILY_ASSESSMENT'}


def test_shadow_timeout_env_is_capped(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps({
                    "policy": "HOLD",
                    "confidence": 0.5,
                    "reason_ru": "test",
                    "key_evidence": [],
                    "counter_evidence": [],
                })}}],
                "model": "test",
            }

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def post(self, *_args, **_kwargs): return Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setenv("OPENROUTER_SHADOW_TIMEOUT_SEC", "99")
    monkeypatch.setattr(shadow.httpx, "Client", Client)
    shadow.request_shadow_decision(_snapshot(eligible=["HOLD"]))

    assert seen["timeout"] == 15.0
