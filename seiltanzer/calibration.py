"""Leakage-safe Q→P calibration measurement primitives.

The production option distribution is labelled Q (pricing/risk-neutral).  This
module measures its relationship with realized outcomes, but does not replace
Q with a physical probability until a reviewed OOS promotion exists.
"""
from __future__ import annotations

import math
from typing import Iterable


CALIBRATION_VERSION = "q-to-p-shadow-f1-oos-v1"


def _finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def brier_score(probabilities: Iterable[float], outcomes: Iterable[float]) -> float | None:
    pairs = [(float(p), float(y)) for p, y in zip(probabilities, outcomes)
             if _finite(p) is not None and _finite(y) is not None]
    if not pairs:
        return None
    return sum((min(max(p, 0.0), 1.0) - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(probabilities: Iterable[float], outcomes: Iterable[float],
             epsilon: float = 1e-6) -> float | None:
    pairs = [(float(p), float(y)) for p, y in zip(probabilities, outcomes)
             if _finite(p) is not None and _finite(y) is not None]
    if not pairs:
        return None
    return -sum(
        y * math.log(min(max(p, epsilon), 1.0 - epsilon))
        + (1.0 - y) * math.log(1.0 - min(max(p, epsilon), 1.0 - epsilon))
        for p, y in pairs
    ) / len(pairs)


def _wilson(events: int, count: int, z: float = 1.96) -> list[float | None]:
    if count <= 0:
        return [None, None]
    p = events / count
    denom = 1.0 + z * z / count
    centre = (p + z * z / (2.0 * count)) / denom
    radius = z * math.sqrt(p * (1.0 - p) / count + z * z / (4.0 * count * count)) / denom
    return [max(0.0, centre - radius), min(1.0, centre + radius)]


def calibration_bins(probabilities: Iterable[float], outcomes: Iterable[float],
                     width: float = 0.10) -> list[dict]:
    bins = []
    pairs = [(min(max(float(p), 0.0), 1.0), float(y))
             for p, y in zip(probabilities, outcomes)
             if _finite(p) is not None and _finite(y) is not None]
    count_bins = int(round(1.0 / width))
    for index in range(count_bins):
        low = index * width
        high = 1.0 if index == count_bins - 1 else (index + 1) * width
        rows = [
            (p, y) for p, y in pairs
            if (low <= p <= high if index == count_bins - 1 else low <= p < high)
        ]
        if not rows:
            continue
        events = sum(y >= 0.5 for _, y in rows)
        bins.append({
            "lo": round(low, 4), "hi": round(high, 4), "n": len(rows),
            "mean_q_probability": sum(p for p, _ in rows) / len(rows),
            "actual_frequency": sum(y for _, y in rows) / len(rows),
            "actual_frequency_ci95": _wilson(events, len(rows)),
        })
    return bins


def binary_scorecard(probabilities: Iterable[float], outcomes: Iterable[float]) -> dict:
    pairs = [(float(p), float(y)) for p, y in zip(probabilities, outcomes)
             if _finite(p) is not None and _finite(y) is not None]
    ps = [p for p, _ in pairs]
    ys = [y for _, y in pairs]
    base_rate = sum(ys) / len(ys) if ys else None
    baseline = [base_rate] * len(ys) if base_rate is not None else []
    model_brier = brier_score(ps, ys)
    naive_brier = brier_score(baseline, ys)
    return {
        "n": len(pairs), "event_count": int(sum(ys)),
        "q_model_brier": model_brier,
        "naive_base_rate_brier": naive_brier,
        "q_model_brier_improvement": (
            naive_brier - model_brier
            if naive_brier is not None and model_brier is not None else None),
        "q_model_log_loss": log_loss(ps, ys),
        "reliability_curve": calibration_bins(ps, ys),
        "probability_measure": "risk_neutral_Q",
    }


def frozen_baseline_scorecard(train_outcomes: Iterable[float],
                              test_probabilities: Iterable[float],
                              test_outcomes: Iterable[float]) -> dict:
    """Evaluate TEST against a base rate fitted on TRAIN and then frozen."""
    train = [float(value) for value in train_outcomes if _finite(value) is not None]
    pairs = [(float(p), float(y)) for p, y in zip(test_probabilities, test_outcomes)
             if _finite(p) is not None and _finite(y) is not None]
    if not train or not pairs:
        return {
            "train_n": len(train), "test_n": len(pairs),
            "frozen_train_base_rate": None, "status": "insufficient_data",
        }
    base_rate = sum(train) / len(train)
    probabilities = [p for p, _ in pairs]
    outcomes = [y for _, y in pairs]
    baseline = [base_rate] * len(pairs)
    model_brier = brier_score(probabilities, outcomes)
    baseline_brier = brier_score(baseline, outcomes)
    model_log = log_loss(probabilities, outcomes)
    baseline_log = log_loss(baseline, outcomes)
    return {
        "train_n": len(train), "test_n": len(pairs),
        "frozen_train_base_rate": base_rate,
        "q_model_brier": model_brier,
        "frozen_baseline_brier": baseline_brier,
        "brier_skill": (
            1.0 - model_brier / baseline_brier
            if model_brier is not None and baseline_brier not in (None, 0.0) else None),
        "q_model_log_loss": model_log,
        "frozen_baseline_log_loss": baseline_log,
        "log_loss_skill": (
            1.0 - model_log / baseline_log
            if model_log is not None and baseline_log not in (None, 0.0) else None),
        "status": "descriptive_oos" if len(pairs) < 30 else "oos_evaluable",
        "test_outcomes_used_for_fit": False,
    }


def simplex_platt_transform(q_probabilities: Iterable[float],
                            *, intercepts: Iterable[float] = (0.0, 0.0, 0.0),
                            slopes: Iterable[float] = (1.0, 1.0, 1.0),
                            epsilon: float = 1e-9) -> list[float]:
    """Coherent shadow-only Platt-style map for TAKE/STOP/NO_TOUCH.

    Parameters must be fitted on TRAIN elsewhere and frozen before TEST. The
    identity parameters map any valid Q simplex back to itself.
    """
    q = [max(float(value), epsilon) for value in q_probabilities]
    a, b = list(intercepts), list(slopes)
    if len(q) != 3 or len(a) != 3 or len(b) != 3:
        raise ValueError("competing-risk vector and Platt parameters must have length 3")
    total = sum(q)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Q probability vector must be finite with positive mass")
    q = [value / total for value in q]
    logits = [float(ai) + float(bi) * math.log(value)
              for value, ai, bi in zip(q, a, b)]
    centre = max(logits)
    weights = [math.exp(value - centre) for value in logits]
    normalizer = sum(weights)
    return [value / normalizer for value in weights]


def prospective_dataset_contract(records: Iterable[dict]) -> dict:
    """Separate headline trade-level OOS units from clustered review panels."""
    rows = sorted((dict(row) for row in records),
                  key=lambda row: float(row.get("prediction_ts") or 0.0))
    eligible = [row for row in rows if row.get("eligible", True) and row.get("trade_id") is not None]
    first = {}
    secondary = []
    for row in eligible:
        trade_id = int(row["trade_id"])
        panel_row = {**row, "cluster_id": trade_id}
        secondary.append(panel_row)
        first.setdefault(trade_id, panel_row)
    return {
        "version": "prospective-q-to-p-g-v1",
        "primary_oos": list(first.values()),
        "primary_unit": "first_eligible_forecast_per_trade",
        "secondary_panel": secondary,
        "secondary_cluster_key": "trade_id",
        "effective_independent_n": len(first),
        "bootstrap_unit": "trade_id",
        "physical_probability_published": False,
        "promotion_allowed": False,
        "production_replacement_allowed": False,
    }


def pinball_loss(forecasts: Iterable[float], outcomes: Iterable[float],
                 quantile: float) -> float | None:
    pairs = [(float(q), float(y)) for q, y in zip(forecasts, outcomes)
             if _finite(q) is not None and _finite(y) is not None]
    if not pairs:
        return None
    tau = min(max(float(quantile), 0.0), 1.0)
    return sum(
        max(tau * (y - q), (tau - 1.0) * (y - q)) for q, y in pairs
    ) / len(pairs)


def quantile_scorecard(rows: Iterable[dict], outcome_key: str = "realized_r") -> dict:
    records = list(rows)
    result = {}
    for key, tau in (("q10", .10), ("q25", .25), ("q50", .50),
                     ("q75", .75), ("q90", .90)):
        pairs = [(float(row[key]), float(row[outcome_key])) for row in records
                 if _finite(row.get(key)) is not None
                 and _finite(row.get(outcome_key)) is not None]
        result[key] = {
            "nominal_coverage": tau, "n": len(pairs),
            "empirical_below_fraction": (
                sum(y <= q for q, y in pairs) / len(pairs) if pairs else None),
            "pinball_loss": pinball_loss(
                [q for q, _ in pairs], [y for _, y in pairs], tau),
        }
    return result


def purged_walk_forward_splits(records: Iterable[dict], *, n_splits: int = 3,
                               embargo_sec: float = 0.0) -> list[dict]:
    """Time-ordered splits with overlapping forecast horizons purged."""
    rows = sorted(
        (dict(row) for row in records if _finite(row.get("prediction_ts")) is not None),
        key=lambda row: float(row["prediction_ts"]),
    )
    if len(rows) < n_splits + 1:
        return []
    block = max(1, len(rows) // (n_splits + 1))
    splits = []
    for fold in range(1, n_splits + 1):
        validation_start = fold * block
        validation_end = len(rows) if fold == n_splits else min(len(rows), (fold + 1) * block)
        validation = rows[validation_start:validation_end]
        if not validation:
            continue
        cutoff = float(validation[0]["prediction_ts"]) - float(embargo_sec)
        train = [row for row in rows[:validation_start]
                 if float(row["prediction_ts"])
                 + max(0.0, float(row.get("horizon_sec") or 0.0)) <= cutoff]
        splits.append({
            "fold": fold,
            "train_indices": [rows.index(row) for row in train],
            "validation_indices": list(range(validation_start, validation_end)),
            "train_end_ts": (float(train[-1]["prediction_ts"]) if train else None),
            "validation_start_ts": float(validation[0]["prediction_ts"]),
            "purged_overlap_count": validation_start - len(train),
            "embargo_sec": float(embargo_sec),
        })
    return splits


def calibration_authority(sample_count: int, event_count: int,
                          effective_independent_n: int | None = None) -> dict:
    effective = int(effective_independent_n or sample_count)
    sufficient = sample_count >= 200 and effective >= 100 and event_count >= 30
    return {
        "version": CALIBRATION_VERSION,
        "q_probability": "published option-implied pricing probability",
        "p_calibrated_shadow": None,
        "status": "shadow_evaluable" if sufficient else "insufficient_evidence",
        "sample_count": int(sample_count), "event_count": int(event_count),
        "effective_independent_n": effective,
        "promotion_allowed": False,
        "production_replacement_allowed": False,
        "sample_count_auto_promotion": False,
        "physical_probability_published": False,
        "identity_baseline": "P=Q",
        "candidate_models": ["platt_logistic", "isotonic_if_sufficient_n"],
    }
