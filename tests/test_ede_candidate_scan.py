from __future__ import annotations

from seiltanzer.edge_discovery.ai_context import _candidate_for_context


def _rule(*, lower: float = 0.0) -> dict:
    return {
        "candidate_id": "candidate",
        "hypothesis_id": "hypothesis",
        "deployment_rule": [{
            "feature_id": "price.ret_5m",
            "kind": "train_relative",
            "state": "ABOVE_MEDIAN",
            "lower": lower,
            "upper": lower,
            "train_cutoff_ts": 90.0,
        }],
        "feature_ids": ["price.ret_5m"],
        "provenance": "POST_VALIDATION_CAUSAL_REFIT",
    }


def _candidate(candidate_id: str, *, deployment_refit) -> dict:
    return {
        "candidate_id": candidate_id,
        "hypothesis_id": f"hypothesis-{candidate_id}",
        "edge_maturity": "PROVISIONAL_EDGE",
        "primary_folds": 2,
        "aggregate_scope": "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY",
        "deployment_refit": deployment_refit,
    }


def _values() -> dict:
    return {
        "price.ret_5m": {
            "feature_id": "price.ret_5m",
            "value": 0.02,
            "availability": "AVAILABLE",
            "available": True,
            "asof": 99.0,
            "stale": False,
            "live_applicability": "LIVE_APPLICABLE",
        }
    }


def test_candidate_scan_skips_missing_rule_and_applies_later_matching_candidate():
    record = {"edge_candidates": [
        _candidate("rank-1", deployment_refit=None),
        _candidate("rank-2", deployment_refit=_rule(lower=0.0)),
    ]}

    candidate, applies, reason = _candidate_for_context(
        record, _values(), snapshot_ts=100.0,
        observation_t0=99.0, max_age_sec=900.0)

    assert candidate["candidate_id"] == "rank-2"
    assert applies is True
    assert reason == "MATCHED_FRESH_DEPLOYMENT_RULE"


def test_candidate_scan_skips_unmatched_rule_and_applies_later_matching_candidate():
    record = {"edge_candidates": [
        _candidate("rank-1", deployment_refit=_rule(lower=999.0)),
        _candidate("rank-2", deployment_refit=_rule(lower=0.0)),
    ]}

    candidate, applies, reason = _candidate_for_context(
        record, _values(), snapshot_ts=100.0,
        observation_t0=99.0, max_age_sec=900.0)

    assert candidate["candidate_id"] == "rank-2"
    assert applies is True
    assert reason == "MATCHED_FRESH_DEPLOYMENT_RULE"


def test_candidate_scan_returns_best_evidence_only_for_display_when_none_apply():
    record = {"edge_candidates": [
        _candidate("rank-1", deployment_refit=None),
        _candidate("rank-2", deployment_refit=_rule(lower=999.0)),
    ]}

    candidate, applies, reason = _candidate_for_context(
        record, _values(), snapshot_ts=100.0,
        observation_t0=99.0, max_age_sec=900.0)

    assert candidate["candidate_id"] == "rank-1"
    assert applies is False
    assert reason == "DEPLOYMENT_RULE_MISSING"
