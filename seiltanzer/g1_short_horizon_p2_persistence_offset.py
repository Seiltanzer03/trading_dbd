"""P2D: nested persistence-offset direction challenger.

P1B/P2 showed that the strongest causal direction baseline is usually the
5-minute persistence probability. A generic classifier wastes capacity trying
to rediscover it and can easily become worse. This experiment makes the baseline
an explicit offset:

    logit(p_up) = logit(p_ret5_persistence) + beta_0 + beta*x_context

Therefore beta=0 recovers the causal baseline exactly. Context is only allowed to
correct that baseline when an inner purged selection window supports it.

This remains offline historical research. It creates no live model/cohort and has
no production authority.
"""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from .g1_short_horizon_champion_runtime import DIRECTION_TARGET
from .g1_short_horizon_historical_wf import (
    EMBARGO_SECONDS,
    MIN_HISTORICAL_EFFECTIVE,
    MIN_HISTORICAL_RAW,
    MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
    _clip_probability,
    _conditional_probability,
    _historical_folds,
    _prob_metrics,
    _weighted_mean,
    _weights,
)
from . import g1_short_horizon_p2_regime_research as _p2
from .g1_short_horizon_p2_fast_gbt import build_contexts_fast


OFFSET_CONTRACT_VERSION = "g1s-p2d-persistence-offset-nested-wf-v1"
OFFSET_EVIDENCE_LABEL = "HISTORICAL_NESTED_WALK_FORWARD"
OFFSET_MODEL_FAMILY = "RET5_PERSISTENCE_OFFSET_LOGISTIC_V1"
NO_CORRECTION = "RET5_PERSISTENCE_BASELINE"
L2_GRID = (0.25, 1.0, 4.0, 16.0)
FOLD_JOINT_NON_DEGRADE_REQUIRED = 3
OUTER_FOLD_COUNT = 4

# BASE remains a candidate because return magnitude can condition the strength of
# persistence even when its sign is already represented by the offset.
OFFSET_FEATURE_FAMILIES = tuple(_p2.FEATURE_FAMILIES)


def _logit_array(probability: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(p / (1.0 - p))


def _persistence_rates(train: list[dict[str, Any]]) -> tuple[float, float]:
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    weights, _ = _weights(train)
    return _conditional_probability(train, y, weights, "ret_5m")


def _persistence_prediction(train: list[dict[str, Any]],
                            rows: list[dict[str, Any]]) -> np.ndarray:
    negative, positive = _persistence_rates(train)
    ret5 = np.asarray([float(row["features"]["ret_5m"]) for row in rows])
    return np.where(ret5 > 0.0, positive, negative).astype(float)


def _fit_standardization(x: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    den = max(float(weights.sum()), 1e-12)
    mean = (weights[:, None] * x).sum(axis=0) / den
    var = (weights[:, None] * (x - mean) ** 2).sum(axis=0) / den
    std = np.sqrt(np.maximum(var, 0.0)); std[std < 1e-12] = 1.0
    return mean, std


def _fit_offset_logistic(train: list[dict[str, Any]], family: str,
                         l2: float) -> dict[str, Any]:
    x = _p2._matrix(train, family)
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    weights, effective = _weights(train)
    baseline = _persistence_prediction(train, train)
    offset = _logit_array(baseline)
    mean, std = _fit_standardization(x, weights)
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1], dtype=float)
    regularizer = np.eye(design.shape[1], dtype=float) * float(l2)
    # Penalize the correction intercept too, but more weakly. This makes beta=0
    # the explicit prior and prevents gratuitous global recalibration.
    regularizer[0, 0] = float(l2) * 0.25
    for _ in range(80):
        score = offset + design @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))
        variance = np.maximum(p * (1.0 - p), 1e-6)
        gradient = design.T @ (weights * (p - y)) + regularizer @ beta
        hessian = design.T @ ((weights * variance)[:, None] * design) + regularizer
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient
        beta -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return {
        "model_family": OFFSET_MODEL_FAMILY,
        "feature_family": family,
        "feature_names": list(_p2._feature_names(family)),
        "l2": float(l2),
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "correction_intercept_and_coefficients": beta.tolist(),
        "train_raw_n": len(train), "train_effective_n": effective,
        "baseline": "causal_ret5_persistence_probability",
        "beta_zero_recovers_baseline_exactly": True,
    }


def _predict_offset(train: list[dict[str, Any]], rows: list[dict[str, Any]],
                    artifact: dict[str, Any]) -> np.ndarray:
    baseline = _persistence_prediction(train, rows)
    x = _p2._matrix(rows, str(artifact["feature_family"]))
    mean = np.asarray(artifact["feature_mean"], dtype=float)
    std = np.asarray(artifact["feature_std"], dtype=float)
    beta = np.asarray(artifact["correction_intercept_and_coefficients"], dtype=float)
    z = (x - mean) / np.where(std < 1e-12, 1.0, std)
    design = np.column_stack([np.ones(len(z)), z])
    score = _logit_array(baseline) + design @ beta
    return 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))


def _inner_select(outer_train: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    inner_train, validation = _p2._inner_split(outer_train, horizon)
    if not inner_train or not validation:
        raise RuntimeError("insufficient purged inner split for persistence offset")
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in validation])
    weights, effective = _weights(validation)
    baselines = _p2._direction_baselines(inner_train, validation)
    baseline_metrics = {name: _prob_metrics(y, prediction, weights)
                        for name, prediction in baselines.items()}
    best_brier_name, best_brier = _p2._best_metric(baseline_metrics, "brier")
    best_log_name, best_log = _p2._best_metric(baseline_metrics, "logloss")

    persistence = baselines["ret5_persistence"]
    persistence_metrics = baseline_metrics["ret5_persistence"]
    candidates: list[dict[str, Any]] = [{
        "candidate": NO_CORRECTION,
        "feature_family": None, "l2": None,
        "score": (float(persistence_metrics["brier"]) / best_brier
                  + float(persistence_metrics["logloss"]) / best_log),
        "metrics": persistence_metrics,
    }]
    for family in OFFSET_FEATURE_FAMILIES:
        for l2 in L2_GRID:
            artifact = _fit_offset_logistic(inner_train, family, l2)
            prediction = _predict_offset(inner_train, validation, artifact)
            metrics = _prob_metrics(y, prediction, weights)
            candidates.append({
                "candidate": OFFSET_MODEL_FAMILY,
                "feature_family": family, "l2": float(l2),
                "score": (float(metrics["brier"]) / best_brier
                          + float(metrics["logloss"]) / best_log),
                "metrics": metrics,
            })
    # Conservative tie handling: exact baseline wins ties. Among learned
    # corrections prefer stronger regularization, then a simpler feature family.
    family_order = {name: index for index, name in enumerate(OFFSET_FEATURE_FAMILIES)}
    def order(row: dict[str, Any]):
        baseline_rank = 0 if row["candidate"] == NO_CORRECTION else 1
        l2_rank = -float(row["l2"] or 0.0)
        family_rank = family_order.get(row.get("feature_family"), -1)
        return (round(float(row["score"]), 12), baseline_rank, l2_rank, family_rank)
    selected = min(candidates, key=order)
    return {
        "selection_method": _p2.INNER_SELECTION_METHOD,
        "inner_train_raw_n": len(inner_train),
        "inner_validation_raw_n": len(validation),
        "inner_validation_effective_n": effective,
        "best_inner_baselines": {
            "brier": best_brier_name, "logloss": best_log_name},
        "baseline_metrics": baseline_metrics,
        "candidates": candidates,
        "selected_candidate": selected["candidate"],
        "selected_feature_family": selected.get("feature_family"),
        "selected_l2": selected.get("l2"),
        "selected_score": selected["score"],
    }


def evaluate_persistence_offset(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    all_y: list[float] = []; all_prediction: list[float] = []; all_weights: list[float] = []
    baseline_values: dict[str, list[float]] = defaultdict(list)
    reports = []
    selection_counts = Counter()
    joint_non_degrade = 0
    for fold in folds:
        train, test = fold["train"], fold["test"]
        selection = _inner_select(train, horizon)
        selected = str(selection["selected_candidate"])
        if selected == NO_CORRECTION:
            prediction = _persistence_prediction(train, test)
            artifact_contract = {
                "candidate": NO_CORRECTION,
                "beta_zero_recovers_baseline_exactly": True,
            }
            selection_counts[NO_CORRECTION] += 1
        else:
            family = str(selection["selected_feature_family"])
            l2 = float(selection["selected_l2"])
            artifact = _fit_offset_logistic(train, family, l2)
            prediction = _predict_offset(train, test, artifact)
            artifact_contract = {
                "candidate": OFFSET_MODEL_FAMILY,
                "feature_family": family, "l2": l2,
                "feature_names": artifact["feature_names"],
                "beta_zero_recovers_baseline_exactly": True,
            }
            selection_counts[f"{family}|l2={l2:g}"] += 1
        y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in test])
        weights, effective = _weights(test)
        baselines = _p2._direction_baselines(train, test)
        model_metrics = _prob_metrics(y, prediction, weights)
        baseline_metrics = {name: _prob_metrics(y, values, weights)
                            for name, values in baselines.items()}
        _brier_name, best_brier = _p2._best_metric(baseline_metrics, "brier")
        _log_name, best_log = _p2._best_metric(baseline_metrics, "logloss")
        joint = (float(model_metrics["brier"]) <= best_brier
                 and float(model_metrics["logloss"]) <= best_log)
        joint_non_degrade += int(joint)
        reports.append({
            "fold_index": fold["fold_index"],
            "train_raw_n": len(train), "test_raw_n": len(test),
            "test_effective_n": effective,
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "selection": selection,
            "selected_artifact_contract": artifact_contract,
            "model": model_metrics, "baselines": baseline_metrics,
            "joint_non_degrade": joint,
        })
        all_y.extend(y.tolist()); all_prediction.extend(prediction.tolist()); all_weights.extend(weights.tolist())
        for name, values in baselines.items():
            baseline_values[name].extend(values.tolist())
    y = np.asarray(all_y); prediction = np.asarray(all_prediction); weights = np.asarray(all_weights)
    model = _prob_metrics(y, prediction, weights)
    baselines = {name: _prob_metrics(y, np.asarray(values), weights)
                 for name, values in baseline_values.items()}
    return {
        "fold_count": len(reports), "folds": reports,
        "model": model, "baselines": baselines,
        "fold_joint_non_degrade_n": joint_non_degrade,
        "selection_counts": dict(sorted(selection_counts.items())),
    }


def offset_winner_gate(evaluation: dict[str, Any], raw_n: int, effective_n: int) -> dict[str, Any]:
    model = evaluation["model"]; baselines = evaluation["baselines"]
    brier_name, best_brier = _p2._best_metric(baselines, "brier")
    log_name, best_log = _p2._best_metric(baselines, "logloss")
    brier_improvement = (best_brier - float(model["brier"])) / best_brier
    log_improvement = (best_log - float(model["logloss"])) / best_log
    sample_gate = raw_n >= MIN_HISTORICAL_RAW and effective_n >= MIN_HISTORICAL_EFFECTIVE
    fold_gate = int(evaluation.get("fold_count") or 0) == OUTER_FOLD_COUNT
    robustness_gate = int(evaluation.get("fold_joint_non_degrade_n") or 0) >= FOLD_JOINT_NON_DEGRADE_REQUIRED
    metric_gate = (brier_improvement >= MIN_PROVISIONAL_RELATIVE_IMPROVEMENT
                   and log_improvement >= MIN_PROVISIONAL_RELATIVE_IMPROVEMENT)
    return {
        "historical_winner": bool(sample_gate and fold_gate and robustness_gate and metric_gate),
        "best_brier_baseline": brier_name, "best_brier": best_brier,
        "model_brier": model["brier"], "brier_relative_improvement": brier_improvement,
        "best_logloss_baseline": log_name, "best_logloss": best_log,
        "model_logloss": model["logloss"], "logloss_relative_improvement": log_improvement,
        "required_relative_improvement": MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
        "sample_gate": sample_gate, "fold_gate": fold_gate,
        "fold_joint_non_degrade_observed": int(evaluation.get("fold_joint_non_degrade_n") or 0),
        "fold_joint_non_degrade_required": FOLD_JOINT_NON_DEGRADE_REQUIRED,
        "robustness_gate": robustness_gate, "metric_gate": metric_gate,
    }


def run_offset_experiment(runtime) -> dict[str, Any]:
    """Run P2D on immutable current P1B sources; no DB writes are required."""
    source_set, sources = _p2._current_sources(runtime)
    contexts = build_contexts_fast(sources)
    results = []
    started = time.time()
    for horizon in (15, 30, 60, 120, 240):
        rows = [row for row in _p2._p2_rows(sources, horizon, contexts)
                if row["direction_label"] != "FLAT"]
        _w, effective = _weights(rows)
        evaluation = evaluate_persistence_offset(rows, horizon)
        gate = offset_winner_gate(evaluation, len(rows), effective)
        results.append({
            "target": DIRECTION_TARGET, "horizon_minutes": horizon,
            "raw_n": len(rows), "effective_n": effective,
            "historical_winner": gate["historical_winner"],
            "brier_relative_improvement": gate["brier_relative_improvement"],
            "logloss_relative_improvement": gate["logloss_relative_improvement"],
            "fold_joint_non_degrade_n": gate["fold_joint_non_degrade_observed"],
            "selection_counts": evaluation["selection_counts"],
            "gate": gate,
        })
    return {
        "contract_version": OFFSET_CONTRACT_VERSION,
        "evidence_label": OFFSET_EVIDENCE_LABEL,
        "source_set_sha256": source_set,
        "target": DIRECTION_TARGET,
        "run_count": len(results),
        "winner_count": sum(bool(row["historical_winner"]) for row in results),
        "results": results,
        "outer_test_used_for_selection": False,
        "baseline_is_exact_model_offset": True,
        "beta_zero_recovers_ret5_persistence": True,
        "historical_options_used": False,
        "live_parity_ready": False,
        "auto_promotion": False,
        "production_authority": False,
        "duration_ms": (time.time() - started) * 1000.0,
    }
