from seiltanzer.active_management import render_active_management
from seiltanzer.unified_edge_audit import render_unified_ensemble_lines
from seiltanzer.unified_edge_ensemble import build_unified_ensemble
from test_unified_edge_ensemble import snapshot


def test_admission_diagnostic_identifies_own_hold_and_model_without_claiming_common_bank():
    text = render_active_management([{
        "policy": "MOVE_TO_BE", "status": "blocked",
        "reason": "NO_MATERIAL_ROBUST_EXPECTED_GAIN", "parameters": {"stop_price": 100},
        "expected_hold_net_r": .16934, "expected_variant_net_r": .20546,
        "expected_delta_vs_hold_r": .03613, "paired_delta_ci95_lower_r": -.06063,
        "materiality_band_r": .03, "paths": 2400,
        "method": "two_seed_stable_path_id_paired_counterfactual_execution_paths",
    }])
    assert "Независимая консервативная проверка допуска" in text
    assert "каждая пара использует свой HOLD" in text
    assert "не заменяют общую экономику" in text
    assert "two_seed_stable_path_id_paired_counterfactual_execution_paths" in text
    assert "Expected net: HOLD +0.16934R" in text
    assert "Δ +0.03613R" in text and "-0.06063R" in text


def test_common_comparison_source_lineage_follows_used_bank_and_does_not_restore_gate(monkeypatch):
    def comparison(_snapshot, candidates):
        return {"available": True, "shared_scenario_bank": True, "version": "common-v1",
                "bank": {"source": "frozen_option_driver_comparison", "bank_id": "bank-1",
                         "exact_authoritative_bank": False},
                "candidates": {row["candidate_id"]: {
                    "available": True, "expected_net_r": .211, "cvar10_net_r": -.2,
                    "delta_expected_r": 0., "hard_risk_pass": True,
                } for row in candidates}}
    monkeypatch.setattr("seiltanzer.unified_candidate_economics.price_unified_candidates", comparison)
    value = snapshot()
    value["policy_manager"]["gate"]["degraded_authority_overlay"]["candidate_summary"]["CLOSE_10"]["qualified"] = False
    result = build_unified_ensemble(value)
    quant = next(row for row in result["components"] if row["component_id"] == "quantitative_base")
    assert quant["source_ids"] == ["frozen_option_driver_comparison:bank-1"]
    assert quant["model_version"] == "common-v1"
    close = next(row for row in result["candidates"] if row["policy"] == "CLOSE_10")
    assert close["expected_net_r"] == .211
    assert close["eligible"] is False
    assert close["reason"] == "ACTION_CONFIRMATION_NOT_PASSED"


def test_unified_report_explains_comparison_delta_cannot_replace_independent_admission():
    text = "\n".join(render_unified_ensemble_lines({
        "available": True, "shared_scenario_bank": True,
        "scenario_bank": {"source": "frozen_option_driver_comparison", "bank_id": "bank-1",
                          "exact_authoritative_bank": False,
                          "execution_assumption": "piecewise_linear_barrier_fill_no_slippage"},
        "candidates": [{"policy": "MOVE_TO_BE", "candidate_id": "be", "eligible": False,
                        "expected_net_r": .392, "delta_expected_r": .181,
                        "reason": "NO_MATERIAL_ROBUST_EXPECTED_GAIN"}],
    }))
    assert "Δ общего сравнения не заменяет" in text
    assert "Независимая консервативная проверка допуска" in text
    assert "исходный авторитетный банк использован: False" in text
    assert "ΔExpected/HOLD +0.181R" in text
