"""Transparent metrics, significance correction and edge-score components."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _prob_metrics, _weights


def metrics(rows: list[dict[str, Any]], prediction: np.ndarray) -> dict[str, Any]:
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in rows])
    weights, effective = _weights(rows)
    result = dict(_prob_metrics(y, np.asarray(prediction, dtype=float), weights))
    direction = np.where(np.asarray(prediction) >= 0.5, 1.0, -1.0)
    returns = np.asarray([float(row["terminal_log_return"]) for row in rows])
    result.update({
        "signed_expectancy": float(np.sum(weights*direction*returns)/max(weights.sum(), 1e-12)),
        "raw_n": len(rows), "effective_n": int(effective),
        "positive_n": int(np.sum(y >= 0.5)), "negative_n": int(np.sum(y < 0.5)),
    })
    return result


def paired_loss_pvalue(rows: list[dict[str, Any]], model: np.ndarray,
                       baseline: np.ndarray) -> float:
    """One-sided normal approximation on dependency-group mean joint losses."""
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in rows])
    model = np.clip(np.asarray(model, dtype=float), 1e-6, 1-1e-6)
    baseline = np.clip(np.asarray(baseline, dtype=float), 1e-6, 1-1e-6)
    model_loss = (model-y)**2 + (-(y*np.log(model)+(1-y)*np.log(1-model)))
    baseline_loss = ((baseline-y)**2
                     + (-(y*np.log(baseline)+(1-y)*np.log(1-baseline))))
    groups: dict[float, list[float]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[float(row["captured_ts"])].append(float(baseline_loss[index]-model_loss[index]))
    values = np.asarray([float(np.mean(items)) for items in groups.values()], dtype=float)
    if len(values) < 3:
        return 1.0
    mean = float(values.mean())
    std = float(values.std(ddof=1))
    if std <= 1e-15:
        return 0.0 if mean > 0 else 1.0
    z = mean/(std/math.sqrt(len(values)))
    return float(0.5*math.erfc(z/math.sqrt(2.0)))


def benjamini_hochberg(pvalues: list[float]) -> list[float]:
    if not pvalues:
        return []
    values = [min(1.0, max(0.0, float(value))) for value in pvalues]
    order = sorted(range(len(values)), key=lambda index: values[index])
    adjusted = [1.0]*len(values)
    running = 1.0
    total = len(values)
    for rank_index in range(total-1, -1, -1):
        index = order[rank_index]
        rank = rank_index+1
        running = min(running, values[index]*total/rank)
        adjusted[index] = min(1.0, running)
    return adjusted


def relative_improvement(model: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    return {
        "brier": ((float(baseline["brier"])-float(model["brier"]))
                  / max(float(baseline["brier"]), 1e-12)),
        "logloss": ((float(baseline["logloss"])-float(model["logloss"]))
                    / max(float(baseline["logloss"]), 1e-12)),
        "signed_expectancy_delta": (float(model["signed_expectancy"])
                                    - float(baseline["signed_expectancy"])),
    }


def edge_score(*, improvement: dict[str, float], coverage: float,
               fold_positive: int, fold_evaluated: int, temporal_blocks: int,
               instrument_count: int, regime_count: int, effective_n: int,
               complexity: int, q_value: float) -> dict[str, Any]:
    magnitude = max(-1.0, min(1.0, 50.0*min(improvement["brier"], improvement["logloss"])))
    coverage_component = math.sqrt(max(0.0, min(1.0, coverage)))
    fold_stability = fold_positive/max(fold_evaluated, 1)
    temporal_stability = min(1.0, temporal_blocks/20.0)
    instrument_breadth = min(1.0, instrument_count/3.0)
    regime_breadth = min(1.0, regime_count/2.0)
    sample_component = min(1.0, math.sqrt(max(effective_n, 0)/400.0))
    complexity_penalty = {1: 1.0, 2: 0.82, 3: 0.64}.get(complexity, 0.0)
    multiple_testing = max(0.0, 1.0-min(1.0, q_value))
    raw = (
        0.30*magnitude + 0.11*coverage_component + 0.16*fold_stability
        + 0.08*temporal_stability + 0.08*instrument_breadth
        + 0.05*regime_breadth + 0.12*sample_component + 0.10*multiple_testing
    )
    score = raw*complexity_penalty
    return {
        "score": float(score),
        "components": {
            "edge_magnitude": magnitude, "coverage": coverage_component,
            "fold_stability": fold_stability, "temporal_stability": temporal_stability,
            "instrument_breadth": instrument_breadth, "regime_breadth": regime_breadth,
            "sample_size": sample_component, "complexity_penalty": complexity_penalty,
            "multiple_testing_penalty_component": multiple_testing,
        },
    }
