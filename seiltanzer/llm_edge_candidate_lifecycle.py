"""Phase-3 adapter from LLM discovery signals to the existing immutable EDE registry."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .edge_discovery.candidate_admission import admit_discovery_candidate
from .edge_discovery.candidate_registry import CandidateRegistry
from .edge_discovery.frozen_candidate import build_structured_frozen_spec
from .edge_discovery.prospective import PROSPECTIVE_ADAPTER_VERSION
from .edge_discovery.registry import EDE_CONTRACT_VERSION
from .edge_discovery.universal_outcome_adapter import UNIVERSAL_OUTCOME_ADAPTER_VERSION
from .edge_discovery.universal_target_scoring import eligible_target_rows
from .llm_edge_evaluator import (
    CONTRACT_VERSION as DETERMINISTIC_EVALUATOR_CONTRACT,
    MAX_Q_VALUE,
    MEASUREMENT_CONTRACT,
    _resolved_rows_at_cutoff,
    _specs,
    _template,
)
from .llm_edge_researcher import (
    CONTRACT_VERSION as LLM_RESEARCHER_CONTRACT,
    PROMPT_VERSION,
)

SOURCE = "LLM_EDGE_RESEARCHER"
CANDIDATE_CONTRACT_VERSION = "llm-edge-prospective-candidate-v1"
RULE_CONTRACT_VERSION = "llm-edge-frozen-rule-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def registry_path(engine: Any) -> Path:
    override = os.environ.get("SEILTANZER_EDE_CANDIDATE_REGISTRY", "").strip()
    if override:
        return Path(override)
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research" / "ede_candidate_registry.jsonl"


def registry_for_engine(engine: Any) -> CandidateRegistry:
    path = registry_path(engine)
    current = getattr(engine, "_llm_edge_candidate_registry", None)
    if current is None or Path(getattr(current, "path", "")) != path:
        current = CandidateRegistry(path)
        engine._llm_edge_candidate_registry = current
    return current


def _load(runtime: Any, table: str, key: str, value: str) -> dict[str, Any] | None:
    with runtime._lock:
        row = runtime._conn.execute(
            f"SELECT * FROM {table} WHERE {key}=? LIMIT 1", (str(value),)
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    if table == "llm_edge_hypotheses":
        result["conditions"] = json.loads(str(result.get("conditions_json") or "[]"))
    return result


def _discoveries(runtime: Any, limit: int) -> list[dict[str, Any]]:
    with runtime._lock:
        rows = runtime._conn.execute(
            """SELECT evaluation_id,run_id,hypothesis_id,evaluation_cutoff_ts,
                      dataset_sha256,measurement_contract,result_json,created_ts
               FROM llm_edge_evaluations
               ORDER BY created_ts DESC,evaluation_id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    output = []
    for raw in rows:
        item = dict(raw)
        try:
            item["result"] = json.loads(str(item["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if item["result"].get("status") == "DISCOVERY_SIGNAL":
            output.append(item)
    return output


def _candidate_id(evaluation_id: str, hypothesis_id: str) -> str:
    return "llm-edge-candidate-" + _sha({
        "source": SOURCE,
        "evaluation_id": str(evaluation_id),
        "hypothesis_id": str(hypothesis_id),
        "candidate_contract_version": CANDIDATE_CONTRACT_VERSION,
    })[:24]


def _already_registered(registry: CandidateRegistry) -> set[str]:
    found: set[str] = set()
    for event in registry.events():
        if event.get("event") != "EVALUATION_RECORDED":
            continue
        item = event.get("evaluation") or {}
        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id.startswith("llm-edge-candidate-"):
            continue
        validation_id = item.get("source_evaluation_id")
        if validation_id:
            found.add(str(validation_id))
    return found


def _epoch_id(run: dict[str, Any], frozen_ts: float) -> str:
    day = time.strftime("%Y-%m-%d", time.gmtime(float(frozen_ts)))
    schema = EDE_CONTRACT_VERSION.replace("g1s-edge-discovery-engine-", "")
    return f"{day}-schema-{schema}-{str(run.get('run_id') or 'unknown')}"


def _rule_sha(frozen: dict[str, Any]) -> str:
    return _sha({
        "rule_contract_version": RULE_CONTRACT_VERSION,
        "feature_schema_version": frozen.get("feature_schema_version"),
        "target_id": frozen.get("target_id"),
        "horizon_minutes": frozen.get("horizon_minutes"),
        "conditions": [{
            "feature_id": item.get("feature_id"),
            "kind": item.get("kind"),
            "state": item.get("state"),
            "lower": item.get("lower"),
            "upper": item.get("upper"),
        } for item in (frozen.get("rule") or {}).get("conditions") or []],
    })


def freeze_one(
    engine: Any, runtime: Any, registry: CandidateRegistry,
    evaluation: dict[str, Any], *, frozen_ts: float,
) -> dict[str, Any]:
    result = evaluation["result"]
    if result.get("status") != "DISCOVERY_SIGNAL":
        return {"frozen": False, "reason": "NOT_DISCOVERY_SIGNAL"}
    if result.get("evaluation_state") != "DETERMINISTIC_EVALUATED":
        return {"frozen": False, "reason": "NOT_DETERMINISTIC_EVALUATED"}
    q = result.get("q_value")
    if q is None or float(q) > float(MAX_Q_VALUE):
        return {"frozen": False, "reason": "DISCOVERY_Q_GATE_FAILED"}
    if result.get("production_authority") is not False:
        return {"frozen": False, "reason": "DISCOVERY_HAS_AUTHORITY"}
    if result.get("prospective_confirmation") is not False:
        return {"frozen": False, "reason": "ALREADY_PROSPECTIVE_CONFIRMED"}

    hypothesis = _load(runtime, "llm_edge_hypotheses", "hypothesis_id", evaluation["hypothesis_id"])
    run = _load(runtime, "llm_edge_research_runs", "run_id", evaluation["run_id"])
    if hypothesis is None or run is None:
        return {"frozen": False, "reason": "PROVENANCE_INCOMPLETE"}

    cutoff = float(evaluation["evaluation_cutoff_ts"])
    rows = _resolved_rows_at_cutoff(runtime, cutoff)
    spec = _specs(rows).get(str(result["target_id"]))
    if spec is None:
        return {"frozen": False, "reason": "TARGET_SPEC_UNAVAILABLE"}
    horizon = int(result["horizon_minutes"])
    target_rows = eligible_target_rows(
        [row for row in rows if int(row.get("horizon_minutes") or 0) == horizon], spec
    )
    if not target_rows:
        return {"frozen": False, "reason": "FINAL_FIT_NO_ELIGIBLE_ROWS"}

    candidate_id = _candidate_id(evaluation["evaluation_id"], evaluation["hypothesis_id"])
    current = registry.current(candidate_id)
    if current and current.get("status") in {"FROZEN_FOR_VALIDATION", "LIVE_VALIDATING", "VALIDATED", "FAILED_LIVE"}:
        return {"frozen": False, "reason": "ALREADY_FROZEN", "candidate_id": candidate_id}

    template = _template(hypothesis)
    discovery = {
        "candidate_id": candidate_id,
        "hypothesis_id": str(evaluation["hypothesis_id"]),
        "template_id": template.template_id,
        "conditions": list(hypothesis.get("conditions") or []),
        "status": "DISCOVERY_SIGNAL",
        "target_id": str(result["target_id"]),
        "target_family": str(result.get("target_family") or hypothesis.get("target_family") or ""),
        "target_kind": str(spec.kind),
        "model_family": "INTERPRETABLE_STRUCTURED_RULE",
        "horizon_minutes": horizon,
        "p_value": result.get("p_value"),
        "q_value": q,
        "primary_improvement": result.get("primary_improvement"),
        "fold_positive": result.get("fold_positive"),
        "fold_evaluated": result.get("evaluated_fold_count"),
        "production_authority": False,
        "auto_promotion": False,
        "prospective_confirmation": False,
    }
    admitted = admit_discovery_candidate(discovery)
    registry.register_evaluation(
        admitted,
        dataset_sha256=str(evaluation["dataset_sha256"]),
        research_run=str(evaluation["run_id"]),
        measurement_contract=str(evaluation.get("measurement_contract") or MEASUREMENT_CONTRACT),
        created_ts=float(evaluation.get("created_ts") or frozen_ts),
    )

    frozen = build_structured_frozen_spec(
        admitted, target_rows, spec, source_set_sha256=str(evaluation["dataset_sha256"])
    )
    frozen.update({
        "name": str(hypothesis.get("name") or candidate_id),
        "source": SOURCE,
        "hypothesis_id": str(evaluation["hypothesis_id"]),
        "source_evaluation_id": str(evaluation["evaluation_id"]),
        "source_research_run_id": str(evaluation["run_id"]),
        "source_snapshot_sha256": str(run.get("snapshot_sha256") or ""),
        "provider_model": str(run.get("model") or ""),
        "prompt_version": str(run.get("prompt_version") or PROMPT_VERSION),
        "proposal_ts": float(run.get("created_ts") or cutoff),
        "evaluation_cutoff_ts": cutoff,
        "frozen_ts": float(frozen_ts),
        "prospective_start_ts": float(frozen_ts),
        "prospective_epoch_id": _epoch_id(run, frozen_ts),
        "feature_schema_version": EDE_CONTRACT_VERSION,
        "prospective_adapter_version": PROSPECTIVE_ADAPTER_VERSION,
        "outcome_adapter_version": UNIVERSAL_OUTCOME_ADAPTER_VERSION,
        "llm_researcher_contract": LLM_RESEARCHER_CONTRACT,
        "deterministic_evaluator_contract": DETERMINISTIC_EVALUATOR_CONTRACT,
        "candidate_contract_version": CANDIDATE_CONTRACT_VERSION,
        "rule_contract_version": RULE_CONTRACT_VERSION,
        "research_only": True,
        "production_authority": False,
        "auto_promotion": False,
        "prospective_confirmation": False,
        "prospective_confirmed": False,
        "discovery_dataset_sha256": str(evaluation["dataset_sha256"]),
        "discovery_q_value": q,
        "discovery_p_value": result.get("p_value"),
        "discovery_effect": result.get("primary_improvement"),
        "discovery_fold_count": result.get("evaluated_fold_count"),
    })
    frozen["rule_sha256"] = _rule_sha(frozen)
    registry.freeze_for_validation(
        candidate_id,
        frozen_spec=frozen,
        training_cutoff_ts=float(frozen["training_cutoff_ts"]),
        frozen_at=float(frozen_ts),
        role="CHALLENGER",
    )
    return {
        "frozen": True,
        "candidate_id": candidate_id,
        "rule_sha256": frozen["rule_sha256"],
        "prospective_start_ts": float(frozen_ts),
        "production_authority": False,
    }


def freeze_discovery_signals(engine: Any, *, now: float | None = None, limit: int = 200) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"frozen_n": 0, "reason": "G1S_RUNTIME_UNAVAILABLE"}
    registry = registry_for_engine(engine)
    known = _already_registered(registry)
    frozen_n = 0
    failures = []
    base_ts = float(time.time() if now is None else now)
    for index, evaluation in enumerate(reversed(_discoveries(runtime, limit))):
        candidate_id = _candidate_id(evaluation["evaluation_id"], evaluation["hypothesis_id"])
        if str(evaluation["evaluation_id"]) in known or registry.current(candidate_id) is not None:
            continue
        try:
            result = freeze_one(
                engine, runtime, registry, evaluation, frozen_ts=base_ts + index * 1e-6
            )
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            failures.append({
                "evaluation_id": str(evaluation["evaluation_id"]),
                "reason": f"{type(exc).__name__}:{str(exc)[:180]}",
            })
            continue
        if result.get("frozen"):
            frozen_n += 1
            known.add(str(evaluation["evaluation_id"]))
        else:
            failures.append({
                "evaluation_id": str(evaluation["evaluation_id"]),
                "reason": str(result.get("reason") or "FREEZE_REJECTED"),
            })
    return {
        "frozen_n": frozen_n,
        "rejected_or_failed_n": len(failures),
        "failures": failures[:20],
        "production_authority": False,
        "writes_active_edge_registry": False,
    }


def active_llm_candidates(registry: CandidateRegistry) -> list[dict[str, Any]]:
    ids: list[str] = []
    for event in registry.events():
        if event.get("event") != "EVALUATION_RECORDED":
            continue
        candidate_id = str((event.get("evaluation") or {}).get("candidate_id") or "")
        if candidate_id.startswith("llm-edge-candidate-") and candidate_id not in ids:
            ids.append(candidate_id)
    output = []
    for candidate_id in ids:
        state = registry.current(candidate_id)
        if state and state.get("status") in {"FROZEN_FOR_VALIDATION", "LIVE_VALIDATING"}:
            output.append(state)
    return output
