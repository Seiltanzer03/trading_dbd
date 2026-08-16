"""Paired champion/challenger diagnostics on shared prospective OOS observations.

A challenger is never allowed to win by being evaluated on an easier time slice.
Only resolved records sharing instrument, T0, target timestamp, target and horizon
with the frozen champion enter comparison. Results are evidence only and cannot
replace the champion automatically.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .prospective_confirmation import EVIDENCE_LABEL, ProspectiveConfirmationLedger
from .universal_target_scoring import (
    UniversalTargetSpec,
    paired_target_pvalue,
    relative_target_improvement,
    target_metrics,
)


PROSPECTIVE_COMPARISON_CONTRACT_VERSION = "g1s-prospective-champion-challenger-v2"


def _resolved_by_key(
    ledger: ProspectiveConfirmationLedger, validation_cohort_id: str,
) -> dict[tuple[Any, ...], dict[str, Any]]:
    events = ledger.cohort_events(validation_cohort_id)
    predictions = {
        str(event["record_id"]): event
        for event in events if event.get("event") == "PREDICTION"
    }
    outcomes = {
        str(event["record_id"]): event
        for event in events if event.get("event") == "OUTCOME"
    }
    output: dict[tuple[Any, ...], dict[str, Any]] = {}
    for record_id, prediction in predictions.items():
        outcome = outcomes.get(record_id)
        if outcome is None:
            continue
        if prediction.get("evidence_label") != EVIDENCE_LABEL:
            continue
        if outcome.get("evidence_label") != EVIDENCE_LABEL:
            continue
        key = (
            str(prediction.get("instrument")),
            float(prediction["t0"]),
            float(prediction["target_ts"]),
            str(prediction.get("target_id")),
            int(prediction.get("horizon_minutes") or 0),
        )
        if key in output:
            raise ValueError(
                "duplicate resolved prospective T0 inside validation cohort")
        output[key] = {
            "prediction": prediction,
            "outcome": outcome["outcome"],
        }
    return output


def _prediction_array(values: list[Any], spec: UniversalTargetSpec) -> np.ndarray:
    if spec.kind == "MULTICLASS":
        arrays = [np.asarray(value, dtype=float) for value in values]
        if any(array.ndim != 1 or len(array) != len(spec.classes)
               for array in arrays):
            raise ValueError("multiclass prospective prediction has invalid shape")
        return np.vstack(arrays)
    return np.asarray([float(value) for value in values], dtype=float)


def compare_shared_prospective_oos(
    ledger: ProspectiveConfirmationLedger, *, champion_cohort_id: str,
    challenger_cohort_id: str, spec: UniversalTargetSpec,
) -> dict[str, Any]:
    champion = _resolved_by_key(ledger, champion_cohort_id)
    challenger = _resolved_by_key(ledger, challenger_cohort_id)
    shared = sorted(
        set(champion).intersection(challenger), key=lambda key: (key[1], key[0]))

    rows: list[dict[str, Any]] = []
    champion_values: list[Any] = []
    challenger_values: list[Any] = []
    for key in shared:
        left = champion[key]
        right = challenger[key]
        if left["outcome"] != right["outcome"]:
            raise ValueError(
                "shared prospective T0 has inconsistent recorded outcome")
        instrument, t0, target_ts, target_id, horizon = key
        if target_id != spec.target_id:
            raise ValueError(
                "prospective comparison target does not match target spec")
        rows.append({
            "instrument": instrument,
            "captured_ts": t0,
            "target_ts": target_ts,
            "horizon_minutes": horizon,
            "universal_target_id": target_id,
            "universal_target_value": left["outcome"],
        })
        champion_values.append(left["prediction"]["prediction"])
        challenger_values.append(right["prediction"]["prediction"])

    if not rows:
        return {
            "contract_version": PROSPECTIVE_COMPARISON_CONTRACT_VERSION,
            "target_id": spec.target_id,
            "champion_cohort_id": champion_cohort_id,
            "challenger_cohort_id": challenger_cohort_id,
            "champion_resolved_n": len(champion),
            "challenger_resolved_n": len(challenger),
            "shared_resolved_n": 0,
            "shared_coverage_vs_champion": 0.0,
            "evidence_label": EVIDENCE_LABEL,
            "comparison_available": False,
            "reason": "NO_SHARED_RESOLVED_PROSPECTIVE_T0",
            "production_authority": False,
            "auto_promotion": False,
            "automatic_champion_replacement": False,
        }

    champion_prediction = _prediction_array(champion_values, spec)
    challenger_prediction = _prediction_array(challenger_values, spec)
    champion_metrics = target_metrics(rows, champion_prediction, spec)
    challenger_metrics = target_metrics(rows, challenger_prediction, spec)
    improvement = relative_target_improvement(
        challenger_metrics, champion_metrics, spec)
    p_value = paired_target_pvalue(
        rows, challenger_prediction, champion_prediction, spec)
    return {
        "contract_version": PROSPECTIVE_COMPARISON_CONTRACT_VERSION,
        "target_id": spec.target_id,
        "target_kind": spec.kind,
        "champion_cohort_id": champion_cohort_id,
        "challenger_cohort_id": challenger_cohort_id,
        "champion_resolved_n": len(champion),
        "challenger_resolved_n": len(challenger),
        "shared_resolved_n": len(rows),
        "shared_coverage_vs_champion": len(rows)/max(1, len(champion)),
        "champion": champion_metrics,
        "challenger": challenger_metrics,
        "challenger_relative_improvement": improvement,
        "paired_dependency_p_value": float(p_value),
        "evidence_label": EVIDENCE_LABEL,
        "comparison_available": True,
        "same_t0_only": True,
        "historical_discovery_evidence_counted": False,
        "production_authority": False,
        "auto_promotion": False,
        "automatic_champion_replacement": False,
    }
