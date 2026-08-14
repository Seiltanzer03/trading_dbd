"""Prospective shadow ledger for EDE v1.3 selective candidates.

Predictions are immutable events created before target resolution. Outcomes are
stored as separate events. Nothing in this module has production trading
authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from seiltanzer.g1_short_horizon_historical_wf import (
    _clip_probability,
    _conditional_probability,
    _weighted_mean,
    _weights,
)
from .filters import FittedCondition, condition_matches

SHADOW_CONTRACT_VERSION = "g1s-ede-prospective-shadow-v1.3"
ACTIVE_MATURITIES = {"RESEARCH_SIGNAL", "PROVISIONAL_EDGE", "ROBUST_EDGE"}
MAX_ASSET_CONCENTRATION = 0.85


def shadow_ledger_path(engine: Any) -> Path:
    override = os.environ.get("SEILTANZER_EDE_SHADOW_LEDGER")
    if override:
        return Path(override)
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research" / "ede_shadow_v13.jsonl"


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _losses(y: float, probability: float) -> tuple[float, float]:
    p = min(1.0-1e-6, max(1e-6, float(probability)))
    return (
        float((p-y)**2),
        float(-(y*math.log(p)+(1.0-y)*math.log(1.0-p))),
    )


def _fitted_condition(payload: dict[str, Any]) -> FittedCondition:
    return FittedCondition(
        feature_id=str(payload["feature_id"]),
        kind=str(payload["kind"]), state=str(payload["state"]),
        lower=payload.get("lower"), upper=payload.get("upper"),
        train_cutoff_ts=payload.get("train_cutoff_ts"),
    )


def _rule_matches(row: dict[str, Any], deployment_rule: list[dict[str, Any]]) -> bool:
    try:
        return all(
            condition_matches(row, _fitted_condition(condition))
            for condition in deployment_rule)
    except (KeyError, TypeError, ValueError):
        return False


class ShadowLedger:
    """Durable append-only JSONL prediction/resolution event ledger."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def events(self, *, cutoff_ts: float | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        output: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if (cutoff_ts is not None
                    and float(event.get("event_ts") or 0.0) > cutoff_ts+1e-6):
                continue
            output.append(event)
        return output

    def _append(self, event: dict[str, Any]) -> bool:
        event_id = str(event["event_id"])
        if any(str(row.get("event_id")) == event_id for row in self.events()):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(event)+"\n")
            handle.flush(); os.fsync(handle.fileno())
        return True

    def append_prediction(self, prediction: dict[str, Any]) -> bool:
        created = float(prediction["prediction_created_ts"])
        target = float(prediction["target_ts"])
        if created >= target-1e-6:
            raise ValueError("shadow prediction must exist before target outcome")
        prediction_id = str(prediction["shadow_prediction_id"])
        return self._append({
            "event": "SHADOW_PREDICTION_CREATED",
            "event_id": "ede-shadow-event-" + _digest({
                "event": "prediction", "prediction_id": prediction_id})[:24],
            "event_ts": created,
            "contract_version": SHADOW_CONTRACT_VERSION,
            "prediction": {
                **prediction,
                "production_authority": False,
                "production_directional_authority": False,
                "auto_promotion": False,
                "may_trigger_exit_or_close": False,
            },
        })

    def append_resolution(self, resolution: dict[str, Any]) -> bool:
        prediction_id = str(resolution["shadow_prediction_id"])
        predictions = {
            str(event["prediction"]["shadow_prediction_id"]): event["prediction"]
            for event in self.events()
            if event.get("event") == "SHADOW_PREDICTION_CREATED"}
        prediction = predictions.get(prediction_id)
        if prediction is None:
            raise ValueError("shadow resolution requires prior immutable prediction")
        if float(resolution["resolved_ts"]) < float(prediction["target_ts"])-1e-6:
            raise ValueError("shadow outcome cannot resolve before target")
        return self._append({
            "event": "SHADOW_RESOLUTION_RECORDED",
            "event_id": "ede-shadow-event-" + _digest({
                "event": "resolution", "prediction_id": prediction_id})[:24],
            "event_ts": float(resolution["resolved_ts"]),
            "contract_version": SHADOW_CONTRACT_VERSION,
            "resolution": {
                **resolution, "production_authority": False,
                "auto_promotion": False,
            },
        })

    def unresolved_predictions(self) -> list[dict[str, Any]]:
        events = self.events()
        resolved = {
            str(event["resolution"]["shadow_prediction_id"])
            for event in events
            if event.get("event") == "SHADOW_RESOLUTION_RECORDED"}
        return [
            event["prediction"] for event in events
            if event.get("event") == "SHADOW_PREDICTION_CREATED"
            and str(event["prediction"]["shadow_prediction_id"]) not in resolved]


def _probability_fit(
    candidate: dict[str, Any], resolved_rows: list[dict[str, Any]], *,
    cutoff_ts: float,
) -> dict[str, Any] | None:
    refit = candidate.get("deployment_refit") or {}
    rule = refit.get("deployment_rule") or []
    horizon = int(candidate.get("horizon_minutes") or 0)
    global_train = [
        row for row in resolved_rows
        if int(row.get("horizon_minutes") or 0) == horizon
        and row.get("outcome_available") and row.get("resolved_ts") is not None
        and float(row["resolved_ts"]) <= cutoff_ts+1e-6
        and row.get("direction_label") in {"UP", "DOWN"}]
    conditional_train = [row for row in global_train if _rule_matches(row, rule)]
    if len(global_train) < 80 or len(conditional_train) < 20:
        return None
    global_y = np.asarray([
        1.0 if row["direction_label"] == "UP" else 0.0 for row in global_train], dtype=float)
    global_weights, _ = _weights(global_train)
    conditional_y = np.asarray([
        1.0 if row["direction_label"] == "UP" else 0.0
        for row in conditional_train], dtype=float)
    conditional_weights, _ = _weights(conditional_train)
    base_rate = _clip_probability(_weighted_mean(global_y, global_weights))
    global_negative, global_positive = _conditional_probability(
        global_train, global_y, global_weights, "ret_5m")
    conditional_negative, conditional_positive = _conditional_probability(
        conditional_train, conditional_y, conditional_weights, "ret_5m")
    return {
        "fit_cutoff_ts": cutoff_ts,
        "global_train_raw": len(global_train),
        "conditional_train_raw": len(conditional_train),
        "global_base_rate": float(base_rate),
        "global_ret5": {"negative": float(global_negative), "positive": float(global_positive)},
        "conditional_ret5": {
            "negative": float(conditional_negative), "positive": float(conditional_positive)},
    }


def _candidate_gate(
    candidate: dict[str, Any], research_candidate: dict[str, Any] | None,
) -> bool:
    if str(candidate.get("edge_maturity")) not in ACTIVE_MATURITIES:
        return False
    if not (candidate.get("deployment_refit") or {}).get("deployment_rule"):
        return False
    if research_candidate is None:
        return False
    practical = research_candidate.get("practical_coverage") or {}
    if practical.get("status") == "LOW_PRACTICAL_COVERAGE":
        return False
    concentration = float(
        (research_candidate.get("asset_distribution") or {}).get(
            "max_asset_concentration") or 0.0)
    return concentration <= MAX_ASSET_CONCENTRATION


def create_shadow_predictions(
    ledger: ShadowLedger, *, frozen_evidence: dict[str, Any],
    selective_report: dict[str, Any], resolved_rows: list[dict[str, Any]],
    pending_rows: list[dict[str, Any]], created_ts: float | None = None,
) -> dict[str, Any]:
    """Create only predictions whose target is still in the future."""
    now = float(created_ts or time.time())
    research_by_id = {
        str(item.get("candidate_id")): item
        for item in selective_report.get("top_20_research_candidates") or []}
    created = 0; considered = 0; skipped_late = 0; skipped_gate = 0
    for candidate in frozen_evidence.get("edge_candidates") or []:
        candidate_id = str(candidate.get("candidate_id"))
        research_candidate = research_by_id.get(candidate_id)
        if not _candidate_gate(candidate, research_candidate):
            skipped_gate += 1
            continue
        fit = _probability_fit(candidate, resolved_rows, cutoff_ts=now)
        if fit is None:
            skipped_gate += 1
            continue
        refit = candidate["deployment_refit"]
        rule = refit["deployment_rule"]
        required_ids = list(refit.get("feature_ids") or [])
        horizon = int(candidate["horizon_minutes"])
        for row in pending_rows:
            if int(row.get("horizon_minutes") or 0) != horizon:
                continue
            considered += 1
            target = float(row["target_ts"])
            if now >= target-1e-6:
                skipped_late += 1
                continue
            if float(row["captured_ts"]) > now+1e-6 or not _rule_matches(row, rule):
                continue
            ret5 = (row.get("features") or {}).get("ret_5m")
            if ret5 is None:
                continue
            side = "positive" if float(ret5) > 0.0 else "negative"
            baseline_probability = float(fit["global_ret5"][side])
            candidate_probability = float(fit["conditional_ret5"][side])
            observation_id = str(row["observation_id"])
            prediction_id = "ede-shadow-" + _digest({
                "candidate_id": candidate_id,
                "observation_id": observation_id,
                "rule_version": refit.get("rule_version"),
            })[:28]
            prediction = {
                "shadow_prediction_id": prediction_id,
                "candidate_id": candidate_id,
                "hypothesis_id": candidate.get("hypothesis_id"),
                "observation_id": observation_id,
                "instrument": str(row["instrument"]),
                "horizon_minutes": horizon,
                "prediction_created_ts": now,
                "t0": float(row["captured_ts"]), "target_ts": target,
                "deployment_rule_version": refit.get("rule_version"),
                "deployment_rule": rule, "conditions_at_t0": rule,
                "feature_values_at_t0": {
                    feature_id: (row.get("ede_features") or {}).get(feature_id)
                    for feature_id in required_ids},
                "baseline_probability": baseline_probability,
                "candidate_probability": candidate_probability,
                "expected_direction": "UP" if candidate_probability >= 0.5 else "DOWN",
                "probability_fit": fit,
                "data_maturity": research_candidate.get("data_maturity"),
                "edge_maturity": candidate.get("edge_maturity"),
                "research_rank": (research_candidate.get("research_rank") or {}).get("score"),
                "status": "SHADOW_ACTIVE",
            }
            if ledger.append_prediction(prediction):
                created += 1
    return {
        "created": created,
        "considered_pending_candidate_rows": considered,
        "skipped_late": skipped_late, "skipped_gate": skipped_gate,
        "contract_version": SHADOW_CONTRACT_VERSION,
    }


def resolve_shadow_predictions(
    ledger: ShadowLedger, *, resolved_rows: list[dict[str, Any]],
    asof_ts: float | None = None,
) -> dict[str, Any]:
    now = float(asof_ts or time.time())
    by_observation = {
        str(row["observation_id"]): row for row in resolved_rows
        if row.get("outcome_available") and row.get("resolved_ts") is not None}
    appended = 0
    for prediction in ledger.unresolved_predictions():
        row = by_observation.get(str(prediction["observation_id"]))
        if row is None:
            continue
        resolved_ts = float(row["resolved_ts"])
        if resolved_ts > now+1e-6:
            continue
        label = str(row.get("direction_label"))
        if label not in {"UP", "DOWN"}:
            continue
        y = 1.0 if label == "UP" else 0.0
        candidate_brier, candidate_logloss = _losses(
            y, float(prediction["candidate_probability"]))
        baseline_brier, baseline_logloss = _losses(
            y, float(prediction["baseline_probability"]))
        resolution = {
            "shadow_prediction_id": prediction["shadow_prediction_id"],
            "candidate_id": prediction["candidate_id"],
            "observation_id": prediction["observation_id"],
            "resolved_ts": resolved_ts, "actual_direction": label,
            "actual_return": row.get("terminal_log_return"),
            "candidate_brier": candidate_brier, "baseline_brier": baseline_brier,
            "candidate_logloss": candidate_logloss, "baseline_logloss": baseline_logloss,
            "delta_brier": baseline_brier-candidate_brier,
            "delta_logloss": baseline_logloss-candidate_logloss,
            "mfe_log_return": row.get("mfe_log_return"),
            "mae_log_return": row.get("mae_log_return"),
        }
        if ledger.append_resolution(resolution):
            appended += 1
    return {
        "resolved": appended,
        "remaining_pending": len(ledger.unresolved_predictions()),
        "contract_version": SHADOW_CONTRACT_VERSION,
    }


def _window(rows: list[dict[str, Any]], size: int | None) -> dict[str, Any]:
    selected = rows[-size:] if size is not None else rows
    if not selected:
        return {
            "n": 0, "delta_brier": None, "delta_logloss": None,
            "candidate_brier": None, "baseline_brier": None,
            "candidate_logloss": None, "baseline_logloss": None,
            "hit_rate": None,
        }
    return {
        "n": len(selected),
        "delta_brier": float(np.mean([row["delta_brier"] for row in selected])),
        "delta_logloss": float(np.mean([row["delta_logloss"] for row in selected])),
        "candidate_brier": float(np.mean([row["candidate_brier"] for row in selected])),
        "baseline_brier": float(np.mean([row["baseline_brier"] for row in selected])),
        "candidate_logloss": float(np.mean([row["candidate_logloss"] for row in selected])),
        "baseline_logloss": float(np.mean([row["baseline_logloss"] for row in selected])),
        "hit_rate": float(np.mean([
            (row["candidate_probability"] >= 0.5) == (row["actual_direction"] == "UP")
            for row in selected])),
    }


def _lifecycle(
    all_metrics: dict[str, Any], last25: dict[str, Any], last50: dict[str, Any],
) -> str:
    n = int(all_metrics["n"])
    if n < 25:
        return "SHADOW_ACTIVE"
    all_positive = (
        float(all_metrics["delta_brier"] or 0.0) > 0.0
        and float(all_metrics["delta_logloss"] or 0.0) > 0.0)
    recent25_positive = (
        float(last25["delta_brier"] or 0.0) > 0.0
        and float(last25["delta_logloss"] or 0.0) > 0.0)
    recent50_positive = (
        float(last50["delta_brier"] or 0.0) > 0.0
        and float(last50["delta_logloss"] or 0.0) > 0.0)
    if n >= 100 and all_positive and recent50_positive:
        return "SHADOW_CONFIRMED"
    if n >= 50 and not all_positive:
        return "SHADOW_FAILED"
    if all_positive and not recent25_positive:
        return "SHADOW_WEAKENING"
    if all_positive:
        return "SHADOW_PROMISING"
    return "SHADOW_ACTIVE"


def shadow_summary(
    ledger: ShadowLedger, *, cutoff_ts: float | None = None,
) -> dict[str, Any]:
    events = ledger.events(cutoff_ts=cutoff_ts)
    predictions = {
        str(event["prediction"]["shadow_prediction_id"]): event["prediction"]
        for event in events if event.get("event") == "SHADOW_PREDICTION_CREATED"}
    resolutions = [
        event["resolution"] for event in events
        if event.get("event") == "SHADOW_RESOLUTION_RECORDED"]
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for resolution in resolutions:
        prediction = predictions.get(str(resolution["shadow_prediction_id"]))
        if prediction is None:
            continue
        row = {**resolution, **{
            "candidate_probability": prediction["candidate_probability"],
            "baseline_probability": prediction["baseline_probability"],
            "prediction_created_ts": prediction["prediction_created_ts"],
        }}
        by_candidate.setdefault(str(resolution["candidate_id"]), []).append(row)
    candidates: dict[str, Any] = {}
    for candidate_id, rows in by_candidate.items():
        rows.sort(key=lambda row: float(row["resolved_ts"]))
        all_metrics = _window(rows, None)
        last25 = _window(rows, 25); last50 = _window(rows, 50); last100 = _window(rows, 100)
        candidates[candidate_id] = {
            "candidate_id": candidate_id,
            "status": _lifecycle(all_metrics, last25, last50),
            "last_25": last25, "last_50": last50, "last_100": last100,
            "all_prospective": all_metrics,
            "production_authority": False, "auto_promotion": False,
        }
    resolved_ids = {str(row["shadow_prediction_id"]) for row in resolutions}
    pending = [
        prediction for prediction_id, prediction in predictions.items()
        if prediction_id not in resolved_ids]
    return {
        "contract_version": SHADOW_CONTRACT_VERSION,
        "prediction_count": len(predictions), "resolved_count": len(resolutions),
        "pending_count": len(pending),
        "candidate_count": len({str(item["candidate_id"]) for item in predictions.values()}),
        "candidates": candidates,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }


def candidate_shadow_summary(
    path: str | Path, candidate_id: str, *, cutoff_ts: float | None = None,
) -> dict[str, Any] | None:
    summary = shadow_summary(ShadowLedger(path), cutoff_ts=cutoff_ts)
    return (summary.get("candidates") or {}).get(str(candidate_id))
