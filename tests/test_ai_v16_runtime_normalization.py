from types import SimpleNamespace

from seiltanzer.ai_policy_v14 import (
    _COST_CTX,
    _RISK_CTX,
    _floor_for_r,
    risk_constraint,
)
from seiltanzer.ai_verdict_v16 import normalize_final_report


def test_final_report_labels_nearest_rung_and_repairs_source_percentage():
    snapshot = {
        "policy_manager": {
            "scenario_geometry": {"next_rung_r": 1.0},
            "inputs": {"T": 8.86},
            "evidence": {
                "option_barrier": {
                    "p_take": 0.16,
                    "p_stop": 0.82,
                    "no_touch": 0.02,
                }
            },
        }
    }
    old = (
        "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** — "
        "Рубеж раньше стопа: 46.4% (3015/6500). "
        "Стоп раньше рубежа: 53.6% (3485/6500).\n"
        "Устойчивость к источнику данных для HOLD: 0/8 (100.0%)."
    )

    out = normalize_final_report(old, snapshot)

    assert "Ближайшая ступень +1.000R раньше стопа: 46.4%" in out
    assert "Стоп раньше ближайшей ступени: 53.6%" in out
    assert "финальный тейк +8.860R раньше стопа: 16.0%" in out
    assert "0/8 (0.0%)" in out
    assert "Рубеж раньше стопа" not in out


def test_hard_cvar_floor_is_net_of_unavoidable_deferred_cost():
    inputs = SimpleNamespace(max_r=0.5, be_after=1.5, r0=0.6)

    fallback = risk_constraint(inputs, {}, {})
    assert fallback["gross_cvar_floor_r"] == -1.0
    assert fallback["unavoidable_deferred_cost_r"] == 0.01
    assert fallback["cvar_floor_r"] == -1.01

    explicit = risk_constraint(
        inputs,
        {"effective_stop_r": -0.5, "deferred_execution_cost_r": 0.02},
        {},
    )
    assert explicit["gross_cvar_floor_r"] == -0.5
    assert explicit["cvar_floor_r"] == -0.52

    risk_token = _RISK_CTX.set(explicit)
    cost_token = _COST_CTX.set({"deferred_full_close_r": 0.02})
    try:
        assert _floor_for_r(inputs.r0) == -0.52
    finally:
        _COST_CTX.reset(cost_token)
        _RISK_CTX.reset(risk_token)
