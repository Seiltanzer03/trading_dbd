"""P3: historical path-geometry research for G.1S management edge.

Direction-only bar models did not beat causal persistence baselines.  P3 tests a
more actionable question: can the terminal estimate *future path geometry* well
enough to improve stops, takes and management timing?

This module is research-only and is intentionally not installed into the runtime
worker.  It consumes the immutable real Yahoo 5m/60d source set created by P1B.

Evidence contract
-----------------
* T0 is a completed 5m bar close.
* Features use completed bars ending <= T0 only.
* Future targets use bars strictly after T0 through the fixed horizon.
* Historical volatility target is explicitly ``future_realized_volatility_5m``.
  It is NOT mislabeled as live ``realized_volatility_1m``.
* MFE uses future bar highs relative to the T0 close; adverse excursion uses
  future bar lows relative to the T0 close, matching live path geometry except
  for the coarser historical 5m sampling frequency.
* Four expanding chronological outer folds use target-overlap purge + embargo.
* Dependency groups keep total weight one.
* Model family is predeclared: a fixed L2 ridge predicts a multiplicative log
  correction around a strong causal anchor.  Zero coefficients recover the
  anchor exactly.
* No option history, no synthetic fills, no live cohort, no authority/promotion.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from typing import Any

import numpy as np

from .config import INSTRUMENTS
from .g1_short_horizon_historical_wf import (
    BAR_SECONDS,
    EMBARGO_SECONDS,
    MIN_HISTORICAL_EFFECTIVE,
    MIN_HISTORICAL_RAW,
    MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
    _anchor_index,
    _historical_folds,
    _json,
    _load_source_bars,
    _target_index,
    _weighted_mean,
    _weights,
)


P3_CONTRACT_VERSION = "g1s-p3-path-geometry-wf-v1"
P3_EVIDENCE_LABEL = "HISTORICAL_WALK_FORWARD_5M_PATH"
P3_MODEL_FAMILY = "CAUSAL_ANCHOR_LOG_RESIDUAL_RIDGE_V1"
P3_FEATURE_CONTRACT = "g1s-p3-pre-t0-bar-state-v1"
P3_L2 = 4.0
P3_OUTER_FOLDS = 4
P3_ROBUST_FOLDS_REQUIRED = 3
EPS = 1e-9

TARGET_FUTURE_RV = "future_realized_volatility_5m"
TARGET_MFE = "mfe_log_return_5m_path"
TARGET_MAE = "mae_magnitude_log_return_5m_path"
TARGETS = (TARGET_FUTURE_RV, TARGET_MFE, TARGET_MAE)
HORIZONS = (15, 30, 60, 120, 240)
INSTRUMENT_DUMMIES = tuple(INSTRUMENTS)[1:]

FEATURE_NAMES = (
    "log_current_rv60_5m",
    "log_current_rv15_5m",
    "ret5_over_rv60",
    "ret15_over_rv60",
    "ret60_over_rv60",
    "rv15_over_rv60",
    "range60_over_rv60",
    "drawup60_over_rv60",
    "drawdown60_over_rv60",
    "trend_agreement_5_15",
    "trend_agreement_15_60",
    "utc_sin",
    "utc_cos",
) + tuple(f"instrument:{code}" for code in INSTRUMENT_DUMMIES)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _safe_ratio(num: float, den: float, clip: float = 12.0) -> float:
    if not math.isfinite(num) or not math.isfinite(den) or abs(den) < EPS:
        return 0.0
    return max(-clip, min(clip, num / den))


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


def _current_sources(runtime) -> tuple[str, list[dict[str, Any]]]:
    """Load exactly the immutable source ids belonging to current P1B set."""
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT state,source_set_sha256 FROM g1s_historical_wf_state WHERE id=1"
        ).fetchone()
    if state is None or str(state["state"]) != "COMPLETE" or not state["source_set_sha256"]:
        raise RuntimeError("P1B historical source set is not COMPLETE")
    source_set = str(state["source_set_sha256"])
    with runtime._lock:
        run = runtime._conn.execute(
            "SELECT artifact_json FROM g1s_historical_wf_runs WHERE source_set_sha256=? "
            "ORDER BY created_ts LIMIT 1", (source_set,)
        ).fetchone()
    if run is None:
        raise RuntimeError("P1B source-set artifact unavailable")
    artifact = json.loads(run["artifact_json"])
    source_ids = [str(row["source_id"]) for row in artifact.get("source_summary") or []]
    if len(source_ids) != len(INSTRUMENTS):
        raise RuntimeError(f"expected {len(INSTRUMENTS)} source ids, got {len(source_ids)}")
    placeholders = ",".join("?" for _ in source_ids)
    with runtime._lock:
        rows = runtime._conn.execute(
            f"SELECT * FROM g1s_historical_sources WHERE source_id IN ({placeholders})",
            tuple(source_ids),
        ).fetchall()
    by_id = {str(row["source_id"]): dict(row) for row in rows}
    sources = []
    for source_id in source_ids:
        item = by_id.get(source_id)
        if item is None:
            raise RuntimeError(f"missing immutable source {source_id}")
        item["bars"] = _load_source_bars(item)
        sources.append(item)
    return source_set, sources


def _pre_t0_context(source: dict[str, Any]) -> dict[float, dict[str, float]]:
    bars = source["bars"]
    times = [float(bar["bar_end_ts"]) for bar in bars]
    closes = np.asarray([float(bar["close"]) for bar in bars], dtype=float)
    highs = np.asarray([float(bar["high"]) for bar in bars], dtype=float)
    lows = np.asarray([float(bar["low"]) for bar in bars], dtype=float)
    steps = np.zeros(len(bars), dtype=float)
    steps[1:] = np.log(closes[1:] / closes[:-1])
    contexts: dict[float, dict[str, float]] = {}
    instrument = str(source["instrument"])

    for index in range(12, len(bars)):
        i5 = _anchor_index(times, index, 5 * 60.0)
        i15 = _anchor_index(times, index, 15 * 60.0)
        i60 = _anchor_index(times, index, 60 * 60.0)
        if None in (i5, i15, i60):
            continue
        assert i5 is not None and i15 is not None and i60 is not None
        if index - i60 < 11:
            continue
        r15 = steps[i15 + 1:index + 1]
        r60 = steps[i60 + 1:index + 1]
        if len(r15) < 2 or len(r60) < 10:
            continue
        current = float(closes[index])
        if current <= 0:
            continue
        rv15 = float(np.std(r15, ddof=0))
        rv60 = float(np.std(r60, ddof=0))
        path_high = float(np.max(highs[i60:index + 1]))
        path_low = float(np.min(lows[i60:index + 1]))
        ret5 = float(math.log(current / closes[i5]))
        ret15 = float(math.log(current / closes[i15]))
        ret60 = float(math.log(current / closes[i60]))
        range60 = math.log(path_high / path_low) if path_high > 0 and path_low > 0 else 0.0
        drawup = math.log(current / path_low) if path_low > 0 else 0.0
        drawdown = max(0.0, -math.log(current / path_high)) if path_high > 0 else 0.0
        captured_ts = float(times[index])
        day_fraction = (captured_ts % 86400.0) / 86400.0
        context = {
            "instrument": instrument,
            "captured_ts": captured_ts,
            "current_close": current,
            "ret_5m": ret5,
            "ret_15m": ret15,
            "ret_60m": ret60,
            "current_realized_volatility_5m_15m": rv15,
            "current_realized_volatility_5m_60m": rv60,
            "range60_log": range60,
            "drawup60_log": drawup,
            "drawdown60_magnitude_log": drawdown,
            "log_current_rv60_5m": math.log(rv60 + EPS),
            "log_current_rv15_5m": math.log(rv15 + EPS),
            "ret5_over_rv60": _safe_ratio(ret5, rv60),
            "ret15_over_rv60": _safe_ratio(ret15, rv60),
            "ret60_over_rv60": _safe_ratio(ret60, rv60),
            "rv15_over_rv60": _safe_ratio(rv15, rv60, 6.0),
            "range60_over_rv60": _safe_ratio(range60, rv60, 20.0),
            "drawup60_over_rv60": _safe_ratio(drawup, rv60, 20.0),
            "drawdown60_over_rv60": _safe_ratio(drawdown, rv60, 20.0),
            "trend_agreement_5_15": _sign(ret5) * _sign(ret15),
            "trend_agreement_15_60": _sign(ret15) * _sign(ret60),
            "utc_sin": math.sin(2.0 * math.pi * day_fraction),
            "utc_cos": math.cos(2.0 * math.pi * day_fraction),
        }
        contexts[captured_ts] = context
    return contexts


def _target_row(source: dict[str, Any], context: dict[str, float],
                horizon_minutes: int) -> dict[str, Any] | None:
    bars = source["bars"]
    times = [float(bar["bar_end_ts"]) for bar in bars]
    captured = float(context["captured_ts"])
    index = bisect_index = None
    # Context timestamps are exact bar-end timestamps; binary search avoids a
    # quadratic list.index pass over ~17k FX bars.
    import bisect
    pos = bisect.bisect_left(times, captured - 1e-6)
    if pos >= len(times) or abs(times[pos] - captured) > 1e-5:
        return None
    index = pos
    target_index = _target_index(times, index, int(horizon_minutes))
    if target_index is None:
        return None
    current = float(context["current_close"])
    future = bars[index + 1:target_index + 1]
    if len(future) < 2 or current <= 0:
        return None
    closes = [current] + [float(bar["close"]) for bar in future]
    if any(price <= 0 for price in closes):
        return None
    future_step_returns = np.diff(np.log(np.asarray(closes, dtype=float)))
    if len(future_step_returns) < 2:
        return None
    future_rv5m = float(np.std(future_step_returns, ddof=0))
    future_high = max([current] + [float(bar["high"]) for bar in future])
    future_low = min([current] + [float(bar["low"]) for bar in future])
    if future_high <= 0 or future_low <= 0:
        return None
    mfe = max(0.0, float(math.log(future_high / current)))
    mae_magnitude = max(0.0, float(-math.log(future_low / current)))
    target_ts = float(times[target_index])
    return {
        **context,
        "target_ts": target_ts,
        "horizon_minutes": int(horizon_minutes),
        "future_steps_5m": len(future_step_returns),
        TARGET_FUTURE_RV: future_rv5m,
        TARGET_MFE: mfe,
        TARGET_MAE: mae_magnitude,
        "path_source": "real_yahoo_5m_ohlc",
        "historical_sampling_interval_sec": int(BAR_SECONDS),
    }


def build_rows(sources: list[dict[str, Any]], horizon_minutes: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        contexts = _pre_t0_context(source)
        for captured_ts in sorted(contexts):
            row = _target_row(source, contexts[captured_ts], horizon_minutes)
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))
    return rows


def _vector(row: dict[str, Any]) -> list[float]:
    values = [float(row[name]) for name in FEATURE_NAMES if not name.startswith("instrument:")]
    values.extend(1.0 if row["instrument"] == code else 0.0 for code in INSTRUMENT_DUMMIES)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("non-finite P3 feature vector")
    return values


def _matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([_vector(row) for row in rows], dtype=float)


def _fit_standardization(x: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    den = max(float(weights.sum()), 1e-12)
    mean = (weights[:, None] * x).sum(axis=0) / den
    var = (weights[:, None] * (x - mean) ** 2).sum(axis=0) / den
    std = np.sqrt(np.maximum(var, 0.0)); std[std < 1e-12] = 1.0
    return mean, std


def _fit_ridge(x: np.ndarray, y: np.ndarray, weights: np.ndarray,
               l2: float = P3_L2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean, std = _fit_standardization(x, weights)
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    regularizer = np.eye(design.shape[1], dtype=float) * float(l2)
    regularizer[0, 0] = float(l2) * 0.25
    weighted = design * weights[:, None]
    beta = np.linalg.pinv(design.T @ weighted + regularizer) @ (design.T @ (weights * y))
    return mean, std, beta


def _predict_ridge(x: np.ndarray, mean: np.ndarray, std: np.ndarray,
                   beta: np.ndarray) -> np.ndarray:
    z = (x - mean) / np.where(std < 1e-12, 1.0, std)
    return beta[0] + z @ beta[1:]


def _anchor_scale(row: dict[str, Any], target: str) -> float:
    rv60 = max(EPS, float(row["current_realized_volatility_5m_60m"]))
    if target == TARGET_FUTURE_RV:
        return rv60
    steps = max(1, int(row["future_steps_5m"]))
    return rv60 * math.sqrt(float(steps))


def _fit_anchor_factor(train: list[dict[str, Any]], target: str) -> float:
    y = np.asarray([float(row[target]) for row in train], dtype=float)
    scale = np.asarray([_anchor_scale(row, target) for row in train], dtype=float)
    weights, _ = _weights(train)
    denominator = float(np.sum(weights * scale * scale))
    if denominator <= EPS:
        return 1.0
    factor = float(np.sum(weights * scale * y) / denominator)
    return max(0.0, min(8.0, factor))


def _anchor_prediction(rows: list[dict[str, Any]], target: str,
                       factor: float) -> np.ndarray:
    return np.asarray([max(0.0, factor * _anchor_scale(row, target)) for row in rows], dtype=float)


def _fit_model(train: list[dict[str, Any]], target: str) -> dict[str, Any]:
    factor = _fit_anchor_factor(train, target)
    anchor = _anchor_prediction(train, target, factor)
    y = np.asarray([float(row[target]) for row in train], dtype=float)
    log_residual = np.log((y + EPS) / (anchor + EPS))
    x = _matrix(train)
    weights, effective = _weights(train)
    mean, std, beta = _fit_ridge(x, log_residual, weights)
    return {
        "contract_version": P3_CONTRACT_VERSION,
        "model_family": P3_MODEL_FAMILY,
        "target": target,
        "feature_names": list(FEATURE_NAMES),
        "l2": P3_L2,
        "anchor_factor": factor,
        "anchor_kind": ("current_rv60_5m" if target == TARGET_FUTURE_RV
                        else "current_rv60_5m_sqrt_future_steps"),
        "feature_mean": mean.tolist(),
        "feature_std": std.tolist(),
        "log_residual_intercept_and_coefficients": beta.tolist(),
        "train_raw_n": len(train),
        "train_effective_n": effective,
        "zero_coefficients_recover_anchor": True,
    }


def _predict_model(rows: list[dict[str, Any]], artifact: dict[str, Any]) -> np.ndarray:
    target = str(artifact["target"])
    anchor = _anchor_prediction(rows, target, float(artifact["anchor_factor"]))
    x = _matrix(rows)
    mean = np.asarray(artifact["feature_mean"], dtype=float)
    std = np.asarray(artifact["feature_std"], dtype=float)
    beta = np.asarray(artifact["log_residual_intercept_and_coefficients"], dtype=float)
    correction = np.clip(_predict_ridge(x, mean, std, beta), -4.0, 4.0)
    return np.maximum(0.0, (anchor + EPS) * np.exp(correction) - EPS)


def _metrics(y: np.ndarray, prediction: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    error = np.asarray(prediction, dtype=float) - np.asarray(y, dtype=float)
    den = max(float(weights.sum()), EPS)
    mae = float(np.sum(weights * np.abs(error)) / den)
    rmse = math.sqrt(max(0.0, float(np.sum(weights * error * error) / den)))
    bias = float(np.sum(weights * error) / den)
    return {"mae": mae, "rmse": rmse, "bias": bias}


def _baselines(train: list[dict[str, Any]], test: list[dict[str, Any]],
               target: str) -> dict[str, np.ndarray]:
    y_train = np.asarray([float(row[target]) for row in train], dtype=float)
    weights, _ = _weights(train)
    historical_mean = _weighted_mean(y_train, weights)
    factor = _fit_anchor_factor(train, target)
    result = {
        "zero": np.zeros(len(test), dtype=float),
        "causal_historical_mean": np.full(len(test), historical_mean, dtype=float),
        "causal_vol_anchor": _anchor_prediction(test, target, factor),
    }
    if target == TARGET_FUTURE_RV:
        result["current_rv60_persistence"] = np.asarray([
            float(row["current_realized_volatility_5m_60m"]) for row in test], dtype=float)
        result["current_rv15_persistence"] = np.asarray([
            float(row["current_realized_volatility_5m_15m"]) for row in test], dtype=float)
    return result


def _best(metrics: dict[str, dict[str, float]], metric: str) -> tuple[str, float]:
    name, values = min(metrics.items(), key=lambda item: float(item[1][metric]))
    return str(name), float(values[metric])


def evaluate_target(rows: list[dict[str, Any]], horizon: int,
                    target: str) -> dict[str, Any]:
    folds = _historical_folds(rows, horizon)
    all_y: list[float] = []; all_pred: list[float] = []; all_w: list[float] = []
    baseline_values: dict[str, list[float]] = defaultdict(list)
    reports = []
    joint_non_degrade = 0
    for fold in folds:
        train, test = fold["train"], fold["test"]
        artifact = _fit_model(train, target)
        prediction = _predict_model(test, artifact)
        y = np.asarray([float(row[target]) for row in test], dtype=float)
        weights, effective = _weights(test)
        baseline_predictions = _baselines(train, test, target)
        model_metrics = _metrics(y, prediction, weights)
        baseline_metrics = {name: _metrics(y, pred, weights)
                            for name, pred in baseline_predictions.items()}
        _mae_name, best_mae = _best(baseline_metrics, "mae")
        _rmse_name, best_rmse = _best(baseline_metrics, "rmse")
        joint = (model_metrics["mae"] <= best_mae and model_metrics["rmse"] <= best_rmse)
        joint_non_degrade += int(joint)
        reports.append({
            "fold_index": fold["fold_index"],
            "train_raw_n": len(train), "test_raw_n": len(test),
            "test_effective_n": effective,
            "purge_embargo_valid": fold["train_target_max_ts"] < fold["purge_boundary_ts"],
            "artifact_contract": {
                "model_family": artifact["model_family"],
                "target": target,
                "anchor_kind": artifact["anchor_kind"],
                "anchor_factor": artifact["anchor_factor"],
                "zero_coefficients_recover_anchor": True,
            },
            "model": model_metrics,
            "baselines": baseline_metrics,
            "joint_non_degrade": joint,
        })
        all_y.extend(y.tolist()); all_pred.extend(prediction.tolist()); all_w.extend(weights.tolist())
        for name, pred in baseline_predictions.items():
            baseline_values[name].extend(pred.tolist())
    y = np.asarray(all_y); pred = np.asarray(all_pred); weights = np.asarray(all_w)
    model = _metrics(y, pred, weights)
    baselines = {name: _metrics(y, np.asarray(values), weights)
                 for name, values in baseline_values.items()}
    return {
        "fold_count": len(reports), "folds": reports,
        "model": model, "baselines": baselines,
        "fold_joint_non_degrade_n": joint_non_degrade,
    }


def winner_gate(evaluation: dict[str, Any], raw_n: int, effective_n: int) -> dict[str, Any]:
    model = evaluation["model"]; baselines = evaluation["baselines"]
    mae_name, best_mae = _best(baselines, "mae")
    rmse_name, best_rmse = _best(baselines, "rmse")
    mae_improvement = (best_mae - float(model["mae"])) / best_mae if best_mae > EPS else 0.0
    rmse_improvement = (best_rmse - float(model["rmse"])) / best_rmse if best_rmse > EPS else 0.0
    sample_gate = raw_n >= MIN_HISTORICAL_RAW and effective_n >= MIN_HISTORICAL_EFFECTIVE
    fold_gate = int(evaluation.get("fold_count") or 0) == P3_OUTER_FOLDS
    robust = int(evaluation.get("fold_joint_non_degrade_n") or 0) >= P3_ROBUST_FOLDS_REQUIRED
    metric_gate = (mae_improvement >= MIN_PROVISIONAL_RELATIVE_IMPROVEMENT
                   and rmse_improvement >= MIN_PROVISIONAL_RELATIVE_IMPROVEMENT)
    return {
        "historical_winner": bool(sample_gate and fold_gate and robust and metric_gate),
        "best_mae_baseline": mae_name, "best_mae": best_mae,
        "model_mae": model["mae"], "mae_relative_improvement": mae_improvement,
        "best_rmse_baseline": rmse_name, "best_rmse": best_rmse,
        "model_rmse": model["rmse"], "rmse_relative_improvement": rmse_improvement,
        "required_relative_improvement": MIN_PROVISIONAL_RELATIVE_IMPROVEMENT,
        "fold_joint_non_degrade_observed": int(evaluation.get("fold_joint_non_degrade_n") or 0),
        "fold_joint_non_degrade_required": P3_ROBUST_FOLDS_REQUIRED,
        "sample_gate": sample_gate, "fold_gate": fold_gate,
        "robustness_gate": robust, "metric_gate": metric_gate,
    }


def run_p3_path_geometry(runtime) -> dict[str, Any]:
    source_set, sources = _current_sources(runtime)
    started = time.time(); results = []
    for horizon in HORIZONS:
        rows = build_rows(sources, horizon)
        _weight, effective = _weights(rows)
        for target in TARGETS:
            evaluation = evaluate_target(rows, horizon, target)
            gate = winner_gate(evaluation, len(rows), effective)
            results.append({
                "target": target, "horizon_minutes": horizon,
                "raw_n": len(rows), "effective_n": effective,
                "historical_winner": gate["historical_winner"],
                "mae_relative_improvement": gate["mae_relative_improvement"],
                "rmse_relative_improvement": gate["rmse_relative_improvement"],
                "fold_joint_non_degrade_n": gate["fold_joint_non_degrade_observed"],
                "best_mae_baseline": gate["best_mae_baseline"],
                "best_rmse_baseline": gate["best_rmse_baseline"],
                "gate": gate,
            })
    return {
        "contract_version": P3_CONTRACT_VERSION,
        "feature_contract_version": P3_FEATURE_CONTRACT,
        "evidence_label": P3_EVIDENCE_LABEL,
        "source_set_sha256": source_set,
        "historical_sampling_interval": "5m",
        "live_path_sampling_interval": "1m_or_recorded_path",
        "historical_future_volatility_name": TARGET_FUTURE_RV,
        "historical_future_volatility_is_live_1m_metric": False,
        "mfe_mae_semantics_match_live_high_low_geometry": True,
        "path_resolution_matches_live": False,
        "run_count": len(results),
        "winner_count": sum(bool(row["historical_winner"]) for row in results),
        "results": results,
        "outer_test_used_for_model_selection": False,
        "fixed_model_family": P3_MODEL_FAMILY,
        "fixed_l2": P3_L2,
        "dependency_group_total_weight_one": True,
        "purge_target_overlap": True,
        "embargo_seconds": EMBARGO_SECONDS,
        "historical_options_used": False,
        "synthetic_option_history": False,
        "live_parity_ready": False,
        "auto_promotion": False,
        "production_authority": False,
        "duration_ms": (time.time() - started) * 1000.0,
    }
