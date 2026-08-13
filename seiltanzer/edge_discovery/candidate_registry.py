"""Durable append-only hypothesis, evaluation and candidate lifecycle registry."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


REGISTRY_CONTRACT_VERSION = "g1s-ede-candidate-registry-v1.1"
STATUSES = (
    "EXPLORATORY", "EXPLORATORY_FDR_FAIL", "HISTORICAL_CANDIDATE", "REJECTED",
    "FROZEN_FOR_VALIDATION", "LIVE_VALIDATING", "VALIDATED", "FAILED_LIVE",
)
TRANSITIONS = {
    "EXPLORATORY": {"REJECTED", "HISTORICAL_CANDIDATE"},
    "EXPLORATORY_FDR_FAIL": {"REJECTED"},
    "HISTORICAL_CANDIDATE": {"FROZEN_FOR_VALIDATION", "REJECTED"},
    "FROZEN_FOR_VALIDATION": {"LIVE_VALIDATING"},
    "LIVE_VALIDATING": {"VALIDATED", "FAILED_LIVE"},
    "REJECTED": set(), "VALIDATED": set(), "FAILED_LIVE": set(),
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _identity(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_id": candidate.get("template_id"),
        "template": candidate.get("template"),
        "signal": candidate.get("signal"),
        "horizon_minutes": candidate.get("horizon_minutes"),
    }


def hypothesis_id(candidate: dict[str, Any]) -> str:
    existing = candidate.get("hypothesis_id")
    if existing:
        return str(existing)
    identity = _identity(candidate)
    if not identity["template_id"] and candidate.get("candidate_id"):
        identity["legacy_candidate_id"] = str(candidate["candidate_id"])
    return "ede-hypothesis-" + _digest(identity)[:24]


def evaluation_id(*, hypothesis: str, dataset_sha256: str,
                  measurement_contract: str) -> str:
    return "ede-evaluation-" + _digest({
        "hypothesis_id": hypothesis,
        "dataset_sha256": str(dataset_sha256),
        "measurement_contract": str(measurement_contract),
    })[:24]


def _evaluation_artifact(candidate: dict[str, Any], *, hypothesis: str,
                         evaluation: str, dataset_sha256: str,
                         measurement_contract: str, research_run: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id") or hypothesis.replace(
            "ede-hypothesis-", "ede-candidate-"),
        "hypothesis_id": hypothesis,
        "evaluation_id": evaluation,
        "template_id": candidate.get("template_id"),
        "template": candidate.get("template"),
        "conditions": candidate.get("conditions") or [],
        "thresholds": candidate.get("thresholds") or [],
        "signal": candidate.get("signal"),
        "horizon_minutes": candidate.get("horizon_minutes"),
        "dataset_source_sha256": str(dataset_sha256),
        "measurement_contract": str(measurement_contract),
        "research_run": str(research_run),
        "status": candidate.get("status"),
        "evidence_maturity": candidate.get("evidence_maturity"),
        "maturity_contract_version": candidate.get("maturity_contract_version"),
        "terminal_use": candidate.get("terminal_use"),
        "where_it_helps": candidate.get("where_it_helps"),
        "where_it_hurts": candidate.get("where_it_hurts"),
        "conditional_ret5": candidate.get("conditional_ret5"),
        "global_ret5": candidate.get("global_ret5"),
        "global_ret5_comparison": candidate.get("global_ret5_comparison"),
        "sanity_baselines": candidate.get("sanity_baselines"),
        "metrics": candidate.get("model"),
        "improvement": candidate.get("improvement"),
        "p_value": candidate.get("p_value"),
        "q_value": candidate.get("q_value"),
        "gates": candidate.get("gates"),
        "coverage": candidate.get("coverage"),
        "raw_n": candidate.get("raw_n"),
        "effective_n": candidate.get("effective_n"),
        "folds": candidate.get("folds") or candidate.get("inner_fold_evaluations") or [],
        "assets": candidate.get("assets") or [],
        "reason_rejected": candidate.get("reason_rejected"),
        "production_authority": False,
        "auto_promotion": False,
    }


class CandidateRegistry:
    """JSONL event ledger suitable for a dedicated version-controlled branch."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._events = self._read()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
        for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError(f"invalid registry event at line {line_number}")
            events.append(event)
        return events

    def _append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(event)+"\n")
        self._events.append(event)

    def _hypothesis_event(self, value: str) -> dict[str, Any] | None:
        return next((event for event in self._events
                     if event.get("event") == "HYPOTHESIS_CREATED"
                     and event.get("hypothesis_id") == value), None)

    def evaluation(self, value: str) -> dict[str, Any] | None:
        event = next((event for event in self._events
                      if event.get("event") == "EVALUATION_RECORDED"
                      and event.get("evaluation_id") == value), None)
        return None if event is None else dict(event["evaluation"])

    def current(self, candidate_id: str) -> dict[str, Any] | None:
        legacy = [event for event in self._events
                  if event.get("candidate_id") == candidate_id]
        created = next((event for event in legacy if event.get("event") == "CREATED"), None)
        evaluations = [event for event in self._events
                       if event.get("event") == "EVALUATION_RECORDED"
                       and (event["evaluation"].get("candidate_id") == candidate_id
                            or event.get("hypothesis_id") == candidate_id)]
        if evaluations:
            state = dict(evaluations[-1]["evaluation"])
            artifact_sha = evaluations[-1]["artifact_sha256"]
        elif created is not None:
            state = dict(created["candidate"])
            artifact_sha = created["artifact_sha256"]
        else:
            return None
        identity = state.get("hypothesis_id") or candidate_id
        transitions = [event for event in self._events
                       if event.get("event") == "STATUS_TRANSITION"
                       and event.get("candidate_id") in {candidate_id, identity}]
        for event in transitions:
            state["status"] = event["to_status"]
            state["last_transition_ts"] = event["event_ts"]
        state["artifact_sha256"] = artifact_sha
        return state

    def register_evaluation(self, candidate: dict[str, Any], *, dataset_sha256: str,
                            research_run: str, measurement_contract: str,
                            created_ts: float | None = None) -> str:
        status = str(candidate.get("status"))
        if status not in STATUSES:
            raise ValueError(f"invalid candidate status: {status}")
        hypothesis = hypothesis_id(candidate)
        identity = _identity(candidate)
        identity_digest = _digest(identity)
        existing_hypothesis = self._hypothesis_event(hypothesis)
        if existing_hypothesis is None:
            self._append({
                "event": "HYPOTHESIS_CREATED",
                "event_ts": float(created_ts or time.time()),
                "registry_contract": REGISTRY_CONTRACT_VERSION,
                "hypothesis_id": hypothesis,
                "candidate_id": candidate.get("candidate_id"),
                "hypothesis": identity,
                "identity_sha256": identity_digest,
                "production_authority": False, "auto_promotion": False,
            })
        elif existing_hypothesis.get("identity_sha256") != identity_digest:
            raise ValueError("hypothesis identity is immutable")
        evaluation = evaluation_id(
            hypothesis=hypothesis, dataset_sha256=dataset_sha256,
            measurement_contract=measurement_contract)
        artifact = _evaluation_artifact(
            candidate, hypothesis=hypothesis, evaluation=evaluation,
            dataset_sha256=dataset_sha256,
            measurement_contract=measurement_contract, research_run=research_run)
        digest = _digest(artifact)
        existing = next((event for event in self._events
                         if event.get("event") == "EVALUATION_RECORDED"
                         and event.get("evaluation_id") == evaluation), None)
        if existing is not None:
            comparable = dict(artifact)
            comparable["research_run"] = existing["evaluation"].get("research_run")
            if existing.get("artifact_sha256") != _digest(comparable):
                raise ValueError("historical evaluation is immutable")
            if not any(event.get("event") == "EVALUATION_RERUN_DEDUPLICATED"
                       and event.get("evaluation_id") == evaluation
                       and event.get("research_run") == str(research_run)
                       for event in self._events):
                self._append({
                    "event": "EVALUATION_RERUN_DEDUPLICATED",
                    "event_ts": float(created_ts or time.time()),
                    "registry_contract": REGISTRY_CONTRACT_VERSION,
                    "hypothesis_id": hypothesis, "evaluation_id": evaluation,
                    "research_run": str(research_run),
                    "artifact_sha256": existing["artifact_sha256"],
                    "production_authority": False, "auto_promotion": False,
                })
            return evaluation
        self._append({
            "event": "EVALUATION_RECORDED",
            "event_ts": float(created_ts or time.time()),
            "registry_contract": REGISTRY_CONTRACT_VERSION,
            "hypothesis_id": hypothesis, "evaluation_id": evaluation,
            "dataset_source_sha256": str(dataset_sha256),
            "research_run": str(research_run),
            "evaluation": artifact, "artifact_sha256": digest,
            "production_authority": False, "auto_promotion": False,
        })
        return evaluation

    def register(self, candidate: dict[str, Any], *, created_ts: float | None = None) -> None:
        self.register_evaluation(
            candidate,
            dataset_sha256=str(candidate.get("source_set_sha256") or "LEGACY_UNSPECIFIED"),
            research_run=str(candidate.get("research_run") or "LEGACY_MANUAL"),
            measurement_contract=str(candidate.get("contract_version") or "LEGACY_V1"),
            created_ts=created_ts,
        )

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
            hypothesis = str(current.get("hypothesis_id") or hypothesis_id(artifact))
            candidate_artifact = _evaluation_artifact(
                artifact, hypothesis=hypothesis,
                evaluation=str(current.get("evaluation_id") or "LEGACY"),
                dataset_sha256=str(current.get("dataset_source_sha256") or "LEGACY_UNSPECIFIED"),
                measurement_contract=str(current.get("measurement_contract") or "LEGACY_V1"),
                research_run=str(current.get("research_run") or "LEGACY_MANUAL"))
            if _digest(candidate_artifact) != current["artifact_sha256"]:
                raise ValueError("candidate artifact is immutable after registration")
        self._append({
            "event": "STATUS_TRANSITION", "event_ts": float(event_ts or time.time()),
            "candidate_id": candidate_id,
            "hypothesis_id": current.get("hypothesis_id"),
            "evaluation_id": current.get("evaluation_id"),
            "from_status": old, "to_status": to_status,
            "artifact_sha256": current["artifact_sha256"],
            "production_authority": False, "auto_promotion": False,
        })

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
