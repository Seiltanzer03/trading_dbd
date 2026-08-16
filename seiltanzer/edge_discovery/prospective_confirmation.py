"""Append-only prospective confirmation ledger for frozen EDE candidates.

Discovery and confirmation are separate experiments. A confirmatory prediction
must physically exist after T0 and before its outcome can exist, and its T0 must
be strictly after both the training cutoff and immutable freeze boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .candidate_registry import CandidateRegistry


PROSPECTIVE_CONFIRMATION_CONTRACT_VERSION = "g1s-universal-prospective-confirmation-v2"
EVIDENCE_LABEL = "LIVE_PROSPECTIVE_OOS"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _validate_numeric_tree(value: Any, *, name: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} contains non-finite numeric value")
        return
    if isinstance(value, str) or value is None:
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_numeric_tree(item, name=f"{name}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_numeric_tree(item, name=f"{name}.{key}")
        return
    raise ValueError(f"unsupported {name} value type: {type(value).__name__}")


def _required_feature_ids(candidate: dict[str, Any]) -> set[str]:
    validation = candidate.get("validation") or {}
    spec = validation.get("frozen_spec") or {}
    conditions = spec.get("conditions") or (spec.get("rule") or {}).get("conditions") or []
    ids = {
        str(item.get("feature_id"))
        for item in conditions
        if isinstance(item, dict) and item.get("feature_id")
    }
    schema = spec.get("feature_schema") or {}
    ids.update(str(value) for value in schema.get("feature_ids") or [])
    return ids


def _candidate_validation(candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("status") not in {"FROZEN_FOR_VALIDATION", "LIVE_VALIDATING"}:
        raise ValueError("candidate must be frozen before prospective confirmation")
    validation = candidate.get("validation")
    if not isinstance(validation, dict):
        raise ValueError("candidate has no immutable validation freeze metadata")
    if validation.get("evidence_label") != EVIDENCE_LABEL:
        raise ValueError("candidate validation evidence label is not prospective OOS")
    if validation.get("production_authority") is not False:
        raise ValueError("prospective confirmation cannot have production authority")
    if validation.get("auto_promotion") is not False:
        raise ValueError("prospective confirmation cannot auto-promote")
    if not validation.get("frozen_spec_sha256"):
        raise ValueError("candidate validation spec is not frozen")
    return validation


class ProspectiveConfirmationLedger:
    """Predictions/outcomes written strictly after an immutable candidate freeze."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._events = self._read()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        output = []
        for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError(f"invalid prospective event at line {line_number}")
            output.append(event)
        return output

    def _append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(event)+"\n")
        self._events.append(event)

    def record_prediction(
        self, *, candidate: dict[str, Any], instrument: str, t0: float,
        target_ts: float, prediction: Any, qualified: bool,
        feature_values: dict[str, dict[str, Any]], recorded_ts: float,
    ) -> str:
        validation = _candidate_validation(candidate)
        freeze_ts = float(validation["frozen_at"])
        cutoff_ts = float(validation["training_cutoff_ts"])
        t0 = float(t0)
        target_ts = float(target_ts)
        recorded_ts = float(recorded_ts)
        if t0 <= freeze_ts + 1e-6:
            raise ValueError("confirmatory T0 must be strictly after candidate freeze")
        if t0 <= cutoff_ts + 1e-6:
            raise ValueError("confirmatory T0 overlaps candidate training period")
        if target_ts <= t0:
            raise ValueError("target timestamp must be after T0")
        if recorded_ts < t0-1e-6 or recorded_ts >= target_ts-1e-6:
            raise ValueError("prospective prediction must be written at/after T0 and before target")
        _validate_numeric_tree(prediction, name="prediction")

        required = _required_feature_ids(candidate)
        if bool(qualified):
            missing = sorted(required-set(feature_values))
            if missing:
                raise ValueError(
                    f"qualified prediction missing frozen features: {missing}")
        for feature_id, item in feature_values.items():
            if not isinstance(item, dict):
                raise ValueError(f"invalid feature payload: {feature_id}")
            asof = item.get("asof")
            if asof is not None and float(asof) > t0+1e-6:
                raise ValueError(f"future feature in prospective record: {feature_id}")
            if bool(qualified) and feature_id in required:
                if item.get("available") is False:
                    raise ValueError(
                        f"qualified prediction used unavailable feature: {feature_id}")
                if item.get("stale") is True:
                    raise ValueError(
                        f"qualified prediction used stale feature: {feature_id}")

        payload = {
            "candidate_id": candidate["candidate_id"],
            "hypothesis_id": candidate.get("hypothesis_id"),
            "validation_cohort_id": validation["validation_cohort_id"],
            "validation_scope_id": validation["scope_id"],
            "validation_role": validation["role"],
            "frozen_spec_sha256": validation["frozen_spec_sha256"],
            "instrument": str(instrument),
            "target_id": candidate.get("target_id"),
            "target_kind": candidate.get("target_kind"),
            "horizon_minutes": candidate.get("horizon_minutes"),
            "model_family": candidate.get("model_family") or "INTERPRETABLE_STRUCTURED_RULE",
            "t0": t0,
            "target_ts": target_ts,
            "qualified": bool(qualified),
            "prediction": prediction,
            "feature_values": feature_values,
            "evidence_label": EVIDENCE_LABEL,
        }
        record_id = "ede-prospective-" + _digest(payload)[:24]
        existing = next((event for event in self._events
                         if event.get("event") == "PREDICTION"
                         and event.get("record_id") == record_id), None)
        if existing is not None:
            return record_id
        self._append({
            "event": "PREDICTION",
            "contract_version": PROSPECTIVE_CONFIRMATION_CONTRACT_VERSION,
            "record_id": record_id,
            "recorded_ts": recorded_ts,
            **payload,
            "outcome": None,
            "prediction_written_before_outcome": True,
            "historical_discovery_evidence_counted": False,
            "production_authority": False,
            "auto_promotion": False,
        })
        return record_id

    def resolve(self, record_id: str, *, outcome: Any, observed_ts: float) -> None:
        prediction = next((event for event in self._events
                           if event.get("event") == "PREDICTION"
                           and event.get("record_id") == record_id), None)
        if prediction is None:
            raise KeyError(record_id)
        if any(event.get("event") == "OUTCOME"
               and event.get("record_id") == record_id for event in self._events):
            raise ValueError("prospective outcome already resolved")
        observed_ts = float(observed_ts)
        if observed_ts < float(prediction["target_ts"])-1e-6:
            raise ValueError("outcome cannot be resolved before target timestamp")
        _validate_numeric_tree(outcome, name="outcome")
        self._append({
            "event": "OUTCOME",
            "contract_version": PROSPECTIVE_CONFIRMATION_CONTRACT_VERSION,
            "record_id": record_id,
            "candidate_id": prediction["candidate_id"],
            "hypothesis_id": prediction.get("hypothesis_id"),
            "validation_cohort_id": prediction["validation_cohort_id"],
            "validation_scope_id": prediction["validation_scope_id"],
            "target_id": prediction.get("target_id"),
            "target_kind": prediction.get("target_kind"),
            "horizon_minutes": prediction.get("horizon_minutes"),
            "instrument": prediction.get("instrument"),
            "outcome": outcome,
            "observed_ts": observed_ts,
            "target_ts": prediction["target_ts"],
            "evidence_label": EVIDENCE_LABEL,
            "retrospective_reconstruction": False,
            "historical_discovery_evidence_counted": False,
            "production_authority": False,
            "auto_promotion": False,
        })

    def cohort_events(self, validation_cohort_id: str) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events
                if event.get("validation_cohort_id") == validation_cohort_id]

    def cohort_status(self, validation_cohort_id: str) -> dict[str, Any]:
        events = self.cohort_events(validation_cohort_id)
        predictions = [event for event in events if event.get("event") == "PREDICTION"]
        outcomes = [event for event in events if event.get("event") == "OUTCOME"]
        resolved_ids = {str(event["record_id"]) for event in outcomes}
        instruments = sorted({str(event.get("instrument")) for event in predictions})
        if predictions:
            first_t0 = min(float(event["t0"]) for event in predictions)
            last_t0 = max(float(event["t0"]) for event in predictions)
        else:
            first_t0 = last_t0 = None
        return {
            "contract_version": PROSPECTIVE_CONFIRMATION_CONTRACT_VERSION,
            "validation_cohort_id": validation_cohort_id,
            "evidence_label": EVIDENCE_LABEL,
            "raw_predictions": len(predictions),
            "resolved_predictions": len(resolved_ids),
            "unresolved_predictions": len(predictions)-len(resolved_ids),
            "qualified_predictions": sum(
                bool(event.get("qualified")) for event in predictions),
            "instruments": instruments,
            "first_t0": first_t0,
            "last_t0": last_t0,
            "historical_discovery_evidence_counted": False,
            "production_authority": False,
            "auto_promotion": False,
        }

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)


def record_registered_prediction(
    registry: CandidateRegistry,
    ledger: ProspectiveConfirmationLedger,
    *, candidate_id: str, instrument: str, t0: float, target_ts: float,
    prediction: Any, qualified: bool,
    feature_values: dict[str, dict[str, Any]], recorded_ts: float,
) -> str:
    """Write the first OOS record before transitioning into LIVE_VALIDATING.

    The order is deliberate: a candidate can never become LIVE_VALIDATING merely
    because code asked it to.  A valid append-only prediction record must already
    exist.  A retry after a ledger write but before registry transition is safe.
    """
    candidate = registry.current(candidate_id)
    if candidate is None:
        raise KeyError(candidate_id)
    record_id = ledger.record_prediction(
        candidate=candidate,
        instrument=instrument,
        t0=t0,
        target_ts=target_ts,
        prediction=prediction,
        qualified=qualified,
        feature_values=feature_values,
        recorded_ts=recorded_ts,
    )
    refreshed = registry.current(candidate_id)
    if refreshed is None:
        raise KeyError(candidate_id)
    if refreshed.get("status") == "FROZEN_FOR_VALIDATION":
        registry.start_live_validation(
            candidate_id,
            first_prospective_record_id=record_id,
            event_ts=recorded_ts,
        )
    elif refreshed.get("status") != "LIVE_VALIDATING":
        raise ValueError("candidate left the active prospective validation lifecycle")
    return record_id
