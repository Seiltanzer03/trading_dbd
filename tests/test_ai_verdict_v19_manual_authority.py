from __future__ import annotations

from seiltanzer import ai_verdict_v19 as v19


def _snapshot() -> dict:
    return {
        "metric_coverage": {"summary": {"available_groups": 12, "total_groups": 12, "coverage_ratio": 1.0}},
        "trade_geometry": {"current": 100.0, "entry": 100.0, "original_stop": 99.0},
        "policy_manager": {
            "management_decision": {
                "authority": "AI_OVERRIDE",
                "policy": "CLOSE_50",
                "model_policy": "CLOSE_50",
                "execution_status": "pending_execution",
                "manual_execution_required": True,
                "incremental_close_fraction": 0.5,
                "remaining_fraction_after_action": 0.5,
                "instruction_ru": "ЗАКРЫТЬ 50% ТЕКУЩЕГО ОСТАТКА СЕЙЧАС; ПОСЛЕ ВЫПОЛНЕНИЯ ПОДТВЕРДИТЬ В ТЕРМИНАЛЕ",
            },
            "recommendation": {
                "policy": "CLOSE_50",
                "raw_optimizer_policy": "HOLD",
            },
            "risk_constraint": {
                "gross_cvar_floor_r": -1.0,
                "net_cvar_floor_r": -1.01,
                "unavoidable_deferred_cost_r": 0.01,
            },
            "selection_rule": {
                "cvar_floor_r": -1.01,
                "eligible": ["HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"],
                "best_expected_r": -0.269,
                "indifference_band_r": 0.03,
            },
            "policies": {
                "HOLD": {"expected_final_r": -0.282, "cvar10_r": -1.01},
                "CLOSE_50": {"expected_final_r": -0.276, "cvar10_r": -0.60},
                "EXIT": {"expected_final_r": -0.269, "cvar10_r": -0.27},
            },
            "gate": {
                "status": "confirmed_degraded_manual",
                "automatic_execution_allowed": False,
                "working_action_confirmed": True,
                "source_stability_share": 0.875,
                "authority_stability": {"checks": 8, "winner_counts": {"CLOSE_50": 7}},
                "degraded_authority_overlay": {
                    "evidence": {
                        "adverse_families": ["live_tape", "strategy_filter", "option_distribution"],
                        "total_adverse_count": 3,
                        "live_adverse_count": 2,
                        "observed_adverse_item_count": 4,
                    }
                },
            },
        },
    }


def test_manual_degraded_gate_does_not_turn_automatic_false_into_hold():
    report = "\n".join(v19._risk_lines(_snapshot()))

    assert "confirmed_degraded_manual" in report
    assert "ЗАКРЫТЬ 50% ТЕКУЩЕГО ОСТАТКА" in report
    assert "только вручную; автоматическое исполнение запрещено" in report
    assert "Рабочее действие: не менять позицию по этому отчёту" not in report


def test_manual_degraded_report_uses_preserved_gate_evidence_instead_of_fake_zero():
    text = """**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —
Независимые семьи подтверждений: 0.
Отдельных строк метрик: 0.

**ПОЧЕМУ ВЫБРАНО** —
старый текст

**КАЧЕСТВО ДАННЫХ** —
старый текст
"""
    report = v19.normalize_final_report(text, _snapshot())

    assert "Независимые семьи подтверждений: 3; live: 2" in report
    assert "Отдельных adverse строк метрик: 4." in report
    assert "Независимые семьи подтверждений: 0" not in report
    assert "Отдельных строк метрик: 0" not in report


def test_raw_optimizer_indifference_band_is_explained_numerically():
    report = "\n".join(v19._risk_lines(_snapshot()))

    assert "Зона безразличия Expected: +0.030R." in report
    assert "Лучший Expected -0.269R" in report
    assert "HOLD отстаёт на +0.013R" in report
    assert "наименее вмешивающаяся допустимая политика" in report


def test_missing_details_do_not_contradict_observed_gate_and_boundary():
    snapshot = _snapshot()
    snapshot["policy_manager"]["cancellation_boundary"] = {"hold_switch": {"r": -.287}}
    snapshot["policy_manager"]["gate"]["authority_stability"]["winner_counts"]["CLOSE_50"] = 1
    snapshot["report_integrity"] = {
        "raw_optimizer_stability": {"selected_count": 9, "checks": 11, "selected_share": 9 / 11},
    }
    text = """**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —
Однонаправленные семьи против удержания: нет.
Метрики против удержания: нет.

**ГРАНИЦА ОТМЕНЫ** —
Пересчитать около r=-0.287R: там net-оптимизатор переключается на HOLD.

**ПОЧЕМУ ВЫБРАНО** —
старый текст
"""
    report = v19.normalize_final_report(text, snapshot)
    assert "Однонаправленные семьи против удержания: нет" not in report
    assert "Метрики против удержания: нет" not in report
    assert "gate сохранил 4 строк" in report
    assert "Это не граница отмены итогового CLOSE_50" in report
    assert "1/8 (12.5%)" in report
    assert "1/8 (87.5%)" not in report
    assert "9/11 (81.8%)" in report
