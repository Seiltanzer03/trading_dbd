"""G.1S historical diagnostics: genuine expanding walk-forward with purge/embargo.

Prospective OOS remains the authoritative evidence path.  These diagnostics are
research-only historical model diagnostics: chronological expanding folds,
never random shuffle, and fixed-horizon overlap is removed by requiring every
training label target to finish strictly before the validation block begins.
Existing immutable model artifacts are not rewritten; the contract applies to
future fitted artifacts only.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import g1_short_horizon_continuous_learning as _continuous
from . import g1_short_horizon_continuous_v2 as _continuous_v2
from . import g1_short_horizon_feature_contract_v2 as _v2
from . import g1_short_horizon_v2_diagnostics as _directional
from .g1_short_horizon_runtime import ShortHorizonRuntime


WALK_FORWARD_VERSION = "g1s-expanding-purged-walk-forward-v1"
INITIAL_TRAIN_FRACTION = 0.60
MAX_FOLDS = 4
MIN_INITIAL_TRAIN = 30
MIN_VALIDATION_TOTAL = 20


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])),
    )


def _fold_ranges(n: int) -> list[tuple[int, int]]:
    """Return non-overlapping validation blocks after an expanding train prefix."""
    first = max(MIN_INITIAL_TRAIN, int(n * INITIAL_TRAIN_FRACTION))
    if first >= n or n - first < MIN_VALIDATION_TOTAL:
        return []
    remaining = n - first
    fold_n = min(MAX_FOLDS, max(2, remaining // 10))
    base = remaining // fold_n
    extra = remaining % fold_n
    ranges: list[tuple[int, int]] = []
    start = first
    for index in range(fold_n):
        size = base + (1 if index < extra else 0)
        end = min(n, start + size)
        if end > start:
            ranges.append((start, end))
        start = end
    return ranges


def _purged_train(
    ordered: list[dict[str, Any]], start: int
) -> tuple[list[dict[str, Any]], float]:
    validation_start = float(ordered[start]["captured_ts"])
    train = [
        row
        for row in ordered[:start]
        if float(row["target_ts"]) < validation_start - 1e-9
    ]
    return train, validation_start


def directional_walk_forward(
    runtime: ShortHorizonRuntime,
    rows: list[dict[str, Any]],
    feature_set: str,
    horizon: int,
) -> dict[str, Any]:
    ordered = _ordered(rows)
    ranges = _fold_ranges(len(ordered))
    if not ranges:
        return {
            "status": "INSUFFICIENT",
            "historical_walk_forward": False,
            "prospective_oos": False,
            "oos_validated": False,
            "diagnostics_contract_version": WALK_FORWARD_VERSION,
        }

    evaluated_rows: list[dict[str, Any]] = []
    probabilities: list[float] = []
    labels: list[int] = []
    base_rate_ps: list[float] = []
    persistence_ps: list[float] = []
    momentum_ps: list[float] = []
    folds: list[dict[str, Any]] = []

    for fold_index, (start, end) in enumerate(ranges, 1):
        train, validation_start = _purged_train(ordered, start)
        test = ordered[start:end]
        if len(train) < 20 or not test:
            continue

        beta, mean, std = _directional._fit_on(runtime, train, feature_set)
        x_test = np.asarray(
            [runtime._feature_vector(row, feature_set)[0] for row in test],
            dtype=float,
        )
        z = (x_test - mean) / std
        scores = beta[0] + z @ beta[1:]
        fold_ps = [
            float(1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, float(score))))))
            for score in scores
        ]
        fold_ys = [1 if row["direction_label"] == "UP" else 0 for row in test]

        train_weights = [float(v) for v in _directional._weights(runtime, train)]
        train_ys = [1 if row["direction_label"] == "UP" else 0 for row in train]
        den = max(sum(train_weights), 1e-12)
        base_rate = sum(w * y for w, y in zip(train_weights, train_ys)) / den
        latest_label = train[-1]["direction_label"] if train else None
        persistence = 0.5 if latest_label is None else (0.55 if latest_label == "UP" else 0.45)

        for row in test:
            ret15 = _v2._v2_values(row).get("ret_15m")
            momentum_ps.append(
                0.5
                if ret15 is None or abs(ret15) < 1e-12
                else (0.55 if ret15 > 0 else 0.45)
            )
        base_rate_ps.extend([base_rate] * len(test))
        persistence_ps.extend([persistence] * len(test))
        evaluated_rows.extend(test)
        probabilities.extend(fold_ps)
        labels.extend(fold_ys)
        folds.append(
            {
                "fold": fold_index,
                "train_n_after_purge": len(train),
                "test_n": len(test),
                "validation_start_ts": validation_start,
                "validation_end_ts": float(test[-1]["captured_ts"]),
                "max_training_target_ts": max(float(row["target_ts"]) for row in train),
            }
        )

    if not evaluated_rows:
        return {
            "status": "INSUFFICIENT_AFTER_PURGE",
            "historical_walk_forward": True,
            "prospective_oos": False,
            "oos_validated": False,
            "diagnostics_contract_version": WALK_FORWARD_VERSION,
            "folds": folds,
        }

    weights = [float(v) for v in _directional._weights(runtime, evaluated_rows)]
    half = [0.5] * len(labels)
    return {
        "status": "HISTORICAL_EXPANDING_WALK_FORWARD",
        "diagnostics_contract_version": WALK_FORWARD_VERSION,
        "historical_walk_forward": True,
        "walk_forward_kind": "expanding_train_non_overlapping_validation_blocks",
        "fold_count": len(folds),
        "folds": folds,
        "random_shuffle": False,
        "purge_applied": True,
        "embargo_sec": int(horizon) * 60,
        "overlap_rule": "training_target_ts_strictly_before_validation_start",
        "validation_n": len(evaluated_rows),
        "validation_effective_n": float(sum(weights)),
        "model_brier": _directional._brier(probabilities, labels, weights),
        "model_log_loss": _directional._logloss(probabilities, labels, weights),
        "constant_0_5_brier": _directional._brier(half, labels, weights),
        "constant_0_5_log_loss": _directional._logloss(half, labels, weights),
        "train_base_rate_brier": _directional._brier(base_rate_ps, labels, weights),
        "train_base_rate_log_loss": _directional._logloss(base_rate_ps, labels, weights),
        "naive_persistence_brier": _directional._brier(persistence_ps, labels, weights),
        "naive_persistence_log_loss": _directional._logloss(persistence_ps, labels, weights),
        "fixed_15m_momentum_brier": _directional._brier(momentum_ps, labels, weights),
        "fixed_15m_momentum_log_loss": _directional._logloss(momentum_ps, labels, weights),
        "dependency_group_total_weight_one": True,
        "prospective_oos": False,
        "oos_validated": False,
        "production_authority": False,
    }


def continuous_walk_forward(
    runtime: ShortHorizonRuntime,
    rows: list[dict[str, Any]],
    feature_set: str,
) -> dict[str, Any]:
    ordered = _ordered(rows)
    ranges = _fold_ranges(len(ordered))
    if not ranges:
        return {
            "status": "INSUFFICIENT",
            "historical_walk_forward": False,
            "prospective_oos": False,
            "oos_validated": False,
            "diagnostics_contract_version": WALK_FORWARD_VERSION,
        }

    evaluated_rows: list[dict[str, Any]] = []
    predicted: list[float] = []
    actual: list[float] = []
    zero: list[float] = []
    train_mean: list[float] = []
    persistence: list[float] = []
    folds: list[dict[str, Any]] = []

    horizon = int(ordered[0]["horizon_minutes"]) if ordered else 0
    for fold_index, (start, end) in enumerate(ranges, 1):
        train, validation_start = _purged_train(ordered, start)
        test = ordered[start:end]
        if len(train) < 20 or not test:
            continue

        beta, mean, std = _continuous._fit_ridge(runtime, train, feature_set)
        x_test, y_test = _continuous._arrays(runtime, test, feature_set)
        z = (x_test - mean) / std
        fold_pred = beta[0] + z @ beta[1:]

        train_weights_np, _ = _continuous._dependency_weights(runtime, train)
        train_weights = [float(v) for v in train_weights_np]
        train_y = [float(row["terminal_log_return"]) for row in train]
        den = max(sum(train_weights), 1e-12)
        causal_mean = sum(w * y for w, y in zip(train_weights, train_y)) / den

        evaluated_rows.extend(test)
        predicted.extend(float(v) for v in fold_pred)
        actual.extend(float(v) for v in y_test)
        zero.extend([0.0] * len(test))
        train_mean.extend([causal_mean] * len(test))
        persistence.extend(_continuous._ret15(row) for row in test)
        folds.append(
            {
                "fold": fold_index,
                "train_n_after_purge": len(train),
                "test_n": len(test),
                "validation_start_ts": validation_start,
                "validation_end_ts": float(test[-1]["captured_ts"]),
                "max_training_target_ts": max(float(row["target_ts"]) for row in train),
            }
        )

    if not evaluated_rows:
        return {
            "status": "INSUFFICIENT_AFTER_PURGE",
            "historical_walk_forward": True,
            "prospective_oos": False,
            "oos_validated": False,
            "diagnostics_contract_version": WALK_FORWARD_VERSION,
            "folds": folds,
        }

    weights_np, _ = _continuous._dependency_weights(runtime, evaluated_rows)
    weights = [float(v) for v in weights_np]
    return {
        "status": "HISTORICAL_EXPANDING_WALK_FORWARD",
        "diagnostics_contract_version": WALK_FORWARD_VERSION,
        "historical_walk_forward": True,
        "walk_forward_kind": "expanding_train_non_overlapping_validation_blocks",
        "fold_count": len(folds),
        "folds": folds,
        "random_shuffle": False,
        "purge_applied": True,
        "embargo_sec": horizon * 60,
        "overlap_rule": "training_target_ts_strictly_before_validation_start",
        "validation_n": len(evaluated_rows),
        "validation_effective_n": float(sum(weights)),
        "model_mae": _continuous._mae(predicted, actual, weights),
        "model_rmse": _continuous._rmse(predicted, actual, weights),
        "zero_mae": _continuous._mae(zero, actual, weights),
        "zero_rmse": _continuous._rmse(zero, actual, weights),
        "causal_train_mean_mae": _continuous._mae(train_mean, actual, weights),
        "causal_train_mean_rmse": _continuous._rmse(train_mean, actual, weights),
        "fixed_ret15_persistence_mae": _continuous._mae(persistence, actual, weights),
        "fixed_ret15_persistence_rmse": _continuous._rmse(persistence, actual, weights),
        "sign_accuracy_secondary": _continuous._sign_accuracy(predicted, actual, weights),
        "dependency_group_total_weight_one": True,
        "prospective_oos": False,
        "oos_validated": False,
        "production_authority": False,
    }


def install_g1_short_horizon_walkforward() -> None:
    if getattr(ShortHorizonRuntime, "_walk_forward_diagnostics_version", None) == WALK_FORWARD_VERSION:
        return

    # All three fitters resolve these module globals at call time.  Patching only
    # the diagnostic callback keeps model fitting/prediction behavior unchanged.
    _directional._historical_diagnostics = directional_walk_forward
    _continuous._historical_diagnostics = continuous_walk_forward
    _continuous_v2._historical_diagnostics = continuous_walk_forward

    previous_status = ShortHorizonRuntime.status

    def status(self):
        report = previous_status(self)
        report["walk_forward_diagnostics"] = {
            "contract_version": WALK_FORWARD_VERSION,
            "kind": "expanding_train_non_overlapping_validation_blocks",
            "purge": "training_target_ts_strictly_before_validation_start",
            "embargo": "fixed_horizon_seconds",
            "random_shuffle": False,
            "historical_only": True,
            "prospective_oos_still_authoritative": True,
            "production_authority": False,
        }
        return report

    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._walk_forward_diagnostics_version = WALK_FORWARD_VERSION
