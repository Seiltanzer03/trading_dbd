"""Durable append-only hypothesis, evaluation and validation lifecycle registry.

PASS 7 extends the existing registry. Legacy hypothesis identities remain
byte-for-byte compatible; universal dimensions are appended only when present.
Validation is research/shadow evidence only and never production authority.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


REGISTRY_CONTRACT_VERSION = "g1s-ede-candidate-registry-v1.2"
VALIDATION_CONTRACT_VERSION = "g1s-ede-prospective-validation-freeze-v2"
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
VALIDATION_ROLES = {"CHAMPION", "CHALLENGER"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _identity(candidate: dict[str, Any]) -> dict[str, Any]:
    identity = {
        "template_id": candidate.get("template_id"),
        "template": candidate.get("template"),
        "signal": candidate.get("signal"),
        "horizon_minutes": candidate.get("horizon_minutes"),
    }
    # Do not add null universal fields: legacy IDs must remain unchanged.
    for key in ("target_id", "target_kind", "model_family", "candidate_family"):
        value = candidate.get(key)
        if value is not None:
            identity[key] = value
    return identity


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


def _optional_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in (
        "target_id", "target_family", "target_kind", "model_family",
        "model_library_version", "model_hyperparameters", "feature_frequency",
        "baseline_method", "dependency_pvalue_method", "rates_features_excluded",
        "rates_exclusion_reason", "primary_improvement", "fold_positive",
        "fold_evaluated", "discovery_only", "prospective_confirmation",
        "source_discovery_status", "source_discovery_contract",
        "admission_contract_version", "evidence_label",
    ):
        value = candidate.get(key)
        if value is not None:
            output[key] = value
    return output


def _evaluation_artifact(candidate: dict[str, Any], *, hypothesis: str,
                         evaluation: str, dataset_sha256: str,
                         measurement_contract: str, research_run: str) -> dict[str, Any]:
    artifact = {
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
        "data_maturity": candidate.get("data_maturity"),
        "edge_maturity": candidate.get("edge_maturity") or candidate.get("evidence_maturity"),
        "evidence_maturity": candidate.get("evidence_maturity"),
        "aggregate_scope": candidate.get("aggregate_scope"),
        "primary_only_aggregate": candidate.get("primary_only_aggregate"),
        "diagnostic_aggregate": candidate.get("diagnostic_aggregate"),
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
    artifact.update(_optional_fields(candidate))
    return artifact


def validation_scope(candidate: dict[str, Any]) -> dict[str, Any]:
    """Comparable prospective experiment scope.

    Model family and feature set are intentionally *not* scope dimensions: a
    structured champion and an ML challenger for the same target+horizon must be
    able to coexist and be compared on the same future T0 cohort.
    """
    return {
        "target_id": candidate.get("target_id") or candidate.get("signal"),
        "target_kind": candidate.get("target_kind"),
        "horizon_minutes": candidate.get("horizon_minutes"),
    }


def validation_scope_id(candidate: dict[str, Any]) -> str:
    return "ede-validation-scope-" + _digest(validation_scope(candidate))[:24]


class CandidateRegistry:
    """Append-only JSONL registry for immutable discovery/validation history."""

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
            if isinstance(event.get("validation"), dict):
                state["validation"] = dict(event["validation"])
            if event.get("first_prospective_record_id"):
                state["first_prospective_record_id"] = str(
                    event["first_prospective_record_id"])
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
                "production_authority": False,
                "auto_promotion": False,
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
                    "hypothesis_id": hypothesis,
                    "evaluation_id": evaluation,
                    "research_run": str(research_run),
                    "artifact_sha256": existing["artifact_sha256"],
                    "production_authority": False,
                    "auto_promotion": False,
                })
            return evaluation

        self._append({
            "event": "EVALUATION_RECORDED",
            "event_ts": float(created_ts or time.time()),
            "registry_contract": REGISTRY_CONTRACT_VERSION,
            "hypothesis_id": hypothesis,
            "evaluation_id": evaluation,
            "dataset_source_sha256": str(dataset_sha256),
            "research_run": str(research_run),
            "evaluation": artifact,
            "artifact_sha256": digest,
            "production_authority": False,
            "auto_promotion": False,
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
                   event_ts: float | None = None,
                   validation: dict[str, Any] | None = None,
                   first_prospective_record_id: str | None = None) -> None:
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
        event = {
            "event": "STATUS_TRANSITION",
            "event_ts": float(event_ts or time.time()),
            "candidate_id": candidate_id,
            "hypothesis_id": current.get("hypothesis_id"),
            "evaluation_id": current.get("evaluation_id"),
            "from_status": old,
            "to_status": to_status,
            "artifact_sha256": current["artifact_sha256"],
            "production_authority": False,
            "auto_promotion": False,
        }
        if validation is not None:
            event["validation"] = validation
        if first_prospective_record_id is not None:
            event["first_prospective_record_id"] = str(first_prospective_record_id)
        self._append(event)

    def freeze_for_validation(
        self, candidate_id: str, *, frozen_spec: dict[str, Any],
        training_cutoff_ts: float, frozen_at: float | None = None,
        role: str = "CHALLENGER", validation_cohort_id: str | None = None,
    ) -> str:
        current = self.current(candidate_id)
        if current is None:
            raise KeyError(candidate_id)
        if str(current.get("status")) != "HISTORICAL_CANDIDATE":
            raise ValueError("only a historical candidate may be frozen for validation")
        if role not in VALIDATION_ROLES:
            raise ValueError(f"invalid validation role: {role}")
        if not isinstance(frozen_spec, dict) or not frozen_spec:
            raise ValueError("an exact non-empty frozen validation spec is required")

        actual_frozen_at = float(time.time() if frozen_at is None else frozen_at)
        cutoff = float(training_cutoff_ts)
        if cutoff > actual_frozen_at + 1e-6:
            raise ValueError("training cutoff cannot be after freeze time")
        spec_cutoff = frozen_spec.get("training_cutoff_ts")
        if spec_cutoff is not None and abs(float(spec_cutoff)-cutoff) > 1e-6:
            raise ValueError("frozen spec training cutoff does not match registry cutoff")
        spec_candidate = frozen_spec.get("candidate_id")
        if spec_candidate not in {None, candidate_id}:
            raise ValueError("frozen spec belongs to a different candidate")
        spec_target = frozen_spec.get("target_id")
        if spec_target is not None and spec_target != current.get("target_id"):
            raise ValueError("frozen spec target does not match candidate")
        spec_horizon = frozen_spec.get("horizon_minutes")
        if spec_horizon is not None and int(spec_horizon) != int(
                current.get("horizon_minutes") or 0):
            raise ValueError("frozen spec horizon does not match candidate")
        spec_source = frozen_spec.get("source_set_sha256")
        registry_source = current.get("dataset_source_sha256")
        if (spec_source not in {None, "LEGACY_UNSPECIFIED"}
                and registry_source not in {None, "LEGACY_UNSPECIFIED"}
                and str(spec_source) != str(registry_source)):
            raise ValueError("frozen spec source set does not match evaluation")

        scope = validation_scope(current)
        scope_id = validation_scope_id(current)
        spec_sha = _digest(frozen_spec)
        cohort = validation_cohort_id or (
            "ede-validation-cohort-" + _digest({
                "candidate_id": candidate_id,
                "artifact_sha256": current["artifact_sha256"],
                "frozen_spec_sha256": spec_sha,
                "training_cutoff_ts": cutoff,
                "frozen_at": actual_frozen_at,
            })[:24]
        )
        if role == "CHAMPION":
            for item in self.validation_candidates(
                    scope_id=scope_id, role="CHAMPION"):
                if item.get("candidate_id") != candidate_id:
                    raise ValueError(
                        "validation scope already has a frozen/live champion")

        validation = {
            "contract_version": VALIDATION_CONTRACT_VERSION,
            "role": role,
            "scope": scope,
            "scope_id": scope_id,
            "validation_cohort_id": str(cohort),
            "frozen_at": actual_frozen_at,
            "training_cutoff_ts": cutoff,
            "oos_start_ts_exclusive": actual_frozen_at,
            "evidence_label": "LIVE_PROSPECTIVE_OOS",
            "discovery_evidence_label": "HISTORICAL_WALK_FORWARD",
            "frozen_spec": frozen_spec,
            "frozen_spec_sha256": spec_sha,
            "source_artifact_sha256": current["artifact_sha256"],
            "production_authority": False,
            "auto_promotion": False,
        }
        self.transition(
            candidate_id, "FROZEN_FOR_VALIDATION",
            event_ts=actual_frozen_at, validation=validation)
        return str(cohort)

    def start_live_validation(
        self, candidate_id: str, *, first_prospective_record_id: str,
        event_ts: float | None = None,
    ) -> None:
        current = self.current(candidate_id)
        if current is None:
            raise KeyError(candidate_id)
        if current.get("status") == "LIVE_VALIDATING":
            if current.get("first_prospective_record_id") not in {
                    None, str(first_prospective_record_id)}:
                raise ValueError("candidate already started with a different OOS record")
            return
        if current.get("status") != "FROZEN_FOR_VALIDATION":
            raise ValueError("candidate must be frozen before live validation starts")
        self.transition(
            candidate_id, "LIVE_VALIDATING",
            event_ts=event_ts,
            first_prospective_record_id=str(first_prospective_record_id))

    def validation_candidates(self, *, scope_id: str,
                              role: str | None = None) -> list[dict[str, Any]]:
        candidate_ids: list[str] = []
        for event in self._events:
            if event.get("event") != "EVALUATION_RECORDED":
                continue
            candidate_id = event.get("evaluation", {}).get("candidate_id")
            if candidate_id and str(candidate_id) not in candidate_ids:
                candidate_ids.append(str(candidate_id))
        output = []
        for candidate_id in candidate_ids:
            state = self.current(candidate_id)
            if state is None or state.get("status") not in {
                    "FROZEN_FOR_VALIDATION", "LIVE_VALIDATING"}:
                continue
            validation = state.get("validation") or {}
            if validation.get("scope_id") != scope_id:
                continue
            if role is not None and validation.get("role") != role:
                continue
            output.append(state)
        output.sort(key=lambda item: (
            0 if (item.get("validation") or {}).get("role") == "CHAMPION" else 1,
            float((item.get("validation") or {}).get("frozen_at") or 0.0),
            str(item.get("candidate_id")),
        ))
        return output

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)
