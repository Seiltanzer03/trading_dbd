"""Bounded nested walk-forward discovery of conditional ret5 persistence edge."""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import (
    _clip_probability,
    _conditional_probability,
    _historical_folds,
    _weighted_mean,
    _weights,
)
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import _inner_split

from .filters import CandidateTemplate, FittedRule, candidate_templates, fit_rule, rule_mask
from .historical import build_discovery_rows
from .scoring import (
    benjamini_hochberg,
    edge_score,
    metrics,
    paired_loss_pvalue,
    relative_improvement,
)


SIGNAL = "ret5_persistence"
HORIZONS = (15, 30, 60, 120, 240)
OUTER_SELECTION_LIMIT = 10
MIN_INNER_TRAIN_RAW = 400
MIN_INNER_TRAIN_EFFECTIVE = 150
MIN_INNER_VALIDATION_RAW = 100
MIN_INNER_VALIDATION_EFFECTIVE = 40
MIN_INNER_CLASS = 20
MIN_RAW = 1000
MIN_EFFECTIVE = 400
MIN_CLASS = 120
MIN_FOLD_POSITIVE = 3
MIN_RELATIVE_IMPROVEMENT = 0.005
MAX_Q_VALUE = 0.10


def _predictions(train: list[dict[str, Any]], test: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    weights, _effective = _weights(train)
    base = _clip_probability(_weighted_mean(y, weights))
    p5_negative, p5_positive = _conditional_probability(train, y, weights, "ret_5m")
    p15_negative, p15_positive = _conditional_probability(train, y, weights, "ret_15m")
    ret5 = np.asarray([float(row["features"]["ret_5m"]) for row in test])
    ret15 = np.asarray([float(row["features"]["ret_15m"]) for row in test])
    return {
        "signal_ret5_persistence": np.where(ret5 > 0, p5_positive, p5_negative),
        "constant_0_5": np.full(len(test), 0.5),
        "causal_base_rate": np.full(len(test), base),
        "ret15_momentum": np.where(ret15 > 0, p15_positive, p15_negative),
    }


def _sample_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    _weights_array, effective = _weights(rows)
    return {
        "raw_n": len(rows), "effective_n": int(effective),
        "positive_n": sum(row["direction_label"] == "UP" for row in rows),
        "negative_n": sum(row["direction_label"] == "DOWN" for row in rows),
    }


def _inner_sample_allowed(train: list[dict[str, Any]], validation: list[dict[str, Any]]) -> bool:
    tr = _sample_counts(train); va = _sample_counts(validation)
    return bool(
        tr["raw_n"] >= MIN_INNER_TRAIN_RAW and tr["effective_n"] >= MIN_INNER_TRAIN_EFFECTIVE
        and va["raw_n"] >= MIN_INNER_VALIDATION_RAW
        and va["effective_n"] >= MIN_INNER_VALIDATION_EFFECTIVE
        and va["positive_n"] >= MIN_INNER_CLASS and va["negative_n"] >= MIN_INNER_CLASS
    )


def _evaluate_inner(template: CandidateTemplate, train: list[dict[str, Any]],
                    validation: list[dict[str, Any]]) -> dict[str, Any] | None:
    rule = fit_rule(template, train)
    if rule is None:
        return None
    selected_train = [row for row, keep in zip(train, rule_mask(train, rule)) if keep]
    selected_validation = [row for row, keep in zip(validation, rule_mask(validation, rule)) if keep]
    if not _inner_sample_allowed(selected_train, selected_validation):
        return None
    predictions = _predictions(selected_train, selected_validation)
    model = metrics(selected_validation, predictions["signal_ret5_persistence"])
    baselines = {name: metrics(selected_validation, values) for name, values in predictions.items()
                 if name != "signal_ret5_persistence"}
    baseline_name = min(
        baselines,
        key=lambda name: float(baselines[name]["brier"])+float(baselines[name]["logloss"]),
    )
    baseline = baselines[baseline_name]
    improvement = relative_improvement(model, baseline)
    p_value = paired_loss_pvalue(
        selected_validation, predictions["signal_ret5_persistence"], predictions[baseline_name])
    score = 0.5*(improvement["brier"]+improvement["logloss"])
    score -= 0.0015*(template.complexity-1)
    return {
        "template_id": template.template_id, "complexity": template.complexity,
        "rule": rule, "baseline_name": baseline_name, "model": model,
        "baseline": baseline, "improvement": improvement, "p_value": p_value,
        "inner_score": score,
    }


def _inner_discovery(outer_train: list[dict[str, Any]], horizon: int,
                     templates: tuple[CandidateTemplate, ...]) -> dict[str, Any]:
    train, validation = _inner_split(outer_train, horizon)
    if not train or not validation:
        return {"tested": 0, "sample_gate_passed": 0, "selected": [],
                "reason": "insufficient purged inner split"}
    evaluated = [item for template in templates
                 if (item := _evaluate_inner(template, train, validation)) is not None]
    q_values = benjamini_hochberg([float(item["p_value"]) for item in evaluated])
    for item, q_value in zip(evaluated, q_values):
        item["q_value"] = q_value
    evaluated.sort(key=lambda item: (
        -float(item["inner_score"]), float(item["q_value"]), item["complexity"],
        item["template_id"],
    ))
    selected = evaluated[:OUTER_SELECTION_LIMIT]
    return {
        "tested": len(templates), "sample_gate_passed": len(evaluated),
        "selection_limit": OUTER_SELECTION_LIMIT,
        "inner_train_end_ts": max(float(row["captured_ts"]) for row in train),
        "inner_validation_end_ts": max(float(row["captured_ts"]) for row in validation),
        "multiple_testing": "Benjamini-Hochberg FDR within inner fold",
        "selected": selected,
    }


def _outer_evaluation(item: dict[str, Any], template: CandidateTemplate,
                      train: list[dict[str, Any]], test: list[dict[str, Any]]) -> dict[str, Any] | None:
    rule = fit_rule(template, train)
    if rule is None:
        return None
    baseline_name = str(item["baseline_name"])
    funnel: list[dict[str, Any]] = []
    for depth in range(0, len(rule.conditions)+1):
        prefix = FittedRule(rule.template_id, rule.conditions[:depth])
        selected_train = (train if depth == 0 else
                          [row for row, keep in zip(train, rule_mask(train, prefix)) if keep])
        selected_test = (test if depth == 0 else
                         [row for row, keep in zip(test, rule_mask(test, prefix)) if keep])
        if len(selected_train) < 100 or len(selected_test) < 20:
            continue
        predictions = _predictions(selected_train, selected_test)
        model_prediction = predictions["signal_ret5_persistence"]
        baseline_prediction = predictions[baseline_name]
        model = metrics(selected_test, model_prediction)
        baseline = metrics(selected_test, baseline_prediction)
        funnel.append({
            "depth": depth, "rows": selected_test,
            "model_prediction": model_prediction,
            "baseline_prediction": baseline_prediction,
            "model": model, "baseline": baseline,
            "improvement": relative_improvement(model, baseline),
        })
    if not funnel or funnel[-1]["depth"] != len(rule.conditions):
        return None
    final = funnel[-1]
    return {
        "rule": rule, "rows": final["rows"],
        "model_prediction": final["model_prediction"],
        "baseline_prediction": final["baseline_prediction"],
        "model": final["model"], "baseline": final["baseline"],
        "baseline_name": baseline_name, "improvement": final["improvement"],
        "joint_positive": (final["improvement"]["brier"] > 0
                           and final["improvement"]["logloss"] > 0),
        "funnel": funnel,
    }


def _conditions_text(rule: FittedRule | None) -> list[dict[str, Any]]:
    return [] if rule is None else rule.as_dict()["conditions"]


def discover_horizon(sources: list[dict[str, Any]], horizon: int,
                     templates: tuple[CandidateTemplate, ...] | None = None) -> dict[str, Any]:
    templates = templates or candidate_templates()
    by_id = {template.template_id: template for template in templates}
    rows = build_discovery_rows(sources, horizon)
    folds = _historical_folds(rows, horizon)
    aggregated: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"rows": [], "model": [], "baseline": [], "folds": [],
                 "baselines": Counter(), "funnel": defaultdict(
                     lambda: {"rows": [], "model": [], "baseline": []})})
    inner_hypotheses = 0
    inner_sample_passed = 0
    fold_reports: list[dict[str, Any]] = []
    for fold in folds:
        discovery = _inner_discovery(fold["train"], horizon, templates)
        inner_hypotheses += int(discovery["tested"])
        inner_sample_passed += int(discovery["sample_gate_passed"])
        selected_ids: list[str] = []
        for rank, item in enumerate(discovery["selected"], 1):
            template = by_id[str(item["template_id"])]
            evaluation = _outer_evaluation(item, template, fold["train"], fold["test"])
            if evaluation is None:
                continue
            selected_ids.append(template.template_id)
            bucket = aggregated[template.template_id]
            bucket["rows"].extend(evaluation["rows"])
            bucket["model"].extend(evaluation["model_prediction"].tolist())
            bucket["baseline"].extend(evaluation["baseline_prediction"].tolist())
            bucket["baselines"][evaluation["baseline_name"]] += 1
            for stage in evaluation["funnel"]:
                funnel_bucket = bucket["funnel"][int(stage["depth"])]
                funnel_bucket["rows"].extend(stage["rows"])
                funnel_bucket["model"].extend(stage["model_prediction"].tolist())
                funnel_bucket["baseline"].extend(stage["baseline_prediction"].tolist())
            bucket["folds"].append({
                "fold_index": fold["fold_index"], "selection_rank": rank,
                "outer_test_start_ts": fold["test_start_ts"],
                "outer_test_end_ts": fold["test_end_ts"],
                "selection_end_ts": discovery.get("inner_validation_end_ts"),
                "outer_test_used_for_selection": False,
                "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
                "raw_n": len(evaluation["rows"]),
                "effective_n": evaluation["model"]["effective_n"],
                "improvement": evaluation["improvement"],
                "joint_positive": evaluation["joint_positive"],
                "baseline_name": evaluation["baseline_name"],
            })
        fold_reports.append({
            "fold_index": fold["fold_index"], "test_raw_n": len(fold["test"]),
            "test_start_ts": fold["test_start_ts"], "test_end_ts": fold["test_end_ts"],
            "inner_hypotheses": discovery["tested"],
            "inner_sample_gate_passed": discovery["sample_gate_passed"],
            "selected_template_ids": selected_ids,
            "outer_test_used_for_selection": False,
        })

    candidates: list[dict[str, Any]] = []
    for template_id, bucket in aggregated.items():
        candidate_rows = bucket["rows"]
        model_prediction = np.asarray(bucket["model"], dtype=float)
        baseline_prediction = np.asarray(bucket["baseline"], dtype=float)
        model = metrics(candidate_rows, model_prediction)
        baseline = metrics(candidate_rows, baseline_prediction)
        improvement = relative_improvement(model, baseline)
        p_value = paired_loss_pvalue(candidate_rows, model_prediction, baseline_prediction)
        full_rule = fit_rule(by_id[template_id], rows)
        funnel_report = []
        funnel_base_n = len(bucket["funnel"].get(0, {}).get("rows", []))
        for depth, stage in sorted(bucket["funnel"].items()):
            stage_rows = stage["rows"]
            stage_model = metrics(stage_rows, np.asarray(stage["model"], dtype=float))
            stage_baseline = metrics(stage_rows, np.asarray(stage["baseline"], dtype=float))
            funnel_report.append({
                "step": "ALL_OBSERVATIONS" if depth == 0 else f"CONDITION_{depth}",
                "conditions": _conditions_text(full_rule)[:depth],
                "raw_n": len(stage_rows), "effective_n": stage_model["effective_n"],
                "coverage": len(stage_rows)/max(1, funnel_base_n),
                "brier": stage_model["brier"], "logloss": stage_model["logloss"],
                "signed_expectancy": stage_model["signed_expectancy"],
                "improvement": relative_improvement(stage_model, stage_baseline),
                "post_hoc_used_for_selection": False,
            })
        folds_evaluated = len(bucket["folds"])
        folds_positive = sum(bool(fold["joint_positive"]) for fold in bucket["folds"])
        counts = _sample_counts(candidate_rows)
        candidates.append({
            "template_id": template_id, "horizon_minutes": horizon, "signal": SIGNAL,
            "conditions": _conditions_text(full_rule),
            "complexity": by_id[template_id].complexity,
            "baseline_selection_counts": dict(bucket["baselines"]),
            "coverage": len(candidate_rows)/max(1, sum(fold["test_raw_n"] for fold in fold_reports)),
            **counts, "model": model, "baseline": baseline,
            "improvement": improvement, "p_value": p_value,
            "folds_evaluated": folds_evaluated, "folds_positive": folds_positive,
            "folds": bucket["folds"],
            "funnel": funnel_report,
            "assets": sorted({str(row["instrument"]) for row in candidate_rows}),
            "sessions": sorted({str(row["ede_features"]["session_utc"]) for row in candidate_rows}),
            "temporal_blocks": len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"])))
                                    for row in candidate_rows}),
        })
    q_values = benjamini_hochberg([float(item["p_value"]) for item in candidates])
    for item, q_value in zip(candidates, q_values):
        item["q_value"] = q_value
        sample_gate = (item["raw_n"] >= MIN_RAW and item["effective_n"] >= MIN_EFFECTIVE
                       and item["positive_n"] >= MIN_CLASS and item["negative_n"] >= MIN_CLASS)
        stability_gate = (item["folds_evaluated"] == 4
                          and item["folds_positive"] >= MIN_FOLD_POSITIVE)
        metric_gate = (item["improvement"]["brier"] >= MIN_RELATIVE_IMPROVEMENT
                       and item["improvement"]["logloss"] >= MIN_RELATIVE_IMPROVEMENT)
        multiple_testing_gate = q_value <= MAX_Q_VALUE
        if not sample_gate:
            status = "EXPLORATORY"
        elif stability_gate and metric_gate and multiple_testing_gate:
            status = "HISTORICAL_CANDIDATE"
        else:
            status = "REJECTED"
        score = edge_score(
            improvement=item["improvement"], coverage=float(item["coverage"]),
            fold_positive=int(item["folds_positive"]),
            fold_evaluated=int(item["folds_evaluated"]),
            temporal_blocks=int(item["temporal_blocks"]),
            instrument_count=len(item["assets"]), regime_count=len(item["sessions"]),
            effective_n=int(item["effective_n"]), complexity=int(item["complexity"]),
            q_value=q_value,
        )
        item.update({
            "status": status, "gates": {
                "sample": sample_gate, "stability": stability_gate,
                "metric": metric_gate, "multiple_testing": multiple_testing_gate,
            }, "edge_score": score,
        })
        raw = json.dumps({key: item[key] for key in
                          ("template_id", "horizon_minutes", "signal", "conditions")},
                         sort_keys=True, separators=(",", ":"))
        item["candidate_id"] = "ede-candidate-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    candidates.sort(key=lambda item: (-float(item["edge_score"]["score"]), item["candidate_id"]))
    return {
        "horizon_minutes": horizon, "observation_count": len(rows),
        "feature_count_used": 8, "template_count": len(templates),
        "inner_hypotheses_tested": inner_hypotheses,
        "inner_sample_gate_passed": inner_sample_passed,
        "fold_count": len(folds), "folds": fold_reports,
        "candidates": candidates,
        "sample_gate_passed": sum(bool(item["gates"]["sample"]) for item in candidates),
        "stability_gate_passed": sum(bool(item["gates"]["stability"]) for item in candidates),
        "historical_candidate_count": sum(item["status"] == "HISTORICAL_CANDIDATE" for item in candidates),
    }


def run_discovery(sources: list[dict[str, Any]], *, source_set_sha256: str) -> dict[str, Any]:
    started = time.time()
    templates = candidate_templates()
    horizons = [discover_horizon(sources, horizon, templates) for horizon in HORIZONS]
    all_candidates = [candidate for result in horizons for candidate in result["candidates"]]
    all_candidates.sort(key=lambda item: (-float(item["edge_score"]["score"]), item["candidate_id"]))
    winners = [item for item in all_candidates if item["status"] == "HISTORICAL_CANDIDATE"]
    if winners:
        verdict = "PROMISING BUT NEEDS VALIDATION"
    elif any(item["gates"]["sample"] for item in all_candidates):
        verdict = "NO ROBUST CONDITIONAL EDGE FOUND"
    else:
        verdict = "INSUFFICIENT DATA"
    return {
        "contract_version": "g1s-edge-discovery-engine-v1",
        "evidence_label": "RESEARCH_DISCOVERY_DATASET",
        "source_set_sha256": source_set_sha256,
        "signal": SIGNAL, "verdict": verdict,
        "horizons": horizons,
        "top_edge_candidates": all_candidates[:10],
        "where_signal_fails": sorted(
            [item for item in all_candidates
             if item["improvement"]["brier"] < 0 and item["improvement"]["logloss"] < 0],
            key=lambda item: (float(item["improvement"]["brier"])
                              + float(item["improvement"]["logloss"])))[:10],
        "observations_by_horizon": {str(item["horizon_minutes"]): item["observation_count"]
                                    for item in horizons},
        "feature_count_used": max((item["feature_count_used"] for item in horizons), default=0),
        "hypotheses_tested": sum(item["inner_hypotheses_tested"] for item in horizons),
        "sample_gate_passed": sum(item["sample_gate_passed"] for item in horizons),
        "stability_gate_passed": sum(item["stability_gate_passed"] for item in horizons),
        "historical_candidate_count": len(winners),
        "multiple_testing": {
            "method": "Benjamini-Hochberg false-discovery-rate correction",
            "max_q_value": MAX_Q_VALUE,
            "scope": "inner-fold discovery and aggregated outer candidate evidence",
        },
        "bounded_search": {"max_conditions": 3, "templates": len(templates),
                           "outer_selection_limit_per_fold": OUTER_SELECTION_LIMIT},
        "research_dataset_is_pristine_oos": False,
        "outer_test_used_for_selection": False,
        "synthetic_options_used": False,
        "live_cohort_created": False,
        "production_authority": False, "auto_promotion": False,
        "duration_ms": (time.time()-started)*1000.0,
    }
