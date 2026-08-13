"""Predeclared EDE v1.2 evidence maturity tiers.

The lower tiers make small real cohorts visible; they do not relax the robust
edge contract or grant decision authority.
"""
from __future__ import annotations

from typing import Any


MATURITY_CONTRACT_VERSION = "g1s-ede-evidence-maturity-v1.2"
MATURITY_THRESHOLDS = {
    "EARLY_CONTEXT": {
        "raw_n": 100, "effective_n": 50, "temporal_blocks": 2,
        "requires_positive_incremental_result": False,
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


def evidence_maturity(*, raw_n: int, effective_n: int, temporal_blocks: int,
                      positive_n: int = 0, negative_n: int = 0,
                      brier_improvement: float | None = None,
                      logloss_improvement: float | None = None,
                      q_value: float | None = None, folds_evaluated: int = 0,
                      folds_positive: int = 0,
                      inner_fdr_passed: bool = False) -> str:
    """Return the highest honest tier without changing confirmatory gates."""
    raw_n, effective_n = int(raw_n), int(effective_n)
    temporal_blocks = int(temporal_blocks)
    positive = bool(
        brier_improvement is not None and logloss_improvement is not None
        and float(brier_improvement) > 0.0 and float(logloss_improvement) > 0.0)
    fdr = bool(q_value is not None and float(q_value) <= 0.10 and inner_fdr_passed)
    robust = MATURITY_THRESHOLDS["ROBUST_EDGE"]
    if (raw_n >= robust["raw_n"] and effective_n >= robust["effective_n"]
            and int(positive_n) >= robust["positive_n"]
            and int(negative_n) >= robust["negative_n"]
            and temporal_blocks >= robust["temporal_blocks"] and positive and fdr
            and int(folds_evaluated) == robust["folds_evaluated"]
            and int(folds_positive) >= robust["folds_positive"]):
        return "ROBUST_EDGE"
    provisional = MATURITY_THRESHOLDS["PROVISIONAL_EDGE"]
    stable = (int(folds_evaluated) >= 2
              and int(folds_positive)/max(1, int(folds_evaluated))
              >= provisional["minimum_positive_fold_fraction"])
    if (raw_n >= provisional["raw_n"] and effective_n >= provisional["effective_n"]
            and temporal_blocks >= provisional["temporal_blocks"]
            and positive and fdr and stable):
        return "PROVISIONAL_EDGE"
    research = MATURITY_THRESHOLDS["RESEARCH_SIGNAL"]
    if (raw_n >= research["raw_n"] and effective_n >= research["effective_n"]
            and temporal_blocks >= research["temporal_blocks"] and positive):
        return "RESEARCH_SIGNAL"
    early = MATURITY_THRESHOLDS["EARLY_CONTEXT"]
    if (raw_n >= early["raw_n"] and effective_n >= early["effective_n"]
            and temporal_blocks >= early["temporal_blocks"]):
        return "EARLY_CONTEXT"
    return "INSUFFICIENT_DATA"


def maturity_contract() -> dict[str, Any]:
    return {
        "contract_version": MATURITY_CONTRACT_VERSION,
        "thresholds": MATURITY_THRESHOLDS,
        "terminal_use": TERMINAL_USE_BY_MATURITY,
        "early_tiers_are_not_validated_edge_claims": True,
        "one_early_metric_may_trigger_exit_or_close": False,
        "production_authority": False,
        "auto_promotion": False,
    }
