"""Materialized LLM Edge Researcher lifecycle and bounded worker tick."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from .llm_edge_candidate_lifecycle import freeze_discovery_signals, registry_for_engine
from .llm_edge_prospective_evaluation import (
    active_promotions,
    candidate_evaluation_snapshot,
    evaluate_and_promote,
    llm_candidate_states,
)
from .llm_edge_prospective_journal import (
    collect_opportunities,
    collect_outcomes,
    initialize_journal_storage,
)

LIFECYCLE_CONTRACT_VERSION = "llm-edge-lifecycle-v1.3-pr-b"
_MATERIALIZED_CACHE_ATTR = "_llm_edge_lifecycle_payload_json"


def _initializing_materialized_lifecycle() -> dict[str, Any]:
    return {
        "contract_version": LIFECYCLE_CONTRACT_VERSION,
        "status": "INITIALIZING",
        "researcher": {
            "proposal_runs": 0, "hypotheses": 0, "pending_hypotheses": 0,
            "discovery_signals": 0, "frozen_prospective": 0, "collecting": 0,
            "underpowered": 0, "prospective_pass": 0, "prospective_fail": 0,
            "active_edge": 0, "strict_reference": 0, "rejected": 0,
        },
        "candidates": [],
        "research_hypotheses": [],
        "writes_active_edge_registry": True,
        "production_authority": False,
        "prospective_confirmation_enabled": True,
        "active_edge_bridge_enabled": True,
        "request_time_history_scan": False,
    }


def publish_materialized_lifecycle_cache(runtime: Any, payload_json: str) -> None:
    """Atomically expose one committed serialized singleton to HTTP readers."""
    setattr(runtime, _MATERIALIZED_CACHE_ATTR, str(payload_json))


def read_cached_materialized_lifecycle_json(runtime: Any) -> str:
    payload_json = getattr(runtime, _MATERIALIZED_CACHE_ATTR, None)
    if isinstance(payload_json, str):
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            return payload_json
    return json.dumps(
        _initializing_materialized_lifecycle(),
        sort_keys=True,
        separators=(",", ":"),
    )


def read_cached_materialized_lifecycle(runtime: Any) -> dict[str, Any]:
    return json.loads(read_cached_materialized_lifecycle_json(runtime))


def researcher_enabled() -> bool:
    value = os.environ.get("LLM_EDGE_RESEARCHER_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def _candidate_details(runtime: Any, candidates: list[dict[str, Any]],
                       *, promoted_ids: set[str]) -> list[dict[str, Any]]:
    output = []
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        validation = candidate.get("validation") or {}
        frozen = validation.get("frozen_spec") or {}
        with runtime._lock:
            row = runtime._conn.execute("""SELECT
                  SUM(CASE WHEN feature_available=1 AND matched IS NOT NULL THEN 1 ELSE 0 END) eligible_n,
                  SUM(CASE WHEN feature_available=1 AND matched=1 THEN 1 ELSE 0 END) matched_n,
                  SUM(CASE WHEN feature_available=0 THEN 1 ELSE 0 END) unavailable_n,
                  SUM(CASE WHEN reason='MISSED_PREDICTION_WINDOW' THEN 1 ELSE 0 END) missed_n
                FROM llm_edge_candidate_opportunities WHERE candidate_id=?""",
                (candidate_id,)).fetchone()
        evaluation = candidate_evaluation_snapshot(runtime, candidate_id)
        latest = evaluation.get("latest") or {}
        promoted = candidate_id in promoted_ids
        output.append({
            "candidate_id": candidate_id,
            "name": frozen.get("name") or candidate_id,
            "source": "LLM",
            "hypothesis_id": frozen.get("hypothesis_id") or candidate.get("hypothesis_id"),
            "evaluation_id": frozen.get("source_evaluation_id"),
            "target": candidate.get("target_id"),
            "horizon": candidate.get("horizon_minutes"),
            "conditions": frozen.get("conditions") or [],
            "rule_sha256": frozen.get("rule_sha256"),
            "prospective_epoch_id": frozen.get("prospective_epoch_id"),
            "discovery": {
                "effect": frozen.get("discovery_effect"),
                "q": frozen.get("discovery_q_value"),
                "p": frozen.get("discovery_p_value"),
                "folds": frozen.get("discovery_fold_count"),
                "dataset_sha256": frozen.get("discovery_dataset_sha256"),
            },
            "prospective": {
                "eligible_opportunities": int(row["eligible_n"] or 0),
                "matched_n": int(row["matched_n"] or 0),
                "unavailable_opportunities": int(row["unavailable_n"] or 0),
                "missed_prediction_windows": int(row["missed_n"] or 0),
                "next_checkpoint": evaluation.get("next_checkpoint"),
                "effect": latest.get("effect"),
                "p": latest.get("p"),
                "q": latest.get("q"),
                "decision": latest.get("decision"),
                "checkpoints": evaluation.get("checkpoints") or [],
                "evidence_label": "LIVE_PROSPECTIVE_OOS",
                "historical_discovery_evidence_counted": False,
            },
            "state": str(candidate.get("status") or "UNKNOWN"),
            "active_edge_status": "PROMOTED_VALIDATED" if promoted else None,
            "active_edge_eligible": promoted,
            "production_authority": promoted,
            "automatic_execution": False,
        })
    return output


def materialize_lifecycle(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"status": "UNAVAILABLE", "reason": "G1S_RUNTIME_UNAVAILABLE"}
    initialize_journal_storage(runtime)
    registry = registry_for_engine(engine)
    candidates = llm_candidate_states(registry)
    promotions = active_promotions(engine)
    promoted_ids = {str(item.get("candidate_id") or "") for item in promotions}
    details = _candidate_details(runtime, candidates, promoted_ids=promoted_ids)
    with runtime._lock:
        proposal_runs = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_research_runs").fetchone()[0])
        hypotheses = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_hypotheses").fetchone()[0])
        evaluations = runtime._conn.execute(
            "SELECT result_json FROM llm_edge_evaluations").fetchall()
        journal = runtime._conn.execute("""SELECT COUNT(*) total_n,
              SUM(CASE WHEN feature_available=1 AND matched IS NOT NULL THEN 1 ELSE 0 END) eligible_n,
              SUM(CASE WHEN feature_available=1 AND matched=1 THEN 1 ELSE 0 END) matched_n,
              SUM(CASE WHEN feature_available=0 THEN 1 ELSE 0 END) unavailable_n,
              SUM(CASE WHEN reason='MISSED_PREDICTION_WINDOW' THEN 1 ELSE 0 END) missed_n
            FROM llm_edge_candidate_opportunities""").fetchone()
        outcome_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_candidate_outcomes").fetchone()[0])
        recent_rows = []
        try:
            recent_rows = runtime._conn.execute("""
                SELECT h.hypothesis_id, h.name, h.target_id, h.target_family,
                       h.horizon_minutes, h.conditions_json, h.status,
                       h.evaluation_state, h.created_ts,
                       e.result_json
                FROM llm_edge_hypotheses h
                LEFT JOIN llm_edge_evaluations e ON h.hypothesis_id = e.hypothesis_id
                ORDER BY h.created_ts DESC
                LIMIT 30
            """).fetchall()
        except Exception:
            recent_rows = []

    discovery = rejected = 0
    for raw in evaluations:
        try:
            result = json.loads(str(raw[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        discovery += int(result.get("status") == "DISCOVERY_SIGNAL")
        rejected += int(result.get("status") in {"RESEARCH_DIAGNOSTIC", "INSUFFICIENT_DATA"})

    research_hypotheses = []
    for r in recent_rows:
        row = dict(r) if not isinstance(r, dict) else r
        try:
            conds = json.loads(str(row.get("conditions_json") or "[]"))
        except Exception:
            conds = []
        eval_result = None
        if row.get("result_json"):
            try:
                eval_result = json.loads(str(row["result_json"]))
            except Exception:
                pass

        if eval_result is None:
            stage_code = "PENDING_DETERMINISTIC_EVALUATION"
            stage_label = "В ОЧЕРЕДИ НА ОЦЕНКУ"
            p_val = q_val = effect = reason = folds = None
        else:
            status_code = str(eval_result.get("status") or "")
            if status_code == "DISCOVERY_SIGNAL":
                stage_code = "DISCOVERY_SIGNAL"
                stage_label = "СТАТИСТИЧЕСКИЙ ПЕРЕВЕС"
            else:
                stage_code = "RESEARCH_REJECTED"
                stage_label = "ОТКЛОНЕНО (ШУМ)"
            p_val = eval_result.get("p_value")
            q_val = eval_result.get("q_value")
            effect = eval_result.get("relative_improvement") or eval_result.get("primary_improvement")
            reason = eval_result.get("reason")
            folds = eval_result.get("folds_stable")

        research_hypotheses.append({
            "hypothesis_id": str(row.get("hypothesis_id") or ""),
            "name": str(row.get("name") or ""),
            "target": str(row.get("target_id") or ""),
            "target_family": str(row.get("target_family") or ""),
            "horizon_minutes": int(row.get("horizon_minutes") or 0),
            "conditions": conds,
            "created_ts": float(row.get("created_ts") or 0.0),
            "stage": {"code": stage_code, "label_ru": stage_label},
            "p_value": p_val,
            "q_value": q_val,
            "effect": effect,
            "folds_stable": folds,
            "rejection_reason": reason,
        })

    statuses = [str(item.get("state") or "") for item in details]
    strict_reference = sum(
        1 for item in details
        if (item.get("prospective") or {}).get("q") is not None
        and float((item.get("prospective") or {})["q"]) <= 0.10
    )
    pending_hypotheses = max(0, hypotheses - len(evaluations))
    payload = {
        "contract_version": LIFECYCLE_CONTRACT_VERSION,
        "status": "OK" if researcher_enabled() else "DISABLED",
        "researcher": {
            "proposal_runs": proposal_runs,
            "hypotheses": hypotheses,
            "pending_hypotheses": pending_hypotheses,
            "discovery_signals": discovery,
            "frozen_prospective": len(details),
            "collecting": sum(state in {"FROZEN_FOR_VALIDATION", "LIVE_VALIDATING"}
                              for state in statuses),
            "underpowered": sum(
                state in {"FROZEN_FOR_VALIDATION", "LIVE_VALIDATING"}
                and not (item.get("prospective") or {}).get("checkpoints")
                for state, item in zip(statuses, details)),
            "prospective_pass": statuses.count("VALIDATED"),
            "prospective_fail": statuses.count("FAILED_LIVE"),
            "active_edge": len(promotions),
            "strict_reference": strict_reference,
            "rejected": rejected,
        },
        "prospective_journal": {
            "opportunities_total": int(journal["total_n"] or 0),
            "eligible_opportunities": int(journal["eligible_n"] or 0),
            "matched_opportunities": int(journal["matched_n"] or 0),
            "unavailable_opportunities": int(journal["unavailable_n"] or 0),
            "missed_prediction_windows": int(journal["missed_n"] or 0),
            "resolved_outcomes": outcome_n,
        },
        "candidates": details,
        "research_hypotheses": research_hypotheses,
        "writes_active_edge_registry": True,
        "production_authority": False,
        "prospective_confirmation_enabled": True,
        "active_edge_bridge_enabled": True,
        "promotion_requires_status": "VALIDATED",
        "request_time_history_scan": False,
        "updated_ts": float(time.time() if now is None else now),
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with runtime._lock:
        with runtime._conn:
            runtime._conn.execute("""INSERT INTO llm_edge_lifecycle_materialized(
                  singleton_id,payload_json,updated_ts) VALUES(1,?,?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                  payload_json=excluded.payload_json,updated_ts=excluded.updated_ts""", (
                    payload_json,
                    payload["updated_ts"],
                ))
        publish_materialized_lifecycle_cache(runtime, payload_json)
    return payload


def read_materialized_lifecycle(runtime: Any) -> dict[str, Any]:
    try:
        with runtime._lock:
            row = runtime._conn.execute(
                "SELECT payload_json FROM llm_edge_lifecycle_materialized WHERE singleton_id=1 LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError as exc:
        # Request-time readers remain read-only. A minimal test runtime or the
        # short startup interval before worker initialization is INITIALIZING,
        # never a reason to create schema or scan history from HTTP.
        if "no such table" not in str(exc).lower():
            raise
        row = None
    if row is not None:
        return json.loads(str(row[0]))
    return _initializing_materialized_lifecycle()


def llm_edge_prospective_tick(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    """Worker phase: resolve future outcomes, evaluate/promote, freeze, then new T0."""
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"status": "UNAVAILABLE", "reason": "G1S_RUNTIME_UNAVAILABLE"}
    initialize_journal_storage(runtime)
    current = float(time.time() if now is None else now)
    if not researcher_enabled():
        lifecycle = materialize_lifecycle(engine, now=current)
        return {
            "status": "DISABLED",
            "reason": "LLM_EDGE_RESEARCHER_ENABLED_FALSE",
            "lifecycle_updated_ts": lifecycle.get("updated_ts"),
            "production_authority": False,
            "writes_active_edge_registry": True,
        }
    outcomes = collect_outcomes(engine, now=current)
    prospective_evaluation = evaluate_and_promote(engine, now=current)
    freeze = freeze_discovery_signals(engine, now=current)
    opportunities = collect_opportunities(engine, now=current)
    lifecycle = materialize_lifecycle(engine, now=current)
    return {
        "contract_version": LIFECYCLE_CONTRACT_VERSION,
        "status": "OK",
        "outcomes": outcomes,
        "prospective_evaluation": prospective_evaluation,
        "freeze": freeze,
        "opportunities": opportunities,
        "lifecycle_updated_ts": lifecycle.get("updated_ts"),
        "production_authority": False,
        "writes_active_edge_registry": True,
        "promotion_requires_status": "VALIDATED",
        "premature_active_edge_influence": False,
        "may_change_position_manager": True,
        "position_manager_change_condition": "VALIDATED_ACTIVE_EDGE_ONLY",
        "may_change_cvar_stop_or_size": False,
    }
