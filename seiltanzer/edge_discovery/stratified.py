"""Post-selection stratified diagnostics for EDE v1.3."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import _historical_folds

from .ablation import family_ablation
from .discovery import _predictions
from .filters import FittedCondition, FittedRule, condition_matches
from .scoring import metrics

STRATIFIED_CONTRACT_VERSION = "g1s-ede-stratified-diagnostics-v1.3.3"
MIN_STRATUM_RAW = 20
MIN_STRATUM_EFFECTIVE = 10


def _deserialize_rule(payload: dict[str, Any]) -> FittedRule | None:
    try:
        return FittedRule(
            str(payload["template_id"]),
            tuple(FittedCondition(
                feature_id=str(item["feature_id"]), kind=str(item["kind"]),
                state=str(item["state"]), lower=item.get("lower"),
                upper=item.get("upper"), train_cutoff_ts=item.get("train_cutoff_ts"),
            ) for item in payload.get("conditions") or []),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _matches(row: dict[str, Any], rule: FittedRule) -> bool:
    return all(condition_matches(row, condition) for condition in rule.conditions)


def _candidate_prediction_records(candidate: dict[str, Any], rows: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    if int(candidate.get("inner_primary_folds") or 0) <= 0:
        return []
    fold_map = {int(fold["fold_index"]): fold for fold in _historical_folds(rows, horizon)}
    records: dict[str, dict[str, Any]] = {}
    for candidate_fold, rule_payload in zip(candidate.get("folds") or [], candidate.get("thresholds") or []):
        if candidate_fold.get("inner_selection_source") != "PRIMARY_FDR_PASS":
            continue
        fold = fold_map.get(int(candidate_fold.get("fold_index") or -1))
        rule = _deserialize_rule(rule_payload)
        if fold is None or rule is None:
            continue
        selected_train = [row for row in fold["train"] if _matches(row, rule)]
        selected_test = [row for row in fold["test"] if _matches(row, rule)]
        if len(selected_train) < 100 or len(selected_test) < 20:
            continue
        predictions = _predictions(fold["train"], selected_test, conditional_train=selected_train)
        for row, candidate_p, baseline_p in zip(
            selected_test,
            predictions["conditional_ret5_persistence"],
            predictions["global_ret5_persistence"],
        ):
            key = str(row.get("observation_id") or f"{row.get('instrument')}:{row.get('captured_ts')}:{horizon}")
            records[key] = {
                "row": row,
                "candidate_probability": float(candidate_p),
                "baseline_probability": float(baseline_p),
            }
    return sorted(records.values(), key=lambda item: (
        float(item["row"]["captured_ts"]), str(item["row"]["instrument"])))


def _stratum_value(row: dict[str, Any], dimension: str) -> str | None:
    ede = row.get("ede_features") or {}
    if dimension == "instrument":
        return str(row.get("instrument")) if row.get("instrument") is not None else None
    keys = {
        "session": ("regime.session_utc", "session_utc"),
        "trend_regime": ("regime.trend",),
        "volatility_regime": ("regime.volatility",),
        "macro_regime": ("regime.macro",),
        "wavelet_phase": ("regime.wavelet_phase",),
    }
    for key in keys.get(dimension, ()):
        value = ede.get(key)
        if value is not None:
            return str(value)
    return None


def _score(records: list[dict[str, Any]], total_n: int) -> dict[str, Any]:
    rows = [item["row"] for item in records]
    conditional = metrics(rows, np.asarray([item["candidate_probability"] for item in records], dtype=float))
    baseline = metrics(rows, np.asarray([item["baseline_probability"] for item in records], dtype=float))
    brier_delta = float(baseline["brier"] - conditional["brier"])
    logloss_delta = float(baseline["logloss"] - conditional["logloss"])
    raw_n = len(rows)
    effective_n = int(conditional["effective_n"])
    return {
        "raw_n": raw_n, "effective_n": effective_n,
        "share_of_candidate": raw_n / max(1, total_n),
        "delta_brier": brier_delta, "delta_logloss": logloss_delta,
        "joint_positive": brier_delta > 0.0 and logloss_delta > 0.0,
        "joint_negative": brier_delta < 0.0 and logloss_delta < 0.0,
        "descriptive_ready": raw_n >= MIN_STRATUM_RAW and effective_n >= MIN_STRATUM_EFFECTIVE,
        "selection_use": False, "edge_maturity_use": False,
    }


def _dimension(records: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        value = _stratum_value(item["row"], name)
        if value is not None:
            grouped[value].append(item)
    output = [{"dimension": name, "value": value, **_score(items, len(records))}
              for value, items in sorted(grouped.items())]
    output.sort(key=lambda item: (
        -int(bool(item["descriptive_ready"])),
        -float(item["delta_brier"] + item["delta_logloss"]), str(item["value"])))
    return output


def _asset_stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row["descriptive_ready"]]
    positive = [row for row in eligible if row["joint_positive"]]
    negative = [row for row in eligible if row["joint_negative"]]
    n = len(eligible)
    if n < 2:
        status = "INSUFFICIENT_ASSET_DIVERSITY"
    elif len(positive) / n >= 2.0 / 3.0:
        status = "BROAD_POSITIVE"
    elif len(negative) / n >= 2.0 / 3.0:
        status = "BROAD_NEGATIVE"
    else:
        status = "MIXED"
    return {
        "status": status, "evaluated_instruments": n,
        "positive_instruments": len(positive), "negative_instruments": len(negative),
        "positive_share": len(positive) / n if n else None,
        "negative_share": len(negative) / n if n else None,
        "descriptive_only": True, "selection_use": False, "edge_maturity_use": False,
    }


def candidate_stratified_diagnostics(candidate: dict[str, Any], rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    records = _candidate_prediction_records(candidate, rows, horizon)
    dimensions = {name: _dimension(records, name) for name in (
        "instrument", "session", "trend_regime", "volatility_regime",
        "macro_regime", "wavelet_phase")}
    return {
        "contract_version": STRATIFIED_CONTRACT_VERSION,
        "primary_outer_prediction_rows": len(records),
        "dimensions": dimensions,
        "cross_instrument_stability": _asset_stability(dimensions["instrument"]),
        "post_selection_descriptive_only": True,
        "used_for_selection": False, "used_for_edge_maturity": False,
        "production_authority": False,
    }


def _contexts(report: dict[str, Any], positive: bool) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for horizon in report.get("horizons") or []:
        for candidate in horizon.get("candidates") or []:
            diagnostics = candidate.get("stratified_diagnostics") or {}
            for dimension, rows in (diagnostics.get("dimensions") or {}).items():
                for row in rows:
                    if not row.get("descriptive_ready"):
                        continue
                    if positive and not row.get("joint_positive"):
                        continue
                    if not positive and not row.get("joint_negative"):
                        continue
                    output.append({
                        "candidate_id": candidate.get("candidate_id"),
                        "hypothesis_id": candidate.get("hypothesis_id"),
                        "horizon_minutes": candidate.get("horizon_minutes"),
                        "feature_ids": [item.get("feature_id") for item in candidate.get("template") or []],
                        "edge_maturity": candidate.get("edge_maturity"),
                        "dimension": dimension, **row,
                    })
    output.sort(key=lambda item: (
        -(float(item["delta_brier"]) + float(item["delta_logloss"]))
        if positive else float(item["delta_brier"]) + float(item["delta_logloss"]),
        str(item.get("candidate_id"))))
    return output


def augment_selective_report_with_strata(report: dict[str, Any], prospective_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in prospective_rows:
        by_horizon[int(row["horizon_minutes"])].append(row)
    for horizon in report.get("horizons") or []:
        h = int(horizon.get("horizon_minutes") or 0)
        for candidate in horizon.get("candidates") or []:
            candidate["stratified_diagnostics"] = candidate_stratified_diagnostics(candidate, by_horizon[h], h)
    report["stratified_diagnostics_contract"] = {
        "version": STRATIFIED_CONTRACT_VERSION,
        "minimum_stratum_raw": MIN_STRATUM_RAW,
        "minimum_stratum_effective": MIN_STRATUM_EFFECTIVE,
        "primary_outer_folds_only": True,
        "post_selection_descriptive_only": True,
        "used_for_selection": False, "used_for_edge_maturity": False,
    }
    report["where_it_helps_contexts"] = _contexts(report, True)[:30]
    report["where_it_hurts_contexts"] = _contexts(report, False)[:30]
    report["family_ablation_by_horizon"] = {
        str(int(horizon.get("horizon_minutes") or 0)): {
            **family_ablation({"horizons": [horizon]}),
            "interpretation": "best bounded conditional candidate within each family envelope; not a separate multivariate refit",
        }
        for horizon in report.get("horizons") or []
    }
    report["cross_instrument_stability"] = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "horizon_minutes": candidate.get("horizon_minutes"),
            **((candidate.get("stratified_diagnostics") or {}).get("cross_instrument_stability") or {}),
        }
        for horizon in report.get("horizons") or []
        for candidate in horizon.get("candidates") or []
    ][:50]
    return report
