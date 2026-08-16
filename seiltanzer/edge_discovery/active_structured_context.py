"""Extra compact target-direction metadata for the active structured edge report.

The underlying discovery/scoring exam is unchanged. This wrapper only preserves
the mean OOS candidate-vs-structural-baseline target shift that PASS 5 already
computed, so downstream AI context can understand whether a discovered state
points up/down, expands/compresses volatility, or changes first-touch geometry.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _weights

from .active_edge_policy import run_active_structured_discovery
from .rates import RatesState


def _shift(occurrences: list[dict[str, Any]], spec: Any) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    model_parts: list[np.ndarray] = []
    baseline_parts: list[np.ndarray] = []
    for occurrence in occurrences:
        evaluation = occurrence.get("evaluation") or {}
        rows.extend(evaluation.get("rows") or [])
        if evaluation.get("model_prediction") is None or evaluation.get("baseline_prediction") is None:
            continue
        model_parts.append(np.asarray(evaluation["model_prediction"], dtype=float))
        baseline_parts.append(np.asarray(evaluation["baseline_prediction"], dtype=float))
    if not rows or not model_parts or len(model_parts) != len(baseline_parts):
        return None
    model = np.concatenate(model_parts, axis=0)
    baseline = np.concatenate(baseline_parts, axis=0)
    weights, _effective = _weights(rows)
    denominator = max(float(np.sum(weights)), 1e-12)

    if str(spec.kind) == "MULTICLASS":
        delta = model-baseline
        weighted = np.sum(delta*weights[:, None], axis=0)/denominator
        classes = {str(name): float(value) for name, value in zip(spec.classes, weighted)}
        strongest = max(classes, key=lambda name: abs(classes[name])) if classes else None
        return {
            "kind": "MULTICLASS_PROBABILITY_SHIFT",
            "classes": classes,
            "strongest_class": strongest,
            "strongest_shift": classes.get(strongest) if strongest else None,
        }

    delta = np.asarray(model-baseline, dtype=float).reshape(-1)
    weighted = float(np.sum(delta*weights)/denominator)
    target = str(spec.target_id)
    if target == "DIRECTION":
        interpretation = "MORE_UP" if weighted > 0 else "MORE_DOWN" if weighted < 0 else "NEUTRAL"
    elif target == "RETURN_SIGMA":
        interpretation = "MORE_UPSIDE_RETURN" if weighted > 0 else "MORE_DOWNSIDE_RETURN" if weighted < 0 else "NEUTRAL"
    elif target == "MFE_SIGMA":
        interpretation = "MORE_UPSIDE_EXCURSION" if weighted > 0 else "LESS_UPSIDE_EXCURSION" if weighted < 0 else "NEUTRAL"
    elif target == "MAE_SIGMA":
        interpretation = "LESS_DOWNSIDE_EXCURSION" if weighted > 0 else "MORE_DOWNSIDE_EXCURSION" if weighted < 0 else "NEUTRAL"
    elif target == "FORWARD_VOL_RATIO":
        interpretation = "VOL_EXPANSION" if weighted > 0 else "VOL_COMPRESSION" if weighted < 0 else "NEUTRAL"
    else:
        interpretation = "POSITIVE_SHIFT" if weighted > 0 else "NEGATIVE_SHIFT" if weighted < 0 else "NEUTRAL"
    return {
        "kind": "SCALAR_TARGET_SHIFT",
        "candidate_minus_structural_baseline": weighted,
        "interpretation": interpretation,
    }


def run_active_structured_discovery_with_context(
    sources: list[dict[str, Any]], *, source_set_sha256: str,
    rates_states: Iterable[RatesState] = (), horizons: Iterable[int],
) -> dict[str, Any]:
    from . import universal_structured_discovery as structured

    original = structured._aggregate_candidate

    def aggregate(template_id, occurrences, spec, *, horizon):
        candidate = original(template_id, occurrences, spec, horizon=horizon)
        target_shift = _shift(occurrences, spec)
        if target_shift is not None:
            candidate["prediction_shift"] = target_shift
        return candidate

    structured._aggregate_candidate = aggregate
    try:
        return run_active_structured_discovery(
            sources,
            source_set_sha256=source_set_sha256,
            rates_states=rates_states,
            horizons=horizons,
        )
    finally:
        structured._aggregate_candidate = original
