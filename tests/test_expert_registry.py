from copy import deepcopy
import json

import pytest

from seiltanzer.expert_registry import CONTRACT, SCORE_SEMANTICS, resolve_expert_registry
from seiltanzer.unified_edge_ensemble import COMPONENTS, SCHEMES, build_unified_ensemble
from seiltanzer.unified_edge_audit import compact_unified_ensemble, render_unified_ensemble_lines
from test_unified_edge_ensemble import snapshot


def registry(frozen):
    t0 = frozen["captured_ts"]
    ids = ["liquidity_model", "session_model"]
    frozen["edge_family_sources"] = {"macro": [{
        "source_id": identity + ":source", "dependency_group": identity + ":origin",
        "instrument": "NAS100", "source_verified": True, "observed_ts": t0,
        "received_ts": t0, "quality": 1., "features": {identity + ".feature": 1.}}
        for identity in ids]}
    definitions = [{"expert_id": identity, "label": "Эксперт " + identity,
        "instrument": "NAS100", "model_version": identity + "-frozen-v1",
        "source_ids": [identity + ":source"], "evidence_family_ids": [identity + ":origin"],
        "max_age_sec": 900, "supported_regimes": ["ALL"], "score_semantics": SCORE_SEMANTICS}
        for identity in ids]
    assessments = [{"expert_id": item["expert_id"], "instrument": "NAS100",
        "model_version": item["model_version"], "available": True, "observed_ts": t0, "received_ts": t0,
        "quality": 1., "scores": {"HOLD": -1., "CLOSE_10": 1., "CLOSE_25": -1.}}
        for item in definitions]
    budgets = {**dict(zip(COMPONENTS, (.4, .10, .10, .10, .10))),
               "liquidity_model": .10, "session_model": .10}
    return {"contract_version": CONTRACT, "definitions": definitions,
            "assessments": assessments, "schemes": {"balanced": budgets}}


@pytest.mark.parametrize("receipt", [None, 1790870001.])
def test_expert_cannot_vote_without_causal_actual_receipt(receipt):
    frozen = snapshot()
    spec = registry(frozen)
    for assessment in spec["assessments"]:
        assessment["received_ts"] = receipt
    frozen["policy_manager"]["expert_registry"] = spec
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == "CLOSE_25"
    assert all(row["effective_weight"] == 0 for row in audit["components"] if row.get("registered_expert"))


def test_expert_cannot_invent_independent_sources_by_registration():
    frozen = snapshot()
    frozen["policy_manager"]["expert_registry"] = registry(frozen)
    frozen.pop("edge_family_sources")
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == "CLOSE_25"
    assert all(row["effective_weight"] == 0 for row in audit["components"] if row.get("registered_expert"))


def test_expert_cannot_use_facts_received_after_assessment_formation():
    frozen = snapshot()
    spec = registry(frozen)
    for assessment in spec["assessments"]:
        assessment["observed_ts"] -= 1
        assessment["received_ts"] -= 1
    frozen["policy_manager"]["expert_registry"] = spec
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == "CLOSE_25"
    assert all(row["effective_weight"] == 0 for row in audit["components"]
               if row.get("registered_expert"))


def test_sixth_and_seventh_registered_experts_change_only_admissible_ranking():
    frozen = snapshot()
    frozen["policy_manager"]["expert_registry"] = registry(frozen)
    before = deepcopy(frozen)
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == "CLOSE_10"
    assert len(audit["components"]) == len(audit["counterfactuals"]) == 7
    assert sum(row["nominal_weight"] for row in audit["components"]) == pytest.approx(1)
    assert sum(row["effective_weight"] for row in audit["components"]) == pytest.approx(1)
    assert next(row for row in audit["scheme_comparisons"] if row["scheme"] == "quant100")["selected_policy"] == "CLOSE_25"
    assert frozen == before
    compact = compact_unified_ensemble(audit)
    assert len(compact["expert_registry"]["definitions"]) == 2
    assert compact["expert_registry"]["definition_sha256"] == audit["expert_registry"]["definition_sha256"]
    assert len(compact["components"]) == len(compact["counterfactuals"]) == 7
    text = "\n".join(render_unified_ensemble_lines(audit))
    assert "Эксперт liquidity_model" in text and "Без Эксперт session_model" in text
    json.dumps(compact, allow_nan=False)


@pytest.mark.parametrize("defect", ["duplicate_id", "duplicate_origin", "budget_120", "partial_budget", "nan", "reserved_id"])
def test_invalid_registry_never_appends_votes_or_budget(defect):
    frozen = snapshot()
    spec = registry(frozen)
    if defect == "duplicate_id":
        spec["definitions"][1]["expert_id"] = spec["definitions"][0]["expert_id"]
    elif defect == "duplicate_origin":
        for key in ("model_version", "source_ids", "evidence_family_ids"):
            spec["definitions"][1][key] = deepcopy(spec["definitions"][0][key])
    elif defect == "budget_120":
        spec["schemes"]["balanced"]["liquidity_model"] += .2
    elif defect == "partial_budget":
        del spec["schemes"]["balanced"]["current_llm"]
    elif defect == "nan":
        spec["assessments"][0]["quality"] = float("nan")
    else:
        spec["definitions"][0]["expert_id"] = "quantitative_base"
    frozen["policy_manager"]["expert_registry"] = spec
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == "CLOSE_25"
    assert len(audit["components"]) == 5
    assert audit["expert_registry"]["available"] is False
    assert {row["component_id"]: row["nominal_weight"] for row in audit["components"]} == SCHEMES["balanced"]


@pytest.mark.parametrize("defect", ["stale", "future", "instrument", "quality", "score", "model"])
def test_invalid_assessments_return_budget_to_quant_without_risk_changes(defect):
    frozen = snapshot()
    spec = registry(frozen)
    for row in spec["assessments"]:
        if defect == "stale": row["observed_ts"] -= 1000
        elif defect == "future": row["observed_ts"] += 5
        elif defect == "instrument": row["instrument"] = "XAU"
        elif defect == "quality": row["quality"] = -1
        elif defect == "score": row["scores"]["CLOSE_10"] = 2
        else: row["model_version"] = "unregistered-model"
    frozen["policy_manager"]["expert_registry"] = spec
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == "CLOSE_25"
    assert all(row["effective_weight"] == 0 for row in audit["components"] if row.get("registered_expert"))
    assert next(row for row in audit["components"] if row["component_id"] == "quantitative_base")["effective_weight"] == 1
    assert audit["hard_risk_override"] is False


def test_registered_expert_cannot_resurrect_hard_cvar_rejected_candidate():
    frozen = snapshot()
    frozen["policy_manager"]["expert_registry"] = registry(frozen)
    frozen["policy_manager"]["policies"]["CLOSE_10"]["cvar10_r"] = -.9
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] != "CLOSE_10"
    assert not next(row for row in audit["candidates"] if row["policy"] == "CLOSE_10")["eligible"]


def test_no_registry_keeps_original_schemes_and_frozen_definition_is_order_invariant():
    rows, schemes, audit = resolve_expert_registry(snapshot(), SCHEMES)
    assert rows == [] and schemes == SCHEMES and not audit["available"]
    frozen = snapshot()
    frozen["policy_manager"]["expert_registry"] = registry(frozen)
    before = resolve_expert_registry(frozen, SCHEMES)[2]["definition_sha256"]
    frozen["policy_manager"]["expert_registry"]["definitions"].reverse()
    assert resolve_expert_registry(frozen, SCHEMES)[2]["definition_sha256"] == before


def test_zero_nominal_registry_peer_does_not_discount_active_core_voice():
    frozen = snapshot()
    spec = registry(frozen)
    for item in spec["definitions"]:
        item["evidence_family_ids"] = ["price_path"]
    spec["schemes"] = {"balanced": {**SCHEMES["balanced"], "liquidity_model": 0., "session_model": 0.}}
    frozen["policy_manager"]["expert_registry"] = spec
    base = build_unified_ensemble(snapshot())
    audit = build_unified_ensemble(frozen)
    assert audit["selected_policy"] == base["selected_policy"]
    assert [row["effective_weight"] for row in audit["components"][:5]] == [row["effective_weight"] for row in base["components"]]
