"""Exact fast path for the P2 dependency-weighted stump search.

The research contract/candidates/folds are unchanged.  The slow reference fitter
recomputed a boolean split and weighted SSE for every threshold on every boosting
round.  This implementation precomputes the fixed quantile split masks once and
uses the identity

  SSE_after_group_means = sum(w*r^2) - sum_left(w*r)^2/sum_left(w)
                                      - sum_right(w*r)^2/sum_right(w)

to rank exactly the same fixed candidates.  Ties retain reference iteration order.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from . import g1_short_horizon_p2_regime_research as _p2
from .g1_short_horizon_historical_wf import _clip_probability, _weighted_mean


FAST_GBT_VERSION = "g1s-p2-weighted-gbt-vectorized-v1"


def _logit(p: float) -> float:
    p = _clip_probability(p)
    return float(np.log(p / (1.0 - p)))


def fit_weighted_gbt_fast(x: np.ndarray, y: np.ndarray,
                          weights: np.ndarray) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    weights = np.asarray(weights, dtype=float)
    base_rate = _clip_probability(_weighted_mean(y, weights))
    score = np.full(len(y), _logit(base_rate), dtype=float)

    masks = []
    metadata: list[tuple[int, float]] = []
    for feature_index in range(x.shape[1]):
        col = x[:, feature_index]
        thresholds = sorted(set(float(np.quantile(col, q)) for q in _p2.GBT_QUANTILES))
        for threshold in thresholds:
            mask = col <= threshold
            if not mask.any() or mask.all():
                continue
            masks.append(mask)
            metadata.append((feature_index, threshold))
    if not masks:
        return {
            "model_family": _p2.GBT_MODEL,
            "base_logit": _logit(base_rate), "base_rate": base_rate,
            "learning_rate": _p2.GBT_LEARNING_RATE, "stumps": [],
            "n_estimators_requested": _p2.GBT_ESTIMATORS,
            "threshold_quantiles": list(_p2.GBT_QUANTILES),
            "dependency_weighted": True, "hyperparameter_search": False,
            "compute_contract": FAST_GBT_VERSION,
        }

    split = np.asarray(masks, dtype=np.float32)
    left_weight = split @ weights
    total_weight = float(weights.sum())
    right_weight = total_weight - left_weight
    valid = (left_weight > 1e-12) & (right_weight > 1e-12)
    stumps: list[dict[str, Any]] = []

    for _ in range(_p2.GBT_ESTIMATORS):
        probability = 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))
        residual = y - probability
        weighted_residual = weights * residual
        total_wr = float(weighted_residual.sum())
        left_wr = split @ weighted_residual
        right_wr = total_wr - left_wr
        total_sse = float(np.sum(weights * residual * residual))
        improvement = np.full(len(metadata), -np.inf, dtype=float)
        improvement[valid] = (
            left_wr[valid] * left_wr[valid] / left_weight[valid]
            + right_wr[valid] * right_wr[valid] / right_weight[valid]
        )
        # Reference minimizes total_sse-improvement, so maximizing improvement is exact.
        best_index = int(np.argmax(improvement))
        if not np.isfinite(improvement[best_index]):
            break
        feature_index, threshold = metadata[best_index]
        left_value = float(left_wr[best_index] / left_weight[best_index])
        right_value = float(right_wr[best_index] / right_weight[best_index])
        chosen = split[best_index].astype(bool, copy=False)
        score += _p2.GBT_LEARNING_RATE * np.where(chosen, left_value, right_value)
        stumps.append({
            "feature_index": int(feature_index), "threshold": float(threshold),
            "left_value": left_value, "right_value": right_value,
            "weighted_sse": total_sse - float(improvement[best_index]),
        })

    return {
        "model_family": _p2.GBT_MODEL,
        "base_logit": _logit(base_rate), "base_rate": base_rate,
        "learning_rate": _p2.GBT_LEARNING_RATE, "stumps": stumps,
        "n_estimators_requested": _p2.GBT_ESTIMATORS,
        "threshold_quantiles": list(_p2.GBT_QUANTILES),
        "dependency_weighted": True, "hyperparameter_search": False,
        "compute_contract": FAST_GBT_VERSION,
    }


def run_p2_nested_research_fast(runtime, *, force: bool = False):
    previous = _p2._fit_weighted_gbt
    _p2._fit_weighted_gbt = fit_weighted_gbt_fast
    try:
        result = _p2.run_p2_nested_research(runtime, force=force)
        result["gbt_compute_contract"] = FAST_GBT_VERSION
        return result
    finally:
        _p2._fit_weighted_gbt = previous
