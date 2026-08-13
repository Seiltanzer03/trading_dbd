"""Exact fast paths for the P2 nested historical experiment.

Two hot loops are optimized without changing the evidence contract:
1) fixed weighted GBT stump candidates are scored by precomputed split masks;
2) 60m same-T0 pair correlations use sliding sufficient statistics instead of
   one ``np.corrcoef`` call for every pair at every timestamp.

Candidate families, timestamps, minimum samples, folds, baselines and gates stay
unchanged.  Tests compare these fast paths to the reference implementation.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np

from . import g1_short_horizon_p2_regime_research as _p2
from .g1_short_horizon_historical_wf import _clip_probability, _weighted_mean


FAST_GBT_VERSION = "g1s-p2-weighted-gbt-vectorized-v1"
FAST_CROSS_VERSION = "g1s-p2-cross-rolling-sufficient-stats-v1"


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
        # Reference minimizes total_sse-improvement, therefore the first maximum
        # in fixed feature/threshold iteration order is the same deterministic tie.
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


def _pair_corr_series(a: dict[float, dict[str, float]],
                      b: dict[float, dict[str, float]]) -> dict[float, float]:
    common = sorted(set(a).intersection(b))
    window: deque[tuple[float, float, float]] = deque()
    sx = sy = sxx = syy = sxy = 0.0
    result: dict[float, float] = {}
    for ts in common:
        lower = float(ts) - 60.0 * 60.0 - 1e-6
        while window and window[0][0] < lower:
            _old_ts, x, y = window.popleft()
            sx -= x; sy -= y; sxx -= x*x; syy -= y*y; sxy -= x*y
        x = float(a[ts]["ret_5m"]); y = float(b[ts]["ret_5m"])
        window.append((float(ts), x, y))
        sx += x; sy += y; sxx += x*x; syy += y*y; sxy += x*y
        n = len(window)
        if n < 6:
            continue
        varx = sxx - sx*sx/n
        vary = syy - sy*sy/n
        if varx / n < 1e-24 or vary / n < 1e-24:
            continue
        cov = sxy - sx*sy/n
        rho = cov / math.sqrt(max(varx*vary, 1e-300))
        if math.isfinite(rho):
            result[float(ts)] = max(-1.0, min(1.0, float(rho)))
    return result


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def build_contexts_fast(sources: list[dict[str, Any]]) -> dict[str, dict[float, dict[str, float]]]:
    contexts = {str(source["instrument"]): _p2._source_context(source) for source in sources}
    instruments = sorted(contexts)
    pair_corr: dict[tuple[str, str], dict[float, float]] = {}
    for i, left in enumerate(instruments):
        for right in instruments[i+1:]:
            pair_corr[(left, right)] = _pair_corr_series(contexts[left], contexts[right])

    for instrument, rows in contexts.items():
        group = _p2._instrument_group(instrument)
        total_possible = max(1, len(contexts)-1)
        family_possible = max(1, sum(
            code != instrument and _p2._instrument_group(code) == group for code in contexts))
        for captured_ts, own in rows.items():
            peers = [(code, values[captured_ts]) for code, values in contexts.items()
                     if code != instrument and captured_ts in values]
            family = [(code, row) for code, row in peers
                      if _p2._instrument_group(code) == group]

            def vals(name: str, members):
                return [float(row[name]) for _code, row in members]

            def breadth(name: str, members) -> float:
                values = vals(name, members)
                return float(np.mean([_p2._sign(value) for value in values])) if values else 0.0

            peer15 = vals("ret_15m", peers); peer60 = vals("ret_60m", peers)
            family15 = vals("ret_15m", family)
            median15 = float(np.median(peer15)) if peer15 else 0.0
            median60 = float(np.median(peer60)) if peer60 else 0.0
            family_median15 = float(np.median(family15)) if family15 else 0.0
            correlations: list[float] = []
            family_correlations: list[float] = []
            for code, _row in peers:
                rho = pair_corr.get(_pair_key(instrument, code), {}).get(float(captured_ts))
                if rho is None:
                    continue
                correlations.append(float(rho))
                if _p2._instrument_group(code) == group:
                    family_correlations.append(float(rho))
            own.update({
                "cross_peer_fraction": len(peers)/total_possible,
                "cross_breadth_ret5": breadth("ret_5m", peers),
                "cross_breadth_ret15": breadth("ret_15m", peers),
                "cross_breadth_ret60": breadth("ret_60m", peers),
                "cross_median_ret15": median15,
                "cross_median_ret60": median60,
                "cross_relative_ret15": float(own["ret_15m"])-median15,
                "cross_relative_ret60": float(own["ret_60m"])-median60,
                "cross_dispersion_ret15": float(np.std(peer15)) if len(peer15) >= 2 else 0.0,
                "cross_dispersion_ret60": float(np.std(peer60)) if len(peer60) >= 2 else 0.0,
                "cross_mean_corr_60": float(np.mean(correlations)) if correlations else 0.0,
                "cross_mean_abs_corr_60": float(np.mean(np.abs(correlations))) if correlations else 0.0,
                "family_peer_fraction": len(family)/family_possible,
                "family_breadth_ret15": breadth("ret_15m", family),
                "family_relative_ret15": float(own["ret_15m"])-family_median15,
                "family_mean_corr_60": float(np.mean(family_correlations)) if family_correlations else 0.0,
            })
    return contexts


def run_p2_nested_research_fast(runtime, *, force: bool = False):
    previous_gbt = _p2._fit_weighted_gbt
    previous_contexts = _p2._build_contexts
    _p2._fit_weighted_gbt = fit_weighted_gbt_fast
    _p2._build_contexts = build_contexts_fast
    try:
        result = _p2.run_p2_nested_research(runtime, force=force)
        result["gbt_compute_contract"] = FAST_GBT_VERSION
        result["cross_compute_contract"] = FAST_CROSS_VERSION
        return result
    finally:
        _p2._fit_weighted_gbt = previous_gbt
        _p2._build_contexts = previous_contexts
