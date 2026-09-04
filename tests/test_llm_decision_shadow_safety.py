from __future__ import annotations

import json

from seiltanzer import llm_decision_shadow as shadow


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
