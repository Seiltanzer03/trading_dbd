"""Bounded nested walk-forward discovery of conditional ret5 persistence edge."""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
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


def _global_ret5_comparison(conditional: dict[str, Any],
                            global_ret5: dict[str, Any]) -> dict[str, float]:
    return {
        "global_ret5_brier": float(global_ret5["brier"]),
        "conditional_ret5_brier": float(conditional["brier"]),
        "brier_delta": float(global_ret5["brier"])-float(conditional["brier"]),
        "global_ret5_logloss": float(global_ret5["logloss"]),
        "conditional_ret5_logloss": float(conditional["logloss"]),
        "logloss_delta": float(global_ret5["logloss"])-float(conditional["logloss"]),
    }


def _predictions(global_train: list[dict[str, Any]], test: list[dict[str, Any]], *,
                 conditional_train: list[dict[str, Any]] | None = None) -> dict[str, np.ndarray]:
    """Fit global and conditional persistence separately, score one test subset.

    The global persistence model always sees the complete causal train cut.  A
    candidate is allowed to restrict only ``conditional_train`` and ``test``.
    This is the primary incremental-edge contract in EDE v1.1.
    """
    conditional_train = global_train if conditional_train is None else conditional_train
    global_y = np.asarray([
        1.0 if row["direction_label"] == "UP" else 0.0 for row in global_train])
    global_weights, _effective = _weights(global_train)
    conditional_y = np.asarray([
        1.0 if row["direction_label"] == "UP" else 0.0 for row in conditional_train])
    conditional_weights, _conditional_effective = _weights(conditional_train)
    base = _clip_probability(_weighted_mean(global_y, global_weights))
    global_p5_negative, global_p5_positive = _conditional_probability(
        global_train, global_y, global_weights, "ret_5m")
    conditional_p5_negative, conditional_p5_positive = _conditional_probability(
        conditional_train, conditional_y, conditional_weights, "ret_5m")
    p15_negative, p15_positive = _conditional_probability(
        global_train, global_y, global_weights, "ret_15m")
    ret5 = np.asarray([float(row["features"]["ret_5m"]) for row in test])
    ret15 = np.asarray([float(row["features"]["ret_15m"]) for row in test])
    return {
        "conditional_ret5_persistence": np.where(
            ret5 > 0, conditional_p5_positive, conditional_p5_negative),
        "global_ret5_persistence": np.where(
            ret5 > 0, global_p5_positive, global_p5_negative),
        # Compatibility alias; all primary gates use the explicit names above.
        "signal_ret5_persistence": np.where(
            ret5 > 0, conditional_p5_positive, conditional_p5_negative),
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
    predictions = _predictions(
        train, selected_validation, conditional_train=selected_train)
    conditional = metrics(
        selected_validation, predictions["conditional_ret5_persistence"])
    global_ret5 = metrics(
        selected_validation, predictions["global_ret5_persistence"])
    sanity = {
        name: metrics(selected_validation, predictions[name])
        for name in ("constant_0_5", "causal_base_rate", "ret15_momentum")
    }
    improvement = relative_improvement(conditional, global_ret5)
    p_value = paired_loss_pvalue(
        selected_validation, predictions["conditional_ret5_persistence"],
        predictions["global_ret5_persistence"])
    score = 0.5*(improvement["brier"]+improvement["logloss"])
    score -= 0.0015*(template.complexity-1)
    return {
        "template_id": template.template_id, "complexity": template.complexity,
        "rule": rule, "primary_baseline_name": "GLOBAL_RET5_PERSISTENCE",
        "conditional_ret5": conditional, "global_ret5": global_ret5,
        "global_ret5_comparison": _global_ret5_comparison(conditional, global_ret5),
        "sanity_baselines": sanity, "improvement": improvement, "p_value": p_value,
        "inner_score": score,
    }


def _inner_discovery(outer_train: list[dict[str, Any]], horizon: int,
                     templates: tuple[CandidateTemplate, ...]) -> dict[str, Any]:
    train, validation = _inner_split(outer_train, horizon)
    if not train or not validation:
        return {"tested": 0, "sample_gate_passed": 0, "fdr_passed": 0,
                "selected": [], "diagnostics": [], "evaluated": [],
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
    primary = [item for item in evaluated if float(item["q_value"]) <= MAX_Q_VALUE]
    diagnostics = [item for item in evaluated if float(item["q_value"]) > MAX_Q_VALUE]
    selected = primary[:OUTER_SELECTION_LIMIT]
    return {
        "tested": len(templates), "sample_gate_passed": len(evaluated),
        "fdr_passed": len(primary),
        "selection_limit": OUTER_SELECTION_LIMIT,
        "inner_train_end_ts": max(float(row["captured_ts"]) for row in train),
        "inner_validation_end_ts": max(float(row["captured_ts"]) for row in validation),
        "multiple_testing": "Benjamini-Hochberg FDR within inner fold",
        "selected": selected,
        "diagnostics": diagnostics[:OUTER_SELECTION_LIMIT],
        "evaluated": evaluated,
    }


def _outer_evaluation(item: dict[str, Any], template: CandidateTemplate,
                      train: list[dict[str, Any]], test: list[dict[str, Any]]) -> dict[str, Any] | None:
    rule = fit_rule(template, train)
    if rule is None:
        return None
    funnel: list[dict[str, Any]] = []
    for depth in range(0, len(rule.conditions)+1):
        prefix = FittedRule(rule.template_id, rule.conditions[:depth])
        selected_train = (train if depth == 0 else
                          [row for row, keep in zip(train, rule_mask(train, prefix)) if keep])
        selected_test = (test if depth == 0 else
                         [row for row, keep in zip(test, rule_mask(test, prefix)) if keep])
        if len(selected_train) < 100 or len(selected_test) < 20:
            continue
        predictions = _predictions(
            train, selected_test, conditional_train=selected_train)
        model_prediction = predictions["conditional_ret5_persistence"]
        baseline_prediction = predictions["global_ret5_persistence"]
        model = metrics(selected_test, model_prediction)
        baseline = metrics(selected_test, baseline_prediction)
        sanity = {
            name: metrics(selected_test, predictions[name])
            for name in ("constant_0_5", "causal_base_rate", "ret15_momentum")
        }
        funnel.append({
            "depth": depth, "rows": selected_test,
            "model_prediction": model_prediction,
            "baseline_prediction": baseline_prediction,
            "model": model, "baseline": baseline,
            "sanity_baselines": sanity,
            "sanity_predictions": {
                name: predictions[name] for name in sanity},
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
        "primary_baseline_name": "GLOBAL_RET5_PERSISTENCE",
        "improvement": final["improvement"],
        "global_ret5_comparison": _global_ret5_comparison(
            final["model"], final["baseline"]),
        "sanity_baselines": final["sanity_baselines"],
        "sanity_predictions": final["sanity_predictions"],
        "joint_positive": (final["improvement"]["brier"] > 0
                           and final["improvement"]["logloss"] > 0),
        "funnel": funnel,
    }


def _conditions_text(rule: FittedRule | None) -> list[dict[str, Any]]:
    return [] if rule is None else rule.as_dict()["conditions"]


def discover_horizon(sources: list[dict[str, Any]], horizon: int,
                     templates: tuple[CandidateTemplate, ...] | None = None, *,
                     rows_override: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    templates = templates or candidate_templates()
    by_id = {template.template_id: template for template in templates}
    rows = (build_discovery_rows(sources, horizon) if rows_override is None
            else sorted(rows_override, key=lambda row: (
                float(row["captured_ts"]), str(row["instrument"]))))
    folds = _historical_folds(rows, horizon)
    aggregated: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"rows": [], "model": [], "baseline": [], "folds": [],
                 "sanity": defaultdict(list), "rules": [],
                 "funnel": defaultdict(
                     lambda: {"rows": [], "model": [], "baseline": [],
                              "sanity": defaultdict(list)})})
    inner_hypotheses = 0
    inner_sample_passed = 0
    inner_fdr_passed = 0
    inner_evaluations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fold_reports: list[dict[str, Any]] = []
    for fold in folds:
        discovery = _inner_discovery(fold["train"], horizon, templates)
        inner_hypotheses += int(discovery["tested"])
        inner_sample_passed += int(discovery["sample_gate_passed"])
        inner_fdr_passed += int(discovery.get("fdr_passed", 0))
        for inner in discovery.get("evaluated", []):
            inner_evaluations[str(inner["template_id"])].append({
                "fold_index": fold["fold_index"],
                "rule": inner["rule"].as_dict(),
                "conditional_ret5": inner["conditional_ret5"],
                "global_ret5": inner["global_ret5"],
                "global_ret5_comparison": inner["global_ret5_comparison"],
                "improvement": inner["improvement"],
                "p_value": inner["p_value"], "q_value": inner["q_value"],
                "fdr_pass": float(inner["q_value"]) <= MAX_Q_VALUE,
            })
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
            bucket["rules"].append(evaluation["rule"].as_dict())
            for name, values in evaluation["sanity_predictions"].items():
                bucket["sanity"][name].extend(values.tolist())
            for stage in evaluation["funnel"]:
                funnel_bucket = bucket["funnel"][int(stage["depth"])]
                funnel_bucket["rows"].extend(stage["rows"])
                funnel_bucket["model"].extend(stage["model_prediction"].tolist())
                funnel_bucket["baseline"].extend(stage["baseline_prediction"].tolist())
                for name, values in stage["sanity_predictions"].items():
                    funnel_bucket["sanity"][name].extend(values.tolist())
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
                "primary_baseline_name": "GLOBAL_RET5_PERSISTENCE",
                "conditional_ret5": evaluation["model"],
                "global_ret5": evaluation["baseline"],
                "global_ret5_comparison": evaluation["global_ret5_comparison"],
            })
        fold_reports.append({
            "fold_index": fold["fold_index"], "test_raw_n": len(fold["test"]),
            "test_start_ts": fold["test_start_ts"], "test_end_ts": fold["test_end_ts"],
            "inner_hypotheses": discovery["tested"],
            "inner_sample_gate_passed": discovery["sample_gate_passed"],
            "inner_fdr_passed": discovery.get("fdr_passed", 0),
            "selected_template_ids": selected_ids,
            "diagnostic_template_ids": [
                str(item["template_id"]) for item in discovery.get("diagnostics", [])],
            "outer_test_used_for_selection": False,
        })

    candidates: list[dict[str, Any]] = []
    for template_id, bucket in aggregated.items():
        candidate_rows = bucket["rows"]
        model_prediction = np.asarray(bucket["model"], dtype=float)
        baseline_prediction = np.asarray(bucket["baseline"], dtype=float)
        model = metrics(candidate_rows, model_prediction)
        baseline = metrics(candidate_rows, baseline_prediction)
        sanity_baselines = {
            name: metrics(candidate_rows, np.asarray(values, dtype=float))
            for name, values in bucket["sanity"].items()
        }
        improvement = relative_improvement(model, baseline)
        p_value = paired_loss_pvalue(candidate_rows, model_prediction, baseline_prediction)
        representative_conditions = list(bucket["rules"][0]["conditions"])
        funnel_report = []
        funnel_base_n = len(bucket["funnel"].get(0, {}).get("rows", []))
        for depth, stage in sorted(bucket["funnel"].items()):
            stage_rows = stage["rows"]
            stage_model = metrics(stage_rows, np.asarray(stage["model"], dtype=float))
            stage_baseline = metrics(stage_rows, np.asarray(stage["baseline"], dtype=float))
            stage_sanity = {
                name: metrics(stage_rows, np.asarray(values, dtype=float))
                for name, values in stage["sanity"].items()
            }
            funnel_report.append({
                "step": "ALL_OBSERVATIONS" if depth == 0 else f"CONDITION_{depth}",
                "conditions": representative_conditions[:depth],
                "raw_n": len(stage_rows), "effective_n": stage_model["effective_n"],
                "coverage": len(stage_rows)/max(1, funnel_base_n),
                "conditional_ret5_brier": stage_model["brier"],
                "global_ret5_brier": stage_baseline["brier"],
                "conditional_ret5_logloss": stage_model["logloss"],
                "global_ret5_logloss": stage_baseline["logloss"],
                "signed_expectancy": stage_model["signed_expectancy"],
                "improvement": relative_improvement(stage_model, stage_baseline),
                "sanity_baselines": stage_sanity,
                "post_hoc_used_for_selection": False,
            })
        folds_evaluated = len(bucket["folds"])
        folds_positive = sum(bool(fold["joint_positive"]) for fold in bucket["folds"])
        counts = _sample_counts(candidate_rows)
        candidates.append({
            "template_id": template_id, "horizon_minutes": horizon, "signal": SIGNAL,
            "template": [
                {"feature_id": condition.feature_id, "kind": condition.kind,
                 "state": condition.state}
                for condition in by_id[template_id].conditions],
            "conditions": representative_conditions,
            "thresholds": list(bucket["rules"]),
            "complexity": by_id[template_id].complexity,
            "primary_baseline_name": "GLOBAL_RET5_PERSISTENCE",
            "coverage": len(candidate_rows)/max(1, sum(fold["test_raw_n"] for fold in fold_reports)),
            **counts, "model": model, "baseline": baseline,
            "conditional_ret5": model, "global_ret5": baseline,
            "global_ret5_comparison": _global_ret5_comparison(model, baseline),
            "sanity_baselines": sanity_baselines,
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
        if not multiple_testing_gate:
            status = "EXPLORATORY_FDR_FAIL"
        elif not sample_gate:
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
                          ("template_id", "horizon_minutes", "signal")},
                         sort_keys=True, separators=(",", ":"))
        identity = hashlib.sha256(raw.encode()).hexdigest()[:24]
        item["hypothesis_id"] = "ede-hypothesis-" + identity
        item["candidate_id"] = "ede-candidate-" + identity
    candidates.sort(key=lambda item: (-float(item["edge_score"]["score"]), item["candidate_id"]))
    candidate_by_template = {str(item["template_id"]): item for item in candidates}
    hypothesis_evaluations: list[dict[str, Any]] = []
    for template in templates:
        template_id = template.template_id
        raw = json.dumps({
            "template_id": template_id, "horizon_minutes": horizon, "signal": SIGNAL,
        }, sort_keys=True, separators=(",", ":"))
        identity = hashlib.sha256(raw.encode()).hexdigest()[:24]
        hypothesis_id = "ede-hypothesis-" + identity
        candidate = candidate_by_template.get(template_id)
        if candidate is not None:
            hypothesis_evaluations.append({
                **candidate, "hypothesis_id": hypothesis_id,
                "reason_rejected": (
                    None if candidate["status"] == "HISTORICAL_CANDIDATE"
                    else "AGGREGATED_OUTER_GATES_NOT_ALL_PASSED"),
            })
            continue
        records = inner_evaluations.get(template_id, [])
        best = max(records, key=lambda row: (
            float(row["improvement"]["brier"])+float(row["improvement"]["logloss"])),
            default=None)
        fdr_seen = any(bool(row["fdr_pass"]) for row in records)
        if not records:
            status = "REJECTED"
            reason = "INNER_SAMPLE_GATE_FAIL"
        elif not fdr_seen:
            status = "EXPLORATORY_FDR_FAIL"
            reason = "INNER_FDR_Q_GT_0_10"
        else:
            status = "REJECTED"
            reason = "INNER_FDR_PASS_BUT_OUTER_EVALUATION_UNAVAILABLE"
        hypothesis_evaluations.append({
            "candidate_id": "ede-candidate-" + identity,
            "hypothesis_id": hypothesis_id,
            "template_id": template_id, "template": [
                {"feature_id": condition.feature_id, "kind": condition.kind,
                 "state": condition.state} for condition in template.conditions],
            "signal": SIGNAL, "horizon_minutes": horizon,
            "conditions": ([] if best is None else best["rule"]["conditions"]),
            "thresholds": [row["rule"] for row in records],
            "status": status, "reason_rejected": reason,
            "q_value": (None if best is None else best["q_value"]),
            "p_value": (None if best is None else best["p_value"]),
            "conditional_ret5": (None if best is None else best["conditional_ret5"]),
            "global_ret5": (None if best is None else best["global_ret5"]),
            "global_ret5_comparison": (
                None if best is None else best["global_ret5_comparison"]),
            "improvement": (None if best is None else best["improvement"]),
            "inner_fold_evaluations": records,
            "production_authority": False, "auto_promotion": False,
        })
    diagnostics = sorted(
        [item for item in hypothesis_evaluations
         if item["status"] == "EXPLORATORY_FDR_FAIL" and item.get("improvement")],
        key=lambda item: -(
            float(item["improvement"]["brier"])+float(item["improvement"]["logloss"])),
    )
    return {
        "horizon_minutes": horizon, "observation_count": len(rows),
        "feature_count_used": 1+len({
            condition.feature_id for template in templates for condition in template.conditions}),
        "template_count": len(templates),
        "inner_hypotheses_tested": inner_hypotheses,
        "inner_sample_gate_passed": inner_sample_passed,
        "inner_fdr_passed": inner_fdr_passed,
        "fold_count": len(folds), "folds": fold_reports,
        "candidates": candidates,
        "diagnostic_candidates": diagnostics[:OUTER_SELECTION_LIMIT],
        "hypothesis_evaluations": hypothesis_evaluations,
        "sample_gate_passed": sum(bool(item["gates"]["sample"]) for item in candidates),
        "fdr_passed": sum(bool(item["gates"]["multiple_testing"]) for item in candidates),
        "stability_gate_passed": sum(bool(item["gates"]["stability"]) for item in candidates),
        "historical_candidate_count": sum(item["status"] == "HISTORICAL_CANDIDATE" for item in candidates),
    }


def run_discovery(sources: list[dict[str, Any]], *, source_set_sha256: str,
                  prospective_rows: list[dict[str, Any]] | None = None,
                  eligible_feature_ids: set[str] | None = None) -> dict[str, Any]:
    started = time.time()
    templates = candidate_templates(eligible_feature_ids)
    by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in prospective_rows or []:
        by_horizon[int(row["horizon_minutes"])].append(row)
    horizons = [
        discover_horizon(
            sources, horizon, templates,
            rows_override=(by_horizon[horizon] if prospective_rows is not None else None))
        for horizon in HORIZONS]
    all_candidates = [candidate for result in horizons for candidate in result["candidates"]]
    all_diagnostics = [candidate for result in horizons
                       for candidate in result["diagnostic_candidates"]]
    all_evaluations = [evaluation for result in horizons
                       for evaluation in result["hypothesis_evaluations"]]
    all_candidates.sort(key=lambda item: (-float(item["edge_score"]["score"]), item["candidate_id"]))
    winners = [item for item in all_candidates if item["status"] == "HISTORICAL_CANDIDATE"]
    if winners:
        verdict = "PROMISING BUT NEEDS PROSPECTIVE VALIDATION"
    elif sum(item["inner_sample_gate_passed"] for item in horizons) > 0:
        verdict = "NO ROBUST CONDITIONAL EDGE FOUND"
    else:
        verdict = "INSUFFICIENT DATA"
    return {
        "contract_version": "g1s-edge-discovery-engine-v1.1",
        "evidence_label": (
            "PROSPECTIVE_T0_RESOLVED_DATASET" if prospective_rows is not None
            else "RESEARCH_DISCOVERY_DATASET"),
        "source_set_sha256": source_set_sha256,
        "signal": SIGNAL, "primary_baseline": "GLOBAL_RET5_PERSISTENCE",
        "verdict": verdict,
        "horizons": horizons,
        "top_edge_candidates": all_candidates[:10],
        "top_diagnostic_fdr_failures": sorted(
            all_diagnostics,
            key=lambda item: -(
                float(item["improvement"]["brier"])
                + float(item["improvement"]["logloss"])),
        )[:10],
        "hypothesis_evaluations": all_evaluations,
        "where_signal_fails": sorted(
            [item for item in all_candidates
             if item["improvement"]["brier"] < 0 and item["improvement"]["logloss"] < 0],
            key=lambda item: (float(item["improvement"]["brier"])
                              + float(item["improvement"]["logloss"])))[:10],
        "observations_by_horizon": {str(item["horizon_minutes"]): item["observation_count"]
                                    for item in horizons},
        "feature_count_used": max((item["feature_count_used"] for item in horizons), default=0),
        "hypotheses_tested": sum(item["inner_hypotheses_tested"] for item in horizons),
        "sample_gate_passed": sum(item["inner_sample_gate_passed"] for item in horizons),
        "fdr_passed": sum(item["inner_fdr_passed"] for item in horizons),
        "aggregated_sample_gate_passed": sum(item["sample_gate_passed"] for item in horizons),
        "stability_gate_passed": sum(item["stability_gate_passed"] for item in horizons),
        "historical_candidate_count": len(winners),
        "multiple_testing": {
            "method": "Benjamini-Hochberg false-discovery-rate correction",
            "max_q_value": MAX_Q_VALUE,
            "scope": "inner-fold discovery and aggregated outer candidate evidence",
        },
        "bounded_search": {"max_conditions": 3, "templates": len(templates),
                           "outer_selection_limit_per_fold": OUTER_SELECTION_LIMIT},
        "research_dataset_is_pristine_oos": prospective_rows is not None,
        "outer_test_used_for_selection": False,
        "synthetic_options_used": False,
        "live_cohort_created": False,
        "production_authority": False, "auto_promotion": False,
        "duration_ms": (time.time()-started)*1000.0,
    }
