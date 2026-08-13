"""Append-only candidate lifecycle registry for negative and frozen findings."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


STATUSES = (
    "EXPLORATORY", "HISTORICAL_CANDIDATE", "REJECTED",
    "FROZEN_FOR_VALIDATION", "LIVE_VALIDATING", "VALIDATED", "FAILED_LIVE",
)
TRANSITIONS = {
    "EXPLORATORY": {"REJECTED", "HISTORICAL_CANDIDATE"},
    "HISTORICAL_CANDIDATE": {"FROZEN_FOR_VALIDATION", "REJECTED"},
    "FROZEN_FOR_VALIDATION": {"LIVE_VALIDATING"},
    "LIVE_VALIDATING": {"VALIDATED", "FAILED_LIVE"},
    "REJECTED": set(), "VALIDATED": set(), "FAILED_LIVE": set(),
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _artifact(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "candidate_id", "signal", "conditions", "horizon_minutes", "complexity",
        "coverage", "raw_n", "effective_n", "improvement", "folds",
        "assets", "status",
    )
    return {key: candidate.get(key) for key in keys}


class CandidateRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._events = self._read()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def _append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(event)+"\n")
        self._events.append(event)

    def current(self, candidate_id: str) -> dict[str, Any] | None:
        events = [event for event in self._events if event.get("candidate_id") == candidate_id]
        if not events:
            return None
        state = dict(events[0]["candidate"])
        for event in events[1:]:
            state["status"] = event["to_status"]
            state["last_transition_ts"] = event["event_ts"]
        state["artifact_sha256"] = events[0]["artifact_sha256"]
        return state

    def register(self, candidate: dict[str, Any], *, created_ts: float | None = None) -> None:
        candidate_id = str(candidate["candidate_id"])
        if self.current(candidate_id) is not None:
            raise ValueError(f"candidate already registered: {candidate_id}")
        status = str(candidate["status"])
        if status not in STATUSES:
            raise ValueError(f"invalid candidate status: {status}")
        artifact = _artifact(candidate)
        digest = hashlib.sha256(_canonical(artifact).encode()).hexdigest()
        self._append({
            "event": "CREATED", "event_ts": float(created_ts or time.time()),
            "candidate_id": candidate_id, "candidate": artifact,
            "artifact_sha256": digest, "production_authority": False,
            "auto_promotion": False,
        })

    def transition(self, candidate_id: str, to_status: str, *,
                   artifact: dict[str, Any] | None = None,
                   event_ts: float | None = None) -> None:
        current = self.current(candidate_id)
        if current is None:
            raise KeyError(candidate_id)
        old = str(current["status"])
        if to_status not in TRANSITIONS.get(old, set()):
            raise ValueError(f"invalid transition {old} -> {to_status}")
        if artifact is not None:
            digest = hashlib.sha256(_canonical(_artifact(artifact)).encode()).hexdigest()
            if digest != current["artifact_sha256"]:
                raise ValueError("candidate artifact is immutable after registration")
        self._append({
            "event": "STATUS_TRANSITION", "event_ts": float(event_ts or time.time()),
            "candidate_id": candidate_id, "from_status": old, "to_status": to_status,
            "artifact_sha256": current["artifact_sha256"],
            "production_authority": False, "auto_promotion": False,
        })

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
