"""P2 nested historical research for regime-conditioned G.1S challengers.

This module is deliberately *not* installed into the production worker yet.
It is an offline/research experiment over the immutable real 5m source set that
P1B already materialized.  We first prove that a predeclared selection process
beats causal baselines on untouched outer walk-forward folds.  Only then is it
worth freezing an exactly matching future T0/live feature contract.

Safety/evidence rules:
- input bars are the immutable P1B Yahoo 5m source artifacts;
- no option history, no synthetic Greeks/options, no future feature backfill;
- all market features use bars ending <= T0;
- cross-asset peers must have an observation at the *same completed 5m T0*;
  stale/closed-session prices are never carried forward as contemporaneous;
- four outer expanding walk-forward folds remain untouched by feature/model
  selection;
- feature/model selection uses only a purged tail holdout inside outer training;
- dependency groups retain total weight 1;
- direction tests fixed logistic and fixed shallow weighted GBT challengers;
- return uses a zero-anchored ridge whose shrinkage is learned only inside train;
- a historical winner is only `HISTORICAL_WINNER_PENDING_LIVE_PARITY`.
  This module creates no G.1S model, live cohort, promotion, or trading authority.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import sqlite3
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from .config import INSTRUMENTS
from .g1_short_horizon_champion_runtime import DIRECTION_TARGET, RETURN_TARGET
from .g1_short_horizon_historical_wf import (
    BAR_SECONDS,
    EMBARGO_SECONDS,
    HISTORICAL_EVIDENCE_LABEL,
    HISTORICAL_WF_CONTRACT_VERSION,
    MIN_HISTORICAL_EFFECTIVE,
    MIN_HISTORICAL_RAW,
    MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
    _anchor_index,
    _build_horizon_rows,
    _clip_probability,
    _conditional_probability,
    _fit_logistic,
    _fit_ridge,
    _historical_folds,
    _json,
    _load_source_bars,
    _predict_linear,
    _prob_metrics,
    _return_metrics,
    _sha,
    _weighted_mean,
    _weights,
)


P2_CONTRACT_VERSION = "g1s-p2-regime-cross-nested-wf-v1"
P2_FEATURE_CONTRACT = "g1s-p2-causal-bar-context-v1"
P2_EVIDENCE_LABEL = "HISTORICAL_NESTED_WALK_FORWARD"
P2_LIVE_LABEL = "LIVE_PROSPECTIVE_OOS"
INNER_SELECTION_METHOD = "purged_tail_20pct_inside_outer_train"
OUTER_FOLD_COUNT = 4
INNER_TAIL_FRACTION = 0.20
FOLD_JOINT_NON_DEGRADE_REQUIRED = 3
GBT_MODEL = "WEIGHTED_SHALLOW_GBT_STUMPS_V1"
LOGISTIC_MODEL = "DEPENDENCY_WEIGHTED_LOGISTIC_V1"
ZERO_SHRUNK_RIDGE_MODEL = "ZERO_ANCHORED_DEPENDENCY_WEIGHTED_RIDGE_V1"
GBT_ESTIMATORS = 18
GBT_LEARNING_RATE = 0.12
GBT_QUANTILES = (0.20, 0.35, 0.50, 0.65, 0.80)

BASE_FEATURES = (
    "ret_5m", "ret_15m", "ret_60m",
    "realized_vol_15m", "realized_vol_60m",
)
REGIME_FEATURES = (
    "ret5_over_rv15", "ret15_over_rv60", "rv15_over_rv60",
    "trend_agreement_5_15", "trend_agreement_15_60",
    "momentum_accel_5_vs_15", "momentum_accel_15_vs_60",
    "trend_efficiency_60", "range60_over_rv60",
    "drawup_60", "drawdown_from_high_60",
    "rv60_rank_32", "abs_ret15_rank_32", "regime_history_ready",
    "utc_sin", "utc_cos", "weekday_sin", "weekday_cos",
)
CROSS_FEATURES = (
    "cross_peer_fraction", "cross_breadth_ret5", "cross_breadth_ret15",
    "cross_breadth_ret60", "cross_median_ret15", "cross_median_ret60",
    "cross_relative_ret15", "cross_relative_ret60",
    "cross_dispersion_ret15", "cross_dispersion_ret60",
    "cross_mean_corr_60", "cross_mean_abs_corr_60",
    "family_peer_fraction", "family_breadth_ret15",
    "family_relative_ret15", "family_mean_corr_60",
)
FEATURE_FAMILIES = {
    "BASE_P2": BASE_FEATURES,
    "REGIME_P2": BASE_FEATURES + REGIME_FEATURES,
    "CROSS_P2": BASE_FEATURES + CROSS_FEATURES,
    "REGIME_CROSS_P2": BASE_FEATURES + REGIME_FEATURES + CROSS_FEATURES,
}
INSTRUMENT_DUMMIES = tuple(INSTRUMENTS)[1:]

_GROUPS = {
    "equity": {"NAS100", "SP500", "US30", "GER40", "UK100", "JPY100"},
    "metal": {"XAU", "XAG"},
    "fx": {"EURUSD", "USDCAD"},
}


def _instrument_group(instrument: str) -> str:
    for group, members in _GROUPS.items():
        if instrument in members:
            return group
    return "other"


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _safe_ratio(num: float, den: float, *, clip: float = 8.0) -> float:
    if not math.isfinite(num) or not math.isfinite(den) or abs(den) < 1e-12:
        return 0.0
    return max(-clip, min(clip, num / den))


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


def _rank_against_history(value: float, previous: list[float]) -> tuple[float, float]:
    history = [float(x) for x in previous[-32:] if math.isfinite(float(x))]
    if len(history) < 8:
        return 0.5, 0.0
    rank = sum(x <= value for x in history) / len(history)
    return float(rank), 1.0


def _source_context(source: dict[str, Any]) -> dict[float, dict[str, float]]:
    """Causal same-instrument context at every valid completed 5m T0."""
    bars = source["bars"]
    times = [float(bar["bar_end_ts"]) for bar in bars]
    closes = np.asarray([float(bar["close"]) for bar in bars], dtype=float)
    highs = np.asarray([float(bar["high"]) for bar in bars], dtype=float)
    lows = np.asarray([float(bar["low"]) for bar in bars], dtype=float)
    log_step = np.zeros(len(bars), dtype=float)
    log_step[1:] = np.log(closes[1:] / closes[:-1])
    rv_history: list[float] = []
    abs_ret_history: list[float] = []
    contexts: dict[float, dict[str, float]] = {}

    for index in range(12, len(bars)):
        i5 = _anchor_index(times, index, 5 * 60.0)
        i15 = _anchor_index(times, index, 15 * 60.0)
        i60 = _anchor_index(times, index, 60 * 60.0)
        if None in (i5, i15, i60):
            continue
        assert i5 is not None and i15 is not None and i60 is not None
        # Same continuity contract as P1B: a valid 60m context requires roughly
        # twelve actual 5m bars, not an overnight/session carry.
        if index - i60 < 11:
            continue
        rv15_values = log_step[i15 + 1:index + 1]
        rv60_values = log_step[i60 + 1:index + 1]
        if len(rv15_values) < 2 or len(rv60_values) < 10:
            continue
        current = float(closes[index])
        if current <= 0:
            continue
        ret5 = float(math.log(current / closes[i5]))
        ret15 = float(math.log(current / closes[i15]))
        ret60 = float(math.log(current / closes[i60]))
        rv15 = float(math.sqrt(float(np.sum(rv15_values * rv15_values))))
        rv60 = float(math.sqrt(float(np.sum(rv60_values * rv60_values))))
        path_high = float(np.max(highs[i60:index + 1]))
        path_low = float(np.min(lows[i60:index + 1]))
        path_abs = float(np.sum(np.abs(rv60_values)))
        efficiency = abs(ret60) / path_abs if path_abs > 1e-12 else 0.0
        range60 = math.log(path_high / path_low) if path_high > 0 and path_low > 0 else 0.0
        rv_rank, rv_ready = _rank_against_history(rv60, rv_history)
        ret_rank, ret_ready = _rank_against_history(abs(ret15), abs_ret_history)
        captured_ts = float(times[index])
        utc_day_fraction = (captured_ts % 86400.0) / 86400.0
        weekday = int(time.gmtime(captured_ts).tm_wday)
        context = {
            "ret_5m": ret5,
            "ret_15m": ret15,
            "ret_60m": ret60,
            "realized_vol_15m": rv15,
            "realized_vol_60m": rv60,
            "ret5_over_rv15": _safe_ratio(ret5, rv15),
            "ret15_over_rv60": _safe_ratio(ret15, rv60),
            "rv15_over_rv60": _safe_ratio(rv15, rv60, clip=3.0),
            "trend_agreement_5_15": _sign(ret5) * _sign(ret15),
            "trend_agreement_15_60": _sign(ret15) * _sign(ret60),
            "momentum_accel_5_vs_15": ret5 - ret15 / 3.0,
            "momentum_accel_15_vs_60": ret15 - ret60 / 4.0,
            "trend_efficiency_60": max(0.0, min(1.0, efficiency)),
            "range60_over_rv60": _safe_ratio(range60, rv60, clip=8.0),
            "drawup_60": math.log(current / path_low) if path_low > 0 else 0.0,
            "drawdown_from_high_60": math.log(current / path_high) if path_high > 0 else 0.0,
            "rv60_rank_32": rv_rank,
            "abs_ret15_rank_32": ret_rank,
            "regime_history_ready": min(rv_ready, ret_ready),
            "utc_sin": math.sin(2.0 * math.pi * utc_day_fraction),
            "utc_cos": math.cos(2.0 * math.pi * utc_day_fraction),
            "weekday_sin": math.sin(2.0 * math.pi * weekday / 7.0),
            "weekday_cos": math.cos(2.0 * math.pi * weekday / 7.0),
        }
        contexts[captured_ts] = context
        rv_history.append(rv60)
        abs_ret_history.append(abs(ret15))
    return contexts


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 6 or len(xs) != len(ys):
        return None
    x = np.asarray(xs, dtype=float); y = np.asarray(ys, dtype=float)
    if float(np.std(x)) < 1e-12 or float(np.std(y)) < 1e-12:
        return None
    value = float(np.corrcoef(x, y)[0, 1])
    return value if math.isfinite(value) else None


def _rolling_pair_corr(
    instrument: str,
    peer: str,
    captured_ts: float,
    contexts: dict[str, dict[float, dict[str, float]]],
    times_by_instrument: dict[str, list[float]],
) -> float | None:
    target_times = times_by_instrument[instrument]
    start = bisect.bisect_left(target_times, captured_ts - 60 * 60.0 - 1e-6)
    end = bisect.bisect_right(target_times, captured_ts + 1e-6)
    xs: list[float] = []; ys: list[float] = []
    peer_context = contexts[peer]
    for ts in target_times[start:end]:
        target_row = contexts[instrument].get(ts)
        peer_row = peer_context.get(ts)
        if target_row is None or peer_row is None:
            continue
        xs.append(float(target_row["ret_5m"]))
        ys.append(float(peer_row["ret_5m"]))
    return _corr(xs, ys)


def _cross_features(
    instrument: str,
    captured_ts: float,
    own: dict[str, float],
    contexts: dict[str, dict[float, dict[str, float]]],
    times_by_instrument: dict[str, list[float]],
) -> dict[str, float]:
    peers = [(code, rows[captured_ts]) for code, rows in contexts.items()
             if code != instrument and captured_ts in rows]
    total_possible = max(1, len(contexts) - 1)
    group = _instrument_group(instrument)
    family_possible = max(1, sum(
        code != instrument and _instrument_group(code) == group for code in contexts))
    family = [(code, row) for code, row in peers if _instrument_group(code) == group]

    def values(name: str, rows: list[tuple[str, dict[str, float]]]) -> list[float]:
        return [float(row[name]) for _code, row in rows]

    def breadth(name: str, rows: list[tuple[str, dict[str, float]]]) -> float:
        vals = values(name, rows)
        return float(np.mean([_sign(v) for v in vals])) if vals else 0.0

    peer15 = values("ret_15m", peers); peer60 = values("ret_60m", peers)
    family15 = values("ret_15m", family)
    median15 = float(np.median(peer15)) if peer15 else 0.0
    median60 = float(np.median(peer60)) if peer60 else 0.0
    family_median15 = float(np.median(family15)) if family15 else 0.0
    correlations: list[float] = []
    family_correlations: list[float] = []
    for code, _row in peers:
        rho = _rolling_pair_corr(instrument, code, captured_ts, contexts, times_by_instrument)
        if rho is None:
            continue
        correlations.append(rho)
        if _instrument_group(code) == group:
            family_correlations.append(rho)
    return {
        "cross_peer_fraction": len(peers) / total_possible,
        "cross_breadth_ret5": breadth("ret_5m", peers),
        "cross_breadth_ret15": breadth("ret_15m", peers),
        "cross_breadth_ret60": breadth("ret_60m", peers),
        "cross_median_ret15": median15,
        "cross_median_ret60": median60,
        "cross_relative_ret15": float(own["ret_15m"]) - median15,
        "cross_relative_ret60": float(own["ret_60m"]) - median60,
        "cross_dispersion_ret15": float(np.std(peer15)) if len(peer15) >= 2 else 0.0,
        "cross_dispersion_ret60": float(np.std(peer60)) if len(peer60) >= 2 else 0.0,
        "cross_mean_corr_60": float(np.mean(correlations)) if correlations else 0.0,
        "cross_mean_abs_corr_60": float(np.mean(np.abs(correlations))) if correlations else 0.0,
        "family_peer_fraction": len(family) / family_possible,
        "family_breadth_ret15": breadth("ret_15m", family),
        "family_relative_ret15": float(own["ret_15m"]) - family_median15,
        "family_mean_corr_60": float(np.mean(family_correlations)) if family_correlations else 0.0,
    }


def _build_contexts(sources: list[dict[str, Any]]) -> dict[str, dict[float, dict[str, float]]]:
    contexts = {str(source["instrument"]): _source_context(source) for source in sources}
    times_by_instrument = {code: sorted(rows) for code, rows in contexts.items()}
    for instrument, rows in contexts.items():
        for captured_ts, own in rows.items():
            own.update(_cross_features(
                instrument, captured_ts, own, contexts, times_by_instrument))
    return contexts


def _p2_rows(sources: list[dict[str, Any]], horizon: int,
             contexts: dict[str, dict[float, dict[str, float]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        instrument = str(source["instrument"])
        source_context = contexts[instrument]
        for row in _build_horizon_rows(source, int(horizon)):
            context = source_context.get(float(row["captured_ts"]))
            if context is None:
                continue
            item = dict(row)
            item["p2_features"] = dict(context)
            rows.append(item)
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))
    return rows


def _feature_names(family: str) -> tuple[str, ...]:
    return tuple(FEATURE_FAMILIES[family]) + tuple(
        f"instrument:{code}" for code in INSTRUMENT_DUMMIES)


def _vector(row: dict[str, Any], family: str) -> list[float]:
    features = row["p2_features"]
    values = [float(features[name]) for name in FEATURE_FAMILIES[family]]
    values.extend(1.0 if row["instrument"] == code else 0.0 for code in INSTRUMENT_DUMMIES)
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"non-finite P2 vector for {family}")
    return values


def _matrix(rows: list[dict[str, Any]], family: str) -> np.ndarray:
    return np.asarray([_vector(row, family) for row in rows], dtype=float)


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
    assert min(float(row["captured_ts"]) for row in validation) >= validation_start
    return train, validation


def _logit(p: float) -> float:
    p = _clip_probability(p)
    return math.log(p / (1.0 - p))


def _fit_weighted_gbt(x: np.ndarray, y: np.ndarray, weights: np.ndarray) -> dict[str, Any]:
    base_rate = _clip_probability(_weighted_mean(y, weights))
    score = np.full(len(y), _logit(base_rate), dtype=float)
    stumps: list[dict[str, Any]] = []
    for _ in range(GBT_ESTIMATORS):
        p = 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))
        residual = y - p
        best = None
        for feature_index in range(x.shape[1]):
            col = x[:, feature_index]
            thresholds = sorted(set(float(np.quantile(col, q)) for q in GBT_QUANTILES))
            for threshold in thresholds:
                left = col <= threshold; right = ~left
                if not left.any() or not right.any():
                    continue
                lw = weights[left]; rw = weights[right]
                if float(lw.sum()) <= 0 or float(rw.sum()) <= 0:
                    continue
                left_value = _weighted_mean(residual[left], lw)
                right_value = _weighted_mean(residual[right], rw)
                prediction = np.where(left, left_value, right_value)
                loss = float(np.sum(weights * (residual - prediction) ** 2))
                candidate = (loss, feature_index, threshold, left_value, right_value)
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
        if best is None:
            break
        _loss, feature_index, threshold, left_value, right_value = best
        score += GBT_LEARNING_RATE * np.where(
            x[:, feature_index] <= threshold, left_value, right_value)
        stumps.append({
            "feature_index": int(feature_index), "threshold": float(threshold),
            "left_value": float(left_value), "right_value": float(right_value),
        })
    return {
        "model_family": GBT_MODEL,
        "base_logit": _logit(base_rate), "base_rate": base_rate,
        "learning_rate": GBT_LEARNING_RATE, "stumps": stumps,
        "n_estimators_requested": GBT_ESTIMATORS,
        "threshold_quantiles": list(GBT_QUANTILES),
        "dependency_weighted": True, "hyperparameter_search": False,
    }


def _predict_weighted_gbt(x: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    score = np.full(len(x), float(params["base_logit"]), dtype=float)
    for stump in params.get("stumps") or []:
        index = int(stump["feature_index"]); threshold = float(stump["threshold"])
        score += float(params["learning_rate"]) * np.where(
            x[:, index] <= threshold,
            float(stump["left_value"]), float(stump["right_value"]))
    return 1.0 / (1.0 + np.exp(-np.clip(score, -35.0, 35.0)))


def _direction_baselines(train: list[dict], test: list[dict]) -> dict[str, np.ndarray]:
    train_y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    train_weights, _ = _weights(train)
    base = _clip_probability(_weighted_mean(train_y, train_weights))
    p5_neg, p5_pos = _conditional_probability(train, train_y, train_weights, "ret_5m")
    p15_neg, p15_pos = _conditional_probability(train, train_y, train_weights, "ret_15m")
    ret5 = np.asarray([float(row["features"]["ret_5m"]) for row in test])
    ret15 = np.asarray([float(row["features"]["ret_15m"]) for row in test])
    return {
        "constant_0_5": np.full(len(test), 0.5),
        "causal_base_rate": np.full(len(test), base),
        "ret5_persistence": np.where(ret5 > 0, p5_pos, p5_neg),
        "ret15_momentum": np.where(ret15 > 0, p15_pos, p15_neg),
    }


def _return_baselines(train: list[dict], test: list[dict]) -> dict[str, np.ndarray]:
    train_y = np.asarray([float(row["terminal_log_return"]) for row in train])
    weights, _ = _weights(train)
    historical_mean = _weighted_mean(train_y, weights)
    return {
        "zero_return": np.zeros(len(test)),
        "causal_historical_mean": np.full(len(test), historical_mean),
        "ret5_persistence": np.asarray([float(row["features"]["ret_5m"]) for row in test]),
        "ret15_momentum": np.asarray([float(row["features"]["ret_15m"]) for row in test]),
    }


def _best_metric(metrics: dict[str, dict], metric: str) -> tuple[str, float]:
    values = [(name, values.get(metric)) for name, values in metrics.items()
              if values.get(metric) is not None]
    if not values:
        raise ValueError(f"no baseline metric {metric}")
    name, value = min(values, key=lambda item: float(item[1]))
    return str(name), float(value)


def _direction_candidate(
    train: list[dict], test: list[dict], family: str, model_family: str,
) -> tuple[np.ndarray, dict[str, Any]]:
    x_train = _matrix(train, family); x_test = _matrix(test, family)
    y_train = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in train])
    weights, effective = _weights(train)
    if model_family == LOGISTIC_MODEL:
        mean, std, beta = _fit_logistic(x_train, y_train, weights)
        prediction = _predict_linear(x_test, mean, std, beta, probability=True)
        params = {
            "feature_mean": mean.tolist(), "feature_std": std.tolist(),
            "intercept_and_coefficients": beta.tolist(),
        }
    elif model_family == GBT_MODEL:
        params = _fit_weighted_gbt(x_train, y_train, weights)
        prediction = _predict_weighted_gbt(x_test, params)
    else:
        raise ValueError(model_family)
    return prediction, {
        "feature_family": family, "model_family": model_family,
        "feature_names": list(_feature_names(family)),
        "train_raw_n": len(train), "train_effective_n": effective,
        "parameters": params,
    }


def _learn_zero_alpha(y: np.ndarray, raw_prediction: np.ndarray,
                      weights: np.ndarray) -> float:
    denominator = float(np.sum(weights * raw_prediction * raw_prediction))
    if denominator <= 1e-18:
        return 0.0
    numerator = float(np.sum(weights * raw_prediction * y))
    return max(0.0, min(1.0, numerator / denominator))


def _return_candidate(
    train: list[dict], test: list[dict], family: str, *, alpha: float | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    x_train = _matrix(train, family); x_test = _matrix(test, family)
    y_train = np.asarray([float(row["terminal_log_return"]) for row in train])
    weights, effective = _weights(train)
    mean, std, beta = _fit_ridge(x_train, y_train, weights)
    raw = _predict_linear(x_test, mean, std, beta, probability=False)
    applied_alpha = 1.0 if alpha is None else float(alpha)
    return raw * applied_alpha, {
        "feature_family": family, "model_family": ZERO_SHRUNK_RIDGE_MODEL,
        "feature_names": list(_feature_names(family)),
        "train_raw_n": len(train), "train_effective_n": effective,
        "zero_anchor_alpha": applied_alpha,
        "parameters": {
            "feature_mean": mean.tolist(), "feature_std": std.tolist(),
            "intercept_and_coefficients": beta.tolist(),
        },
    }


def _select_direction(train: list[dict], horizon: int) -> dict[str, Any]:
    inner_train, inner_validation = _inner_split(train, horizon)
    if not inner_train or not inner_validation:
        raise RuntimeError("insufficient inner direction split")
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0
                    for row in inner_validation])
    weights, _ = _weights(inner_validation)
    baseline_predictions = _direction_baselines(inner_train, inner_validation)
    baseline_metrics = {name: _prob_metrics(y, pred, weights)
                        for name, pred in baseline_predictions.items()}
    _brier_name, best_brier = _best_metric(baseline_metrics, "brier")
    _log_name, best_log = _best_metric(baseline_metrics, "logloss")
    candidates = []
    for family in FEATURE_FAMILIES:
        for model_family in (LOGISTIC_MODEL, GBT_MODEL):
            prediction, artifact = _direction_candidate(
                inner_train, inner_validation, family, model_family)
            metrics = _prob_metrics(y, prediction, weights)
            score = float(metrics["brier"]) / best_brier + float(metrics["logloss"]) / best_log
            candidates.append({
                "feature_family": family, "model_family": model_family,
                "score": score, "metrics": metrics, "artifact": artifact,
            })
    selected = min(candidates, key=lambda row: (
        float(row["score"]), str(row["feature_family"]), str(row["model_family"])))
    return {
        "selection_method": INNER_SELECTION_METHOD,
        "inner_train_raw_n": len(inner_train),
        "inner_validation_raw_n": len(inner_validation),
        "baseline_metrics": baseline_metrics,
        "candidates": [{k: v for k, v in row.items() if k != "artifact"} for row in candidates],
        "selected_feature_family": selected["feature_family"],
        "selected_model_family": selected["model_family"],
        "selected_score": selected["score"],
    }


def _select_return(train: list[dict], horizon: int) -> dict[str, Any]:
    inner_train, inner_validation = _inner_split(train, horizon)
    if not inner_train or not inner_validation:
        raise RuntimeError("insufficient inner return split")
    y = np.asarray([float(row["terminal_log_return"]) for row in inner_validation])
    weights, _ = _weights(inner_validation)
    baseline_predictions = _return_baselines(inner_train, inner_validation)
    baseline_metrics = {name: _return_metrics(y, pred, weights)
                        for name, pred in baseline_predictions.items()}
    _mae_name, best_mae = _best_metric(baseline_metrics, "mae")
    _rmse_name, best_rmse = _best_metric(baseline_metrics, "rmse")
    candidates = []
    for family in FEATURE_FAMILIES:
        raw_prediction, _artifact = _return_candidate(inner_train, inner_validation, family)
        alpha = _learn_zero_alpha(y, raw_prediction, weights)
        prediction = raw_prediction * alpha
        metrics = _return_metrics(y, prediction, weights)
        score = float(metrics["mae"]) / best_mae + float(metrics["rmse"]) / best_rmse
        candidates.append({
            "feature_family": family, "model_family": ZERO_SHRUNK_RIDGE_MODEL,
            "zero_anchor_alpha": alpha, "score": score, "metrics": metrics,
        })
    selected = min(candidates, key=lambda row: (float(row["score"]), str(row["feature_family"])))
    return {
        "selection_method": INNER_SELECTION_METHOD,
        "inner_train_raw_n": len(inner_train),
        "inner_validation_raw_n": len(inner_validation),
        "baseline_metrics": baseline_metrics,
        "candidates": candidates,
        "selected_feature_family": selected["feature_family"],
        "selected_model_family": ZERO_SHRUNK_RIDGE_MODEL,
        "selected_zero_anchor_alpha": selected["zero_anchor_alpha"],
        "selected_score": selected["score"],
    }


def _evaluate_direction(rows: list[dict], horizon: int) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    all_y: list[float] = []; all_p: list[float] = []; all_w: list[float] = []
    baseline_values: dict[str, list[float]] = defaultdict(list)
    reports = []
    selection_counts = Counter()
    joint_non_degrade = 0
    for fold in folds:
        train, test = fold["train"], fold["test"]
        selection = _select_direction(train, horizon)
        family = str(selection["selected_feature_family"])
        model_family = str(selection["selected_model_family"])
        prediction, artifact = _direction_candidate(train, test, family, model_family)
        y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in test])
        weights, test_effective = _weights(test)
        baselines = _direction_baselines(train, test)
        model_metrics = _prob_metrics(y, prediction, weights)
        baseline_metrics = {name: _prob_metrics(y, pred, weights) for name, pred in baselines.items()}
        _brier_name, best_brier = _best_metric(baseline_metrics, "brier")
        _log_name, best_log = _best_metric(baseline_metrics, "logloss")
        joint = float(model_metrics["brier"]) <= best_brier and float(model_metrics["logloss"]) <= best_log
        joint_non_degrade += int(joint)
        selection_counts[(family, model_family)] += 1
        reports.append({
            "fold_index": fold["fold_index"], "train_raw_n": len(train),
            "test_raw_n": len(test), "test_effective_n": test_effective,
            "test_start_ts": fold["test_start_ts"], "test_end_ts": fold["test_end_ts"],
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "selection": selection, "selected_artifact_contract": {
                "feature_family": artifact["feature_family"],
                "model_family": artifact["model_family"],
                "feature_names": artifact["feature_names"],
            },
            "model": model_metrics, "baselines": baseline_metrics,
            "joint_non_degrade": joint,
        })
        all_y.extend(y.tolist()); all_p.extend(prediction.tolist()); all_w.extend(weights.tolist())
        for name, pred in baselines.items():
            baseline_values[name].extend(pred.tolist())
    if not all_y:
        return {"fold_count": 0, "folds": reports}
    y = np.asarray(all_y); p = np.asarray(all_p); weights = np.asarray(all_w)
    model = _prob_metrics(y, p, weights)
    baselines = {name: _prob_metrics(y, np.asarray(pred), weights)
                 for name, pred in baseline_values.items()}
    return {
        "fold_count": len(reports), "folds": reports, "model": model, "baselines": baselines,
        "fold_joint_non_degrade_n": joint_non_degrade,
        "selection_counts": {f"{family}|{model}": count
                             for (family, model), count in sorted(selection_counts.items())},
    }


def _evaluate_return(rows: list[dict], horizon: int) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    all_y: list[float] = []; all_p: list[float] = []; all_w: list[float] = []
    baseline_values: dict[str, list[float]] = defaultdict(list)
    reports = []
    selection_counts = Counter()
    joint_non_degrade = 0
    for fold in folds:
        train, test = fold["train"], fold["test"]
        selection = _select_return(train, horizon)
        family = str(selection["selected_feature_family"])
        alpha = float(selection["selected_zero_anchor_alpha"])
        prediction, artifact = _return_candidate(train, test, family, alpha=alpha)
        y = np.asarray([float(row["terminal_log_return"]) for row in test])
        weights, test_effective = _weights(test)
        baselines = _return_baselines(train, test)
        model_metrics = _return_metrics(y, prediction, weights)
        baseline_metrics = {name: _return_metrics(y, pred, weights) for name, pred in baselines.items()}
        _mae_name, best_mae = _best_metric(baseline_metrics, "mae")
        _rmse_name, best_rmse = _best_metric(baseline_metrics, "rmse")
        joint = float(model_metrics["mae"]) <= best_mae and float(model_metrics["rmse"]) <= best_rmse
        joint_non_degrade += int(joint)
        selection_counts[(family, f"alpha={alpha:.4f}")] += 1
        reports.append({
            "fold_index": fold["fold_index"], "train_raw_n": len(train),
            "test_raw_n": len(test), "test_effective_n": test_effective,
            "test_start_ts": fold["test_start_ts"], "test_end_ts": fold["test_end_ts"],
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "selection": selection, "selected_artifact_contract": {
                "feature_family": artifact["feature_family"],
                "model_family": artifact["model_family"],
                "feature_names": artifact["feature_names"],
                "zero_anchor_alpha": alpha,
            },
            "model": model_metrics, "baselines": baseline_metrics,
            "joint_non_degrade": joint,
        })
        all_y.extend(y.tolist()); all_p.extend(prediction.tolist()); all_w.extend(weights.tolist())
        for name, pred in baselines.items():
            baseline_values[name].extend(pred.tolist())
    if not all_y:
        return {"fold_count": 0, "folds": reports}
    y = np.asarray(all_y); p = np.asarray(all_p); weights = np.asarray(all_w)
    model = _return_metrics(y, p, weights)
    baselines = {name: _return_metrics(y, np.asarray(pred), weights)
                 for name, pred in baseline_values.items()}
    return {
        "fold_count": len(reports), "folds": reports, "model": model, "baselines": baselines,
        "fold_joint_non_degrade_n": joint_non_degrade,
        "selection_counts": {f"{family}|{alpha}": count
                             for (family, alpha), count in sorted(selection_counts.items())},
    }


def _winner_gate(target: str, evaluation: dict[str, Any], raw_n: int,
                 effective_n: int) -> dict[str, Any]:
    model = evaluation.get("model") or {}; baselines = evaluation.get("baselines") or {}
    if target == DIRECTION_TARGET:
        first, second = "brier", "logloss"
    else:
        first, second = "mae", "rmse"
    first_name, first_best = _best_metric(baselines, first)
    second_name, second_best = _best_metric(baselines, second)
    first_model = float(model[first]); second_model = float(model[second])
    required = MIN_PROVISIONAL_RELATIVE_IMPROVEMENT
    first_improvement = (first_best - first_model) / first_best if first_best > 0 else 0.0
    second_improvement = (second_best - second_model) / second_best if second_best > 0 else 0.0
    sample_gate = raw_n >= MIN_HISTORICAL_RAW and effective_n >= MIN_HISTORICAL_EFFECTIVE
    fold_gate = int(evaluation.get("fold_count") or 0) == OUTER_FOLD_COUNT
    robustness_gate = int(evaluation.get("fold_joint_non_degrade_n") or 0) >= FOLD_JOINT_NON_DEGRADE_REQUIRED
    metric_gate = first_improvement >= required and second_improvement >= required
    winner = bool(sample_gate and fold_gate and robustness_gate and metric_gate)
    return {
        "historical_winner": winner,
        "required_relative_improvement": required,
        "first_metric": first, "first_model": first_model,
        "first_best_baseline": first_name, "first_best": first_best,
        "first_relative_improvement": first_improvement,
        "second_metric": second, "second_model": second_model,
        "second_best_baseline": second_name, "second_best": second_best,
        "second_relative_improvement": second_improvement,
        "sample_gate": sample_gate, "fold_gate": fold_gate,
        "fold_joint_non_degrade_required": FOLD_JOINT_NON_DEGRADE_REQUIRED,
        "fold_joint_non_degrade_observed": int(evaluation.get("fold_joint_non_degrade_n") or 0),
        "robustness_gate": robustness_gate, "metric_gate": metric_gate,
    }


def _current_sources(runtime) -> tuple[str, list[dict[str, Any]]]:
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
    return source_set, sources


def _ensure_research_tables(runtime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_historical_p2_runs(
                run_id TEXT PRIMARY KEY,
                contract_version TEXT NOT NULL,
                evidence_label TEXT NOT NULL,
                source_set_sha256 TEXT NOT NULL,
                target TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                historical_winner INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                artifact_json TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                live_parity_ready INTEGER NOT NULL DEFAULT 0,
                production_authority INTEGER NOT NULL DEFAULT 0,
                auto_promotion INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                UNIQUE(contract_version,source_set_sha256,target,horizon_minutes)
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_historical_p2_state(
                id INTEGER PRIMARY KEY CHECK(id=1),
                contract_version TEXT NOT NULL,
                state TEXT NOT NULL,
                source_set_sha256 TEXT,
                run_count INTEGER NOT NULL DEFAULT 0,
                winner_count INTEGER NOT NULL DEFAULT 0,
                last_started_ts REAL,
                last_success_ts REAL,
                last_error TEXT,
                updated_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_historical_p2_state(id,contract_version,state,updated_ts) "
            "VALUES(1,?,'PENDING',?)", (P2_CONTRACT_VERSION, time.time()))
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1s_historical_p2_runs_immutable_update
            BEFORE UPDATE ON g1s_historical_p2_runs
            BEGIN SELECT RAISE(ABORT,'immutable P2 historical evidence row'); END""")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1s_historical_p2_runs_immutable_delete
            BEFORE DELETE ON g1s_historical_p2_runs
            BEGIN SELECT RAISE(ABORT,'immutable P2 historical evidence row'); END""")


def _set_p2_state(runtime, **updates: Any) -> None:
    updates = dict(updates); updates["updated_ts"] = time.time()
    assignments = ",".join(f"{key}=?" for key in updates)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            f"UPDATE g1s_historical_p2_state SET {assignments} WHERE id=1",
            tuple(updates.values()))


def run_p2_nested_research(runtime, *, force: bool = False) -> dict[str, Any]:
    """Run the predeclared nested P2 experiment on the current P1B source set."""
    _ensure_research_tables(runtime)
    source_set, sources = _current_sources(runtime)
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT * FROM g1s_historical_p2_state WHERE id=1").fetchone()
    if (not force and state is not None and str(state["state"]) == "COMPLETE"
            and str(state["source_set_sha256"] or "") == source_set
            and int(state["run_count"] or 0) == 2 * len((15, 30, 60, 120, 240))):
        return {
            "refreshed": False, "reason": "ALREADY_MATERIALIZED",
            "contract_version": P2_CONTRACT_VERSION,
            "source_set_sha256": source_set,
            "run_count": int(state["run_count"] or 0),
            "winner_count": int(state["winner_count"] or 0),
        }
    started = time.time()
    _set_p2_state(runtime, contract_version=P2_CONTRACT_VERSION, state="RUNNING",
                  source_set_sha256=source_set, run_count=0, winner_count=0,
                  last_started_ts=started, last_error=None)
    results = []
    try:
        contexts = _build_contexts(sources)
        for horizon in (15, 30, 60, 120, 240):
            rows = _p2_rows(sources, horizon, contexts)
            for target in (DIRECTION_TARGET, RETURN_TARGET):
                target_rows = ([row for row in rows if row["direction_label"] != "FLAT"]
                               if target == DIRECTION_TARGET else rows)
                _weights_array, effective = _weights(target_rows)
                evaluation = (_evaluate_direction(target_rows, horizon)
                              if target == DIRECTION_TARGET else _evaluate_return(target_rows, horizon))
                gate = _winner_gate(target, evaluation, len(target_rows), effective)
                winner = bool(gate["historical_winner"])
                verdict = ("HISTORICAL_WINNER_PENDING_LIVE_PARITY"
                           if winner else "P2_BASELINE_NOT_BEATEN")
                artifact = {
                    "contract_version": P2_CONTRACT_VERSION,
                    "feature_contract_version": P2_FEATURE_CONTRACT,
                    "evidence_label": P2_EVIDENCE_LABEL,
                    "live_validation_label": P2_LIVE_LABEL,
                    "source_set_sha256": source_set,
                    "target": target, "horizon_minutes": horizon,
                    "feature_families": {name: list(features)
                                         for name, features in FEATURE_FAMILIES.items()},
                    "direction_candidate_models": [LOGISTIC_MODEL, GBT_MODEL],
                    "return_candidate_model": ZERO_SHRUNK_RIDGE_MODEL,
                    "nested_selection": {
                        "outer_folds": OUTER_FOLD_COUNT,
                        "inner_method": INNER_SELECTION_METHOD,
                        "outer_test_used_for_selection": False,
                        "purge_target_overlap": True, "embargo_seconds": EMBARGO_SECONDS,
                        "dependency_group_total_weight_one": True,
                    },
                    "feature_semantics": {
                        "completed_5m_bars_only": True,
                        "cross_asset_exact_same_t0_only": True,
                        "stale_peer_carry_forward": False,
                        "historical_option_features_used": False,
                        "synthetic_option_history": False,
                        "session_time_features_utc_only": True,
                        "causal_rolling_rank_uses_only_prior_context": True,
                    },
                    "sample": {"raw_n": len(target_rows), "effective_n": effective},
                    "evaluation": evaluation, "selection_gate": gate,
                    "verdict": verdict,
                    "live_parity_ready": False,
                    "live_cohort_created": False,
                    "production_authority": False, "auto_promotion": False,
                }
                raw = _json(artifact); digest = _sha(raw)
                run_id = "g1s-p2-" + hashlib.sha256(
                    f"{P2_CONTRACT_VERSION}|{source_set}|{target}|{horizon}".encode()).hexdigest()[:28]
                with runtime._lock, runtime._conn:
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_historical_p2_runs("
                        "run_id,contract_version,evidence_label,source_set_sha256,target,"
                        "horizon_minutes,historical_winner,verdict,artifact_json,artifact_sha256,"
                        "live_parity_ready,production_authority,auto_promotion,created_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,0,0,0,?)",
                        (run_id, P2_CONTRACT_VERSION, P2_EVIDENCE_LABEL, source_set, target,
                         horizon, int(winner), verdict, raw, digest, time.time()))
                results.append({
                    "run_id": run_id, "target": target, "horizon_minutes": horizon,
                    "historical_winner": winner, "verdict": verdict,
                    "raw_n": len(target_rows), "effective_n": effective,
                    "first_relative_improvement": gate["first_relative_improvement"],
                    "second_relative_improvement": gate["second_relative_improvement"],
                    "fold_joint_non_degrade_n": gate["fold_joint_non_degrade_observed"],
                    "selection_counts": evaluation.get("selection_counts") or {},
                })
                winner_count = sum(bool(row["historical_winner"]) for row in results)
                _set_p2_state(runtime, run_count=len(results), winner_count=winner_count)
        winner_count = sum(bool(row["historical_winner"]) for row in results)
        _set_p2_state(runtime, state="COMPLETE", source_set_sha256=source_set,
                      run_count=len(results), winner_count=winner_count,
                      last_success_ts=time.time(), last_error=None)
        return {
            "refreshed": True, "contract_version": P2_CONTRACT_VERSION,
            "source_set_sha256": source_set, "run_count": len(results),
            "winner_count": winner_count, "results": results,
            "duration_ms": (time.time() - started) * 1000.0,
            "production_authority": False, "auto_promotion": False,
        }
    except Exception as exc:
        _set_p2_state(runtime, state="ERROR",
                      last_error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise


def p2_status(runtime) -> dict[str, Any]:
    _ensure_research_tables(runtime)
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT * FROM g1s_historical_p2_state WHERE id=1").fetchone()
        runs = runtime._conn.execute(
            "SELECT run_id,target,horizon_minutes,historical_winner,verdict,"
            "live_parity_ready,production_authority,auto_promotion,created_ts "
            "FROM g1s_historical_p2_runs WHERE contract_version=? "
            "ORDER BY target,horizon_minutes", (P2_CONTRACT_VERSION,)).fetchall()
    return {
        "contract_version": P2_CONTRACT_VERSION,
        "feature_contract_version": P2_FEATURE_CONTRACT,
        "evidence_label": P2_EVIDENCE_LABEL,
        "state": (state["state"] if state else "PENDING"),
        "source_set_sha256": (state["source_set_sha256"] if state else None),
        "run_count": int(state["run_count"] or 0) if state else 0,
        "winner_count": int(state["winner_count"] or 0) if state else 0,
        "last_error": state["last_error"] if state else None,
        "runs": [dict(row) for row in runs],
        "feature_families": {name: list(features) for name, features in FEATURE_FAMILIES.items()},
        "outer_test_used_for_selection": False,
        "historical_options_used": False,
        "live_parity_ready": False,
        "auto_promotion": False, "production_authority": False,
    }
