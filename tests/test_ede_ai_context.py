from __future__ import annotations

from types import SimpleNamespace

import pytest

from seiltanzer.edge_discovery import ai_context
from seiltanzer.edge_discovery.evidence_ledger import (
    append_frozen_evidence,
    family_data_maturity,
)


def _family(name: str, maturity: str = "DATA_READY_EARLY") -> dict:
    return {
        "data_maturity": maturity,
        "horizons": {str(horizon): {
            "raw": 120, "resolved": 120, "effective": 60,
            "temporal_blocks": 2, "coverage_pct": 50.0,
            "data_maturity": maturity,
        } for horizon in (15, 30, 60, 120, 240)},
    }


def _record(*, frozen_at: float, maturity: str = "INSUFFICIENT_DATA",
            aggregate_scope: str = "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY") -> dict:
    candidates = []
    if maturity != "INSUFFICIENT_DATA":
        primary_folds = 4 if maturity == "ROBUST_EDGE" else 2 if maturity == "PROVISIONAL_EDGE" else 1
        candidates = [{
            "candidate_id": "candidate-1", "hypothesis_id": "hypothesis-1",
            "horizon_minutes": 15,
            "conditions": [{
                "feature_id": "price.ret_5m", "kind": "train_relative",
                "state": "ABOVE_MEDIAN", "lower": 0.0, "upper": 0.0,
                "train_cutoff_ts": 90.0,
            }],
            "edge_maturity": maturity, "delta_brier": -0.01,
            "delta_logloss": -0.02, "q_value": 0.04,
            "primary_folds": primary_folds, "folds_evaluated": primary_folds,
            "folds_positive": primary_folds,
            "directional_evidence": "SUPPORTS_PERSISTENCE",
            "feature_families": ["PRICE"], "aggregate_scope": aggregate_scope,
        }]
    return {
        "contract_version": "g1s-ede-frozen-evidence-v1.2.1",
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


@pytest.fixture
def frozen_context(monkeypatch):
    frozen = {
        "observation_t0": 99.0,
        "option_static": {"available": True, "iv": .22, "skew": -.04},
        "option_dynamics": {"available": True, "derivatives": {"iv": {"slope": .001}}},
        "cross_asset": {"available": True}, "macro": {"available": True},
        "price_volatility": {"available": True, "ret_5m": .02},
        "_raw_frozen": {"g1s_evidence_v3": {
            "price_volatility": {"available": True, "ret_5m": .02},
            "option_static": {"available": True, "iv": .22, "skew": -.04},
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


@pytest.mark.parametrize("maturity,expected", [
    ("RESEARCH_SIGNAL", .05),
    ("PROVISIONAL_EDGE", .15),
    ("ROBUST_EDGE", .25),
])
def test_frozen_primary_edge_reaches_ai_with_bounded_confidence(
        tmp_path, frozen_context, maturity, expected):
    path = tmp_path / "research" / "ede_frozen_evidence.jsonl"
    append_frozen_evidence(path, _record(frozen_at=95.0, maturity=maturity))
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path)))
    snapshot = _snapshot()
    before = dict(snapshot["policy_manager"]["management_decision"])
    result = ai_context.build_ai_ede_context(engine, snapshot)
    assert result["edge_maturity"] == maturity
    assert result["edge"]["candidate_id"] == "candidate-1"
    assert result["edge"]["aggregate_scope"] == "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY"
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


def test_diagnostic_candidate_never_changes_confidence(tmp_path, frozen_context):
    path = tmp_path / "research" / "ede_frozen_evidence.jsonl"
    append_frozen_evidence(path, _record(
        frozen_at=95.0, maturity="ROBUST_EDGE", aggregate_scope="DIAGNOSTIC_DISPLAY_ONLY"))
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path)))
    result = ai_context.build_ai_ede_context(engine, _snapshot())
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
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path)))
    result = ai_context.build_ai_ede_context(engine, _snapshot())
    assert result["edge_maturity"] == "INSUFFICIENT_DATA"
    assert result["confidence_modifier"] == 0.0


def test_insufficient_data_has_zero_confidence(tmp_path, frozen_context):
    append_frozen_evidence(
        tmp_path / "research" / "ede_frozen_evidence.jsonl",
        _record(frozen_at=95.0))
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path)))
    result = ai_context.build_ai_ede_context(engine, _snapshot())
    assert result["data_maturity"] == "DATA_READY_EARLY"
    assert result["edge_maturity"] == "INSUFFICIENT_DATA"
    assert result["confidence_modifier"] == 0.0


def test_late_evidence_cannot_change_old_frozen_snapshot(tmp_path, frozen_context):
    path = tmp_path / "research" / "ede_frozen_evidence.jsonl"
    append_frozen_evidence(path, _record(frozen_at=95.0, maturity="RESEARCH_SIGNAL"))
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=str(tmp_path)))
    before = ai_context.build_ai_ede_context(engine, _snapshot(100.0))
    append_frozen_evidence(path, _record(frozen_at=110.0, maturity="ROBUST_EDGE"))
    after = ai_context.build_ai_ede_context(engine, _snapshot(100.0))
    assert after == before
    current = ai_context.build_ai_ede_context(engine, _snapshot(120.0))
    assert current["edge_maturity"] == "ROBUST_EDGE"
    assert before["evidence_frozen_at"] <= before["asof"]
    assert before["evidence_cutoff_ts"] <= before["asof"]
