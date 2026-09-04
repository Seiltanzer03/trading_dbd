from __future__ import annotations

from seiltanzer import ai_verdict


def test_shadow_skips_legacy_snapshot_byte_for_byte(monkeypatch):
    original = {
        "verdict": "legacy-provider-report",
        "model": "test-model",
        "captured_ts": 1,
    }
    monkeypatch.setattr(
        ai_verdict,
        "_BASE_REQUEST_VERDICT_WITHOUT_SHADOW",
        lambda _snapshot: dict(original),
    )

    def must_not_call(_snapshot):
        raise AssertionError("shadow must not run for incomplete legacy snapshot")

    monkeypatch.setattr(ai_verdict, "_request_llm_shadow_decision", must_not_call)
    result = ai_verdict.request_verdict({"captured_ts": 1})

    assert result == original
    assert "llm_shadow_decision" not in result


def test_shadow_requires_live_trade_and_management_decision():
    complete_manager = {
        "management_decision": {"policy": "HOLD"},
        "policies": {"HOLD": {"expected_final_r": 0.1, "cvar10_r": -0.5}},
        "selection_rule": {"cvar_floor_r": -1.0},
    }
    assert ai_verdict._shadow_contract_active({"policy_manager": complete_manager}) is False
    assert ai_verdict._shadow_contract_active({"trade_id": 7, "policy_manager": {}}) is False
    assert ai_verdict._shadow_contract_active({
        "trade_id": 7,
        "policy_manager": complete_manager,
    }) is True


def test_shadow_wrapper_preserves_v18_public_module_identity():
    assert ai_verdict.request_verdict.__module__ == "seiltanzer.ai_verdict_v18"
