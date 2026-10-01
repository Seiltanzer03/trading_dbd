from copy import deepcopy
import json

import pytest

from seiltanzer.unified_edge_ensemble import build_unified_ensemble, candidate_id, SCHEMES


def snapshot():
    policies = {}
    for policy, expected in (("HOLD", .10), ("CLOSE_10", .15), ("CLOSE_25", .16),
                             ("CLOSE_50", .12), ("EXIT", -.50)):
        policies[policy] = {"expected_final_r": expected, "cvar10_r": -.20,
                            "outcomes_include_execution_costs": True, "execution_cost_r": .005}
    policies["EXIT"]["cvar10_r"] = -.50
    return {"captured_ts": 1790870000., "strategy": {"instrument": "NAS100"},
            "policy_manager": {"policies": policies,
                "risk_constraint": {"net_cvar_floor_r": -.40},
                "selection_rule": {"eligible": ["HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50"],
                                   "indifference_band_r": .03},
                "gate": {"degraded_authority_overlay": {"candidate_summary": {
                    "CLOSE_10": {"qualified": True}, "CLOSE_25": {"qualified": True}}}},
                "evidence": {"context_observations": [{"family": "independent_macro_text", "available": True},
                                                        {"family": "same_news", "available": True}]},
                "management_decision": {"policy": "CLOSE_25"}}}


def llm(**extra):
    return {"status": "ok", "policy": "CLOSE_10", "confidence": 0.,
            "policy_scores": {"HOLD": 0., "CLOSE_10": 1., "CLOSE_25": -1.},
            "evidence_families": ["independent_macro_text"], **extra}


def test_current_llm_changes_authorized_ranking_without_confidence_weights():
    frozen = snapshot()
    control = deepcopy(frozen)
    audit = build_unified_ensemble(frozen, llm())
    assert audit["selected_policy"] == "CLOSE_10"
    assert next(c for c in audit["counterfactuals"] if c["excluded_component_id"] == "current_llm")["selected_policy"] == "CLOSE_25"
    current = next(c for c in audit["components"] if c["component_id"] == "current_llm")
    assert current["effective_weight"] == pytest.approx(.15)
    assert current["self_confidence_used_for_weight"] is False
    assert frozen == control
    assert len(audit["candidates"]) == 12
    assert len(audit["counterfactuals"]) == 5
    assert len(audit["scheme_comparisons"]) == 4
    json.dumps(audit, allow_nan=False)


def test_unavailable_returns_budget_to_quant_and_keeps_absence_null():
    audit = build_unified_ensemble(snapshot())
    assert audit["selected_policy"] == "CLOSE_25"
    quant = next(c for c in audit["components"] if c["component_id"] == "quantitative_base")
    assert quant["effective_weight"] == 1.
    row = next(c for c in audit["candidates"] if c["policy"] == "CLOSE_25")
    assert next(c for c in row["component_contributions"] if c["component_id"] == "current_llm")["score"] is None


@pytest.mark.parametrize("reason", ["hard_risk", "confirmation", "price", "stop", "cost"])
def test_all_expert_votes_cannot_resurrect_blocked_action(reason):
    frozen = snapshot()
    if reason == "hard_risk":
        frozen["policy_manager"]["policies"]["CLOSE_10"]["cvar10_r"] = -.5
    elif reason == "confirmation":
        frozen["policy_manager"]["gate"]["degraded_authority_overlay"]["candidate_summary"]["CLOSE_10"]["qualified"] = False
    elif reason == "price":
        frozen["policy_manager"]["input_audit"] = {"rows": {"instrument_price": {
            "available": True, "production_authority": False, "source": "Bybit reference"}}}
    elif reason == "stop":
        frozen["trade_geometry"] = {"active_risk_barrier_breached": True}
    elif reason == "cost":
        frozen["policy_manager"]["policies"]["CLOSE_10"]["outcomes_include_execution_costs"] = False
    audit = build_unified_ensemble(frozen, llm())
    assert audit["selected_policy"] != "CLOSE_10"
    row = next(c for c in audit["candidates"] if c["policy"] == "CLOSE_10")
    assert not row["eligible"]


def test_stale_and_future_expert_receive_zero_and_nominal_schemes_sum_one():
    frozen = snapshot()
    for timestamp in (frozen["captured_ts"] - 1000, frozen["captured_ts"] + 5):
        audit = build_unified_ensemble(frozen, llm(captured_ts=timestamp))
        assert next(c for c in audit["components"] if c["component_id"] == "current_llm")["effective_weight"] == 0.
        assert audit["selected_policy"] == "CLOSE_25"
    assert all(sum(weights.values()) == pytest.approx(1.) for weights in SCHEMES.values())


def test_duplicate_origin_reduces_additional_weight_without_double_counting():
    frozen = snapshot()
    frozen["policy_manager"]["active_edge_provisional_weight"] = {
        "available": True, "direction_score": -.8, "evidence_family_ids": ["same_news"]}
    audit = build_unified_ensemble(frozen, llm(evidence_families=["same_news"]))
    for identity in ("active_edge", "current_llm"):
        row = next(c for c in audit["components"] if c["component_id"] == identity)
        assert row["effective_weight"] == pytest.approx(.075)
        assert row["dependence_multiplier"] == .5
    assert sum(c["effective_weight"] for c in audit["components"]) == pytest.approx(1.)


def test_llm_cannot_self_declare_unseen_independent_news_family():
    frozen = snapshot()
    frozen["policy_manager"]["evidence"]["lineage_budget_status"] = {"available": False}
    audit = build_unified_ensemble(frozen, llm(evidence_families=["made_up_news"]))
    current = next(c for c in audit["components"] if c["component_id"] == "current_llm")
    assert current["unverified_claimed_family_ids"] == ["made_up_news"]
    assert current["dependence_multiplier"] == .5
    assert "made_up_news" not in current["evidence_family_ids"]
    assert "EVIDENCE_LINEAGE_EXCLUDED_BY_SNAPSHOT_BYTE_BUDGET" in current["reason"]


def test_instrument_qualified_option_lineage_is_shared_with_quant_and_llm():
    frozen = snapshot()
    frozen['policy_manager']['active_edge_provisional_weight'] = {
        'available': True, 'direction_score': -.5,
        'evidence_family_ids': ['NAS100:option_distribution']}
    audit = build_unified_ensemble(frozen, llm(evidence_families=['option_chain']))
    current = next(c for c in audit['components'] if c['component_id'] == 'current_llm')
    active = next(c for c in audit['components'] if c['component_id'] == 'active_edge')
    assert current['dependence_multiplier'] == pytest.approx(1/3)
    assert active['dependence_multiplier'] == pytest.approx(1/3)
    assert current['evidence_family_ids'] == ['option_distribution']
    assert not current['unverified_claimed_family_ids']


def test_movement_only_math_is_not_a_directional_close_vote():
    frozen = snapshot()
    frozen["policy_manager"]["mathematical_edge"] = {
        "available": True, "direction_score": -.9, "base_policy_eligible": False,
        "eligible_extended_roles": ["TIME_STOP", "REDUCE_TAKE"],
        "latest_bar_end_ts": frozen["captured_ts"]}
    audit = build_unified_ensemble(frozen)
    for row in audit["candidates"]:
        math_row = next(c for c in row["component_contributions"] if c["component_id"] == "mathematical_edge")
        if row["policy"] in {"HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT", "EXTEND_TAKE"}:
            assert math_row["score"] is None


def test_extended_candidates_compete_with_closes_and_keep_economics_separate():
    frozen = snapshot()
    params = {"stop_price": 19999.}
    frozen["active_management_candidates"] = [{"policy": "TIGHTEN_STOP", "parameters": params,
        "status": "eligible", "reason": "ROBUST_EXPECTED_GAIN_AND_CVAR_PASS",
        "expected_variant_net_r": .27, "expected_hold_net_r": .11,
        "expected_delta_vs_hold_r": .16, "worst_seed_cvar10_net_r": -.1,
        "worst_seed_hold_cvar10_net_r": -.2, "execution_cost_r": .01}]
    audit = build_unified_ensemble(frozen, llm())
    assert audit["selected_policy"] == "TIGHTEN_STOP"
    row = next(c for c in audit["candidates"] if c["policy"] == "TIGHTEN_STOP")
    assert row["candidate_id"] == candidate_id("TIGHTEN_STOP", params)
    assert row["expected_net_r"] == pytest.approx(.26)
    assert row["raw_expected_variant_net_r"] == .27
    assert audit["historical_profit_proven"] is False


def test_hard_floor_unavailable_cannot_be_replaced_by_zero_or_expert_vote():
    frozen = snapshot()
    frozen["policy_manager"]["risk_constraint"] = {}
    assert not build_unified_ensemble(frozen, llm())["available"]


def test_invalid_common_bank_blocks_new_expert_selection(monkeypatch):
    monkeypatch.setattr("seiltanzer.unified_candidate_economics.price_unified_candidates",
                        lambda *_: {"available": False, "reason": "INVALID_SCENARIO_BANK"})
    audit = build_unified_ensemble(snapshot(), llm())
    assert not audit["available"]
    assert audit["selected_candidate_id"] is None
    assert all(not row["eligible"] for row in audit["candidates"])


def test_profile_regime_and_freshness_limits_are_applied():
    frozen = snapshot()
    frozen["market_regime"] = {"regime": "RANGE"}
    frozen["policy_manager"]["active_edge_provisional_weight"] = {
        "available": True, "direction_score": -.8, "supported_regimes": ["TREND"],
        "observed_ts": frozen["captured_ts"] - 20, "max_age_sec": 30}
    audit = build_unified_ensemble(frozen)
    active = next(row for row in audit["components"] if row["component_id"] == "active_edge")
    assert active["effective_weight"] == 0
    assert active["reason"] == "REGIME_NOT_SUPPORTED"
    frozen["market_regime"] = "TREND"
    audit = build_unified_ensemble(frozen)
    active = next(row for row in audit["components"] if row["component_id"] == "active_edge")
    assert active["freshness_multiplier"] == pytest.approx(1/3)
    assert active["effective_weight"] > 0
    frozen["edge_regime"] = {"available": True, "contract_version": "edge-regime-working-v1"}
    audit = build_unified_ensemble(frozen)
    active = next(row for row in audit["components"] if row["component_id"] == "active_edge")
    assert active["effective_weight"] == 0
    assert active["reason"] == "REGIME_CONTRACT_UNVERIFIED"
    frozen["policy_manager"]["active_edge_provisional_weight"]["regime_contract_version"] = "edge-regime-working-v1"
    audit = build_unified_ensemble(frozen)
    assert next(row for row in audit["components"] if row["component_id"] == "active_edge")["effective_weight"] > 0
