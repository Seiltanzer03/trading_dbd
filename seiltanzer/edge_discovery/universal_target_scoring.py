"""Target-aware scoring for strategy-agnostic universal market outcomes.

This module intentionally does not alter legacy directional EDE scoring.  It
provides a parallel research-only contract for continuous and multiclass path
outcomes introduced by PASS 2.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _weighted_mean, _weights


UNIVERSAL_SCORING_CONTRACT_VERSION = "g1s-universal-target-scoring-v1.1"
DEPENDENCY_PVALUE_METHOD = "HORIZON_BUCKET_PARITY_CLUSTER_MAX_NORMAL_SIGN_V1"
BASELINE_METHOD = "TRAIN_ONLY_INSTRUMENT_FAMILY_GLOBAL_RESIDUAL_V1"
MIN_PROBABILITY = 1e-6
MIN_STRUCTURAL_BASELINE_ROWS = 20


@dataclass(frozen=True)
class UniversalTargetSpec:
    target_id: str
    family: str
    kind: str  # BINARY, CONTINUOUS, MULTICLASS
    classes: tuple[str, ...] = ()
    primary_metrics: tuple[str, str] = ()


def universal_target_specs(barrier_ids: Iterable[str]) -> tuple[UniversalTargetSpec, ...]:
    values = [
        UniversalTargetSpec("DIRECTION", "DIRECTION", "BINARY", ("DOWN", "UP"),
                            ("brier", "logloss")),
        UniversalTargetSpec("RETURN_SIGMA", "RETURN", "CONTINUOUS", (),
                            ("mae", "rmse")),
        UniversalTargetSpec("MFE_SIGMA", "MFE", "CONTINUOUS", (),
                            ("mae", "rmse")),
        UniversalTargetSpec("MAE_SIGMA", "MAE", "CONTINUOUS", (),
                            ("mae", "rmse")),
        UniversalTargetSpec("FORWARD_VOL_RATIO", "FORWARD_VOLATILITY", "CONTINUOUS", (),
                            ("mae", "rmse")),
    ]
    values.extend(
        UniversalTargetSpec(
            f"FIRST_TOUCH:{barrier_id}", "FIRST_TOUCH", "MULTICLASS",
            ("DOWN_FIRST", "NO_TOUCH", "UP_FIRST"), ("brier", "logloss"),
        )
        for barrier_id in sorted(set(str(value) for value in barrier_ids))
    )
    return tuple(values)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def target_value(row: dict[str, Any], spec: UniversalTargetSpec) -> float | str | None:
    outcome = row.get("universal_outcome") or {}
    if not bool(outcome.get("available")) or not bool(outcome.get("path_complete")):
        return None
    sigma = _finite(outcome.get("t0_local_sigma_h"))
    if spec.target_id == "DIRECTION":
        value = str(outcome.get("direction_label") or "")
        return value if value in {"UP", "DOWN"} else None
    if sigma is None or sigma <= 0.0:
        return None
    if spec.target_id == "RETURN_SIGMA":
        value = _finite(outcome.get("terminal_log_return"))
        return None if value is None else value/sigma
    if spec.target_id == "MFE_SIGMA":
        return _finite(outcome.get("mfe_sigma"))
    if spec.target_id == "MAE_SIGMA":
        return _finite(outcome.get("mae_sigma"))
    if spec.target_id == "FORWARD_VOL_RATIO":
        value = _finite(outcome.get("forward_rv_log_return"))
        return None if value is None else value/sigma
    if spec.target_id.startswith("FIRST_TOUCH:"):
        barrier_id = spec.target_id.split(":", 1)[1]
        barrier = (outcome.get("barriers") or {}).get(barrier_id) or {}
        if not bool(barrier.get("clean_label")):
            return None
        label = str(barrier.get("label") or "")
        return label if label in set(spec.classes) else None
    return None


def eligible_target_rows(rows: Iterable[dict[str, Any]], spec: UniversalTargetSpec) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in rows:
        value = target_value(source, spec)
        if value is None:
            continue
        row = dict(source)
        row["universal_target_id"] = spec.target_id
        row["universal_target_value"] = value
        output.append(row)
    return output


def _continuous_metrics(y: np.ndarray, pred: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    error = np.asarray(pred, dtype=float)-np.asarray(y, dtype=float)
    return {
        "mae": _weighted_mean(np.abs(error), weights),
        "rmse": math.sqrt(max(0.0, _weighted_mean(error*error, weights))),
    }


def _binary_metrics(y: np.ndarray, pred: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    p = np.clip(np.asarray(pred, dtype=float), MIN_PROBABILITY, 1-MIN_PROBABILITY)
    target = np.asarray(y, dtype=float)
    return {
        "brier": _weighted_mean((p-target)**2, weights),
        "logloss": _weighted_mean(-(target*np.log(p)+(1-target)*np.log(1-p)), weights),
    }


def _multiclass_metrics(y: list[str], pred: np.ndarray, weights: np.ndarray,
                        classes: tuple[str, ...]) -> dict[str, float]:
    probabilities = np.clip(np.asarray(pred, dtype=float), MIN_PROBABILITY, 1.0)
    probabilities = probabilities/np.maximum(probabilities.sum(axis=1, keepdims=True), 1e-12)
    index = {label: idx for idx, label in enumerate(classes)}
    observed = np.zeros_like(probabilities)
    for row_index, label in enumerate(y):
        observed[row_index, index[label]] = 1.0
    brier_by_row = np.sum((probabilities-observed)**2, axis=1)
    logloss_by_row = -np.log(np.asarray([
        probabilities[row_index, index[label]] for row_index, label in enumerate(y)
    ]))
    return {
        "brier": _weighted_mean(brier_by_row, weights),
        "logloss": _weighted_mean(logloss_by_row, weights),
    }


def _weighted_class_distribution(rows: list[dict[str, Any]], spec: UniversalTargetSpec) -> np.ndarray:
    weights, _effective = _weights(rows)
    classes = tuple(spec.classes)
    values = [str(row["universal_target_value"]) for row in rows]
    total = max(float(weights.sum()), 1e-12)
    distribution = np.asarray([
        float(sum(weight for value, weight in zip(values, weights) if value == label))/total
        for label in classes
    ], dtype=float)
    distribution = np.maximum(distribution, MIN_PROBABILITY)
    return distribution/distribution.sum()


def _asset_family(row: dict[str, Any]) -> str | None:
    value = row.get("asset_family")
    if value is None:
        features = row.get("ede_features") or {}
        value = features.get("regime.asset_family")
        if value is None:
            value = features.get("asset_family")
    return None if value in {None, ""} else str(value)


def _fit_constant(rows: list[dict[str, Any]], spec: UniversalTargetSpec) -> float | np.ndarray:
    if not rows:
        raise ValueError("cannot fit target constant without rows")
    weights, _effective = _weights(rows)
    if spec.kind == "CONTINUOUS":
        y = np.asarray([float(row["universal_target_value"]) for row in rows], dtype=float)
        return float(_weighted_mean(y, weights))
    if spec.kind == "BINARY":
        positive = spec.classes[-1]
        y = np.asarray([
            1.0 if str(row["universal_target_value"]) == positive else 0.0 for row in rows
        ], dtype=float)
        value = _weighted_mean(y, weights)
        return float(min(1-MIN_PROBABILITY, max(MIN_PROBABILITY, value)))
    if spec.kind == "MULTICLASS":
        return _weighted_class_distribution(rows, spec)
    raise ValueError(f"unsupported target kind: {spec.kind}")


def structural_baseline_predictions(
    train: list[dict[str, Any]], test: list[dict[str, Any]], spec: UniversalTargetSpec,
) -> np.ndarray:
    """Causal structural baseline: instrument -> family -> global, fitted on train only.

    Static cross-sectional differences between instruments are not a market-state
    edge. A USDCAD rule, for example, must improve over the train-only USDCAD
    base rate rather than win merely because USDCAD differs from the pooled market.
    """
    if not train or not test:
        raise ValueError("structural baseline sets must be non-empty")
    by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train:
        instrument = str(row.get("instrument") or "")
        if instrument:
            by_instrument[instrument].append(row)
        family = _asset_family(row)
        if family:
            by_family[family].append(row)
    global_value = _fit_constant(train, spec)
    instrument_values = {
        key: _fit_constant(rows, spec)
        for key, rows in by_instrument.items() if len(rows) >= MIN_STRUCTURAL_BASELINE_ROWS
    }
    family_values = {
        key: _fit_constant(rows, spec)
        for key, rows in by_family.items() if len(rows) >= MIN_STRUCTURAL_BASELINE_ROWS
    }

    values: list[float | np.ndarray] = []
    for row in test:
        instrument = str(row.get("instrument") or "")
        value = instrument_values.get(instrument)
        if value is None:
            family = _asset_family(row)
            value = family_values.get(family) if family is not None else None
        values.append(global_value if value is None else value)
    if spec.kind == "MULTICLASS":
        return np.vstack([np.asarray(value, dtype=float) for value in values])
    return np.asarray([float(value) for value in values], dtype=float)


def fitted_constant_predictions(
    global_train: list[dict[str, Any]], conditional_train: list[dict[str, Any]],
    test: list[dict[str, Any]], spec: UniversalTargetSpec,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit a train-only state residual over the structural asset baseline."""
    if not global_train or not conditional_train or not test:
        raise ValueError("prediction sets must be non-empty")
    conditional_weights, _ = _weights(conditional_train)
    baseline_test = structural_baseline_predictions(global_train, test, spec)
    baseline_conditional = structural_baseline_predictions(global_train, conditional_train, spec)
    den = max(float(conditional_weights.sum()), 1e-12)

    if spec.kind == "CONTINUOUS":
        y = np.asarray([float(row["universal_target_value"]) for row in conditional_train], dtype=float)
        residual = y-np.asarray(baseline_conditional, dtype=float)
        shift = float(np.sum(conditional_weights*residual)/den)
        return np.asarray(baseline_test, dtype=float)+shift, np.asarray(baseline_test, dtype=float)

    if spec.kind == "BINARY":
        positive = spec.classes[-1]
        y = np.asarray([
            1.0 if str(row["universal_target_value"]) == positive else 0.0
            for row in conditional_train
        ], dtype=float)
        residual = y-np.asarray(baseline_conditional, dtype=float)
        shift = float(np.sum(conditional_weights*residual)/(den+2.0))
        conditional = np.clip(
            np.asarray(baseline_test, dtype=float)+shift,
            MIN_PROBABILITY, 1.0-MIN_PROBABILITY)
        return conditional, np.asarray(baseline_test, dtype=float)

    if spec.kind == "MULTICLASS":
        index = {label: idx for idx, label in enumerate(spec.classes)}
        observed = np.zeros((len(conditional_train), len(spec.classes)), dtype=float)
        for row_index, row in enumerate(conditional_train):
            observed[row_index, index[str(row["universal_target_value"])]] = 1.0
        residual = observed-np.asarray(baseline_conditional, dtype=float)
        shift = np.sum(residual*conditional_weights[:, None], axis=0)/(den+2.0)
        conditional = np.asarray(baseline_test, dtype=float)+shift[None, :]
        conditional = np.maximum(conditional, MIN_PROBABILITY)
        conditional = conditional/np.maximum(conditional.sum(axis=1, keepdims=True), 1e-12)
        return conditional, np.asarray(baseline_test, dtype=float)

    raise ValueError(f"unsupported target kind: {spec.kind}")


def target_metrics(rows: list[dict[str, Any]], prediction: np.ndarray,
                   spec: UniversalTargetSpec) -> dict[str, Any]:
    weights, effective = _weights(rows)
    if spec.kind == "CONTINUOUS":
        y = np.asarray([float(row["universal_target_value"]) for row in rows])
        values = _continuous_metrics(y, prediction, weights)
    elif spec.kind == "BINARY":
        positive = spec.classes[-1]
        y = np.asarray([1.0 if row["universal_target_value"] == positive else 0.0
                        for row in rows])
        values = _binary_metrics(y, prediction, weights)
    elif spec.kind == "MULTICLASS":
        y = [str(row["universal_target_value"]) for row in rows]
        values = _multiclass_metrics(y, prediction, weights, spec.classes)
    else:
        raise ValueError(f"unsupported target kind: {spec.kind}")
    values.update({"raw_n": len(rows), "effective_n": int(effective)})
    return values


def relative_target_improvement(model: dict[str, Any], baseline: dict[str, Any],
                                spec: UniversalTargetSpec) -> dict[str, float]:
    output: dict[str, float] = {}
    for name in spec.primary_metrics:
        base = float(baseline[name]); candidate = float(model[name])
        output[name] = (base-candidate)/max(abs(base), 1e-12)
    return output


def _row_losses(rows: list[dict[str, Any]], prediction: np.ndarray,
                spec: UniversalTargetSpec) -> np.ndarray:
    if spec.kind == "CONTINUOUS":
        y = np.asarray([float(row["universal_target_value"]) for row in rows])
        error = np.asarray(prediction, dtype=float)-y
        return np.abs(error)+error*error
    if spec.kind == "BINARY":
        positive = spec.classes[-1]
        y = np.asarray([1.0 if row["universal_target_value"] == positive else 0.0
                        for row in rows])
        p = np.clip(np.asarray(prediction, dtype=float), MIN_PROBABILITY, 1-MIN_PROBABILITY)
        return (p-y)**2-(y*np.log(p)+(1-y)*np.log(1-p))
    if spec.kind == "MULTICLASS":
        probabilities = np.clip(np.asarray(prediction, dtype=float), MIN_PROBABILITY, 1.0)
        probabilities = probabilities/np.maximum(probabilities.sum(axis=1, keepdims=True), 1e-12)
        index = {label: idx for idx, label in enumerate(spec.classes)}
        losses = []
        for row_index, row in enumerate(rows):
            target_index = index[str(row["universal_target_value"])]
            observed = np.zeros(len(spec.classes), dtype=float); observed[target_index] = 1.0
            losses.append(float(np.sum((probabilities[row_index]-observed)**2)
                                - math.log(probabilities[row_index, target_index])))
        return np.asarray(losses, dtype=float)
    raise ValueError(f"unsupported target kind: {spec.kind}")


def paired_target_loss_deltas(
    rows: list[dict[str, Any]], model: np.ndarray, baseline: np.ndarray,
    spec: UniversalTargetSpec,
) -> np.ndarray:
    """Legacy timestamp-paired deltas retained for diagnostics/backward compatibility."""
    model_loss = _row_losses(rows, model, spec)
    baseline_loss = _row_losses(rows, baseline, spec)
    if len(model_loss) != len(rows) or len(baseline_loss) != len(rows):
        raise ValueError("paired universal target inputs must have identical lengths")
    groups: dict[float, list[float]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[float(row["captured_ts"])].append(float(baseline_loss[index]-model_loss[index]))
    return np.asarray([float(np.mean(values)) for values in groups.values()], dtype=float)


def paired_target_dependency_cohorts(
    rows: list[dict[str, Any]], model: np.ndarray, baseline: np.ndarray,
    spec: UniversalTargetSpec,
) -> tuple[np.ndarray, np.ndarray]:
    """Build two non-overlapping horizon-block cohorts for significance testing."""
    model_loss = _row_losses(rows, model, spec)
    baseline_loss = _row_losses(rows, baseline, spec)
    if len(model_loss) != len(rows) or len(baseline_loss) != len(rows):
        raise ValueError("paired universal target inputs must have identical lengths")
    grouped: dict[tuple[int, int], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for index, row in enumerate(rows):
        horizon = max(1, int(row.get("horizon_minutes") or 0))
        captured_ts = float(row["captured_ts"])
        bucket = int(captured_ts // (horizon*60.0))
        instrument = str(row.get("instrument") or "UNKNOWN")
        delta = float(baseline_loss[index]-model_loss[index])
        grouped[(horizon, bucket)][instrument].append(delta)
    cohorts: list[list[float]] = [[], []]
    for (_horizon, bucket), instruments in sorted(grouped.items()):
        instrument_means = [float(np.mean(values)) for values in instruments.values() if values]
        if instrument_means:
            cohorts[bucket % 2].append(float(np.mean(instrument_means)))
    return tuple(np.asarray(values, dtype=float) for values in cohorts)  # type: ignore[return-value]


def _normal_mean_pvalue(values: np.ndarray) -> float:
    if len(values) < 3:
        return 1.0
    mean = float(values.mean()); std = float(values.std(ddof=1))
    if mean <= 0.0:
        return 1.0
    if std <= 1e-15:
        return 0.0
    z = mean/(std/math.sqrt(len(values)))
    return float(0.5*math.erfc(z/math.sqrt(2.0)))


def _sign_consistency_pvalue(values: np.ndarray) -> float:
    nonzero = np.asarray([float(value) for value in values if abs(float(value)) > 1e-15])
    n = len(nonzero)
    if n < 3:
        return 1.0
    positive = int(np.sum(nonzero > 0.0))
    if positive <= n/2:
        return 1.0
    if n <= 64:
        tail = sum(math.comb(n, count) for count in range(positive, n+1))
        return float(tail/(2**n))
    z = (positive-0.5*n-0.5)/math.sqrt(0.25*n)
    return float(0.5*math.erfc(z/math.sqrt(2.0)))


def paired_target_pvalue(rows: list[dict[str, Any]], model: np.ndarray,
                         baseline: np.ndarray, spec: UniversalTargetSpec) -> float:
    cohorts = paired_target_dependency_cohorts(rows, model, baseline, spec)
    cohort_pvalues: list[float] = []
    for values in cohorts:
        if len(values) < 3:
            return 1.0
        cohort_pvalues.append(max(_normal_mean_pvalue(values), _sign_consistency_pvalue(values)))
    return max(cohort_pvalues) if cohort_pvalues else 1.0
