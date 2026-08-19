"""Prospective confirmation and evidence-gated promotion for LLM-origin edge.

PR A freezes an immutable rule and writes causal future predictions/outcomes.
This module performs the next experiment only: it scores those already-written
future MATCH records against the already-frozen structural baseline, applies a
multiple-testing gate, persists checkpoints, moves CandidateRegistry to
VALIDATED/FAILED_LIVE, and promotes only VALIDATED candidates.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import numpy as np

from .edge_discovery.candidate_registry import CandidateRegistry
from .edge_discovery.scoring import benjamini_hochberg
from .edge_discovery.universal_target_scoring import (
    UniversalTargetSpec,
    paired_target_dependency_cohorts,
    paired_target_pvalue,
    relative_target_improvement,
    target_metrics,
    target_value,
)
from .llm_edge_candidate_lifecycle import registry_for_engine
from .llm_edge_prospective_journal import initialize_journal_storage, ledger_for_engine


CONTRACT_VERSION = "llm-edge-prospective-evaluation-v1.1"
PROMOTION_CONTRACT_VERSION = "llm-edge-active-promotion-v1"
CHECKPOINTS = (24, 48, 96)
FDR_SCOPE = "PROSPECTIVE_EPOCH_TARGET_HORIZON_CHECKPOINT"
OVERALL_FDR_BUDGET = 0.10
LOOK_ADJUSTED_Q_MAX = OVERALL_FDR_BUDGET / len(CHECKPOINTS)
MIN_PRIMARY_IMPROVEMENT = 0.01
MIN_DEPENDENCY_COHORT_N = 3
VALIDATION_STATUSES = {
    "FROZEN_FOR_VALIDATION", "LIVE_VALIDATING", "VALIDATED", "FAILED_LIVE"
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def initialize_evaluation_storage(runtime: Any) -> None:
    initialize_journal_storage(runtime)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_edge_candidate_checkpoints(
                candidate_id TEXT NOT NULL,
                checkpoint_n INTEGER NOT NULL,
                prospective_epoch_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                family_sha256 TEXT NOT NULL,
                sample_sha256 TEXT NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n INTEGER NOT NULL,
                dependency_cohort_even_n INTEGER NOT NULL,
                dependency_cohort_odd_n INTEGER NOT NULL,
                model_json TEXT NOT NULL,
                baseline_json TEXT NOT NULL,
                improvement_json TEXT NOT NULL,
                primary_improvement REAL NOT NULL,
                p_value REAL NOT NULL,
                q_value REAL NOT NULL,
                q_value_max REAL NOT NULL,
                decision TEXT NOT NULL,
                evaluated_ts REAL NOT NULL,
                contract_version TEXT NOT NULL,
                PRIMARY KEY(candidate_id,checkpoint_n)
            )"""
        )
        runtime._conn.execute(
            """CREATE INDEX IF NOT EXISTS ix_llm_edge_checkpoints_family
               ON llm_edge_candidate_checkpoints(
                 prospective_epoch_id,target_id,horizon_minutes,checkpoint_n
               )"""
        )
        runtime._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_edge_active_promotions(
                candidate_id TEXT PRIMARY KEY,
                promotion_sha256 TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                promoted_ts REAL NOT NULL,
                contract_version TEXT NOT NULL
            )"""
        )
        for table in ("llm_edge_candidate_checkpoints", "llm_edge_active_promotions"):
            runtime._conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable llm edge PR-B row'); END"""
            )
            runtime._conn.execute(
                f"""CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable llm edge PR-B row'); END"""
            )


def llm_candidate_states(registry: CandidateRegistry) -> list[dict[str, Any]]:
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
        if state and str(state.get("status")) in VALIDATION_STATUSES:
            output.append(state)
    return output


def _frozen(candidate: dict[str, Any]) -> dict[str, Any]:
    return (candidate.get("validation") or {}).get("frozen_spec") or {}


def _spec(candidate: dict[str, Any]) -> UniversalTargetSpec:
    frozen = _frozen(candidate)
    return UniversalTargetSpec(
        str(frozen["target_id"]),
        str(frozen.get("target_family") or candidate.get("target_family") or "UNKNOWN"),
        str(frozen["target_kind"]),
        tuple(str(item) for item in frozen.get("target_classes") or []),
        tuple(str(item) for item in frozen.get("primary_metrics") or []),
    )


def _prediction_events(engine: Any, candidate_id: str) -> dict[str, dict[str, Any]]:
    return {
        str(event["record_id"]): event
        for event in ledger_for_engine(engine).events()
        if event.get("event") == "PREDICTION"
        and str(event.get("candidate_id") or "") == str(candidate_id)
        and bool(event.get("qualified"))
    }


def _resolved_samples(
    engine: Any, runtime: Any, candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return only actually-written MATCH predictions with resolved future outcomes."""
    candidate_id = str(candidate["candidate_id"])
    predictions = _prediction_events(engine, candidate_id)
    if not predictions:
        return []
    with runtime._lock:
        rows = runtime._conn.execute(
            """SELECT
                 o.observation_id,o.prospective_record_id,o.instrument,o.captured_ts,
                 o.target_ts,o.horizon_minutes,x.outcome_value_json,x.resolved_ts
               FROM llm_edge_candidate_opportunities o
               JOIN llm_edge_candidate_outcomes x
                 ON x.candidate_id=o.candidate_id
                AND x.observation_id=o.observation_id
               WHERE o.candidate_id=? AND o.feature_available=1 AND o.feature_stale=0
                 AND o.matched=1 AND o.prospective_record_id IS NOT NULL
               ORDER BY o.captured_ts,o.observation_id""",
            (candidate_id,),
        ).fetchall()

    spec = _spec(candidate)
    result: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        record_id = str(item["prospective_record_id"])
        prediction_event = predictions.get(record_id)
        if prediction_event is None:
            continue
        t0 = float(item["captured_ts"])
        if abs(float(prediction_event["t0"]) - t0) > 1e-6:
            continue
        if float(prediction_event["recorded_ts"]) >= float(item["target_ts"]) - 1e-6:
            continue
        if float(item["resolved_ts"]) < float(item["target_ts"]) - 1e-6:
            continue
        try:
            outcome = json.loads(str(item["outcome_value_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        row = {
            "observation_id": str(item["observation_id"]),
            "record_id": record_id,
            "instrument": str(item["instrument"]),
            "captured_ts": t0,
            "target_ts": float(item["target_ts"]),
            "resolved_ts": float(item["resolved_ts"]),
            "horizon_minutes": int(item["horizon_minutes"]),
            "universal_outcome": outcome,
        }
        observed = target_value(row, spec)
        if observed is None:
            continue
        prediction = prediction_event.get("prediction") or {}
        if str(prediction.get("target_id") or "") != spec.target_id:
            continue
        row["universal_target_id"] = spec.target_id
        row["universal_target_value"] = observed
        row["candidate_prediction"] = prediction.get("candidate_prediction")
        row["baseline_prediction"] = prediction.get("baseline_prediction")
        result.append(row)
    return result


def _prediction_arrays(
    rows: list[dict[str, Any]], spec: UniversalTargetSpec
) -> tuple[np.ndarray, np.ndarray]:
    if spec.kind == "MULTICLASS":
        model = np.asarray([row["candidate_prediction"] for row in rows], dtype=float)
        baseline = np.asarray([row["baseline_prediction"] for row in rows], dtype=float)
        expected = (len(rows), len(spec.classes))
        if model.shape != expected or baseline.shape != expected:
            raise ValueError("invalid multiclass prospective prediction shape")
        return model, baseline
    return (
        np.asarray([float(row["candidate_prediction"]) for row in rows], dtype=float),
        np.asarray([float(row["baseline_prediction"]) for row in rows], dtype=float),
    )


def _evaluate_sample(
    rows: list[dict[str, Any]], candidate: dict[str, Any], checkpoint_n: int
) -> dict[str, Any]:
    # The first two looks are exactly frozen-N looks. The final exam begins at
    # N=96 but keeps accumulating until the dependency-aware daily cohorts are
    # actually testable; otherwise 96 intraday rows could create a false failure.
    sample = list(rows) if int(checkpoint_n) == CHECKPOINTS[-1] else rows[:checkpoint_n]
    spec = _spec(candidate)
    model_prediction, baseline_prediction = _prediction_arrays(sample, spec)
    model = target_metrics(sample, model_prediction, spec)
    baseline = target_metrics(sample, baseline_prediction, spec)
    improvement = relative_target_improvement(model, baseline, spec)
    cohorts = paired_target_dependency_cohorts(
        sample, model_prediction, baseline_prediction, spec
    )
    primary = min(float(improvement[name]) for name in spec.primary_metrics)
    sample_sha = _sha([
        {
            "observation_id": row["observation_id"],
            "record_id": row["record_id"],
            "captured_ts": row["captured_ts"],
            "resolved_ts": row["resolved_ts"],
            "target": row["universal_target_value"],
            "candidate_prediction": row["candidate_prediction"],
            "baseline_prediction": row["baseline_prediction"],
        }
        for row in sample
    ])
    return {
        "raw_n": len(sample),
        "effective_n": int(model["effective_n"]),
        "dependency_cohort_even_n": len(cohorts[0]),
        "dependency_cohort_odd_n": len(cohorts[1]),
        "model": model,
        "baseline": baseline,
        "improvement": improvement,
        "primary_improvement": primary,
        "p_value": float(
            paired_target_pvalue(sample, model_prediction, baseline_prediction, spec)
        ),
        "sample_sha256": sample_sha,
    }


def _checkpoint_row(
    runtime: Any, candidate_id: str, checkpoint_n: int
) -> dict[str, Any] | None:
    with runtime._lock:
        row = runtime._conn.execute(
            """SELECT * FROM llm_edge_candidate_checkpoints
               WHERE candidate_id=? AND checkpoint_n=? LIMIT 1""",
            (str(candidate_id), int(checkpoint_n)),
        ).fetchone()
    return None if row is None else dict(row)


def checkpoint_rows(runtime: Any, candidate_id: str) -> list[dict[str, Any]]:
    initialize_evaluation_storage(runtime)
    with runtime._lock:
        rows = runtime._conn.execute(
            """SELECT * FROM llm_edge_candidate_checkpoints
               WHERE candidate_id=? ORDER BY checkpoint_n""",
            (str(candidate_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def _family_key(
    candidate: dict[str, Any], checkpoint_n: int
) -> tuple[str, str, int, int]:
    frozen = _frozen(candidate)
    return (
        str(frozen.get("prospective_epoch_id") or "UNKNOWN"),
        str(candidate.get("target_id") or frozen.get("target_id") or ""),
        int(candidate.get("horizon_minutes") or frozen.get("horizon_minutes") or 0),
        int(checkpoint_n),
    )


def _family_members(
    states: list[dict[str, Any]], candidate: dict[str, Any], checkpoint_n: int
) -> list[dict[str, Any]]:
    key = _family_key(candidate, checkpoint_n)
    return sorted(
        (item for item in states if _family_key(item, checkpoint_n) == key),
        key=lambda item: str(item.get("candidate_id") or ""),
    )


def _eligible_for_final_exam(evaluation: dict[str, Any]) -> bool:
    return (
        int(evaluation.get("dependency_cohort_even_n") or 0) >= MIN_DEPENDENCY_COHORT_N
        and int(evaluation.get("dependency_cohort_odd_n") or 0) >= MIN_DEPENDENCY_COHORT_N
    )


def _fdr_for_candidate(
    engine: Any,
    runtime: Any,
    states: list[dict[str, Any]],
    candidate: dict[str, Any],
    checkpoint_n: int,
    candidate_eval: dict[str, Any],
    sample_cache: dict[str, list[dict[str, Any]]],
) -> tuple[float, str]:
    """BH over the pre-existing run family; not-yet-powered members are p=1."""
    members = _family_members(states, candidate, checkpoint_n)
    ids = [str(item["candidate_id"]) for item in members]
    candidate_id = str(candidate["candidate_id"])
    target_index = ids.index(candidate_id)
    p_values: list[float] = []
    for member in members:
        member_id = str(member["candidate_id"])
        if member_id == candidate_id:
            p_values.append(float(candidate_eval["p_value"]))
            continue
        existing = _checkpoint_row(runtime, member_id, checkpoint_n)
        if existing is not None:
            p_values.append(float(existing["p_value"]))
            continue
        rows = sample_cache.setdefault(
            member_id, _resolved_samples(engine, runtime, member)
        )
        if len(rows) < checkpoint_n:
            p_values.append(1.0)
            continue
        evaluation = _evaluate_sample(rows, member, checkpoint_n)
        if checkpoint_n == CHECKPOINTS[-1] and not _eligible_for_final_exam(evaluation):
            p_values.append(1.0)
        else:
            p_values.append(float(evaluation["p_value"]))
    q_values = benjamini_hochberg(p_values)
    family_sha = _sha({
        "scope": FDR_SCOPE,
        "key": _family_key(candidate, checkpoint_n),
        "candidate_ids": ids,
    })
    return float(q_values[target_index]), family_sha


def _insert_checkpoint(
    runtime: Any,
    candidate: dict[str, Any],
    checkpoint_n: int,
    evaluation: dict[str, Any],
    q_value: float,
    family_sha: str,
    decision: str,
    evaluated_ts: float,
) -> bool:
    epoch, target_id, horizon, _ = _family_key(candidate, checkpoint_n)
    with runtime._lock, runtime._conn:
        cursor = runtime._conn.execute(
            """INSERT OR IGNORE INTO llm_edge_candidate_checkpoints(
                 candidate_id,checkpoint_n,prospective_epoch_id,target_id,horizon_minutes,
                 family_sha256,sample_sha256,raw_n,effective_n,
                 dependency_cohort_even_n,dependency_cohort_odd_n,
                 model_json,baseline_json,improvement_json,primary_improvement,
                 p_value,q_value,q_value_max,decision,evaluated_ts,contract_version
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(candidate["candidate_id"]),
                int(checkpoint_n),
                epoch,
                target_id,
                horizon,
                family_sha,
                str(evaluation["sample_sha256"]),
                int(evaluation["raw_n"]),
                int(evaluation["effective_n"]),
                int(evaluation["dependency_cohort_even_n"]),
                int(evaluation["dependency_cohort_odd_n"]),
                _canonical(evaluation["model"]),
                _canonical(evaluation["baseline"]),
                _canonical(evaluation["improvement"]),
                float(evaluation["primary_improvement"]),
                float(evaluation["p_value"]),
                float(q_value),
                float(LOOK_ADJUSTED_Q_MAX),
                str(decision),
                float(evaluated_ts),
                CONTRACT_VERSION,
            ),
        )
    return bool(cursor.rowcount)


def _terminal_validation(
    candidate: dict[str, Any], checkpoint: dict[str, Any], *, confirmed: bool
) -> dict[str, Any]:
    validation = dict(candidate.get("validation") or {})
    validation["prospective_confirmation"] = {
        "contract_version": CONTRACT_VERSION,
        "evidence_label": "LIVE_PROSPECTIVE_OOS",
        "historical_discovery_evidence_counted": False,
        "checkpoint_n": int(checkpoint["checkpoint_n"]),
        "raw_n": int(checkpoint["raw_n"]),
        "effective_n": int(checkpoint["effective_n"]),
        "dependency_cohort_even_n": int(checkpoint["dependency_cohort_even_n"]),
        "dependency_cohort_odd_n": int(checkpoint["dependency_cohort_odd_n"]),
        "primary_improvement": float(checkpoint["primary_improvement"]),
        "p_value": float(checkpoint["p_value"]),
        "q_value": float(checkpoint["q_value"]),
        "q_value_max": float(checkpoint["q_value_max"]),
        "fdr_scope": FDR_SCOPE,
        "family_sha256": str(checkpoint["family_sha256"]),
        "sample_sha256": str(checkpoint["sample_sha256"]),
        "decision": str(checkpoint["decision"]),
        "prospective_confirmed": bool(confirmed),
        "production_authority": False,
        "auto_promotion": False,
    }
    validation["prospective_confirmed"] = bool(confirmed)
    return validation


def _transition_from_checkpoint(
    registry: CandidateRegistry,
    candidate_id: str,
    checkpoint: dict[str, Any],
    *,
    event_ts: float,
) -> str | None:
    state = registry.current(candidate_id)
    if state is None or str(state.get("status")) != "LIVE_VALIDATING":
        return None
    decision = str(checkpoint["decision"])
    if decision not in {"PASS", "FAIL"}:
        return None
    to_status = "VALIDATED" if decision == "PASS" else "FAILED_LIVE"
    registry.transition(
        candidate_id,
        to_status,
        event_ts=event_ts,
        validation=_terminal_validation(
            state, checkpoint, confirmed=(to_status == "VALIDATED")
        ),
    )
    return to_status


def _promotion_payload(state: dict[str, Any]) -> dict[str, Any]:
    validation = state.get("validation") or {}
    confirmation = validation.get("prospective_confirmation") or {}
    frozen = validation.get("frozen_spec") or {}
    if (
        str(state.get("status")) != "VALIDATED"
        or not bool(confirmation.get("prospective_confirmed"))
        or str(confirmation.get("evidence_label")) != "LIVE_PROSPECTIVE_OOS"
    ):
        raise ValueError("only prospectively validated candidates may enter Active Edge")
    return {
        "contract_version": PROMOTION_CONTRACT_VERSION,
        "candidate_id": str(state["candidate_id"]),
        "hypothesis_id": state.get("hypothesis_id"),
        "source": "LLM_PROSPECTIVE_VALIDATED",
        "target_id": str(state.get("target_id") or frozen.get("target_id") or ""),
        "target_family": str(
            state.get("target_family") or frozen.get("target_family") or ""
        ),
        "target_kind": str(state.get("target_kind") or frozen.get("target_kind") or ""),
        "target_classes": list(frozen.get("target_classes") or []),
        "horizon_minutes": int(
            state.get("horizon_minutes") or frozen.get("horizon_minutes") or 0
        ),
        "conditions": (
            (frozen.get("rule") or {}).get("conditions")
            or frozen.get("conditions")
            or []
        ),
        "rule_sha256": frozen.get("rule_sha256"),
        "state_residual": frozen.get("state_residual"),
        "discovery_q_value": frozen.get("discovery_q_value"),
        "discovery_effect": frozen.get("discovery_effect"),
        "prospective_checkpoint": confirmation,
        "prospective_validated": True,
        "eligible_for_active_edge": True,
        "automatic_execution": False,
        "may_override_cvar_floor": False,
        "may_widen_stop": False,
        "promotion_basis": "VALIDATED_LIVE_PROSPECTIVE_OOS",
    }


def _promote_validated(
    runtime: Any, registry: CandidateRegistry, *, now: float
) -> int:
    inserted = 0
    for state in llm_candidate_states(registry):
        if str(state.get("status")) != "VALIDATED":
            continue
        payload = _promotion_payload(state)
        promotion_sha = _sha(payload)
        with runtime._lock, runtime._conn:
            cursor = runtime._conn.execute(
                """INSERT OR IGNORE INTO llm_edge_active_promotions(
                     candidate_id,promotion_sha256,payload_json,promoted_ts,contract_version
                   ) VALUES(?,?,?,?,?)""",
                (
                    str(state["candidate_id"]),
                    promotion_sha,
                    _canonical(payload),
                    float(now),
                    PROMOTION_CONTRACT_VERSION,
                ),
            )
        inserted += int(bool(cursor.rowcount))
    return inserted


def active_promotions(engine: Any) -> list[dict[str, Any]]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return []
    initialize_evaluation_storage(runtime)
    with runtime._lock:
        rows = runtime._conn.execute(
            """SELECT candidate_id,payload_json,promoted_ts,promotion_sha256
               FROM llm_edge_active_promotions ORDER BY promoted_ts,candidate_id"""
        ).fetchall()
    output = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not bool(payload.get("prospective_validated")):
            continue
        payload["promoted_ts"] = float(row["promoted_ts"])
        payload["promotion_sha256"] = str(row["promotion_sha256"])
        output.append(payload)
    return output


def candidate_evaluation_snapshot(runtime: Any, candidate_id: str) -> dict[str, Any]:
    rows = checkpoint_rows(runtime, candidate_id)
    latest = rows[-1] if rows else None
    return {
        "checkpoints": [
            {
                "checkpoint_n": int(row["checkpoint_n"]),
                "raw_n": int(row["raw_n"]),
                "effective_n": int(row["effective_n"]),
                "dependency_cohort_even_n": int(row["dependency_cohort_even_n"]),
                "dependency_cohort_odd_n": int(row["dependency_cohort_odd_n"]),
                "primary_improvement": float(row["primary_improvement"]),
                "p_value": float(row["p_value"]),
                "q_value": float(row["q_value"]),
                "q_value_max": float(row["q_value_max"]),
                "decision": str(row["decision"]),
            }
            for row in rows
        ],
        "next_checkpoint": next(
            (
                value
                for value in CHECKPOINTS
                if not any(int(row["checkpoint_n"]) == value for row in rows)
            ),
            None,
        ),
        "latest": None
        if latest is None
        else {
            "checkpoint_n": int(latest["checkpoint_n"]),
            "raw_n": int(latest["raw_n"]),
            "effect": float(latest["primary_improvement"]),
            "p": float(latest["p_value"]),
            "q": float(latest["q_value"]),
            "decision": str(latest["decision"]),
        },
    }


def evaluate_and_promote(
    engine: Any, *, now: float | None = None
) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"status": "UNAVAILABLE", "reason": "G1S_RUNTIME_UNAVAILABLE"}
    initialize_evaluation_storage(runtime)
    registry = registry_for_engine(engine)
    states = llm_candidate_states(registry)
    current = float(time.time() if now is None else now)
    samples: dict[str, list[dict[str, Any]]] = {}
    inserted = 0
    transitions: list[dict[str, Any]] = []
    awaiting_independent_days = 0

    for candidate in states:
        if str(candidate.get("status")) != "LIVE_VALIDATING":
            continue
        candidate_id = str(candidate["candidate_id"])
        rows = samples.setdefault(
            candidate_id, _resolved_samples(engine, runtime, candidate)
        )
        for checkpoint_n in CHECKPOINTS:
            existing = _checkpoint_row(runtime, candidate_id, checkpoint_n)
            if existing is not None:
                terminal = _transition_from_checkpoint(
                    registry, candidate_id, existing, event_ts=current
                )
                if terminal:
                    transitions.append({
                        "candidate_id": candidate_id,
                        "checkpoint_n": checkpoint_n,
                        "to_status": terminal,
                        "recovered_from_persisted_checkpoint": True,
                    })
                    break
                continue

            if len(rows) < checkpoint_n:
                break
            evaluation = _evaluate_sample(rows, candidate, checkpoint_n)
            if (
                checkpoint_n == CHECKPOINTS[-1]
                and not _eligible_for_final_exam(evaluation)
            ):
                awaiting_independent_days += 1
                break

            q_value, family_sha = _fdr_for_candidate(
                engine,
                runtime,
                states,
                candidate,
                checkpoint_n,
                evaluation,
                samples,
            )
            passed = (
                q_value <= LOOK_ADJUSTED_Q_MAX
                and float(evaluation["primary_improvement"])
                >= MIN_PRIMARY_IMPROVEMENT
            )
            decision = (
                "PASS"
                if passed
                else "FAIL"
                if checkpoint_n == CHECKPOINTS[-1]
                else "CONTINUE"
            )
            if _insert_checkpoint(
                runtime,
                candidate,
                checkpoint_n,
                evaluation,
                q_value,
                family_sha,
                decision,
                current,
            ):
                inserted += 1
            checkpoint = _checkpoint_row(runtime, candidate_id, checkpoint_n)
            if checkpoint is None:
                continue
            terminal = _transition_from_checkpoint(
                registry, candidate_id, checkpoint, event_ts=current
            )
            if terminal:
                transitions.append({
                    "candidate_id": candidate_id,
                    "checkpoint_n": checkpoint_n,
                    "to_status": terminal,
                    "recovered_from_persisted_checkpoint": False,
                })
                break

    # Crash boundary: a VALIDATED registry transition can exist before its SQLite
    # promotion row. Re-scanning terminal registry states makes this idempotent.
    promotions_inserted = _promote_validated(runtime, registry, now=current)
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "OK",
        "checkpoints_inserted": inserted,
        "transitions": transitions,
        "promotions_inserted": promotions_inserted,
        "active_validated_promotions": len(active_promotions(engine)),
        "awaiting_independent_days": awaiting_independent_days,
        "checkpoints": list(CHECKPOINTS),
        "overall_fdr_budget": OVERALL_FDR_BUDGET,
        "look_adjusted_q_value_max": LOOK_ADJUSTED_Q_MAX,
        "minimum_primary_improvement": MIN_PRIMARY_IMPROVEMENT,
        "minimum_dependency_cohort_n": MIN_DEPENDENCY_COHORT_N,
        "fdr_scope": FDR_SCOPE,
        "historical_discovery_evidence_counted": False,
        "premature_active_edge_influence": False,
    }
