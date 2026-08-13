"""Predeclared EDE v1.2 data and edge maturity tiers.

The lower tiers make small real cohorts visible; they do not relax the robust
edge contract or grant decision authority.
"""
from __future__ import annotations

from typing import Any


MATURITY_CONTRACT_VERSION = "g1s-ede-maturity-separation-v1.2"
DATA_MATURITY_THRESHOLDS = {
    "DATA_READY_EARLY": {"raw_n": 100, "effective_n": 50, "temporal_blocks": 2},
    "DATA_READY_RESEARCH": {"raw_n": 250, "effective_n": 100, "temporal_blocks": 2},
    "DATA_READY_PROVISIONAL": {"raw_n": 500, "effective_n": 200, "temporal_blocks": 3},
    "DATA_READY_ROBUST": {"raw_n": 1000, "effective_n": 400, "temporal_blocks": 4},
}
EDGE_MATURITY_THRESHOLDS = {
    "EARLY_CONTEXT": {
        "raw_n": 100, "effective_n": 50, "temporal_blocks": 2,
        "requires_positive_incremental_result": True,
        "requires_fdr": False,
    },
    "RESEARCH_SIGNAL": {
        "raw_n": 250, "effective_n": 100, "temporal_blocks": 2,
        "requires_positive_incremental_result": True,
        "requires_fdr": False,
    },
    "PROVISIONAL_EDGE": {
        "raw_n": 500, "effective_n": 200, "temporal_blocks": 3,
        "requires_positive_incremental_result": True,
        "requires_fdr": True, "max_q": 0.10,
        "minimum_positive_fold_fraction": 0.50,
    },
    "ROBUST_EDGE": {
        "raw_n": 1000, "effective_n": 400, "positive_n": 120,
        "negative_n": 120, "temporal_blocks": 4,
        "requires_positive_incremental_result": True,
        "requires_fdr": True, "max_q": 0.10,
        "folds_evaluated": 4, "folds_positive": 3,
    },
}
# Compatibility name for report readers which consumed the old contract key.
MATURITY_THRESHOLDS = EDGE_MATURITY_THRESHOLDS

TERMINAL_USE_BY_MATURITY = {
    "INSUFFICIENT_DATA": {
        "mode": "IGNORE_AS_EDGE", "production_decision_score_weight": 0.0,
        "max_shadow_decision_score_weight": 0.0,
        "may_trigger_exit_or_close": False,
    },
    "EARLY_CONTEXT": {
        "mode": "EXPLANATION_AND_CONFIDENCE_CONTEXT",
        "production_decision_score_weight": 0.0,
        "max_shadow_decision_score_weight": 0.0,
        "may_trigger_exit_or_close": False,
    },
    "RESEARCH_SIGNAL": {
        "mode": "WEAK_CONFIRM_OR_CONTRADICT_CONTEXT",
        "production_decision_score_weight": 0.0,
        "max_shadow_decision_score_weight": 0.05,
        "may_trigger_exit_or_close": False,
    },
    "PROVISIONAL_EDGE": {
        "mode": "BOUNDED_SHADOW_SCORE_CONTRIBUTION",
        "production_decision_score_weight": 0.0,
        "max_shadow_decision_score_weight": 0.15,
        "may_trigger_exit_or_close": False,
    },
    "ROBUST_EDGE": {
        "mode": "VALIDATED_COMPONENT_ELIGIBLE_AFTER_SEPARATE_PROMOTION",
        "production_decision_score_weight": 0.0,
        "max_shadow_decision_score_weight": 0.30,
        "may_trigger_exit_or_close": False,
    },
}


def data_maturity(*, raw_n: int, effective_n: int, temporal_blocks: int) -> str:
    """Describe causal sample readiness without making an edge claim."""
    values = (int(raw_n), int(effective_n), int(temporal_blocks))
    for name in (
        "DATA_READY_ROBUST", "DATA_READY_PROVISIONAL",
        "DATA_READY_RESEARCH", "DATA_READY_EARLY",
    ):
        gate = DATA_MATURITY_THRESHOLDS[name]
        if (values[0] >= gate["raw_n"] and values[1] >= gate["effective_n"]
                and values[2] >= gate["temporal_blocks"]):
            return name
    return "INSUFFICIENT_DATA"


def edge_maturity(*, raw_n: int, effective_n: int, temporal_blocks: int,
                      positive_n: int = 0, negative_n: int = 0,
                      brier_improvement: float | None = None,
                      logloss_improvement: float | None = None,
                      q_value: float | None = None, folds_evaluated: int = 0,
                      folds_positive: int = 0,
                      inner_fdr_passed: bool = False,
                      candidate_tested: bool = False) -> str:
    """Describe tested incremental edge using primary-only outer evidence."""
    if not candidate_tested:
        return "INSUFFICIENT_DATA"
    raw_n, effective_n = int(raw_n), int(effective_n)
    temporal_blocks = int(temporal_blocks)
    positive = bool(
        brier_improvement is not None and logloss_improvement is not None
        and float(brier_improvement) > 0.0 and float(logloss_improvement) > 0.0)
    fdr = bool(q_value is not None and float(q_value) <= 0.10 and inner_fdr_passed)
    robust = EDGE_MATURITY_THRESHOLDS["ROBUST_EDGE"]
    if (raw_n >= robust["raw_n"] and effective_n >= robust["effective_n"]
            and int(positive_n) >= robust["positive_n"]
            and int(negative_n) >= robust["negative_n"]
            and temporal_blocks >= robust["temporal_blocks"] and positive and fdr
            and int(folds_evaluated) == robust["folds_evaluated"]
            and int(folds_positive) >= robust["folds_positive"]):
        return "ROBUST_EDGE"
    provisional = EDGE_MATURITY_THRESHOLDS["PROVISIONAL_EDGE"]
    stable = (int(folds_evaluated) >= 2
              and int(folds_positive)/max(1, int(folds_evaluated))
              >= provisional["minimum_positive_fold_fraction"])
    if (raw_n >= provisional["raw_n"] and effective_n >= provisional["effective_n"]
            and temporal_blocks >= provisional["temporal_blocks"]
            and positive and fdr and stable):
        return "PROVISIONAL_EDGE"
    research = EDGE_MATURITY_THRESHOLDS["RESEARCH_SIGNAL"]
    if (raw_n >= research["raw_n"] and effective_n >= research["effective_n"]
            and temporal_blocks >= research["temporal_blocks"] and positive):
        return "RESEARCH_SIGNAL"
    early = EDGE_MATURITY_THRESHOLDS["EARLY_CONTEXT"]
    if (raw_n >= early["raw_n"] and effective_n >= early["effective_n"]
            and temporal_blocks >= early["temporal_blocks"] and positive):
        return "EARLY_CONTEXT"
    return "INSUFFICIENT_DATA"


def evidence_maturity(**kwargs: Any) -> str:
    """Backward-compatible alias; an explicit tested-candidate flag is required."""
    return edge_maturity(**kwargs)


def maturity_contract() -> dict[str, Any]:
    return {
        "contract_version": MATURITY_CONTRACT_VERSION,
        "data_maturity_thresholds": DATA_MATURITY_THRESHOLDS,
        "edge_maturity_thresholds": EDGE_MATURITY_THRESHOLDS,
        "thresholds": EDGE_MATURITY_THRESHOLDS,
        "terminal_use": TERMINAL_USE_BY_MATURITY,
        "early_tiers_are_not_validated_edge_claims": True,
        "data_maturity_never_promotes_edge_maturity": True,
        "edge_aggregate_scope": "PRIMARY_FDR_PASS_OUTER_FOLDS_ONLY",
        "one_early_metric_may_trigger_exit_or_close": False,
        "production_authority": False,
        "auto_promotion": False,
    }
