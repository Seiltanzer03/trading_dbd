import json
from copy import deepcopy

import pytest

from seiltanzer import ai_runtime_report_v20 as runtime
from seiltanzer import ai_verdict_v19 as report
from seiltanzer.llm_decision_shadow import VALID_POLICIES
from seiltanzer.unified_edge_ensemble import collect_candidates
from test_ai_report_coherence import selected_snapshot
from test_unified_edge_ensemble import snapshot as ensemble_snapshot


def structured_reply(**extra):
    return {"policy": "HOLD", "confidence": .6,
            "policy_scores": {policy: float(policy == "HOLD") for policy in VALID_POLICIES},
            "reason_ru": "Текущие факты не доказывают пользу дополнительного вмешательства.",
            **extra}


@pytest.mark.parametrize("commentary", [None, "", "Сейчас нужно закрыть позицию."])
def test_valid_structured_preference_survives_unusable_optional_prose(commentary):
    body = {"shadow_decision": structured_reply()}
    if commentary is not None:
        body["explanation_ru"] = commentary
    _, shadow = runtime._provider_payload(json.dumps(body))
    assert shadow["policy"] == "HOLD"
    assert set(shadow["policy_scores"]) == set(VALID_POLICIES)


def test_unusable_prose_does_not_hide_an_invalid_structured_preference():
    body = {"explanation_ru": "", "shadow_decision": structured_reply(policy="BUY")}
    with pytest.raises(RuntimeError, match="policy"):
        runtime._provider_payload(json.dumps(body))


def test_unified_cancellation_names_actual_authority_and_missing_boundary():
    snapshot = selected_snapshot("CLOSE_25")
    before = deepcopy(snapshot)
    lines = report._terminal_cancellation_lines(snapshot)
    text = "\n".join(lines)
    assert "UNIFIED_EDGE_ENSEMBLE" in text
    assert "risk-overlay" not in text
    assert "r=—" not in text
    assert "не рассчитана" in text
    assert snapshot == before


def test_unified_report_replaces_legacy_override_rules_with_actual_selection_contract():
    snapshot = selected_snapshot("CLOSE_25")
    text = "\n".join([
        "**ДЕЙСТВИЕ СЕЙЧАС** — CLOSE_25.",
        "**ЧТО ДОЛЖНО ИЗМЕНИТЬ РЕШЕНИЕ** —",
        "Delayed option_distribution никогда не разрешает действие без live-tape.",
        "Аварийный EXIT разрешён по старому overlay.",
        "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ** —", "Новая котировка."])
    normalized = report.normalize_final_report(text, snapshot)
    section = normalized.split("**ЧТО ДОЛЖНО ИЗМЕНИТЬ РЕШЕНИЕ**", 1)[1].split("**СЛЕДУЮЩИЙ", 1)[0]
    assert "UNIFIED_EDGE_ENSEMBLE" in section
    assert "базового контроля" in section
    assert "Аварийный EXIT разрешён" not in section
    assert "никогда не разрешает" not in section


def test_missing_metric_count_does_not_become_zero_for_present_observed_rows():
    snapshot = selected_snapshot("CLOSE_25")
    snapshot["policy_manager"]["evidence"] = {
        "adverse_confirmation_families": ["option_distribution"],
        "adverse_confirmations": [{"metric": "barrier_ev_r", "value": -.2835,
                                    "family": "option_distribution"}]}
    # Exercise the real renderer chain which used to insert a default zero.
    text = report.render_policy_report(snapshot)
    assert "Отдельных строк метрик: 0" not in text
    assert "Отдельных строк метрик: 1" in text


def test_unavailable_metric_workspace_is_not_counted_as_one_metric(monkeypatch):
    snapshot = selected_snapshot("CLOSE_25")
    snapshot["policy_manager"].pop("option_derivative_state", None)
    snapshot["policy_manager"]["evidence"] = {}
    monkeypatch.setattr(runtime, "_BASE_METRIC_AUDIT_LINES", report._metric_audit_lines, raising=False)
    text = "\n".join(runtime._metric_audit_lines(snapshot))
    assert "Audit summary: rows=0;" in text
    assert "UNAVAILABLE" in text


@pytest.mark.parametrize("policy,reason", [
    ("MOVE_TO_BE", "BREAK_EVEN_IS_NOT_A_VALID_TIGHTER_STOP"),
    ("TRAIL_GAMMA_FLIP", "GEX_CONTEXT_NOT_A_VERIFIED_EXECUTION_ANCHOR"),
    ("EXTEND_TAKE", "OPTION_WALL_NOT_A_VERIFIED_EXECUTION_ANCHOR")])
def test_source_block_without_parameters_keeps_actual_cause(policy, reason):
    snapshot = ensemble_snapshot()
    snapshot["active_management_candidates"] = [{"policy": policy,
        "parameters": {}, "status": "blocked", "reason": reason}]
    row = next(item for item in collect_candidates(snapshot) if item["policy"] == policy)
    assert row["eligible"] is False
    assert row["reason"] == reason
    assert row["expected_net_r"] is None

