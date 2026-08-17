from __future__ import annotations

import pytest

from seiltanzer.ai_report_semantics_guard import (
    repair_report_semantics,
    repair_snapshot_geometry,
)


def test_break_even_distance_uses_displayed_active_stop_price_long():
    snapshot = {
        "trade_geometry": {
            "entry": 4393.0,
            "original_stop": 4380.0,
            "active_risk_barrier": 4393.0,
            "active_risk_barrier_type": "BREAK_EVEN",
            "current_r": 0.9654,
            "r_to_active_stop": 1.9654,
        }
    }
    repair_snapshot_geometry(snapshot)
    assert snapshot["trade_geometry"]["r_to_active_stop"] == pytest.approx(0.9654)


def test_break_even_distance_uses_position_space_sign_short():
    snapshot = {
        "trade_geometry": {
            "entry": 100.0,
            "original_stop": 110.0,
            "active_risk_barrier": 100.0,
            "active_risk_barrier_type": "BREAK_EVEN",
            "current_r": 0.75,
            "r_to_active_stop": 1.75,
        }
    }
    repair_snapshot_geometry(snapshot)
    assert snapshot["trade_geometry"]["r_to_active_stop"] == pytest.approx(0.75)


def test_compact_report_does_not_render_zero_audit_or_bounded_placeholders():
    snapshot = {
        "snapshot_budget": {
            "report_integrity_degraded": True,
            "degrade_reason": "BASE_REPORT_INTEGRITY_BYTE_BUDGET",
        },
        "policy_manager": {
            "policies": {
                "HOLD": {"expected_final_r": 0.956, "cvar10_r": -0.01},
                "EXIT": {"expected_final_r": 0.955, "cvar10_r": 0.955},
            },
            "scenario_geometry": {},
        },
        "ede_causal_context": {
            "authority": {
                "production_directional_authority": False,
                "may_trigger_exit_or_close": False,
            }
        },
    }
    report = """**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** —
Один набор из — путей. Ближайшая ступень — раньше стопа: —.

**РАСЧЁТ ПОЛИТИК** —
Base production policy distribution (common execution-MC paths):
HOLD: Expected net +0.956R.

**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —
Ограничения: correlation_regime_shift: pairs=[{'baseline': '[bounded]', 'delta': '[bounded]'}].

**КАКИЕ ДАННЫЕ РЕАЛЬНО УЧТЕНЫ** —
Снимок аудита: время не указано. Доступно 0/0 групп.
  [bounded]: —; [bounded].
Цена инструмента: есть.

**КАЧЕСТВО ДАННЫХ** —
Покрытие decision metrics: 12/12 (100.0%). Input audit: UNAVAILABLE.

**EDE CAUSAL MARKET CONTEXT** —
IV/GEX/skew подтверждают удержание как causal context.
Authority: production_directional_authority=false; auto_promotion=false; may_trigger_exit_or_close=false.

**FULL METRIC AUDIT** —
p_take: current=0.83 probability; slope=-0.001 probability/min.
"""
    repaired = repair_report_semantics(report, snapshot)

    assert "Доступно 0/0 групп" not in repaired
    assert "[bounded]" not in repaired
    assert "Input audit: UNAVAILABLE" not in repaired
    assert "Input audit: COMPACTED" in repaired
    assert "это не означает отсутствие данных" in repaired
    assert "Детальная scenario-path geometry: UNAVAILABLE" in repaired
    assert "common execution-MC paths" not in repaired
    assert "policy outcomes preserved" in repaired
    assert "не самостоятельное production-подтверждение" in repaired
    assert "НЕ authoritative execution-MC вероятность FINAL TAKE vs active STOP/BE" in repaired


def test_non_emergency_snapshot_never_leaks_bounded_rows():
    snapshot = {
        "policy_manager": {"scenario_geometry": {"scenario_count": 6500}},
        "ede_causal_context": {"authority": {"production_directional_authority": True}},
    }
    report = """**КАКИЕ ДАННЫЕ РЕАЛЬНО УЧТЕНЫ** —
Индексы волатильности: есть.
  [bounded]: —; [bounded].
Цена инструмента: есть.

**FULL METRIC AUDIT** —
p_take: current=0.5 probability.
"""
    repaired = repair_report_semantics(report, snapshot)
    assert "[bounded]" not in repaired
    assert "Индексы волатильности: есть." in repaired
    assert "Цена инструмента: есть." in repaired


def test_source_stability_percentage_is_recomputed_from_counts():
    snapshot = {
        "policy_manager": {
            "recommendation": {"policy": "HOLD"},
            "gate": {
                "source_stability_share": 1.0,
                "authority_stability": {
                    "checks": 8,
                    "winner_counts": {"HOLD": 0},
                },
            },
            "scenario_geometry": {"scenario_count": 6500},
        },
        "ede_causal_context": {"authority": {"production_directional_authority": True}},
    }
    report = """**ПОЧЕМУ ВЫБРАНО** —
Устойчивость к источнику данных для HOLD: 0/8 (100.0%).

**FULL METRIC AUDIT** —
p_take: current=0.5 probability.
"""
    repaired = repair_report_semantics(report, snapshot)
    assert "0/8 (100.0%)" not in repaired
    assert "Устойчивость к источнику данных для HOLD: 0/8 (0.0%)." in repaired


def test_strategy_take_exit_overrides_stale_hold_wording():
    snapshot = {
        "policy_manager": {
            "management_decision": {
                "authority": "STRATEGY",
                "policy": "EXIT",
                "strategy_terminal_event": "FINAL_TAKE_REACHED",
            },
            "scenario_geometry": {"scenario_count": 6500},
        },
        "ede_causal_context": {"authority": {"production_directional_authority": True}},
    }
    report = """**ДЕЙСТВИЕ СЕЙЧАС** — HOLD ПОДТВЕРЖДЁН.
Арбитр: STRATEGY → HOLD. Причина: старый текст.

**ПОЧЕМУ ВЫБРАНО** —
Итог gate: confirmed_hold. Рабочее действие: не менять позицию по этому отчёту.

**FULL METRIC AUDIT** —
p_take: current=0.5 probability.
"""
    repaired = repair_report_semantics(report, snapshot)
    assert "HOLD ПОДТВЕРЖДЁН" not in repaired
    assert "FINAL TAKE ДОСТИГНУТ/ПЕРЕСЕЧЁН" in repaired
    assert "Арбитр: STRATEGY → EXIT" in repaired
    assert "Рабочее действие: закрыть весь текущий остаток по стратегии." in repaired


def test_full_scenario_keeps_common_path_label():
    snapshot = {
        "policy_manager": {"scenario_geometry": {"scenario_count": 6500}},
        "ede_causal_context": {"authority": {"production_directional_authority": True}},
    }
    report = """**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** —
Один набор из 6500 путей.

**РАСЧЁТ ПОЛИТИК** —
Base production policy distribution (common execution-MC paths):
HOLD: Expected net +0.100R.

**FULL METRIC AUDIT** —
p_take: current=0.5 probability.
"""
    repaired = repair_report_semantics(report, snapshot)
    assert "Base production policy distribution (common execution-MC paths):" in repaired
    assert "Один набор из 6500 путей." in repaired
