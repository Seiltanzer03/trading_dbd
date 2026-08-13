"""P2E: predeclared segment-conditioned persistence research.

Hypothesis: a pooled ret5 persistence probability can hide stable differences
between asset families, UTC sessions and instruments.  The persistence
probability remains an explicit offset, so the zero correction is exactly the
causal baseline.  Candidate families, sessions, L2 grid, folds and winner gate
are frozen in this module before any outer-test result is inspected.

This module is offline historical research only.  It creates no live cohort,
worker, production model, policy, execution path or automatic promotion.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from .config import INSTRUMENTS
from .g1_short_horizon_champion_runtime import DIRECTION_TARGET
from .g1_short_horizon_historical_wf import (
    EMBARGO_SECONDS,
    MIN_HISTORICAL_EFFECTIVE,
    MIN_HISTORICAL_RAW,
    MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
    _anchor_index,
    _build_horizon_rows,
    _clip_probability,
    _conditional_probability,
    _historical_folds,
    _load_source_bars,
    _prob_metrics,
    _weighted_mean,
    _weights,
)


P2E_CONTRACT_VERSION = "g1s-p2e-segmented-persistence-nested-wf-v1"
P2E_EVIDENCE_LABEL = "HISTORICAL_NESTED_WALK_FORWARD"
P2E_MODEL_FAMILY = "SEGMENTED_RET5_PERSISTENCE_OFFSET_LOGISTIC_V1"
P2E_VERDICT_WINNER = "HISTORICAL_WINNER"
P2E_VERDICT_NEGATIVE = "P2E_NEGATIVE"
P2E_VERDICT_INSUFFICIENT = "INSUFFICIENT"
NO_CORRECTION = "C0_RET5_PERSISTENCE"
OUTER_FOLD_COUNT = 4
FOLD_JOINT_NON_DEGRADE_REQUIRED = 3
L2_GRID = (1.0, 4.0, 16.0, 64.0)
# A learned correction must improve the joint inner score by more than 0.1%;
# otherwise the practically tied no-correction candidate wins.
INNER_PRACTICAL_TIE_RELATIVE = 0.001
INNER_SELECTION_METHOD = "purged_tail_20pct_inside_outer_train"
INNER_TAIL_FRACTION = 0.20
MIN_POSITIVE = 120
MIN_NEGATIVE = 120
MIN_TEMPORAL_BLOCKS = 20
MIN_REGIMES = 2

ASSET_FAMILY_BY_INSTRUMENT = {
    "NAS100": "EQUITY_INDICES",
    "SP500": "EQUITY_INDICES",
    "US30": "EQUITY_INDICES",
    "GER40": "EQUITY_INDICES",
    "UK100": "EQUITY_INDICES",
    "JPY100": "EQUITY_INDICES",
    "XAU": "METALS",
    "XAG": "METALS",
    "EURUSD": "FX",
    "USDCAD": "FX",
}
if set(ASSET_FAMILY_BY_INSTRUMENT) != set(INSTRUMENTS):
    raise RuntimeError("P2E asset-family mapping must exactly cover config.INSTRUMENTS")

ASSET_FAMILIES = ("EQUITY_INDICES", "METALS", "FX")
SESSIONS = ("ASIA", "EUROPE", "US", "LATE")
SESSION_UTC_INTERVALS = {
    "ASIA": (0, 8),
    "EUROPE": (8, 13),
    "US": (13, 21),
    "LATE": (21, 24),
}
REGIME_FEATURES = (
    "rv15_over_rv60",
    "ret5_over_rv15",
    "ret15_over_rv60",
    "trend_agreement_5_15",
    "trend_efficiency_60",
)
CANDIDATES = (
    "C0_NO_CORRECTION",
    "C1_ASSET_FAMILY_OFFSET",
    "C2_SESSION_OFFSET",
    "C3_INSTRUMENT_SHRUNK_OFFSET",
    "C4_FAMILY_SESSION_OFFSET",
    "C5_FAMILY_SESSION_SMALL_REGIME",
)


def session_utc(captured_ts: float) -> str:
    hour = int(time.gmtime(float(captured_ts)).tm_hour)
    if hour < 8:
        return "ASIA"
    if hour < 13:
        return "EUROPE"
    if hour < 21:
        return "US"
    return "LATE"


def asset_family(instrument: str) -> str:
    try:
        return ASSET_FAMILY_BY_INSTRUMENT[str(instrument)]
    except KeyError as exc:
        raise ValueError(f"instrument absent from frozen P2E mapping: {instrument}") from exc


def _safe_ratio(numerator: float, denominator: float, *, clip: float = 8.0) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or abs(denominator) < 1e-12:
        return 0.0
    return max(-clip, min(clip, numerator / denominator))


def _sign(value: float) -> float:
    return 1.0 if value > 0.0 else -1.0 if value < 0.0 else 0.0


def _source_context(source: dict[str, Any]) -> dict[float, dict[str, float]]:
    """Minimal causal P2E context from completed same-instrument 5m bars."""
    bars = source["bars"]
    times = [float(bar["bar_end_ts"]) for bar in bars]
    closes = np.asarray([float(bar["close"]) for bar in bars], dtype=float)
    highs = np.asarray([float(bar["high"]) for bar in bars], dtype=float)
    lows = np.asarray([float(bar["low"]) for bar in bars], dtype=float)
    log_step = np.zeros(len(bars), dtype=float)
    log_step[1:] = np.log(closes[1:] / closes[:-1])
    contexts: dict[float, dict[str, float]] = {}
    for index in range(12, len(bars)):
        i5 = _anchor_index(times, index, 5 * 60.0)
        i15 = _anchor_index(times, index, 15 * 60.0)
        i60 = _anchor_index(times, index, 60 * 60.0)
        if None in (i5, i15, i60):
            continue
        assert i5 is not None and i15 is not None and i60 is not None
        if index - i60 < 11:
            continue
        rv15_values = log_step[i15 + 1:index + 1]
        rv60_values = log_step[i60 + 1:index + 1]
        if len(rv15_values) < 2 or len(rv60_values) < 10:
            continue
        current = float(closes[index])
        if current <= 0.0:
            continue
        ret5 = float(math.log(current / closes[i5]))
        ret15 = float(math.log(current / closes[i15]))
        rv15 = float(math.sqrt(float(np.sum(rv15_values * rv15_values))))
        rv60 = float(math.sqrt(float(np.sum(rv60_values * rv60_values))))
        path_abs = float(np.sum(np.abs(rv60_values)))
        ret60 = float(math.log(current / closes[i60]))
        efficiency = abs(ret60) / path_abs if path_abs > 1e-12 else 0.0
        contexts[float(times[index])] = {
            "realized_vol_15m": rv15,
            "realized_vol_60m": rv60,
            "rv15_over_rv60": _safe_ratio(rv15, rv60, clip=3.0),
            "ret5_over_rv15": _safe_ratio(ret5, rv15),
            "ret15_over_rv60": _safe_ratio(ret15, rv60),
            "trend_agreement_5_15": _sign(ret5) * _sign(ret15),
            "trend_efficiency_60": max(0.0, min(1.0, efficiency)),
            # Retained to make the contract directly auditable against the bar path.
            "path_high_60": float(np.max(highs[i60:index + 1])),
            "path_low_60": float(np.min(lows[i60:index + 1])),
        }
    return contexts


def _build_contexts(sources: list[dict[str, Any]]) -> dict[str, dict[float, dict[str, float]]]:
    return {str(source["instrument"]): _source_context(source) for source in sources}


def _segmented_rows(sources: list[dict[str, Any]], horizon: int,
                    contexts: dict[str, dict[float, dict[str, float]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        instrument = str(source["instrument"])
        for row in _build_horizon_rows(source, horizon):
            context = contexts[instrument].get(float(row["captured_ts"]))
            if context is None:
                continue
            item = dict(row); item["p2_features"] = dict(context)
            rows.append(item)
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))
    return rows


def _inner_split(rows: list[dict[str, Any]], horizon: int) -> tuple[list[dict], list[dict]]:
    ordered = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))
    unique_times = sorted({float(row["captured_ts"]) for row in ordered})
    if len(unique_times) < 50:
        return [], []
    split = max(1, int(len(unique_times) * (1.0 - INNER_TAIL_FRACTION)))
    if split >= len(unique_times):
        return [], []
    validation_start = unique_times[split]
    purge_boundary = validation_start - EMBARGO_SECONDS
    train = [row for row in ordered if float(row["target_ts"]) < purge_boundary - 1e-9]
    validation = [row for row in ordered if float(row["captured_ts"]) >= validation_start]
    if len(train) < 100 or len(validation) < 20:
        return [], []
    assert max(float(row["target_ts"]) for row in train) < purge_boundary
    return train, validation


def _direction_baselines(train: list[dict[str, Any]],
                         test: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    weights, _ = _weights(train)
    base = _clip_probability(_weighted_mean(y, weights))
    p5_negative, p5_positive = _conditional_probability(train, y, weights, "ret_5m")
    p15_negative, p15_positive = _conditional_probability(train, y, weights, "ret_15m")
    ret5 = np.asarray([float(row["features"]["ret_5m"]) for row in test])
    ret15 = np.asarray([float(row["features"]["ret_15m"]) for row in test])
    return {
        "constant_0_5": np.full(len(test), 0.5),
        "causal_base_rate": np.full(len(test), base),
        "ret5_persistence": np.where(ret5 > 0.0, p5_positive, p5_negative),
        "ret15_momentum": np.where(ret15 > 0.0, p15_positive, p15_negative),
    }


def _persistence_prediction(train: list[dict[str, Any]],
                            rows: list[dict[str, Any]]) -> np.ndarray:
    return _direction_baselines(train, rows)["ret5_persistence"]


def _logit_array(probability: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(p / (1.0 - p))


def _fit_standardization(x: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    denominator = max(float(weights.sum()), 1e-12)
    mean = (weights[:, None] * x).sum(axis=0) / denominator
    variance = (weights[:, None] * (x - mean) ** 2).sum(axis=0) / denominator
    std = np.sqrt(np.maximum(variance, 0.0)); std[std < 1e-12] = 1.0
    return mean, std


def _one_hot(value: str, values: tuple[str, ...]) -> list[float]:
    # First category is the fixed reference; the intercept represents it.
    return [1.0 if value == item else 0.0 for item in values[1:]]


def candidate_feature_names(candidate: str) -> list[str]:
    family = [f"family:{item}" for item in ASSET_FAMILIES[1:]]
    family_sign = [f"persistence_sign*family:{item}" for item in ASSET_FAMILIES[1:]]
    sessions = [f"session:{item}" for item in SESSIONS[1:]]
    session_sign = [f"persistence_sign*session:{item}" for item in SESSIONS[1:]]
    instruments = [f"instrument:{item}" for item in tuple(INSTRUMENTS)[1:]]
    instrument_sign = [f"persistence_sign*instrument:{item}" for item in tuple(INSTRUMENTS)[1:]]
    if candidate == "C1_ASSET_FAMILY_OFFSET":
        return family + family_sign
    if candidate == "C2_SESSION_OFFSET":
        return sessions + session_sign
    if candidate == "C3_INSTRUMENT_SHRUNK_OFFSET":
        return instruments + instrument_sign
    if candidate == "C4_FAMILY_SESSION_OFFSET":
        return family + family_sign + sessions + session_sign
    if candidate == "C5_FAMILY_SESSION_SMALL_REGIME":
        return (family + family_sign + sessions + session_sign
                + list(REGIME_FEATURES))
    if candidate in {"C0_NO_CORRECTION", NO_CORRECTION}:
        return []
    raise ValueError(candidate)


def _candidate_vector(row: dict[str, Any], candidate: str) -> list[float]:
    sign = 1.0 if float(row["features"]["ret_5m"]) > 0.0 else -1.0
    family = _one_hot(asset_family(str(row["instrument"])), ASSET_FAMILIES)
    session = _one_hot(session_utc(float(row["captured_ts"])), SESSIONS)
    instrument = _one_hot(str(row["instrument"]), tuple(INSTRUMENTS))
    if candidate == "C1_ASSET_FAMILY_OFFSET":
        values = family + [sign * value for value in family]
    elif candidate == "C2_SESSION_OFFSET":
        values = session + [sign * value for value in session]
    elif candidate == "C3_INSTRUMENT_SHRUNK_OFFSET":
        values = instrument + [sign * value for value in instrument]
    elif candidate == "C4_FAMILY_SESSION_OFFSET":
        values = (family + [sign * value for value in family]
                  + session + [sign * value for value in session])
    elif candidate == "C5_FAMILY_SESSION_SMALL_REGIME":
        context = row["p2_features"]
        values = (family + [sign * value for value in family]
                  + session + [sign * value for value in session]
                  + [float(context[name]) for name in REGIME_FEATURES])
    else:
        raise ValueError(candidate)
    if len(values) != len(candidate_feature_names(candidate)) or not np.all(np.isfinite(values)):
        raise ValueError(f"invalid P2E feature vector: {candidate}")
    return values


def _candidate_matrix(rows: list[dict[str, Any]], candidate: str) -> np.ndarray:
    return np.asarray([_candidate_vector(row, candidate) for row in rows], dtype=float)


def _fit_candidate(train: list[dict[str, Any]], candidate: str, l2: float) -> dict[str, Any]:
    x = _candidate_matrix(train, candidate)
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    weights, effective = _weights(train)
    offset = _logit_array(_persistence_prediction(train, train))
    mean, std = _fit_standardization(x, weights)
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1], dtype=float)
    regularizer = np.eye(design.shape[1], dtype=float) * float(l2)
    regularizer[0, 0] = float(l2) * 0.25
    for _ in range(80):
        score = offset + design @ beta
        probability = 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))
        variance = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = design.T @ (weights * (probability - y)) + regularizer @ beta
        hessian = design.T @ ((weights * variance)[:, None] * design) + regularizer
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient
        beta -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return {
        "contract_version": P2E_CONTRACT_VERSION,
        "model_family": P2E_MODEL_FAMILY,
        "candidate": candidate,
        "feature_names": candidate_feature_names(candidate),
        "l2": float(l2),
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "correction_intercept_and_coefficients": beta.tolist(),
        "train_raw_n": len(train),
        "train_effective_n": effective,
        "baseline": "causal_ret5_persistence_probability",
        "beta_zero_recovers_baseline_exactly": True,
        "train_only_standardization": True,
    }


def _predict_candidate(train: list[dict[str, Any]], rows: list[dict[str, Any]],
                       artifact: dict[str, Any]) -> np.ndarray:
    baseline = _persistence_prediction(train, rows)
    x = _candidate_matrix(rows, str(artifact["candidate"]))
    mean = np.asarray(artifact["feature_mean"], dtype=float)
    std = np.asarray(artifact["feature_std"], dtype=float)
    beta = np.asarray(artifact["correction_intercept_and_coefficients"], dtype=float)
    z = (x - mean) / np.where(std < 1e-12, 1.0, std)
    design = np.column_stack([np.ones(len(z)), z])
    score = _logit_array(baseline) + design @ beta
    return 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))


def _weighted_pr_auc(y: np.ndarray, score: np.ndarray,
                     weights: np.ndarray) -> float | None:
    positive_weight = float(weights[y >= 0.5].sum())
    if positive_weight <= 0.0 or float(weights[y < 0.5].sum()) <= 0.0:
        return None
    order = np.argsort(-score, kind="mergesort")
    y = y[order]; score = score[order]; weights = weights[order]
    tp = 0.0; fp = 0.0; previous_recall = 0.0; area = 0.0; index = 0
    while index < len(score):
        end = index + 1
        while end < len(score) and score[end] == score[index]:
            end += 1
        group_y = y[index:end]; group_w = weights[index:end]
        tp += float(group_w[group_y >= 0.5].sum())
        fp += float(group_w[group_y < 0.5].sum())
        recall = tp / positive_weight
        precision = tp / max(tp + fp, 1e-12)
        area += (recall - previous_recall) * precision
        previous_recall = recall
        index = end
    return float(area)


def _metrics(y: np.ndarray, prediction: np.ndarray,
             weights: np.ndarray) -> dict[str, float | None]:
    result = dict(_prob_metrics(y, prediction, weights))
    result["pr_auc"] = _weighted_pr_auc(y, prediction, weights)
    return result


def _inner_select(outer_train: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    inner_train, validation = _inner_split(outer_train, horizon)
    if not inner_train or not validation:
        raise RuntimeError("insufficient purged inner split for P2E")
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in validation])
    weights, effective = _weights(validation)
    baseline_predictions = _direction_baselines(inner_train, validation)
    baseline_metrics = {name: _metrics(y, values, weights)
                        for name, values in baseline_predictions.items()}
    c0_metrics = baseline_metrics["ret5_persistence"]
    c0_score = 2.0
    candidates: list[dict[str, Any]] = [{
        "candidate": "C0_NO_CORRECTION", "l2": None,
        "score": c0_score, "metrics": c0_metrics,
    }]
    for candidate in CANDIDATES[1:]:
        for l2 in L2_GRID:
            artifact = _fit_candidate(inner_train, candidate, l2)
            prediction = _predict_candidate(inner_train, validation, artifact)
            metrics = _metrics(y, prediction, weights)
            score = (float(metrics["brier"]) / float(c0_metrics["brier"])
                     + float(metrics["logloss"]) / float(c0_metrics["logloss"]))
            candidates.append({
                "candidate": candidate, "l2": float(l2),
                "score": score, "metrics": metrics,
            })
    learned = min(candidates[1:], key=lambda row: (
        float(row["score"]), -float(row["l2"]), CANDIDATES.index(str(row["candidate"]))))
    selected = (learned if float(learned["score"]) < c0_score * (1.0 - INNER_PRACTICAL_TIE_RELATIVE)
                else candidates[0])
    return {
        "selection_method": INNER_SELECTION_METHOD,
        "inner_train_raw_n": len(inner_train),
        "inner_validation_raw_n": len(validation),
        "inner_validation_effective_n": effective,
        "baseline_metrics": baseline_metrics,
        "candidates": candidates,
        "practical_tie_relative": INNER_PRACTICAL_TIE_RELATIVE,
        "selected_candidate": selected["candidate"],
        "selected_l2": selected.get("l2"),
        "selected_score": selected["score"],
    }


def _breakdown(records: list[dict[str, Any]], key) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(key(record["row"]))].append(record)
    result = {}
    for name, items in sorted(groups.items()):
        y = np.asarray([item["y"] for item in items], dtype=float)
        prediction = np.asarray([item["prediction"] for item in items], dtype=float)
        weights = np.asarray([item["weight"] for item in items], dtype=float)
        baseline_names = sorted(items[0]["baselines"])
        result[name] = {
            "raw_n": len(items),
            "dependency_weight_sum": float(weights.sum()),
            "model": _metrics(y, prediction, weights),
            "baselines": {
                baseline: _metrics(
                    y, np.asarray([item["baselines"][baseline] for item in items]), weights)
                for baseline in baseline_names
            },
        }
    return result


def evaluate_segmented_persistence(rows: list[dict[str, Any]],
                                   horizon: int) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    records: list[dict[str, Any]] = []
    reports = []
    selection_counts = Counter()
    joint_non_degrade = 0
    for fold in folds:
        train, test = fold["train"], fold["test"]
        selection = _inner_select(train, horizon)
        selected = str(selection["selected_candidate"])
        if selected == "C0_NO_CORRECTION":
            prediction = _persistence_prediction(train, test)
            artifact_contract = {
                "candidate": "C0_NO_CORRECTION",
                "beta_zero_recovers_baseline_exactly": True,
            }
        else:
            artifact = _fit_candidate(train, selected, float(selection["selected_l2"]))
            prediction = _predict_candidate(train, test, artifact)
            artifact_contract = {
                "candidate": selected,
                "l2": artifact["l2"],
                "feature_names": artifact["feature_names"],
                "beta_zero_recovers_baseline_exactly": True,
            }
        selection_counts[f"{selected}|l2={selection.get('selected_l2')}"] += 1
        y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in test])
        weights, effective = _weights(test)
        baseline_predictions = _direction_baselines(train, test)
        model_metrics = _metrics(y, prediction, weights)
        baseline_metrics = {name: _metrics(y, values, weights)
                            for name, values in baseline_predictions.items()}
        best_brier = min(float(item["brier"]) for item in baseline_metrics.values())
        best_logloss = min(float(item["logloss"]) for item in baseline_metrics.values())
        joint = (float(model_metrics["brier"]) <= best_brier
                 and float(model_metrics["logloss"]) <= best_logloss)
        joint_non_degrade += int(joint)
        reports.append({
            "fold_index": fold["fold_index"],
            "train_raw_n": len(train), "test_raw_n": len(test),
            "test_effective_n": effective,
            "test_start_ts": fold["test_start_ts"],
            "test_end_ts": fold["test_end_ts"],
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "selection": selection,
            "selected_artifact_contract": artifact_contract,
            "model": model_metrics, "baselines": baseline_metrics,
            "joint_non_degrade": joint,
        })
        for index, row in enumerate(test):
            records.append({
                "row": row, "y": float(y[index]),
                "prediction": float(prediction[index]), "weight": float(weights[index]),
                "baselines": {name: float(values[index])
                              for name, values in baseline_predictions.items()},
            })
    if not records:
        return {"fold_count": 0, "folds": reports}
    y = np.asarray([item["y"] for item in records])
    prediction = np.asarray([item["prediction"] for item in records])
    weights = np.asarray([item["weight"] for item in records])
    baseline_names = sorted(records[0]["baselines"])
    return {
        "fold_count": len(reports), "folds": reports,
        "model": _metrics(y, prediction, weights),
        "baselines": {
            name: _metrics(y, np.asarray([item["baselines"][name] for item in records]), weights)
            for name in baseline_names
        },
        "fold_joint_non_degrade_n": joint_non_degrade,
        "selection_counts": dict(sorted(selection_counts.items())),
        "per_family": _breakdown(records, lambda row: asset_family(row["instrument"])),
        "per_instrument": _breakdown(records, lambda row: row["instrument"]),
        "per_session": _breakdown(records, lambda row: session_utc(row["captured_ts"])),
    }


def _market_regime_count(rows: list[dict[str, Any]]) -> int:
    # Frozen causal two-state volatility regime.  The 15m RV is scaled by sqrt(4)
    # before comparison with 60m RV; only completed pre-T0 bars are involved.
    regimes = set()
    for row in rows:
        context = row["p2_features"]
        rv15 = float(context["realized_vol_15m"])
        rv60 = float(context["realized_vol_60m"])
        regimes.add("SHORT_VOL_HIGH" if 2.0 * rv15 >= rv60 else "SHORT_VOL_LOW")
    return len(regimes)


def winner_gate(evaluation: dict[str, Any], rows: list[dict[str, Any]],
                effective_n: int) -> dict[str, Any]:
    model = evaluation.get("model") or {}
    baselines = evaluation.get("baselines") or {}
    if not model or not baselines:
        return {"historical_winner": False, "status": P2E_VERDICT_INSUFFICIENT}
    best_brier_name = min(baselines, key=lambda name: float(baselines[name]["brier"]))
    best_logloss_name = min(baselines, key=lambda name: float(baselines[name]["logloss"]))
    best_brier = float(baselines[best_brier_name]["brier"])
    best_logloss = float(baselines[best_logloss_name]["logloss"])
    brier_improvement = (best_brier - float(model["brier"])) / best_brier
    logloss_improvement = (best_logloss - float(model["logloss"])) / best_logloss
    positive = sum(row["direction_label"] == "UP" for row in rows)
    negative = sum(row["direction_label"] == "DOWN" for row in rows)
    temporal_blocks = len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"])))
                           for row in rows})
    regimes = _market_regime_count(rows)
    sample_gate = (len(rows) >= MIN_HISTORICAL_RAW
                   and effective_n >= MIN_HISTORICAL_EFFECTIVE
                   and positive >= MIN_POSITIVE and negative >= MIN_NEGATIVE
                   and temporal_blocks >= MIN_TEMPORAL_BLOCKS and regimes >= MIN_REGIMES)
    fold_gate = int(evaluation.get("fold_count") or 0) == OUTER_FOLD_COUNT
    robustness_gate = int(evaluation.get("fold_joint_non_degrade_n") or 0) >= FOLD_JOINT_NON_DEGRADE_REQUIRED
    metric_gate = (brier_improvement >= MIN_PROVISIONAL_RELATIVE_IMPROVEMENT
                   and logloss_improvement >= MIN_PROVISIONAL_RELATIVE_IMPROVEMENT)
    winner = bool(sample_gate and fold_gate and robustness_gate and metric_gate)
    return {
        "historical_winner": winner,
        "status": P2E_VERDICT_WINNER if winner else P2E_VERDICT_NEGATIVE,
        "best_brier_baseline": best_brier_name, "best_brier": best_brier,
        "model_brier": model["brier"], "brier_relative_improvement": brier_improvement,
        "best_logloss_baseline": best_logloss_name, "best_logloss": best_logloss,
        "model_logloss": model["logloss"], "logloss_relative_improvement": logloss_improvement,
        "required_relative_improvement": MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
        "raw_n": len(rows), "effective_n": effective_n,
        "positive_n": positive, "negative_n": negative,
        "temporal_blocks": temporal_blocks, "regimes": regimes,
        "sample_gate": sample_gate, "fold_gate": fold_gate,
        "fold_joint_non_degrade_observed": int(evaluation.get("fold_joint_non_degrade_n") or 0),
        "fold_joint_non_degrade_required": FOLD_JOINT_NON_DEGRADE_REQUIRED,
        "robustness_gate": robustness_gate, "metric_gate": metric_gate,
    }


def run_segmented_persistence_experiment(
    sources: list[dict[str, Any]], *, source_set_sha256: str,
) -> dict[str, Any]:
    """Execute the one allowed P2E cycle on immutable real 5m sources."""
    started = time.time()
    contexts = _build_contexts(sources)
    results = []
    for horizon in (15, 30, 60, 120, 240):
        rows = [row for row in _segmented_rows(sources, horizon, contexts)
                if row["direction_label"] != "FLAT"]
        _weight_array, effective = _weights(rows)
        evaluation = evaluate_segmented_persistence(rows, horizon)
        gate = winner_gate(evaluation, rows, effective)
        results.append({
            "target": DIRECTION_TARGET, "horizon_minutes": horizon,
            "raw_n": len(rows), "effective_n": effective,
            "historical_winner": gate["historical_winner"],
            "verdict": gate["status"],
            "brier_relative_improvement": gate.get("brier_relative_improvement"),
            "logloss_relative_improvement": gate.get("logloss_relative_improvement"),
            "fold_joint_non_degrade_n": gate.get("fold_joint_non_degrade_observed", 0),
            "selection_counts": evaluation.get("selection_counts") or {},
            "gate": gate,
            "evaluation": evaluation,
        })
    winners = sum(bool(row["historical_winner"]) for row in results)
    return {
        "contract_version": P2E_CONTRACT_VERSION,
        "evidence_label": P2E_EVIDENCE_LABEL,
        "source_set_sha256": source_set_sha256,
        "target": DIRECTION_TARGET,
        "verdict": P2E_VERDICT_WINNER if winners else P2E_VERDICT_NEGATIVE,
        "run_count": len(results), "winner_count": winners,
        "results": results,
        "frozen_contract": {
            "asset_family_by_instrument": ASSET_FAMILY_BY_INSTRUMENT,
            "session_utc_intervals": SESSION_UTC_INTERVALS,
            "candidates": CANDIDATES,
            "l2_grid": L2_GRID,
            "outer_folds": OUTER_FOLD_COUNT,
            "inner_method": INNER_SELECTION_METHOD,
            "inner_practical_tie_relative": INNER_PRACTICAL_TIE_RELATIVE,
            "winner_relative_improvement": MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
        },
        "completed_5m_bars_only": True,
        "target_overlap_purge": True, "embargo_seconds": EMBARGO_SECONDS,
        "shuffle": False, "train_only_standardization": True,
        "dependency_group_total_weight_one": True,
        "outer_test_used_for_selection": False,
        "post_hoc_breakdowns_used_for_selection": False,
        "historical_options_used": False, "synthetic_options_used": False,
        "live_parity_ready": False, "live_cohort_created": False,
        "auto_promotion": False, "production_authority": False,
        "duration_ms": (time.time() - started) * 1000.0,
    }


def run_from_runtime(runtime) -> dict[str, Any]:
    """Use the current immutable P1B source set without writing production DB."""
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT state,source_set_sha256 FROM g1s_historical_wf_state WHERE id=1").fetchone()
    if state is None or str(state["state"]) != "COMPLETE" or not state["source_set_sha256"]:
        raise RuntimeError("P1B historical source set is not COMPLETE")
    source_set = str(state["source_set_sha256"])
    with runtime._lock:
        run = runtime._conn.execute(
            "SELECT artifact_json FROM g1s_historical_wf_runs WHERE source_set_sha256=? "
            "ORDER BY created_ts LIMIT 1", (source_set,)).fetchone()
    if run is None:
        raise RuntimeError("P1B current run artifact unavailable")
    artifact = json.loads(run["artifact_json"])
    source_ids = [str(row["source_id"]) for row in artifact.get("source_summary") or []]
    if len(source_ids) != len(INSTRUMENTS):
        raise RuntimeError(f"expected {len(INSTRUMENTS)} source ids, got {len(source_ids)}")
    placeholders = ",".join("?" for _ in source_ids)
    with runtime._lock:
        rows = runtime._conn.execute(
            f"SELECT * FROM g1s_historical_sources WHERE source_id IN ({placeholders})",
            tuple(source_ids)).fetchall()
    by_id = {str(row["source_id"]): dict(row) for row in rows}
    sources = []
    for source_id in source_ids:
        item = by_id.get(source_id)
        if item is None:
            raise RuntimeError(f"missing immutable source {source_id}")
        item["bars"] = _load_source_bars(item)
        sources.append(item)
    return run_segmented_persistence_experiment(
        sources, source_set_sha256=source_set)


def dumps_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
