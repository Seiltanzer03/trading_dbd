from copy import deepcopy
import math

import pytest

from seiltanzer import ai_verdict, ai_verdict_v18, ai_snapshot_budget_guard, ai_verdict_budget_bridge
from seiltanzer.mathematical_action_preferences import TARGETS
from seiltanzer.operational_edge_compaction import compact_operational_edges, EXCLUDED_REASON
from seiltanzer.unified_edge_ensemble import build_unified_ensemble
from test_unified_edge_ensemble import snapshot, llm
from test_edge_family_adapters import family_fixture, snapshot as family_snapshot, T0


def operational_snapshot():
    value = snapshot()
    value["captured_ts"] = T0
    value["market_regime"] = "TREND"
    value["strategy"]["direction"] = "long"
    value["trade_geometry"] = {"current": 100., "entry": 100., "original_stop": 90.,
        "active_risk_barrier": 100.*math.exp(-.0002), "final_take": 100.*math.exp(.0002)}
    manager = value["policy_manager"]
    manager["inputs"] = {"horizon_minutes": 30., "rungs": []}
    for key, direction, quality, family in (
        ("mathematical_edge", .7, .3, "price_path"),
        ("active_edge_provisional_weight", -.8, .6, "same_news"),
        ("llm_edge_exploratory_weight", -.4, .5, "same_news")):
        manager[key] = {"available": True, "instrument": "NAS100", "regime": "TREND",
            "contract_version": "frozen-example-v1", "direction_score": direction,
            "quality_multiplier": quality, "quality": quality, "observed_ts": T0-120.,
            "latest_bar_end_ts": T0-120., "captured_ts": T0,
            "training_cutoff": T0-86400., "source_ids": ["source:"+key],
            "evidence_family_ids": [family], "base_policy_eligible": True,
            "eligible_extended_roles": ["TIME_STOP"], "horizon_minutes": 30.,
            "diagnostics": {"oversized_explanation": "x"*80000}}
    manager["mathematical_edge"]["path_predictions"] = {"upper_before_lower": {
        "probability": .8, "baseline_probability": .5, "quality_multiplier": .4,
        "horizon_minutes": 30., "training_age_days": 1.,
        "target_semantics": TARGETS["upper_before_lower"]}}
    value["report_integrity"] = {"oversized_explanation": "x"*80000}
    return value


def signatures(audit):
    fields = ("component_id", "available", "quality", "effective_weight", "observed_ts",
              "evidence_family_ids", "source_ids", "dependence_multiplier", "path_target_applicability")
    return {"selected": audit["selected_candidate_id"],
            "components": [{field: component.get(field) for field in fields}
                           for component in audit["components"]],
            "candidate_scores": [(row["candidate_id"], row.get("score"),
                                  row.get("component_contributions")) for row in audit["candidates"]]}


@pytest.mark.parametrize("tier", ["v18", "strict", "emergency", "bridge", "facade"])
def test_oversized_explanations_cannot_change_cached_operational_ranking(tier, monkeypatch):
    original = operational_snapshot()
    expected = signatures(build_unified_ensemble(original, llm(captured_ts=T0)))
    assert expected["selected"] is not None
    compacted = deepcopy(original)
    if tier == "v18":
        compacted.pop("report_integrity")
        compacted["policy_manager"]["redundant_explanation"] = "x"*80000
        ai_verdict_v18._enforce_snapshot_budget(compacted)
    elif tier == "strict":
        ai_snapshot_budget_guard._strict_authoritative_compaction(compacted)
    elif tier == "emergency":
        ai_snapshot_budget_guard._strict_authoritative_compaction(compacted)
        ai_snapshot_budget_guard._emergency_authoritative_compaction(compacted, ai_verdict)
    elif tier == "bridge":
        ai_verdict_budget_bridge._drop_duplicate_integrity_views(compacted)
    else:
        original_public = ai_verdict._enforce_snapshot_budget_with_report_integrity
        original_impl = ai_verdict._impl._enforce_snapshot_budget
        installed = ai_snapshot_budget_guard._INSTALLED
        try:
            ai_snapshot_budget_guard._INSTALLED = False
            ai_snapshot_budget_guard.install_ai_snapshot_budget_guard()
            ai_verdict._impl._enforce_snapshot_budget(compacted)
        finally:
            ai_verdict._enforce_snapshot_budget_with_report_integrity = original_public
            ai_verdict._impl._enforce_snapshot_budget = original_impl
            ai_snapshot_budget_guard._INSTALLED = installed
    assert signatures(build_unified_ensemble(compacted, llm(captured_ts=T0))) == expected
    for key in ("mathematical_edge", "active_edge_provisional_weight", "llm_edge_exploratory_weight"):
        profile = compacted["policy_manager"][key]
        assert "diagnostics" not in profile
        assert profile["observed_ts"] == T0-120.
        assert profile["instrument"] == "NAS100" and profile["regime"] == "TREND"
    assert "upper_before_lower" in compacted["policy_manager"]["mathematical_edge"]["path_predictions"]


@pytest.mark.parametrize("field,value", [("observed_ts", T0-10000), ("latest_bar_end_ts", T0+1),
                                        ("quality_multiplier", 0.)])
def test_stale_future_zero_quality_are_not_promoted_by_strict_compaction(field, value):
    frozen = operational_snapshot()
    frozen["policy_manager"]["active_edge_provisional_weight"][field] = value
    before = signatures(build_unified_ensemble(frozen))
    ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    assert signatures(build_unified_ensemble(frozen)) == before


@pytest.mark.parametrize("tier", ["v18", "strict", "emergency", "bridge"])
def test_bounded_exact_family_models_and_pit_sources_survive(tier):
    data, feature = family_fixture("macro")
    frozen = operational_snapshot()
    frozen.update(family_snapshot("macro", data, feature))
    expected = signatures(build_unified_ensemble(frozen))
    raw = deepcopy((frozen["edge_family_sources"], frozen["edge_family_models"]))
    if tier == "v18":
        ai_verdict_v18._compact_snapshot_payload(frozen)
    elif tier == "strict":
        ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    elif tier == "emergency":
        ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
        ai_snapshot_budget_guard._emergency_authoritative_compaction(frozen, ai_verdict)
    else:
        ai_verdict_budget_bridge._drop_duplicate_integrity_views(frozen)
    assert (frozen["edge_family_sources"], frozen["edge_family_models"]) == raw
    assert signatures(build_unified_ensemble(frozen)) == expected


def test_oversized_operational_input_is_explicit_unavailable_not_truncated_fresh_vote():
    frozen = operational_snapshot()
    frozen["policy_manager"]["active_edge_provisional_weight"]["source_ids"] = ["large"*3000]
    frozen["edge_family_models"] = {"macro": {"coefficients": "x"*10000}}
    frozen["edge_family_sources"] = {"macro": {"verified_fact": 3.}}
    compact_operational_edges(frozen)
    profile = frozen["policy_manager"]["active_edge_provisional_weight"]
    assert profile["available"] is False
    assert profile["quality_multiplier"] == 0.
    assert profile["reason"] == EXCLUDED_REASON
    assert "observed_ts" not in profile
    assert "edge_family_models" not in frozen
    assert frozen["edge_family_sources"] == {"macro": {"verified_fact": 3.}}
    assert frozen["edge_family_budget_status"] == {
        "contract_version": "edge-family-byte-budget-v1", "excluded_roots": ["edge_family_models"],
        "retained_roots": ["edge_family_sources"], "reason": EXCLUDED_REASON}
    ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    assert frozen["edge_family_budget_status"]["excluded_roots"] == ["edge_family_models"]


def test_oversized_whole_lineage_is_explicit_and_repeat_compaction_keeps_marker():
    frozen = operational_snapshot()
    frozen["policy_manager"]["evidence"] = {"context_observations": [
        {"family": "distinct-origin-"+str(index), "available": True} for index in range(300)]}
    ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    marker = frozen["policy_manager"]["evidence"]["lineage_budget_status"]
    assert marker == {"available": False, "reason": EXCLUDED_REASON, "partial_lineage_retained": False}
    assert "context_observations" not in frozen["policy_manager"]["evidence"]
    ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    ai_snapshot_budget_guard._emergency_authoritative_compaction(frozen, ai_verdict)
    assert frozen["policy_manager"]["evidence"]["lineage_budget_status"] == marker


def test_old_presentation_copy_cannot_resurrect_excluded_mathematical_path_vote():
    frozen = operational_snapshot()
    profile = frozen["policy_manager"]["mathematical_edge"]
    frozen["report_integrity"] = {"mathematical_edge": deepcopy(profile)}
    profile["source_ids"] = ["x"*10000]
    ai_verdict._enforce_snapshot_budget_with_report_integrity(frozen)
    compacted = frozen["policy_manager"]["mathematical_edge"]
    assert compacted["available"] is False
    assert compacted["reason"] == EXCLUDED_REASON
    assert "path_predictions" not in compacted
    audit = build_unified_ensemble(frozen)
    math_component = next(row for row in audit["components"] if row["component_id"] == "mathematical_edge")
    assert math_component["effective_weight"] == 0.


@pytest.mark.parametrize("tier", ["v18", "strict", "emergency"])
def test_real_common_economics_and_actual_hold_survive_all_pressure_tiers(tier, monkeypatch):
    import numpy as np
    from seiltanzer import unified_candidate_economics
    from test_extended_policy_evaluation import _snapshot
    frozen = operational_snapshot()
    geometry = frozen["trade_geometry"]
    manager = frozen["policy_manager"]
    manager["inputs"] = _snapshot()["policy_manager"]["inputs"]
    manager["inputs"].update(r0=0., T=(geometry["final_take"]-100.)/10., sigma_R=.002,
        stop_r=(geometry["active_risk_barrier"]-100.)/10., horizon_minutes=30., max_r=0.,
        rungs=[], be_after=10.)
    manager["execution_cost_model"] = {"immediate_full_close_r": .0001, "deferred_full_close_r": .0001}
    # Exercise real shared-bank replay, not an unavailable-economics fallback.
    monkeypatch.setattr(unified_candidate_economics, "_option_driver_bank", lambda inputs: (
        np.array([[0., .001, .001], [0., -.003, -.003]]), np.array([.5, .5]), np.array([0, 0])))
    original = build_unified_ensemble(frozen)
    assert original["shared_scenario_bank"] is True
    assert original["selected_candidate_id"] == "HOLD"
    expected = signatures(original)
    if tier == "v18":
        frozen.pop("report_integrity")
        frozen["policy_manager"]["redundant_explanation"] = "x"*80000
        ai_verdict_v18._enforce_snapshot_budget(frozen)
    elif tier == "strict":
        ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    else:
        ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
        ai_snapshot_budget_guard._emergency_authoritative_compaction(frozen, ai_verdict)
    result = build_unified_ensemble(frozen)
    assert result["shared_scenario_bank"] is True
    assert signatures(result) == expected


def test_bounded_regime_classifier_audit_and_manager_regime_survive_strict_cache():
    frozen = operational_snapshot()
    frozen["edge_regime"] = {"regime": "TREND", "observed_ts": T0-120.,
                             "quality": .8, "reason": "CAUSAL_CLASSIFIER"}
    frozen["policy_manager"]["market_regime"] = "TREND"
    expected = deepcopy(frozen["edge_regime"])
    ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    assert frozen["edge_regime"] == expected
    assert frozen["market_regime"] == "TREND"
    assert frozen["policy_manager"]["market_regime"] == "TREND"


def test_oversized_regime_audit_becomes_unknown_without_inventing_freshness():
    frozen = operational_snapshot()
    frozen["policy_manager"]["market_regime"] = "TREND"
    frozen["edge_regime"] = {"regime": "TREND", "raw_history": "x"*8000}
    ai_snapshot_budget_guard._strict_authoritative_compaction(frozen)
    assert frozen["edge_regime"] == {"regime": "UNKNOWN", "available": False, "reason": EXCLUDED_REASON}
    assert frozen["market_regime"] == "UNKNOWN"
    assert frozen["policy_manager"]["market_regime"] == "UNKNOWN"
