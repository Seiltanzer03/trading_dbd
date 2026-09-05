from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from seiltanzer.llm_decision_shadow import (
    BASE_POLICIES,
    EXTENDED_DYNAMIC_POLICIES,
    VALID_POLICIES,
    _disagreement_category,
    _hard_guard,
    _validate_model_payload,
    append_shadow_section,
    request_shadow_decision,
)


def test_valid_policies_contains_all_extended_dynamic_policies():
    assert "HOLD" in BASE_POLICIES
    assert "EXIT" in BASE_POLICIES
    assert "MOVE_TO_BE" in EXTENDED_DYNAMIC_POLICIES
    assert "TRAIL_GAMMA_FLIP" in EXTENDED_DYNAMIC_POLICIES
    assert "TIGHTEN_STOP" in EXTENDED_DYNAMIC_POLICIES
    assert "EXTEND_TAKE" in EXTENDED_DYNAMIC_POLICIES
    assert "REDUCE_TAKE" in EXTENDED_DYNAMIC_POLICIES
    assert "SCALE_OUT_ON_SPIKE" in EXTENDED_DYNAMIC_POLICIES
    assert "TIME_STOP" in EXTENDED_DYNAMIC_POLICIES
    for policy in EXTENDED_DYNAMIC_POLICIES:
        assert policy in VALID_POLICIES


def test_validate_model_payload_accepts_extended_policies():
    for policy in EXTENDED_DYNAMIC_POLICIES:
        payload = {
            "policy": policy,
            "confidence": 0.85,
            "reason_ru": f"Тестирование политики {policy}",
            "key_evidence": ["Факт 1", "Факт 2"],
            "counter_evidence": ["Контраргумент 1"],
        }
        validated = _validate_model_payload(payload)
        assert validated["policy"] == policy
        assert validated["confidence"] == 0.85
        assert validated["reason_ru"] == f"Тестирование политики {policy}"


def test_hard_guard_passes_for_dynamic_policies_when_hold_is_feasible():
    snapshot = {
        "policy_manager": {
            "selection_rule": {
                "eligible": ["HOLD", "CLOSE_25"],
                "cvar_floor_r": -1.0,
            },
            "policies": {
                "HOLD": {"cvar10_r": -0.75, "expected_final_r": 0.45},
                "CLOSE_25": {"cvar10_r": -0.60, "expected_final_r": 0.40},
            },
        }
    }
    for policy in EXTENDED_DYNAMIC_POLICIES:
        ok, reasons = _hard_guard(snapshot, policy)
        assert ok is True
        assert reasons == []


def test_hard_guard_blocks_dynamic_policies_when_hold_is_ineligible():
    snapshot = {
        "policy_manager": {
            "selection_rule": {
                "eligible": ["CLOSE_50", "EXIT"],
                "cvar_floor_r": -0.5,
            },
            "policies": {
                "HOLD": {"cvar10_r": -0.90, "expected_final_r": 0.10},
                "CLOSE_50": {"cvar10_r": -0.40, "expected_final_r": 0.20},
                "EXIT": {"cvar10_r": -0.10, "expected_final_r": 0.0},
            },
        }
    }
    for policy in ("MOVE_TO_BE", "TRAIL_GAMMA_FLIP", "EXTEND_TAKE", "TIME_STOP"):
        ok, reasons = _hard_guard(snapshot, policy)
        assert ok is False
        assert "POLICY_OUTSIDE_PUBLISHED_CVAR_FEASIBLE_SET" in reasons


def test_hard_guard_blocks_when_cvar_below_floor():
    snapshot = {
        "policy_manager": {
            "selection_rule": {
                "eligible": ["HOLD"],
                "cvar_floor_r": -0.50,
            },
            "policies": {
                "HOLD": {"cvar10_r": -0.80, "expected_final_r": 0.20},
            },
        }
    }
    ok, reasons = _hard_guard(snapshot, "MOVE_TO_BE")
    assert ok is False
    assert "POLICY_CVAR10_BELOW_HARD_FLOOR" in reasons


def test_disagreement_categories():
    assert _disagreement_category("HOLD", "HOLD") is None
    assert _disagreement_category("MOVE_TO_BE", "HOLD") == "DYNAMIC_STOP_MANAGEMENT"
    assert _disagreement_category("TRAIL_GAMMA_FLIP", "HOLD") == "DYNAMIC_STOP_MANAGEMENT"
    assert _disagreement_category("TIGHTEN_STOP", "HOLD") == "DYNAMIC_STOP_MANAGEMENT"
    assert _disagreement_category("EXTEND_TAKE", "HOLD") == "TAKE_PROFIT_MANAGEMENT"
    assert _disagreement_category("REDUCE_TAKE", "HOLD") == "TAKE_PROFIT_MANAGEMENT"
    assert _disagreement_category("SCALE_OUT_ON_SPIKE", "HOLD") == "CONDITIONAL_TIME_MANAGEMENT"
    assert _disagreement_category("TIME_STOP", "HOLD") == "CONDITIONAL_TIME_MANAGEMENT"
    assert _disagreement_category("CLOSE_25", "HOLD") == "EARLY_DERISK"
    assert _disagreement_category("HOLD", "CLOSE_25") == "HIGHER_CONVICTION_HOLD"


def test_append_shadow_section_includes_disagreement_category():
    shadow = {
        "status": "ok",
        "quant_policy": "HOLD",
        "policy": "TRAIL_GAMMA_FLIP",
        "confidence": 0.82,
        "agreement": False,
        "disagreement_category": "DYNAMIC_STOP_MANAGEMENT",
        "blocked_by_hard_guard": False,
        "hard_guard_reasons": [],
        "reason_ru": "Гамма дилеров сменила знак на уровне 2720.",
        "key_evidence": ["Zero-Gamma на 2720", "GEX flip"],
        "counter_evidence": ["Возможен ложный прокол"],
    }
    report = append_shadow_section("Исходный отчет", shadow)
    assert "**LLM SHADOW DECISION · БЕЗ PRODUCTION AUTHORITY**" in report
    assert "Quant: HOLD. Независимый LLM: TRAIL_GAMMA_FLIP; confidence 82.0%;" in report
    assert "с quant расходится [DYNAMIC_STOP_MANAGEMENT]; PASS hard-risk guard." in report
    assert "Гамма дилеров сменила знак на уровне 2720." in report
