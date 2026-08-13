"""Prospective-only shadow ledger for explicitly frozen candidates."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


class LiveShadowLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._events = [] if not self.path.exists() else [
            json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_json(event)+"\n")
        self._events.append(event)

    def record_prediction(self, *, candidate: dict[str, Any], t0: float,
                          target_ts: float, qualified: bool, signal: float,
                          prediction: float, feature_values: dict[str, dict[str, Any]],
                          recorded_ts: float | None = None) -> str:
        if candidate.get("status") not in {"FROZEN_FOR_VALIDATION", "LIVE_VALIDATING"}:
            raise ValueError("candidate must be frozen before prospective recording")
        if float(target_ts) <= float(t0):
            raise ValueError("target timestamp must be after T0")
        for feature_id, item in feature_values.items():
            asof = item.get("asof")
            if asof is not None and float(asof) > float(t0)+1e-6:
                raise ValueError(f"future feature in live record: {feature_id}")
        actual_recorded_ts = float(recorded_ts or time.time())
        if actual_recorded_ts < float(t0)-1e-6 or actual_recorded_ts >= float(target_ts):
            raise ValueError("prediction must be recorded at/after T0 and before target timestamp")
        payload = {
            "candidate_id": candidate["candidate_id"], "t0": float(t0),
            "target_ts": float(target_ts), "qualified": bool(qualified),
            "signal": float(signal), "prediction": float(prediction),
            "feature_values": feature_values,
        }
        record_id = "ede-live-" + hashlib.sha256(_json(payload).encode()).hexdigest()[:24]
        if any(event.get("record_id") == record_id for event in self._events):
            return record_id
        self._append({
            "event": "PREDICTION", "record_id": record_id,
            "recorded_ts": actual_recorded_ts, **payload,
            "outcome": None, "prediction_written_before_outcome": True,
            "production_authority": False,
        })
        return record_id

    def resolve(self, record_id: str, *, outcome: float, observed_ts: float) -> None:
        prediction = next((event for event in self._events
                           if event.get("event") == "PREDICTION"
                           and event.get("record_id") == record_id), None)
        if prediction is None:
            raise KeyError(record_id)
        if float(observed_ts) < float(prediction["target_ts"]):
            raise ValueError("outcome cannot be resolved before target timestamp")
        if any(event.get("event") == "OUTCOME" and event.get("record_id") == record_id
               for event in self._events):
            raise ValueError("outcome already recorded")
        self._append({
            "event": "OUTCOME", "record_id": record_id,
            "candidate_id": prediction["candidate_id"], "outcome": float(outcome),
            "observed_ts": float(observed_ts), "target_ts": prediction["target_ts"],
            "retrospective_reconstruction": False, "production_authority": False,
        })

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
