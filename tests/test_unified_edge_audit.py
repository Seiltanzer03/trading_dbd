from copy import deepcopy

from seiltanzer import ai_verdict, ai_verdict_v19
from seiltanzer.ai_snapshot_budget_guard import _strict_authoritative_compaction
from seiltanzer.unified_edge_audit import compact_unified_ensemble, render_unified_ensemble_lines


POLICIES = ["HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT",
            "TRAIL_GAMMA_FLIP", "TIGHTEN_STOP", "EXTEND_TAKE", "REDUCE_TAKE",
            "SCALE_OUT_ON_SPIKE", "TIME_STOP", "MOVE_TO_BE"]


def audit():
    return {
        "available": True, "applied": True, "scheme": "balanced40",
        "instrument": "NAS100", "regime": "TREND",
        "selected_policy": "TIGHTEN_STOP", "selected_candidate_id": "candidate-6",
        "components": [{
            "component_id": "current_llm", "nominal_weight": .15,
            "effective_weight": .075, "quality": .8, "age_sec": 45,
            "availability": "AVAILABLE", "reason": "DUPLICATE_OPTION_CHAIN",
            "source_ids": ["chain-1"], "evidence_family_ids": ["OPTIONS"],
            "duplicate_factor": .5, "verbose_workspace": "x" * 80_000,
        }],
        "candidates": [{
            "candidate_id": f"candidate-{index}", "policy": policy,
            "parameters": {"new_stop": 105} if policy == "TIGHTEN_STOP" else {},
            "eligible": policy != "EXTEND_TAKE", "reason": "CVAR_FLOOR" if policy == "EXTEND_TAKE" else None,
            "expected_net_r": .12, "cvar10_net_r": -.4,
            "delta_expected_r": -.01, "score": .73,
            "component_contributions": {"quant": .3, "current_llm": .075},
            "paths": ["x" * 1000] * 300,
        } for index, policy in enumerate(POLICIES)],
        "counterfactuals": [{"excluded_component_id": "current_llm",
                             "selected_policy": "HOLD", "selected_candidate_id": "candidate-0"}],
        "scheme_comparisons": [{"scheme": scheme, "selected_policy": "HOLD",
                                "expected_net_r": .13, "cvar10_net_r": -.5}
                               for scheme in ["balanced40", "llm20", "quant100", "legacy"]],
    }


def snapshot():
    return {
        "metric_coverage": {"summary": {"total_groups": 1, "available_groups": 1}},
        "trade_geometry": {"current": 106},
        "policy_manager": {
            "management_decision": {"policy": "TIGHTEN_STOP", "authority": "UNIFIED_EDGE_ENSEMBLE",
                                    "execution_status": "pending_execution"},
            "recommendation": {"policy": "HOLD"},
            "risk_constraint": {"net_cvar_floor_r": -.5}, "selection_rule": {},
            "policies": {"HOLD": {"expected_final_r": .13, "cvar10_r": -.5}},
            "unified_edge_ensemble": audit(),
        },
    }


def test_audit_keeps_all_actions_and_exclusions_without_expensive_paths():
    compact = compact_unified_ensemble(audit())
    assert len(compact["candidates"]) == len(POLICIES)
    assert compact["candidates"][7]["eligible"] is False
    assert compact["candidates"][7]["reason"] == "CVAR_FLOOR"
    assert compact["candidates"][6]["parameters"] == {"new_stop": 105}
    assert all("paths" not in row for row in compact["candidates"])
    assert "verbose_workspace" not in compact["components"][0]


def test_report_exposes_economic_tradeoff_without_turning_score_into_profit():
    report = "\n".join(render_unified_ensemble_lines(audit()))
    assert "15.0% → 7.5%" in report
    assert "DUPLICATE_OPTION_CHAIN" in report
    assert "EXTEND_TAKE" in report and "исключён" in report and "CVAR_FLOOR" in report
    assert "ΔExpected/HOLD -0.010R; балл +0.730" in report
    assert "+0.730R" not in report
    assert "Без Текущий LLM: HOLD" in report
    assert all(scheme in report for scheme in ["balanced40", "llm20", "quant100", "legacy"])
    assert "историческая прибыль" in report.lower()


def test_oversized_snapshot_and_emergency_compaction_preserve_final_selection():
    snap = snapshot()
    snap["policy_manager"]["evidence"] = {"oversized": "x" * 120_000}
    snap["ede_causal_context"] = {"oversized": "x" * 120_000}
    ai_verdict._enforce_snapshot_budget_with_report_integrity(snap)
    kept = snap["policy_manager"]["unified_edge_ensemble"]
    assert kept["selected_policy"] == "TIGHTEN_STOP"
    assert len(kept["candidates"]) == 12
    assert kept["components"][0]["effective_weight"] == .075
    assert snap["snapshot_budget"]["final_bytes"] < ai_verdict.SNAPSHOT_LIMIT_BYTES
    _strict_authoritative_compaction(snap)
    assert snap["policy_manager"]["unified_edge_ensemble"] == kept


def test_final_normalization_replaces_legacy_priority_language():
    snap = snapshot()
    before = deepcopy(snap["policy_manager"]["management_decision"])
    report = ai_verdict_v19.normalize_final_report(
        "**ЕДИНЫЙ ПЛАН МЕНЕДЖМЕНТА** —\nСтарый план\n\n**КАЧЕСТВО ДАННЫХ** —\nСтарая проверка", snap)
    assert "production policy: TIGHTEN_STOP" in report
    assert "Единый ансамбль выбрал TIGHTEN_STOP" in report
    assert "Баллы и бонус не определяют победителя" not in report
    assert "подтверждённый overlay получает приоритет" not in report
    assert "**ЕДИНЫЙ ВЫБОР ДЕЙСТВИЯ**" in report
    assert snap["policy_manager"]["management_decision"] == before


def test_core_payload_keeps_list_contributions_and_family_availability():
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    snap = snapshot()
    snap["captured_ts"] = 1790870000.
    snap["strategy"] = {"instrument": "NAS100"}
    manager = snap["policy_manager"]
    manager["policies"] = {name: {"expected_final_r": .13, "cvar10_r": -.4,
                                 "outcomes_include_execution_costs": True}
                           for name in POLICIES[:5]}
    manager["selection_rule"]["eligible"] = POLICIES[:5]
    raw = build_unified_ensemble(snap, {"status": "ok", "policy": "HOLD",
                                      "policy_scores": {"HOLD": 1.}})
    compact = compact_unified_ensemble(raw)
    assert len(compact["candidates"]) == 12
    selected = next(row for row in compact["candidates"]
                    if row["candidate_id"] == compact["selected_candidate_id"])
    assert len(selected["component_contributions"]) >= 5
    assert any(row["component_id"] == "current_llm" for row in selected["component_contributions"])
    assert len(compact["edge_families"]) == len(raw["edge_families"])
    text = "\n".join(render_unified_ensemble_lines(raw))
    assert "Вклад в балл выбранного действия" in text
    assert "Текущий LLM +" in text
    assert "Общий набор сценариев не подтверждён" in text
