from __future__ import annotations

import math

import numpy as np

from seiltanzer.edge_discovery.bottleneck import build_bottleneck_power_audit
from seiltanzer.edge_discovery.discovery import (
    MAX_Q_VALUE,
    MIN_EFFECTIVE,
    MIN_RAW,
    MIN_RELATIVE_IMPROVEMENT,
)
from seiltanzer.edge_discovery.scoring import paired_loss_power_diagnostics


def _power_rows(count: int) -> tuple[list[dict], np.ndarray, np.ndarray]:
    rows = []
    model = []
    baseline = []
    strengths = (0.54, 0.58, 0.63, 0.68)
    for index in range(count):
        up = index % 2 == 0
        rows.append({
            "direction_label": "UP" if up else "DOWN",
            "captured_ts": float(index + 1),
        })
        strength = strengths[index % len(strengths)]
        model.append(strength if up else 1.0-strength)
        baseline.append(0.5)
    return rows, np.asarray(model), np.asarray(baseline)


def test_paired_loss_power_uses_dependency_groups_and_more_groups_lower_mde():
    small = _power_rows(20)
    large = _power_rows(80)
    small_power = paired_loss_power_diagnostics(*small)
    large_power = paired_loss_power_diagnostics(*large)

    assert small_power["group_n"] == 20
    assert large_power["group_n"] == 80
    assert small_power["sampling_unit"].startswith("DEPENDENCY_GROUP_MEAN")
    assert large_power["minimum_detectable_joint_loss_delta"] < small_power[
        "minimum_detectable_joint_loss_delta"
    ]
    assert large_power["gate_effect"] == "DIAGNOSTIC_ONLY_DOES_NOT_CHANGE_EDGE_GATES"
    for report in (small_power, large_power):
        for key in (
            "observed_mean_joint_loss_delta",
            "group_std_joint_loss_delta",
            "standard_error",
            "minimum_detectable_joint_loss_delta",
        ):
            assert math.isfinite(float(report[key]))


def _outer_candidate(
    candidate_id: str, *, raw: int, effective: int, brier: float, logloss: float,
    q: float, folds_positive: int,
) -> dict:
    sample_ok = raw >= 1000 and effective >= 400
    metric_ok = brier >= 0.005 and logloss >= 0.005
    fdr_ok = q <= 0.10
    stable = folds_positive >= 3
    return {
        "candidate_id": candidate_id,
        "hypothesis_id": "h-" + candidate_id,
        "template_id": "t-" + candidate_id,
        "horizon_minutes": 30,
        "template": [{"feature_id": "vol.rv15_over_rv60", "kind": "train_relative", "state": "ABOVE_MEDIAN"}],
        "status": "REJECTED",
        "edge_maturity": "INSUFFICIENT_DATA",
        "reason_rejected": "AGGREGATED_OUTER_GATES_NOT_ALL_PASSED",
        "raw_n": raw,
        "effective_n": effective,
        "positive_n": raw // 2,
        "negative_n": raw - raw // 2,
        "improvement": {"brier": brier, "logloss": logloss},
        "p_value": 0.02,
        "q_value": q,
        "folds_evaluated": 4,
        "folds_positive": folds_positive,
        "temporal_blocks": 4,
        "coverage": 0.2,
        "gates": {
            "inner_fdr": True,
            "sample": sample_ok,
            "metric": metric_ok,
            "multiple_testing": fdr_ok,
            "stability": stable,
        },
    }


def test_bottleneck_audit_separates_sample_fdr_and_stability_failures():
    evaluations = [
        {
            "candidate_id": "inner-sample",
            "hypothesis_id": "h-inner-sample",
            "template_id": "t-inner-sample",
            "horizon_minutes": 30,
            "template": [],
            "status": "REJECTED",
            "reason_rejected": "INNER_SAMPLE_GATE_FAIL",
            "improvement": None,
            "p_value": None,
            "q_value": None,
        },
        _outer_candidate(
            "outer-sample", raw=700, effective=250,
            brier=0.02, logloss=0.02, q=0.05, folds_positive=4,
        ),
        _outer_candidate(
            "fdr", raw=1200, effective=500,
            brier=0.02, logloss=0.02, q=0.15, folds_positive=4,
        ),
        _outer_candidate(
            "folds", raw=1200, effective=500,
            brier=0.02, logloss=0.02, q=0.05, folds_positive=2,
        ),
    ]
    report = {
        "hypotheses_tested": 16,
        "sample_gate_passed": 8,
        "fdr_passed": 3,
        "horizons": [{
            "horizon_minutes": 30,
            "candidates": [],
            "hypothesis_evaluations": evaluations,
        }],
    }
    audit = build_bottleneck_power_audit(report, [])

    counts = audit["primary_failure_reason_counts"]
    assert counts["INNER_SAMPLE_GATE_FAIL"] == 1
    assert counts["AGGREGATED_SAMPLE_FAIL"] == 1
    assert counts["FDR_FAIL"] == 1
    assert counts["FOLD_INSTABILITY"] == 1
    assert audit["search_accounting"]["inner_hypothesis_tests"] == 16
    assert audit["search_accounting"]["unique_candidate_hypotheses"] == 4


def test_bottleneck_audit_reports_exact_existing_gate_contract_without_relaxing_it():
    report = {
        "hypotheses_tested": 0,
        "sample_gate_passed": 0,
        "fdr_passed": 0,
        "horizons": [],
    }
    audit = build_bottleneck_power_audit(report, [])
    gates = audit["gate_contract"]
    assert gates["raw_n"] == MIN_RAW == 1000
    assert gates["effective_n"] == MIN_EFFECTIVE == 400
    assert gates["minimum_relative_improvement_each"] == MIN_RELATIVE_IMPROVEMENT == 0.005
    assert gates["max_q_value"] == MAX_Q_VALUE == 0.10
    assert gates["unchanged_by_this_audit"] is True
    assert audit["diagnostic_scope"] == "RESEARCH_ONLY_NO_GATE_OR_MATURITY_CHANGES"
