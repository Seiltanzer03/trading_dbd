"""Research-only bottleneck and statistical-power diagnostics for EDE.

This module explains why hypotheses fail.  It deliberately does not change any
sample, effect-size, FDR, stability or maturity gate.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _historical_folds

from .discovery import (
    MAX_Q_VALUE,
    MIN_CLASS,
    MIN_EFFECTIVE,
    MIN_FOLD_POSITIVE,
    MIN_RAW,
    MIN_RELATIVE_IMPROVEMENT,
    _outer_evaluation,
)
from .filters import CandidateTemplate, ConditionTemplate
from .scoring import paired_loss_power_diagnostics


BOTTLENECK_AUDIT_CONTRACT_VERSION = "g1s-ede-bottleneck-power-audit-v1"


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _template(candidate: dict[str, Any]) -> CandidateTemplate | None:
    try:
        conditions = tuple(
            ConditionTemplate(
                str(item["feature_id"]), str(item["kind"]), str(item["state"])
            )
            for item in candidate.get("template") or []
        )
    except (KeyError, TypeError, ValueError):
        return None
    return CandidateTemplate(conditions) if conditions else None


def _sample_diagnostics(item: dict[str, Any]) -> dict[str, Any]:
    actual = {
        "raw_n": int(item.get("raw_n") or 0),
        "effective_n": int(item.get("effective_n") or 0),
        "positive_n": int(item.get("positive_n") or 0),
        "negative_n": int(item.get("negative_n") or 0),
    }
    required = {
        "raw_n": MIN_RAW,
        "effective_n": MIN_EFFECTIVE,
        "positive_n": MIN_CLASS,
        "negative_n": MIN_CLASS,
    }
    deficit = {
        key: max(0, int(required[key])-int(actual[key]))
        for key in required
    }
    return {"actual": actual, "required": required, "deficit": deficit}


def _effect_diagnostics(item: dict[str, Any]) -> dict[str, Any]:
    improvement = item.get("improvement") or {}
    brier = _finite(improvement.get("brier"))
    logloss = _finite(improvement.get("logloss"))
    return {
        "brier_relative_improvement": brier,
        "logloss_relative_improvement": logloss,
        "required_relative_improvement_each": MIN_RELATIVE_IMPROVEMENT,
        "brier_deficit": (
            None if brier is None else max(0.0, MIN_RELATIVE_IMPROVEMENT-brier)
        ),
        "logloss_deficit": (
            None if logloss is None else max(0.0, MIN_RELATIVE_IMPROVEMENT-logloss)
        ),
        "joint_positive": bool(
            brier is not None and logloss is not None and brier > 0.0 and logloss > 0.0
        ),
    }


def _fdr_diagnostics(item: dict[str, Any]) -> dict[str, Any]:
    q_value = _finite(item.get("q_value"))
    p_value = _finite(item.get("p_value"))
    return {
        "p_value": p_value,
        "q_value": q_value,
        "required_max_q": MAX_Q_VALUE,
        "q_excess": None if q_value is None else max(0.0, q_value-MAX_Q_VALUE),
    }


def _stability_diagnostics(item: dict[str, Any]) -> dict[str, Any]:
    folds_evaluated = int(item.get("folds_evaluated") or 0)
    folds_positive = int(item.get("folds_positive") or 0)
    return {
        "folds_evaluated": folds_evaluated,
        "folds_positive": folds_positive,
        "required_folds_evaluated": 4,
        "required_positive_folds": MIN_FOLD_POSITIVE,
        "positive_fold_deficit": max(0, MIN_FOLD_POSITIVE-folds_positive),
        "full_fold_coverage": folds_evaluated == 4,
    }


def _failure_reasons(item: dict[str, Any]) -> list[str]:
    reason = str(item.get("reason_rejected") or "")
    if reason == "INNER_SAMPLE_GATE_FAIL":
        return ["INNER_SAMPLE_GATE_FAIL"]
    if reason == "INNER_FDR_Q_GT_0_10":
        return ["INNER_FDR_FAIL"]
    if reason == "INNER_FDR_PASS_BUT_OUTER_EVALUATION_UNAVAILABLE":
        return ["OUTER_EVALUATION_UNAVAILABLE"]

    gates = item.get("gates") or {}
    if not gates:
        return ["UNCLASSIFIED_NO_OUTER_GATE_DIAGNOSTICS"]
    reasons: list[str] = []
    if not bool(gates.get("inner_fdr")):
        reasons.append("INNER_FDR_FAIL")
    if not bool(gates.get("sample")):
        reasons.append("AGGREGATED_SAMPLE_FAIL")

    effect = _effect_diagnostics(item)
    brier = effect["brier_relative_improvement"]
    logloss = effect["logloss_relative_improvement"]
    if brier is not None and logloss is not None:
        if brier <= 0.0 and logloss <= 0.0:
            reasons.append("NO_INCREMENTAL_EFFECT")
        else:
            if brier < MIN_RELATIVE_IMPROVEMENT:
                reasons.append("BRIER_FAIL")
            if logloss < MIN_RELATIVE_IMPROVEMENT:
                reasons.append("LOGLOSS_FAIL")
    if not bool(gates.get("multiple_testing")):
        reasons.append("FDR_FAIL")
    if not bool(gates.get("stability")):
        reasons.append("FOLD_INSTABILITY")
    if not reasons and item.get("status") == "HISTORICAL_CANDIDATE":
        return ["PASSED_ALL_HISTORICAL_GATES"]
    return reasons or ["AGGREGATED_OUTER_GATES_NOT_ALL_PASSED"]


def _primary_failure(reasons: list[str]) -> str:
    priority = (
        "INNER_SAMPLE_GATE_FAIL",
        "INNER_FDR_FAIL",
        "OUTER_EVALUATION_UNAVAILABLE",
        "AGGREGATED_SAMPLE_FAIL",
        "NO_INCREMENTAL_EFFECT",
        "BRIER_FAIL",
        "LOGLOSS_FAIL",
        "FDR_FAIL",
        "FOLD_INSTABILITY",
        "PASSED_ALL_HISTORICAL_GATES",
        "UNCLASSIFIED_NO_OUTER_GATE_DIAGNOSTICS",
        "AGGREGATED_OUTER_GATES_NOT_ALL_PASSED",
    )
    return next((reason for reason in priority if reason in reasons), reasons[0])


def _near_miss_distance(item: dict[str, Any]) -> float | None:
    gates = item.get("gates") or {}
    if not gates or not bool(gates.get("inner_fdr")):
        return None
    sample = _sample_diagnostics(item)
    effect = _effect_diagnostics(item)
    fdr = _fdr_diagnostics(item)
    stability = _stability_diagnostics(item)
    components = [
        sample["deficit"]["raw_n"]/max(1, MIN_RAW),
        sample["deficit"]["effective_n"]/max(1, MIN_EFFECTIVE),
        sample["deficit"]["positive_n"]/max(1, MIN_CLASS),
        sample["deficit"]["negative_n"]/max(1, MIN_CLASS),
    ]
    if effect["brier_deficit"] is not None:
        components.append(min(2.0, effect["brier_deficit"]/MIN_RELATIVE_IMPROVEMENT))
    if effect["logloss_deficit"] is not None:
        components.append(min(2.0, effect["logloss_deficit"]/MIN_RELATIVE_IMPROVEMENT))
    if fdr["q_excess"] is not None:
        components.append(min(2.0, fdr["q_excess"]/MAX_Q_VALUE))
    components.append(
        stability["positive_fold_deficit"]/max(1, MIN_FOLD_POSITIVE)
    )
    if not stability["full_fold_coverage"]:
        components.append((4-stability["folds_evaluated"])/4.0)
    return float(sum(max(0.0, value) for value in components))


def _replay_power(candidate: dict[str, Any], rows: list[dict[str, Any]],
                  horizon: int) -> dict[str, Any]:
    """Replay only already-evaluated outer folds to expose paired-loss MDE."""
    template = _template(candidate)
    if template is None:
        return {"status": "TEMPLATE_UNAVAILABLE"}
    requested_folds = {
        int(item.get("fold_index") or 0)
        for item in candidate.get("folds") or []
        if int(item.get("fold_index") or 0) > 0
    }
    if not requested_folds:
        return {"status": "NO_OUTER_FOLDS_TO_REPLAY"}
    outer_rows: list[dict[str, Any]] = []
    model: list[float] = []
    baseline: list[float] = []
    replayed: list[int] = []
    for fold in _historical_folds(rows, horizon):
        fold_index = int(fold["fold_index"])
        if fold_index not in requested_folds:
            continue
        evaluation = _outer_evaluation({}, template, fold["train"], fold["test"])
        if evaluation is None:
            continue
        outer_rows.extend(evaluation["rows"])
        model.extend(np.asarray(evaluation["model_prediction"], dtype=float).tolist())
        baseline.extend(np.asarray(evaluation["baseline_prediction"], dtype=float).tolist())
        replayed.append(fold_index)
    if not outer_rows:
        return {"status": "OUTER_REPLAY_UNAVAILABLE", "requested_folds": sorted(requested_folds)}
    power = paired_loss_power_diagnostics(
        outer_rows, np.asarray(model, dtype=float), np.asarray(baseline, dtype=float)
    )
    return {
        **power,
        "requested_folds": sorted(requested_folds),
        "replayed_folds": sorted(replayed),
        "replayed_raw_n": len(outer_rows),
        "aggregate_scope": candidate.get("aggregate_scope"),
        "research_only": True,
    }


def _candidate_diagnostic(item: dict[str, Any], *, power: dict[str, Any] | None) -> dict[str, Any]:
    reasons = _failure_reasons(item)
    return {
        "candidate_id": item.get("candidate_id"),
        "hypothesis_id": item.get("hypothesis_id"),
        "template_id": item.get("template_id"),
        "horizon_minutes": item.get("horizon_minutes"),
        "template": item.get("template") or [],
        "status": item.get("status"),
        "edge_maturity": item.get("edge_maturity"),
        "sample": _sample_diagnostics(item) if item.get("gates") else None,
        "effect": _effect_diagnostics(item),
        "multiple_testing": _fdr_diagnostics(item),
        "stability": _stability_diagnostics(item) if item.get("gates") else None,
        "temporal_blocks": item.get("temporal_blocks"),
        "coverage": item.get("coverage"),
        "failure_reasons": reasons,
        "primary_failure_reason": _primary_failure(reasons),
        "near_miss_distance": _near_miss_distance(item),
        "power_diagnostics": power,
        "diagnostic_only_not_edge_claim": True,
    }


def build_bottleneck_power_audit(
    selective_report: dict[str, Any], resolved_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build candidate-level rejection funnel plus paired OOS power diagnostics."""
    rows_by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in resolved_rows:
        rows_by_horizon[int(row["horizon_minutes"])].append(row)

    evaluations: list[dict[str, Any]] = []
    power_by_candidate: dict[str, dict[str, Any]] = {}
    replay_errors = 0
    for horizon_report in selective_report.get("horizons") or []:
        horizon = int(horizon_report.get("horizon_minutes") or 0)
        horizon_rows = sorted(
            rows_by_horizon.get(horizon, []),
            key=lambda row: (float(row["captured_ts"]), str(row["instrument"])),
        )
        for candidate in horizon_report.get("candidates") or []:
            candidate_id = str(candidate.get("candidate_id") or "")
            if not candidate_id:
                continue
            try:
                power_by_candidate[candidate_id] = _replay_power(
                    candidate, horizon_rows, horizon
                )
            except Exception as exc:  # diagnostic must never abort the core EDE run
                replay_errors += 1
                power_by_candidate[candidate_id] = {
                    "status": "POWER_REPLAY_ERROR",
                    "error_type": type(exc).__name__,
                    "research_only": True,
                }
        evaluations.extend(horizon_report.get("hypothesis_evaluations") or [])

    diagnostics = [
        _candidate_diagnostic(
            item,
            power=power_by_candidate.get(str(item.get("candidate_id") or "")),
        )
        for item in evaluations
    ]
    primary_counts = Counter(item["primary_failure_reason"] for item in diagnostics)
    all_counts = Counter(
        reason for item in diagnostics for reason in item["failure_reasons"]
    )

    outer = [item for item in diagnostics if item.get("sample") is not None]
    positive = [
        item for item in outer
        if bool((item.get("effect") or {}).get("joint_positive"))
    ]
    statistical = [
        item for item in outer
        if (item.get("multiple_testing") or {}).get("p_value") is not None
        and float((item.get("multiple_testing") or {})["p_value"]) <= MAX_Q_VALUE
    ]
    fdr = [
        item for item in outer
        if (item.get("multiple_testing") or {}).get("q_value") is not None
        and float((item.get("multiple_testing") or {})["q_value"]) <= MAX_Q_VALUE
    ]
    sample = [
        item for item in outer
        if not any((item.get("sample") or {})["deficit"].values())
    ]
    stable = [
        item for item in outer
        if (item.get("stability") or {}).get("folds_evaluated") == 4
        and (item.get("stability") or {}).get("folds_positive", 0) >= MIN_FOLD_POSITIVE
    ]
    passed = [
        item for item in diagnostics
        if item["primary_failure_reason"] == "PASSED_ALL_HISTORICAL_GATES"
    ]
    near_misses = sorted(
        [item for item in diagnostics if item.get("near_miss_distance") is not None],
        key=lambda item: (float(item["near_miss_distance"]), str(item.get("candidate_id"))),
    )[:20]

    powers = [
        item["power_diagnostics"] for item in diagnostics
        if isinstance(item.get("power_diagnostics"), dict)
        and item["power_diagnostics"].get("minimum_detectable_joint_loss_delta") is not None
    ]
    underpowered = sum(
        item.get("status") == "UNDERPOWERED_FOR_OBSERVED_EFFECT" for item in powers
    )
    above_mde = sum(
        item.get("status") == "OBSERVED_EFFECT_AT_OR_ABOVE_MDE" for item in powers
    )

    return {
        "contract_version": BOTTLENECK_AUDIT_CONTRACT_VERSION,
        "diagnostic_scope": "RESEARCH_ONLY_NO_GATE_OR_MATURITY_CHANGES",
        "gate_contract": {
            "raw_n": MIN_RAW,
            "effective_n": MIN_EFFECTIVE,
            "positive_n": MIN_CLASS,
            "negative_n": MIN_CLASS,
            "minimum_relative_improvement_each": MIN_RELATIVE_IMPROVEMENT,
            "max_q_value": MAX_Q_VALUE,
            "folds_evaluated": 4,
            "minimum_positive_folds": MIN_FOLD_POSITIVE,
            "unchanged_by_this_audit": True,
        },
        "search_accounting": {
            "inner_hypothesis_tests": int(selective_report.get("hypotheses_tested") or 0),
            "inner_sample_gate_passed_tests": int(selective_report.get("sample_gate_passed") or 0),
            "inner_fdr_passed_tests": int(selective_report.get("fdr_passed") or 0),
            "unique_candidate_hypotheses": len(diagnostics),
            "note": "inner counts are fold-level tests; candidate counts are unique template x horizon hypotheses",
        },
        "candidate_funnel": {
            "eligible_feature_hypotheses": len(diagnostics),
            "outer_oos_evaluated": len(outer),
            "aggregated_sample_ready": len(sample),
            "joint_positive_incremental_effect": len(positive),
            "nominal_p_le_0_10": len(statistical),
            "aggregated_fdr_q_le_0_10": len(fdr),
            "fold_stable_3_of_4": len(stable),
            "passed_all_historical_gates": len(passed),
            "prospective_confirmation": None,
            "prospective_confirmation_status": "NOT_PART_OF_PASS_1_DISCOVERY_AUDIT",
        },
        "primary_failure_reason_counts": dict(sorted(primary_counts.items())),
        "all_failure_reason_counts": dict(sorted(all_counts.items())),
        "near_misses": near_misses,
        "power_summary": {
            "candidate_power_estimates": len(powers),
            "underpowered_for_observed_effect": underpowered,
            "observed_effect_at_or_above_mde": above_mde,
            "power_replay_errors": replay_errors,
            "mde_unit": "BASELINE_MINUS_MODEL_JOINT_BRIER_PLUS_LOGLOSS_PER_DEPENDENCY_GROUP",
            "mde_is_not_relative_brier_or_logloss": True,
        },
        "candidate_diagnostics": diagnostics,
        "production_authority": False,
        "auto_promotion": False,
    }
