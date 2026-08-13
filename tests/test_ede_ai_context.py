from __future__ import annotations

from types import SimpleNamespace

import pytest

from seiltanzer.edge_discovery import ai_context
from seiltanzer.edge_discovery.evidence_ledger import (
    _deployment_refit,
    append_frozen_evidence,
    family_data_maturity,
)
from seiltanzer.edge_discovery.registry import FEATURES


def _family(name: str, maturity: str = "DATA_READY_EARLY") -> dict:
    return {
        "data_maturity": maturity,
        "horizons": {str(horizon): {
            "raw": 120, "resolved": 120, "effective": 60,
            "temporal_blocks": 2, "coverage_pct": 50.0,
            "data_maturity": maturity,
        } for horizon in (15, 30, 60, 120, 240)},
    }


def _default_deployment_rule() -> dict:
    return {
        "candidate_id": "candidate-1", "hypothesis_id": "hypothesis-1",
        "deployment_rule": [{
            "feature_id": "price.ret_5m", "kind": "train_relative",
            "state": "ABOVE_MEDIAN", "lower": 0.0, "upper": 0.0,
            "train_cutoff_ts": 90.0,
        }],
        "feature_ids": ["price.ret_5m"],
        "thresholds_or_categories": [{
            "feature_id": "price.ret_5m", "kind": "train_relative",
            "state": "ABOVE_MEDIAN", "lower": 0.0, "upper": 0.0,
        }],
        "fitted_at": 94.0, "training_cutoff": 90.0,
        "evidence_cutoff_ts": 94.0, "dataset_hash": "sha-95.0",
        "rule_version": "ede-post-validation-causal-refit-v1",
        "provenance": "POST_VALIDATION_CAUSAL_REFIT",
        "validation_metrics_recomputed": False,
    }


def _record(*, frozen_at: float, maturity: str = "INSUFFICIENT_DATA",
            aggregate_scope: str = "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY",
            deployment_refit=...) -> dict:
    candidates = []
    if maturity != "INSUFFICIENT_DATA":
        primary_folds = 4 if maturity == "ROBUST_EDGE" else 2 if maturity == "PROVISIONAL_EDGE" else 1
        refit = _default_deployment_rule() if deployment_refit is ... else deployment_refit
        candidates = [{
            "candidate_id": "candidate-1", "hypothesis_id": "hypothesis-1",
            "horizon_minutes": 15,
            # Deliberately impossible: validation thresholds are display/debug only.
            "validation_conditions": [{
                "feature_id": "price.ret_5m", "kind": "train_relative",
                "state": "ABOVE_MEDIAN", "lower": 999.0, "upper": 999.0,
                "train_cutoff_ts": 80.0,
            }],
            "deployment_refit": refit,
            "edge_maturity": maturity, "delta_brier": -0.01,
            "delta_logloss": -0.02, "q_value": 0.04,
            "primary_folds": primary_folds, "folds_evaluated": primary_folds,
            "folds_positive": primary_folds,
            "directional_evidence": "SUPPORTS_PERSISTENCE",
            "feature_families": ["PRICE"], "aggregate_scope": aggregate_scope,
        }]
    return {
        "contract_version": "g1s-ede-frozen-evidence-v1.2.2",
        "frozen_at": frozen_at, "evidence_cutoff_ts": frozen_at - 1,
        "dataset_sha256": f"sha-{frozen_at}",
        "family_data_maturity": {
            family: _family(family) for family in (
                "PRICE", "VOLATILITY", "OPTIONS", "OPTION_DYNAMICS",
                "CROSS_ASSET", "REGIME")},
        "edge_candidates": candidates, "edge_maturity": maturity,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False, "may_trigger_exit_or_close": False,
    }


def _snapshot(captured_ts: float = 100.0) -> dict:
    return {
        "captured_ts": captured_ts,
        "strategy": {"instrument": "NAS100", "direction": "long"},
        "policy_manager": {
            "management_decision": {"policy": "HOLD", "action": "HOLD"},
            "evidence": {},
            "option_derivative_state": {
                "available": True, "option_state_score": .2, "metrics": {}},
        },
    }


def _engine(tmp_path, *, max_age: float = 900.0):
    return SimpleNamespace(settings=SimpleNamespace(
        data_dir=str(tmp_path), ede_context_max_age_sec=max_age))


@pytest.fixture
def frozen_context(monkeypatch):
    quality = {"source_ts": 99.0, "source_quality": 1.0, "stale": False}
    frozen = {
        "observation_t0": 99.0,
        "option_static": {"available": True, "iv": .22, "skew": -.04},
        "option_dynamics": {"available": True, "derivatives": {"iv": {"slope": .001}}},
        "cross_asset": {"available": True}, "macro": {"available": True},
        "price_volatility": {"available": True, "ret_5m": .02},
        "_raw_frozen": {"g1s_evidence_v3": {
            "captured_ts": 99.0,
            "price_volatility": {
                "available": True, "captured_ts": 99.0, "quality": quality,
                "ret_5m": .02, "realized_vol_15m": .20,
                "realized_vol_60m": .10,
            },
            "option_static": {
                "available": True, "captured_ts": 99.0, "quality": quality,
                "iv": .22, "skew": -.04,
            },
        }},
    }
    monkeypatch.setattr(ai_context, "_latest_frozen_context", lambda engine, snapshot: frozen)
    return frozen


def test_family_maturity_uses_only_its_own_observations():
    def feature(feature_id, raw, effective, blocks):
        return {"feature_id": feature_id, "by_horizon": {"15": {
            "raw": raw, "resolved": raw, "effective": effective,
            "temporal_blocks": blocks, "coverage_pct": 100.0}}}
    result = family_data_maturity({"features": [
        feature("price.ret_5m", 1200, 500, 5),
        feature("option.iv", 120, 60, 2),
    ]})
    assert result["PRICE"]["data_maturity"] == "DATA_READY_ROBUST"
    assert result["OPTIONS"]["data_maturity"] == "DATA_READY_EARLY"
    assert result["CROSS_ASSET"]["data_maturity"] == "INSUFFICIENT_DATA"


def test_canonical_feature_map_has_registry_parity_and_exact_rv_id(frozen_context):
    values = ai_context.canonical_current_feature_map(frozen_context, "NAS100")
    expected = {item.feature_id for item in FEATURES
                if item.research_scope == "G1S" and item.training_eligibility}
    assert set(values) == expected
    assert all(row["live_applicability"] in {
        "LIVE_APPLICABLE", "NOT_LIVE_APPLICABLE"} for row in values.values())
    assert values["vol.rv15_over_rv60"]["available"] is True
    assert values["vol.rv15_over_rv60"]["value"] == pytest.approx(2.0)
    assert "rv15_over_rv60" not in values


@pytest.mark.parametrize("maturity,expected", [
    ("RESEARCH_SIGNAL", .05),
    ("PROVISIONAL_EDGE", .15),
    ("ROBUST_EDGE", .25),
])
def test_frozen_primary_edge_reaches_ai_with_bounded_confidence(
        tmp_path, frozen_context, maturity, expected):
    path = tmp_path / "research" / "ede_frozen_evidence.jsonl"
    append_frozen_evidence(path, _record(frozen_at=95.0, maturity=maturity))
    snapshot = _snapshot()
    before = dict(snapshot["policy_manager"]["management_decision"])
    result = ai_context.build_ai_ede_context(_engine(tmp_path), snapshot)
    assert result["edge_maturity"] == maturity
    assert result["edge"]["candidate_id"] == "candidate-1"
    assert result["edge"]["aggregate_scope"] == "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY"
    assert result["edge"]["applicability_reason"] == "MATCHED_FRESH_DEPLOYMENT_RULE"
    assert result["confidence_modifier"] == expected
    assert snapshot["policy_manager"]["management_decision"] == before
    assert result["authority"] == {
        "role": "EXPLANATION_AND_CONFIDENCE_CONTEXT",
        "production_authority": False,
        "production_directional_authority": False,
        "may_trigger_exit_or_close": False,
        "explicit_promotion_required": True,
        "auto_promotion": False,
    }


def test_validation_fold_threshold_is_never_a_deployment_rule(tmp_path, frozen_context):
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl",
        _record(frozen_at=95.0, maturity="PROVISIONAL_EDGE"))
    result = ai_context.build_ai_ede_context(_engine(tmp_path), _snapshot())
    assert result["edge"]["validation_conditions"][0]["lower"] == 999.0
    assert result["edge"]["deployment_refit"]["deployment_rule"][0]["lower"] == 0.0
    assert result["edge"]["applies_to_current_context"] is True
    assert result["confidence_modifier"] == .15


def test_stale_observation_has_zero_modifier(tmp_path, frozen_context):
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl",
        _record(frozen_at=95.0, maturity="PROVISIONAL_EDGE"))
    result = ai_context.build_ai_ede_context(
        _engine(tmp_path, max_age=0.5), _snapshot())
    assert result["edge"]["applicability_reason"] == "STALE_OR_UNAVAILABLE_CONTEXT"
    assert result["confidence_modifier"] == 0.0


def test_stale_required_feature_has_zero_modifier(tmp_path, frozen_context):
    frozen_context["_raw_frozen"]["g1s_evidence_v3"]["price_volatility"]["quality"]["stale"] = True
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl",
        _record(frozen_at=95.0, maturity="PROVISIONAL_EDGE"))
    result = ai_context.build_ai_ede_context(_engine(tmp_path), _snapshot())
    assert result["edge"]["applicability_reason"] == "STALE_OR_UNAVAILABLE_CONTEXT"
    assert result["confidence_modifier"] == 0.0


def test_missing_canonical_feature_has_zero_modifier(tmp_path, frozen_context):
    refit = _default_deployment_rule()
    refit["deployment_rule"] = [{
        "feature_id": "cross.confirmation", "kind": "categorical",
        "state": "SAME", "train_cutoff_ts": 90.0}]
    refit["feature_ids"] = ["cross.confirmation"]
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl",
        _record(frozen_at=95.0, maturity="PROVISIONAL_EDGE", deployment_refit=refit))
    result = ai_context.build_ai_ede_context(_engine(tmp_path), _snapshot())
    assert result["edge"]["current_required_features"]["cross.confirmation"][
        "live_applicability"] == "NOT_LIVE_APPLICABLE"
    assert result["edge"]["applicability_reason"] == "STALE_OR_UNAVAILABLE_CONTEXT"
    assert result["confidence_modifier"] == 0.0


def test_candidate_without_deployment_rule_has_zero_modifier(tmp_path, frozen_context):
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl",
        _record(frozen_at=95.0, maturity="PROVISIONAL_EDGE", deployment_refit=None))
    result = ai_context.build_ai_ede_context(_engine(tmp_path), _snapshot())
    assert result["edge"]["applicability_reason"] == "DEPLOYMENT_RULE_MISSING"
    assert result["confidence_modifier"] == 0.0


def test_diagnostic_candidate_never_changes_confidence(tmp_path, frozen_context):
    path = tmp_path / "research" / "ede_frozen_evidence.jsonl"
    append_frozen_evidence(path, _record(
        frozen_at=95.0, maturity="ROBUST_EDGE", aggregate_scope="DIAGNOSTIC_DISPLAY_ONLY"))
    result = ai_context.build_ai_ede_context(_engine(tmp_path), _snapshot())
    assert result["edge_maturity"] == "INSUFFICIENT_DATA"
    assert result["edge"] is None
    assert result["confidence_modifier"] == 0.0


def test_one_primary_plus_diagnostics_cannot_reach_provisional_or_robust(
        tmp_path, frozen_context):
    record = _record(frozen_at=95.0, maturity="ROBUST_EDGE")
    record["edge_candidates"][0].update({
        "primary_folds": 1, "folds_evaluated": 4, "folds_positive": 4})
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl", record)
    result = ai_context.build_ai_ede_context(_engine(tmp_path), _snapshot())
    assert result["edge_maturity"] == "INSUFFICIENT_DATA"
    assert result["confidence_modifier"] == 0.0


def test_insufficient_data_has_zero_confidence(tmp_path, frozen_context):
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl",
        _record(frozen_at=95.0))
    result = ai_context.build_ai_ede_context(_engine(tmp_path), _snapshot())
    assert result["data_maturity"] == "DATA_READY_EARLY"
    assert result["edge_maturity"] == "INSUFFICIENT_DATA"
    assert result["confidence_modifier"] == 0.0


def test_late_evidence_cannot_change_old_frozen_snapshot(tmp_path, frozen_context):
    path = tmp_path / "research" / "ede_frozen_evidence.jsonl"
    append_frozen_evidence(path, _record(frozen_at=95.0, maturity="RESEARCH_SIGNAL"))
    engine = _engine(tmp_path)
    before = ai_context.build_ai_ede_context(engine, _snapshot(100.0))
    append_frozen_evidence(path, _record(frozen_at=110.0, maturity="ROBUST_EDGE"))
    after = ai_context.build_ai_ede_context(engine, _snapshot(100.0))
    assert after == before
    current = ai_context.build_ai_ede_context(engine, _snapshot(120.0))
    assert current["edge_maturity"] == "ROBUST_EDGE"
    assert before["evidence_frozen_at"] <= before["asof"]
    assert before["evidence_cutoff_ts"] <= before["asof"]


def test_deployment_refit_is_causal_and_immutable_to_late_outcomes():
    candidate = {
        "candidate_id": "candidate-1", "hypothesis_id": "hypothesis-1",
        "horizon_minutes": 15,
        "template": [{"feature_id": "rv15_over_rv60", "kind": "train_relative",
                      "state": "ABOVE_MEDIAN"}],
    }
    early = [{
        "captured_ts": float(index), "resolved_ts": float(index + 1),
        "horizon_minutes": 15,
        "ede_features": {"vol.rv15_over_rv60": float(index)},
    } for index in range(1, 31)]
    late = [{
        "captured_ts": float(index), "resolved_ts": float(index + 1),
        "horizon_minutes": 15,
        "ede_features": {"vol.rv15_over_rv60": 10000.0},
    } for index in range(101, 131)]
    before = _deployment_refit(
        candidate, early, evidence_cutoff_ts=50.0, fitted_at=60.0,
        dataset_sha256="dataset")
    after = _deployment_refit(
        candidate, early + late, evidence_cutoff_ts=50.0, fitted_at=60.0,
        dataset_sha256="dataset")
    assert after == before
    assert before is not None
    assert before["feature_ids"] == ["vol.rv15_over_rv60"]
    assert before["training_cutoff"] <= before["evidence_cutoff_ts"] == 50.0
    assert before["provenance"] == "POST_VALIDATION_CAUSAL_REFIT"
    assert before["validation_metrics_recomputed"] is False
