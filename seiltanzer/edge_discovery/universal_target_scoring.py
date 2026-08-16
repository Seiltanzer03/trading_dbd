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


UNIVERSAL_SCORING_CONTRACT_VERSION = "g1s-universal-target-scoring-v1"
DEPENDENCY_PVALUE_METHOD = "HORIZON_BUCKET_PARITY_CLUSTER_MAX_NORMAL_SIGN_V1"
MIN_PROBABILITY = 1e-6


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


def fitted_constant_predictions(
    global_train: list[dict[str, Any]], conditional_train: list[dict[str, Any]],
    test: list[dict[str, Any]], spec: UniversalTargetSpec,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit unconditional and conditional state distributions using train only."""
    if not global_train or not conditional_train or not test:
        raise ValueError("prediction sets must be non-empty")
    global_weights, _ = _weights(global_train)
    conditional_weights, _ = _weights(conditional_train)
    if spec.kind == "CONTINUOUS":
        global_y = np.asarray([float(row["universal_target_value"]) for row in global_train])
        conditional_y = np.asarray([float(row["universal_target_value"]) for row in conditional_train])
        baseline = _weighted_mean(global_y, global_weights)
        conditional = _weighted_mean(conditional_y, conditional_weights)
        return np.full(len(test), conditional), np.full(len(test), baseline)
    if spec.kind == "BINARY":
        positive = spec.classes[-1]
        global_y = np.asarray([
            1.0 if row["universal_target_value"] == positive else 0.0 for row in global_train])
        conditional_y = np.asarray([
            1.0 if row["universal_target_value"] == positive else 0.0 for row in conditional_train])
        baseline = min(1-MIN_PROBABILITY, max(MIN_PROBABILITY,
            _weighted_mean(global_y, global_weights)))
        den = float(conditional_weights.sum())
        observed = float(np.sum(conditional_weights*conditional_y))
        conditional = (observed+2.0*baseline)/(den+2.0)
        conditional = min(1-MIN_PROBABILITY, max(MIN_PROBABILITY, conditional))
        return np.full(len(test), conditional), np.full(len(test), baseline)
    if spec.kind == "MULTICLASS":
        baseline = _weighted_class_distribution(global_train, spec)
        raw = _weighted_class_distribution(conditional_train, spec)
        den = float(conditional_weights.sum())
        # Shrink the conditional distribution toward the global train state.
        conditional = (den*raw+2.0*baseline)/(den+2.0)
        return (np.tile(conditional, (len(test), 1)),
                np.tile(baseline, (len(test), 1)))
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
        # Target values are already volatility-normalized. Joint L1+L2 loss
        # prevents a single tail point from being the only source of significance.
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
    """Build two non-overlapping horizon-block cohorts for significance testing.

    5m T0 rows for a 60/120/240m target overlap heavily. Treating each T0 as an
    independent loss observation dramatically overstates power. We first average
    all rows inside one horizon-sized wall-clock bucket, with equal weight per
    instrument inside the bucket. Adjacent horizon buckets may still overlap at
    their target windows, so significance is computed separately on even and odd
    buckets. Buckets within each parity are separated by a full horizon and their
    future target windows therefore do not overlap. Cross-asset rows sharing the
    same time bucket are clustered together instead of pretending to be separate
    trials.
    """
    model_loss = _row_losses(rows, model, spec)
    baseline_loss = _row_losses(rows, baseline, spec)
    if len(model_loss) != len(rows) or len(baseline_loss) != len(rows):
        raise ValueError("paired universal target inputs must have identical lengths")

    grouped: dict[tuple[int, int], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list))
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
        if not instrument_means:
            continue
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
    """One-sided sign-consistency guard, exact for small cohorts."""
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
    # Normal approximation with continuity correction is sufficient here because
    # this term is only a conservative guard combined with the clustered mean test.
    z = (positive-0.5*n-0.5)/math.sqrt(0.25*n)
    return float(0.5*math.erfc(z/math.sqrt(2.0)))


def paired_target_pvalue(rows: list[dict[str, Any]], model: np.ndarray,
                         baseline: np.ndarray, spec: UniversalTargetSpec) -> float:
    """Conservative one-sided p-value under overlapping-horizon dependence.

    A candidate must look positive in both alternating non-overlapping horizon
    cohorts. Within each cohort we require both a positive clustered mean and
    broad sign consistency, then take the worst p-value. This intentionally gives
    up nominal power to prevent 5m overlap or synchronous cross-asset rows from
    manufacturing tiny p-values.
    """
    cohorts = paired_target_dependency_cohorts(rows, model, baseline, spec)
    cohort_pvalues: list[float] = []
    for values in cohorts:
        if len(values) < 3:
            return 1.0
        cohort_pvalues.append(max(
            _normal_mean_pvalue(values),
            _sign_consistency_pvalue(values),
        ))
    return max(cohort_pvalues) if cohort_pvalues else 1.0
