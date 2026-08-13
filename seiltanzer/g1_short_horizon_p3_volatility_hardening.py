"""P3B: harden the historical future-volatility edge against strong baselines.

P3 found an all-fold historical edge for future 5m realized volatility.  Before
building live parity, this module asks a stricter question on the *same target*:
does the frozen P3 model still beat volatility-specialist baselines?

Additional causal state, all known by T0:
- 240m realized volatility from completed 5m returns;
- fixed-lambda EWMA volatility over the same trailing 240m window.

Additional baselines, fit on outer-train only:
- raw RV240 persistence;
- raw EWMA persistence;
- train-only scalar-adjusted EWMA;
- a predeclared HAR-like log-volatility ridge using RV15/RV60/RV240/EWMA and
  instrument dummies.

The original P3 model/feature vector is unchanged.  No hyperparameter/model
selection uses outer test. Instrument results are descriptive heterogeneity only
and cannot create a winner by post-hoc filtering.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any

import numpy as np

from .config import INSTRUMENTS
from .g1_short_horizon_historical_wf import _anchor_index, _historical_folds, _weights
from . import g1_short_horizon_p3_path_geometry as _p3
from .g1_short_horizon_p3_fast import _precompute_sources, build_rows_fast


P3B_CONTRACT_VERSION = "g1s-p3b-volatility-hardening-v1"
P3B_HAR_FAMILY = "HAR_5M_LOG_VOL_RIDGE_V1"
P3B_HAR_L2 = 4.0
EWMA_LAMBDA = 0.94
TRAILING_WINDOW_MINUTES = 240
INSTRUMENT_DUMMIES = tuple(INSTRUMENTS)[1:]
HAR_FEATURE_NAMES = (
    "log_rv15_5m", "log_rv60_5m", "log_rv240_5m", "log_ewma240_5m",
) + tuple(f"instrument:{code}" for code in INSTRUMENT_DUMMIES)


def _ewma_volatility(returns: np.ndarray, lam: float = EWMA_LAMBDA) -> float:
    values = np.asarray(returns, dtype=float)
    if len(values) < 2:
        return 0.0
    # Most recent return receives the largest weight. Normalization makes the
    # finite-window estimate comparable across the exact number of available bars.
    powers = np.arange(len(values)-1, -1, -1, dtype=float)
    weights = (1.0-float(lam)) * np.power(float(lam), powers)
    total = float(weights.sum())
    if total <= 0:
        return 0.0
    variance = float(np.sum(weights * values * values) / total)
    return math.sqrt(max(0.0, variance))


def _hardening_context(source: dict[str, Any]) -> dict[float, dict[str, float]]:
    bars = source["bars"]
    times = [float(bar["bar_end_ts"]) for bar in bars]
    closes = np.asarray([float(bar["close"]) for bar in bars], dtype=float)
    steps = np.zeros(len(bars), dtype=float)
    steps[1:] = np.log(closes[1:] / closes[:-1])
    result: dict[float, dict[str, float]] = {}
    for index in range(48, len(bars)):
        i240 = _anchor_index(times, index, TRAILING_WINDOW_MINUTES*60.0)
        if i240 is None or index-i240 < 47:
            continue
        trailing = steps[i240+1:index+1]
        if len(trailing) < 47:
            continue
        rv240 = float(np.std(trailing, ddof=0))
        ewma = _ewma_volatility(trailing)
        result[float(times[index])] = {
            "current_realized_volatility_5m_240m": rv240,
            "current_ewma_volatility_5m_240m": ewma,
        }
    return result


def _enriched_precompute(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    precomputed = _precompute_sources(sources)
    for instrument, item in precomputed.items():
        hard = _hardening_context(item["source"])
        base = item["contexts"]
        # Require true 240m pre-T0 history. We do not fill session-open gaps or
        # stale values from a previous session.
        item["contexts"] = {
            ts: {**context, **hard[ts]}
            for ts, context in base.items() if ts in hard
        }
    return precomputed


def _har_vector(row: dict[str, Any]) -> list[float]:
    values = [
        math.log(float(row["current_realized_volatility_5m_15m"])+_p3.EPS),
        math.log(float(row["current_realized_volatility_5m_60m"])+_p3.EPS),
        math.log(float(row["current_realized_volatility_5m_240m"])+_p3.EPS),
        math.log(float(row["current_ewma_volatility_5m_240m"])+_p3.EPS),
    ]
    values.extend(1.0 if row["instrument"] == code else 0.0 for code in INSTRUMENT_DUMMIES)
    return values


def _har_matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([_har_vector(row) for row in rows], dtype=float)


def _fit_har(train: list[dict[str, Any]]) -> dict[str, Any]:
    x = _har_matrix(train)
    y = np.log(np.asarray([float(row[_p3.TARGET_FUTURE_RV]) for row in train])+_p3.EPS)
    weights, effective = _weights(train)
    mean, std, beta = _p3._fit_ridge(x, y, weights, l2=P3B_HAR_L2)
    return {
        "model_family": P3B_HAR_FAMILY,
        "feature_names": list(HAR_FEATURE_NAMES),
        "l2": P3B_HAR_L2,
        "feature_mean": mean.tolist(), "feature_std": std.tolist(),
        "intercept_and_coefficients": beta.tolist(),
        "train_raw_n": len(train), "train_effective_n": effective,
    }


def _predict_har(rows: list[dict[str, Any]], artifact: dict[str, Any]) -> np.ndarray:
    x = _har_matrix(rows)
    mean = np.asarray(artifact["feature_mean"], dtype=float)
    std = np.asarray(artifact["feature_std"], dtype=float)
    beta = np.asarray(artifact["intercept_and_coefficients"], dtype=float)
    log_prediction = np.clip(_p3._predict_ridge(x, mean, std, beta), -20.0, 2.0)
    return np.maximum(0.0, np.exp(log_prediction)-_p3.EPS)


def _fit_scalar(train: list[dict[str, Any]], field: str) -> float:
    scale = np.asarray([max(_p3.EPS, float(row[field])) for row in train], dtype=float)
    y = np.asarray([float(row[_p3.TARGET_FUTURE_RV]) for row in train], dtype=float)
    weights, _ = _weights(train)
    den = float(np.sum(weights*scale*scale))
    if den <= _p3.EPS:
        return 1.0
    value = float(np.sum(weights*scale*y)/den)
    return max(0.0, min(8.0, value))


def _strong_baselines(train: list[dict[str, Any]], test: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    baselines = dict(_p3._baselines(train, test, _p3.TARGET_FUTURE_RV))
    baselines["current_rv240_persistence"] = np.asarray([
        float(row["current_realized_volatility_5m_240m"]) for row in test], dtype=float)
    baselines["ewma240_persistence"] = np.asarray([
        float(row["current_ewma_volatility_5m_240m"]) for row in test], dtype=float)
    ewma_factor = _fit_scalar(train, "current_ewma_volatility_5m_240m")
    baselines["causal_scaled_ewma240"] = ewma_factor * baselines["ewma240_persistence"]
    har = _fit_har(train)
    baselines["har_5m_log_vol_ridge"] = _predict_har(test, har)
    return baselines


def _instrument_metrics(test: list[dict[str, Any]], prediction: np.ndarray,
                        baselines: dict[str, np.ndarray], weights: np.ndarray) -> list[dict[str, Any]]:
    out = []
    instruments = sorted({str(row["instrument"]) for row in test})
    for instrument in instruments:
        mask = np.asarray([str(row["instrument"]) == instrument for row in test], dtype=bool)
        if not np.any(mask):
            continue
        y = np.asarray([float(row[_p3.TARGET_FUTURE_RV]) for row in test], dtype=float)[mask]
        w = weights[mask]
        model = _p3._metrics(y, prediction[mask], w)
        base_metrics = {name: _p3._metrics(y, values[mask], w)
                        for name, values in baselines.items()}
        mae_name, best_mae = _p3._best(base_metrics, "mae")
        rmse_name, best_rmse = _p3._best(base_metrics, "rmse")
        out.append({
            "instrument": instrument, "n": int(mask.sum()),
            "model_mae": model["mae"], "best_mae_baseline": mae_name,
            "mae_relative_improvement": ((best_mae-model["mae"])/best_mae if best_mae>_p3.EPS else 0.0),
            "model_rmse": model["rmse"], "best_rmse_baseline": rmse_name,
            "rmse_relative_improvement": ((best_rmse-model["rmse"])/best_rmse if best_rmse>_p3.EPS else 0.0),
        })
    return out


def evaluate_hardened(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    all_y: list[float] = []; all_pred: list[float] = []; all_w: list[float] = []
    baseline_values: dict[str, list[float]] = defaultdict(list)
    fold_reports = []
    instrument_accumulator: dict[str, dict[str, list]] = defaultdict(
        lambda: {"y": [], "pred": [], "w": [], "baselines": defaultdict(list)})
    joint_non_degrade = 0

    for fold in folds:
        train, test = fold["train"], fold["test"]
        artifact = _p3._fit_model(train, _p3.TARGET_FUTURE_RV)
        prediction = _p3._predict_model(test, artifact)
        y = np.asarray([float(row[_p3.TARGET_FUTURE_RV]) for row in test], dtype=float)
        weights, effective = _weights(test)
        baselines = _strong_baselines(train, test)
        model_metrics = _p3._metrics(y, prediction, weights)
        baseline_metrics = {name: _p3._metrics(y, values, weights)
                            for name, values in baselines.items()}
        _mae_name, best_mae = _p3._best(baseline_metrics, "mae")
        _rmse_name, best_rmse = _p3._best(baseline_metrics, "rmse")
        joint = model_metrics["mae"] <= best_mae and model_metrics["rmse"] <= best_rmse
        joint_non_degrade += int(joint)
        fold_reports.append({
            "fold_index": fold["fold_index"], "train_raw_n": len(train),
            "test_raw_n": len(test), "test_effective_n": effective,
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "model": model_metrics, "baselines": baseline_metrics,
            "joint_non_degrade": joint,
        })
        all_y.extend(y.tolist()); all_pred.extend(prediction.tolist()); all_w.extend(weights.tolist())
        for name, values in baselines.items():
            baseline_values[name].extend(values.tolist())
        for index, row in enumerate(test):
            bucket = instrument_accumulator[str(row["instrument"])]
            bucket["y"].append(float(y[index])); bucket["pred"].append(float(prediction[index])); bucket["w"].append(float(weights[index]))
            for name, values in baselines.items():
                bucket["baselines"][name].append(float(values[index]))

    y = np.asarray(all_y); prediction = np.asarray(all_pred); weights = np.asarray(all_w)
    model = _p3._metrics(y, prediction, weights)
    baselines = {name: _p3._metrics(y, np.asarray(values), weights)
                 for name, values in baseline_values.items()}
    instruments = []
    for instrument, bucket in sorted(instrument_accumulator.items()):
        iy = np.asarray(bucket["y"]); ip = np.asarray(bucket["pred"]); iw = np.asarray(bucket["w"])
        model_i = _p3._metrics(iy, ip, iw)
        base_i = {name: _p3._metrics(iy, np.asarray(values), iw)
                  for name, values in bucket["baselines"].items()}
        mae_name, best_mae = _p3._best(base_i, "mae")
        rmse_name, best_rmse = _p3._best(base_i, "rmse")
        instruments.append({
            "instrument": instrument, "n": len(iy),
            "best_mae_baseline": mae_name,
            "mae_relative_improvement": ((best_mae-model_i["mae"])/best_mae if best_mae>_p3.EPS else 0.0),
            "best_rmse_baseline": rmse_name,
            "rmse_relative_improvement": ((best_rmse-model_i["rmse"])/best_rmse if best_rmse>_p3.EPS else 0.0),
        })
    return {
        "fold_count": len(fold_reports), "folds": fold_reports,
        "model": model, "baselines": baselines,
        "fold_joint_non_degrade_n": joint_non_degrade,
        "instrument_heterogeneity": instruments,
    }


def run_p3b_volatility_hardening(runtime) -> dict[str, Any]:
    source_set, sources = _p3._current_sources(runtime)
    started = time.time(); precomputed = _enriched_precompute(sources)
    results = []
    for horizon in _p3.HORIZONS:
        rows = build_rows_fast(precomputed, horizon)
        _w, effective = _weights(rows)
        evaluation = evaluate_hardened(rows, horizon)
        gate = _p3.winner_gate(evaluation, len(rows), effective)
        instruments = evaluation["instrument_heterogeneity"]
        results.append({
            "target": _p3.TARGET_FUTURE_RV,
            "horizon_minutes": horizon,
            "raw_n": len(rows), "effective_n": effective,
            "historical_winner": gate["historical_winner"],
            "mae_relative_improvement": gate["mae_relative_improvement"],
            "rmse_relative_improvement": gate["rmse_relative_improvement"],
            "fold_joint_non_degrade_n": gate["fold_joint_non_degrade_observed"],
            "best_mae_baseline": gate["best_mae_baseline"],
            "best_rmse_baseline": gate["best_rmse_baseline"],
            "instrument_positive_both_n": sum(
                row["mae_relative_improvement"] > 0 and row["rmse_relative_improvement"] > 0
                for row in instruments),
            "instrument_count": len(instruments),
            "instrument_heterogeneity": instruments,
            "gate": gate,
        })
    return {
        "contract_version": P3B_CONTRACT_VERSION,
        "parent_contract_version": _p3.P3_CONTRACT_VERSION,
        "source_set_sha256": source_set,
        "target": _p3.TARGET_FUTURE_RV,
        "historical_sampling_interval": "5m",
        "strong_baselines": [
            "zero", "causal_historical_mean", "causal_vol_anchor",
            "current_rv60_persistence", "current_rv15_persistence",
            "current_rv240_persistence", "ewma240_persistence",
            "causal_scaled_ewma240", "har_5m_log_vol_ridge",
        ],
        "har_contract": {"model_family": P3B_HAR_FAMILY, "l2": P3B_HAR_L2,
                         "feature_names": list(HAR_FEATURE_NAMES)},
        "ewma_contract": {"lambda": EWMA_LAMBDA,
                           "trailing_window_minutes": TRAILING_WINDOW_MINUTES},
        "run_count": len(results),
        "winner_count": sum(bool(row["historical_winner"]) for row in results),
        "results": results,
        "instrument_heterogeneity_is_descriptive_only": True,
        "posthoc_instrument_selection_allowed": False,
        "outer_test_used_for_model_selection": False,
        "historical_options_used": False,
        "live_parity_ready": False,
        "auto_promotion": False,
        "production_authority": False,
        "duration_ms": (time.time()-started)*1000.0,
    }
