"""Phase G.1B no-lookahead and dependency-accounting refinements.

The first baseline layer deliberately exposed task-level metrics. This refinement
keeps those diagnostics but makes top-level evidence N match G.1A aggregate
anchor/window dependence and makes the historical base-rate baseline use an
outcome only after its recorded resolved_ts was available.
"""
from __future__ import annotations

from collections import defaultdict
import math

from . import g1_baseline_runtime as _b
from . import g1_dataset_runtime as _g1

_REFINEMENT_VERSION = "g1b-integrity-refinement-v1"
_ORIGINAL_REPORT_FOR_ROWS = _b._report_for_rows


def _strict_prequential_base_rate(rows: list[dict]):
    """Cohort-local base rate using only outcomes resolved before each T0."""
    histories: dict[str, list[dict]] = defaultdict(list)
    probabilities: list[float] = []
    outcomes: list[int] = []
    history_before: list[int] = []
    unavailable_prior_n = 0
    missing_resolved_ts_n = 0

    ordered = sorted(
        rows,
        key=lambda item: (
            float(item.get("captured_ts") or 0.0),
            str(item.get("base_cohort_id")),
            str(item.get("observation_id")),
        ),
    )
    for row in ordered:
        y = _b._future_direction(row)
        if y is None:
            continue
        cohort_id = str(row.get("base_cohort_id"))
        captured_ts = _b._finite(row.get("captured_ts"))
        if captured_ts is None:
            continue
        available = []
        for prior in histories[cohort_id]:
            resolved_ts = _b._finite(prior.get("resolved_ts"))
            if resolved_ts is None:
                missing_resolved_ts_n += 1
                continue
            if resolved_ts <= captured_ts + 1e-9:
                available.append(prior)
            else:
                unavailable_prior_n += 1
        successes = sum(int(item["outcome"]) for item in available)
        n = len(available)
        probability = (successes + _b.BASE_RATE_ALPHA) / (
            n + 2.0 * _b.BASE_RATE_ALPHA
        )
        probabilities.append(probability)
        outcomes.append(y)
        history_before.append(n)
        histories[cohort_id].append({
            "outcome": y,
            "resolved_ts": row.get("resolved_ts"),
            "observation_id": row.get("observation_id"),
        })

    return probabilities, outcomes, {
        "contract_version": "g1-prequential-base-rate-resolved-time-v2",
        "supersedes": _b.G1_BASE_RATE_CONTRACT_VERSION,
        "alpha": _b.BASE_RATE_ALPHA,
        "cohort_local": True,
        "past_only": True,
        "availability_basis": "prior_resolved_ts_lte_current_captured_ts",
        "random_shuffle": False,
        "cold_start_probability": 0.5,
        "cold_start_n": sum(1 for value in history_before if value == 0),
        "min_history_before_prediction": min(history_before) if history_before else None,
        "max_history_before_prediction": max(history_before) if history_before else None,
        "unavailable_prior_comparisons_n": unavailable_prior_n,
        "missing_resolved_ts_comparisons_n": missing_resolved_ts_n,
    }


def _aggregate_dependency_manifest(rows: list[dict]) -> tuple[int, str, list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("instrument")), str(row.get("dependency_group_id")))].append(row)

    by_instrument: dict[str, list[dict]] = defaultdict(list)
    for (instrument, dependency_group_id), members in grouped.items():
        by_instrument[instrument].append({
            "instrument": instrument,
            "dependency_group_id": dependency_group_id,
            "captured_ts": min(float(item["captured_ts"]) for item in members),
            "target_ts": max(float(item["target_ts"]) for item in members),
            "members": [
                {
                    "observation_id": str(item.get("observation_id")),
                    "source_record_sha256": str(item.get("source_record_sha256") or ""),
                    "cohort_id": str(item.get("base_cohort_id") or ""),
                }
                for item in sorted(members, key=lambda value: str(value.get("observation_id")))
            ],
        })

    selected: list[dict] = []
    for instrument in sorted(by_instrument):
        last_end = -math.inf
        for interval in sorted(
            by_instrument[instrument],
            key=lambda item: (
                float(item["captured_ts"]),
                float(item["target_ts"]),
                str(item["dependency_group_id"]),
            ),
        ):
            if float(interval["captured_ts"]) >= last_end - 1e-9:
                selected.append(interval)
                last_end = float(interval["target_ts"])

    payload = {
        "contract_version": _g1.G1_EFFECTIVE_N_CONTRACT_VERSION,
        "scope": "aggregate_instrument_dependency_nonoverlap",
        "selected_dependency_intervals": selected,
    }
    return len(selected), _b._sha256(payload), selected


def _report_for_rows_refined(rows: list[dict], source_meta: dict) -> dict:
    result = dict(_ORIGINAL_REPORT_FOR_ROWS(rows, source_meta))
    metric_task_n = int(result.get("effective_n") or 0)
    metric_task_manifest = result.get("sample_manifest_sha256")
    aggregate_n, aggregate_manifest, selected = _aggregate_dependency_manifest(rows)

    first_ts = min((float(row["captured_ts"]) for row in rows), default=None)
    last_ts = max((float(row["captured_ts"]) for row in rows), default=None)
    span_days = (
        max(0.0, (last_ts - first_ts) / 86400.0)
        if first_ts is not None and last_ts is not None else 0.0
    )
    # Cross-check against the authoritative G.1A counting implementation. Fail
    # closed in telemetry rather than silently reporting two definitions of N.
    authoritative_n = _g1._effective_n_nonoverlap(rows, aggregate=True)
    mismatch = aggregate_n != authoritative_n

    direction = dict(result.get("directional_baselines") or {})
    direction.update({
        "metric_scope": "pooled_cohort_local_task_diagnostic",
        "pooled_metric_task_n": metric_task_n,
        "system_independent_effective_n": authoritative_n,
        "independence_note": (
            "pooled metric task rows can include different horizon/cohort tasks from the same T0; "
            "system evidence status uses aggregate dependency non-overlap N"
        ),
        "edge_claim": False,
    })
    result["directional_baselines"] = direction

    fixed = dict(result.get("fixed_horizon_reference") or {})
    fixed["effective_n_scope"] = "cohort_task_nonoverlap_not_system_independence"
    result["fixed_horizon_reference"] = fixed

    q = dict(result.get("terminal_q_identity") or {})
    q["effective_n_scope"] = "Q_eligible_cohort_task_nonoverlap"
    result["terminal_q_identity"] = q

    result.update({
        "g1b_integrity_refinement_version": _REFINEMENT_VERSION,
        "pooled_metric_task_n": metric_task_n,
        "pooled_metric_task_manifest_sha256": metric_task_manifest,
        "effective_n": authoritative_n,
        "effective_n_scope": "G1A aggregate instrument/dependency nonoverlap",
        "aggregate_evidence_manifest_sha256": aggregate_manifest,
        "sample_manifest_sha256": aggregate_manifest,
        "sample_manifest_scope": "aggregate_dependency_evidence",
        "selected_dependency_interval_n": len(selected),
        "effective_n_contract_mismatch": mismatch,
        "evidence_status": (
            "INSUFFICIENT" if mismatch
            else _b._metric_evidence_status(authoritative_n, span_days)
        ),
        "evidence_status_scope": "aggregate_dependency_baseline_measurement_only_not_edge_claim",
    })
    return result


def install_g1_baseline_refinement() -> None:
    if getattr(_b._ENGINE, "_g1_baseline_refinement", None) == _REFINEMENT_VERSION:
        return
    _b._prequential_base_rate = _strict_prequential_base_rate
    _b._report_for_rows = _report_for_rows_refined
    _b._ENGINE._g1_baseline_refinement = _REFINEMENT_VERSION
