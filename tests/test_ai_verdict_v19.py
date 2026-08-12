from __future__ import annotations

from seiltanzer import ai_verdict_v19 as v19


def _snapshot():
    return {
        "metric_coverage": {
            "summary": {
                "available_groups": 12, "total_groups": 12,
                "coverage_ratio": 1.0, "coverage_label": "12/12",
            }
        },
        "position_state": {
            "remaining_position_fraction": 1.0,
            "realized_position_fraction": 0.0,
        },
        "trade_geometry": {
            "current": 29795.4863, "entry": 29808.295,
            "original_stop": 29750.0, "active_risk_barrier": 29750.0,
            "active_risk_barrier_type": "ORIGINAL_STOP", "final_take": 29920.0,
            "current_r": -0.2197, "r_to_active_stop": 0.7803,
            "r_to_final_take": 2.1359,
            "take_first": None, "stop_or_be_first": None, "no_touch": None,
            "p50_resolution_minutes": None,
        },
        "policy_manager": {
            "management_decision": {
                "policy": "HOLD", "authority": "STRATEGY",
                "execution_status": "not_required", "continuity": None,
                "incremental_close_fraction": 0.0,
                "remaining_fraction_after_action": 1.0,
            },
            "management_arbiter": {
                "winner": "STRATEGY", "strategy_score": -0.556,
                "ai_score_before_priority": -0.556,
                "ai_score_after_priority": -0.556,
            },
            "shadow_policy_contract": {
                "old_policy": "HOLD", "new_candidate_policy": "HOLD",
                "promotion_allowed": False,
            },
            "recommendation": {
                "policy": "HOLD", "raw_optimizer_policy": "HOLD",
            },
            "risk_constraint": {
                "cvar_floor_r": -1.0, "gross_cvar_floor_r": -1.0,
                "net_cvar_floor_r": -1.01,
                "unavoidable_deferred_cost_r": 0.01,
                "source": "strategy initial stop -1R: BE threshold not reached",
                "rule": "current stop/BE; indicator trailing is outside the quantitative model",
            },
            "selection_rule": {
                "cvar_floor_r": -1.01,
                "eligible": ["HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"],
                "ineligible": {},
            },
            "execution_cost_model": {"deferred_full_close_r": 0.01},
            "policies": {
                "HOLD": {"cvar10_r": -1.01, "expected_final_r": -0.203},
            },
            "risk_tradeoff": {
                "expected_delta_vs_hold_r": 0.0,
                "cvar_improvement_vs_hold_r": 0.0,
            },
            "raw_optimizer_stability": {
                "selected_count": 11, "checks": 11, "selected_share": 1.0,
            },
            "stability": {
                "selected_count": 11, "checks": 11, "selected_share": 1.0,
            },
            "gate": {
                "status": "manual_data_conflict", "automatic_execution_allowed": False,
                "source_stability_share": 1.0,
                "authority_stability": {
                    "checks": 8, "winner_counts": {"HOLD": 8},
                },
            },
            "scenario_geometry": {
                "scenario_count": 6500, "next_rung_r": 1.0,
                "rung_first_count": 2458, "stop_first_count": 4042,
                "unresolved_count": 0, "resolved_count": 6500,
                "p_next_rung_before_stop": 0.378154,
                "p_stop_before_next_rung": 0.621846,
                "p_unresolved_full_horizon": 0.0,
                "full_horizon_minutes": 3005.1,
                "mean_event_minutes_given_resolved": 141.3,
                "no_event_windows": {
                    "60m": {
                        "events": 1598, "no_event_count": 4902,
                        "scenarios": 6500, "no_event_probability": 0.754154,
                    }
                },
            },
            "inputs": {
                "T": 1.9156, "chain_status": "delayed",
                "proxy_quality": "reference_proxy",
            },
            "input_audit": {"available_count": 10, "total_count": 10},
            "evidence": {
                "option_barrier": {
                    "p_take": 0.26, "p_stop": 0.74, "no_touch": 0.0,
                },
                "data_quality": {
                    "reliability": {
                        "level": "низкая",
                        "reasons": ["опционная цепочка: delayed"],
                    }
                },
            },
            "state_change_attribution": {
                "what_improved": [
                    {"metric": "q50_r", "delta": 0.0, "reference": "ENTRY"},
                    {"metric": "real_change", "delta": 0.01234, "reference": "ENTRY"},
                ],
                "what_deteriorated": [
                    {"metric": "barrier_ev_r", "delta": -0.0, "reference": "ENTRY"},
                ],
            },
            "option_derivative_state": {
                "metrics": {
                    "p_take": {
                        "value": 0.3415, "value_units": "probability",
                        "slope": None, "slope_units": "probability/min",
                        "acceleration": None, "sample_count": 1,
                        "time_span_minutes": 0.0, "confidence": 0.0,
                        "source_quality": 0.85,
                    },
                    "barrier_ev": {
                        "value": -0.004117, "value_units": "R",
                        "slope": None, "slope_units": "R/min",
                        "sample_count": 1, "time_span_minutes": 0.0,
                        "confidence": 0.0, "source_quality": 0.85,
                    },
                }
            },
        },
    }


def _legacy_text():
    return """**ДЕЙСТВИЕ СЕЙЧАС** — HOLD ПОДТВЕРЖДЁН.

**ЕДИНЫЙ ПЛАН МЕНЕДЖМЕНТА** —
Авторитет плана: —; политика действия: HOLD; модельный выбор: —.

**ГЕОМЕТРИЯ СДЕЛКИ** —
старая геометрия

**TAKE vs STOP/BE · execution-MC estimate** —
TAKE раньше: —; STOP: —; NO TOUCH: —.

Shadow metrics: Expected -0.196R; CVaR10 -1.010R.

**ЧТО УЛУЧШИЛОСЬ** —
q50_r: +0.00000 vs ENTRY.

**ЧТО УХУДШИЛОСЬ** —
barrier_ev_r: -0.00000 vs ENTRY.

**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** —
Один набор из 6500 путей. Ближайшая ступень +1.000R раньше стопа: 37.8% (2458/6500). Стоп раньше ближайшей ступени: 62.
По опционной barrier-модели финальный тейк +1.916R раньше стопа: 26.0%.
.2% (4042/6500).

**РАСЧЁТ ПОЛИТИК** —
HOLD: Expected net -0.203R.

**ПОЧЕМУ ВЫБРАНО** —
HOLD проходит hard CVaR: -1.010R >= -1.000R.

**КАЧЕСТВО ДАННЫХ** —
Покрытие: 0/0. Надёжность расчёта: низкая.
"""


def test_v19_repairs_production_coverage_cvar_geometry_and_authority_from_snapshot():
    report = v19.normalize_final_report(_legacy_text(), _snapshot())

    assert "Покрытие decision metrics: 12/12 (100.0%). Input audit: 10/10." in report
    assert "Покрытие: 0/0" not in report

    assert "Gross strategy CVaR floor: -1.000R." in report
    assert "Unavoidable deferred close cost: +0.010R." in report
    assert "Net selection floor: -1.010R." in report
    assert "HOLD CVaR10 net: -1.010R >= -1.010R → ELIGIBLE." in report
    assert "-1.010R >= -1.000R" not in report

    assert "37.8% (2458/6500)" in report
    assert "62.2% (4042/6500)" in report
    assert "62.\n" not in report
    assert "Авторитет плана: STRATEGY" in report
    assert "production policy: HOLD" in report
    assert "shadow/model candidate: HOLD" in report


def test_v19_explicitly_marks_authoritative_take_stop_unavailable_and_labels_layers():
    report = v19.normalize_final_report(_legacy_text(), _snapshot())
    assert "Authoritative execution-MC TAKE vs active STOP: UNAVAILABLE." in report
    assert "insufficient authoritative execution-MC data" in report
    assert "не подменяет вероятность FINAL TAKE" in report
    assert "Base production policy distribution (common execution-MC paths):" in report
    assert "Derived shadow scenario distribution:" in report


def test_v19_filters_numerical_zero_changes_and_keeps_material_delta():
    report = v19.normalize_final_report(_legacy_text(), _snapshot())
    assert "+0.00000" not in report
    assert "-0.00000" not in report
    assert "real_change: +0.01234 vs ENTRY." in report
    assert "Материального изменения относительно reference нет." in report


def test_v19_metric_audit_keeps_current_value_when_derivative_is_unavailable():
    report = v19.normalize_final_report(_legacy_text(), _snapshot())
    assert "**FULL METRIC AUDIT**" in report
    assert "p_take: current=0.3415 probability; slope=UNAVAILABLE" in report
    assert "N=1; span=0.0m; confidence=0.0%; source_quality=85.0%." in report
    assert "barrier_ev: current=-0.004117 R; slope=UNAVAILABLE" in report
