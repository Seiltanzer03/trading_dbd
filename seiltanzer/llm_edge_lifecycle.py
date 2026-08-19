"""Materialized LLM Edge Researcher lifecycle and bounded worker tick."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from .llm_edge_candidate_lifecycle import (
    active_llm_candidates,
    freeze_discovery_signals,
    registry_for_engine,
)
from .llm_edge_prospective_journal import (
    collect_opportunities,
    collect_outcomes,
    initialize_journal_storage,
)

LIFECYCLE_CONTRACT_VERSION = "llm-edge-lifecycle-v1.3"


def researcher_enabled() -> bool:
    value = os.environ.get("LLM_EDGE_RESEARCHER_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def _candidate_details(runtime: Any, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        validation = candidate.get("validation") or {}
        frozen = validation.get("frozen_spec") or {}
        with runtime._lock:
            row = runtime._conn.execute(
                """SELECT
                     SUM(CASE WHEN feature_available=1 AND matched IS NOT NULL THEN 1 ELSE 0 END) eligible_n,
                     SUM(CASE WHEN feature_available=1 AND matched=1 THEN 1 ELSE 0 END) matched_n,
                     SUM(CASE WHEN feature_available=0 THEN 1 ELSE 0 END) unavailable_n,
                     SUM(CASE WHEN reason='MISSED_PREDICTION_WINDOW' THEN 1 ELSE 0 END) missed_n
                   FROM llm_edge_candidate_opportunities WHERE candidate_id=?""",
                (candidate_id,),
            ).fetchone()
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
                "next_checkpoint": None,
                "effect": None,
                "q": None,
            },
            "state": "COLLECTING_PROSPECTIVE",
            "active_edge_status": None,
            "production_authority": False,
        })
    return output


def materialize_lifecycle(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"status": "UNAVAILABLE", "reason": "G1S_RUNTIME_UNAVAILABLE"}
    initialize_journal_storage(runtime)
    candidates = active_llm_candidates(registry_for_engine(engine))
    details = _candidate_details(runtime, candidates)
    with runtime._lock:
        proposal_runs = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_research_runs"
        ).fetchone()[0])
        hypotheses = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_hypotheses"
        ).fetchone()[0])
        evaluations = runtime._conn.execute(
            "SELECT result_json FROM llm_edge_evaluations"
        ).fetchall()
        journal = runtime._conn.execute(
            """SELECT COUNT(*) total_n,
                 SUM(CASE WHEN feature_available=1 AND matched IS NOT NULL THEN 1 ELSE 0 END) eligible_n,
                 SUM(CASE WHEN feature_available=1 AND matched=1 THEN 1 ELSE 0 END) matched_n,
                 SUM(CASE WHEN feature_available=0 THEN 1 ELSE 0 END) unavailable_n,
                 SUM(CASE WHEN reason='MISSED_PREDICTION_WINDOW' THEN 1 ELSE 0 END) missed_n
               FROM llm_edge_candidate_opportunities"""
        ).fetchone()
        outcome_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_candidate_outcomes"
        ).fetchone()[0])

    discovery = rejected = 0
    for raw in evaluations:
        try:
            result = json.loads(str(raw[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        discovery += int(result.get("status") == "DISCOVERY_SIGNAL")
        rejected += int(result.get("status") in {"RESEARCH_DIAGNOSTIC", "INSUFFICIENT_DATA"})

    payload = {
        "contract_version": LIFECYCLE_CONTRACT_VERSION,
        "status": "OK" if researcher_enabled() else "DISABLED",
        "researcher": {
            "proposal_runs": proposal_runs,
            "hypotheses": hypotheses,
            "discovery_signals": discovery,
            "frozen_prospective": len(details),
            "collecting": len(details),
            "underpowered": len(details),
            "prospective_pass": 0,
            "prospective_fail": 0,
            "active_edge": 0,
            "strict_reference": 0,
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
        "writes_active_edge_registry": False,
        "production_authority": False,
        "prospective_confirmation_enabled": False,
        "active_edge_bridge_enabled": False,
        "request_time_history_scan": False,
        "updated_ts": float(time.time() if now is None else now),
    }
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            """INSERT INTO llm_edge_lifecycle_materialized(singleton_id,payload_json,updated_ts)
               VALUES(1,?,?)
               ON CONFLICT(singleton_id) DO UPDATE SET
                 payload_json=excluded.payload_json,updated_ts=excluded.updated_ts""",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), payload["updated_ts"]),
        )
    return payload


def read_materialized_lifecycle(runtime: Any) -> dict[str, Any]:
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT payload_json FROM llm_edge_lifecycle_materialized WHERE singleton_id=1 LIMIT 1"
        ).fetchone()
    if row is not None:
        return json.loads(str(row[0]))
    return {
        "contract_version": LIFECYCLE_CONTRACT_VERSION,
        "status": "INITIALIZING",
        "researcher": {
            "proposal_runs": 0, "hypotheses": 0, "discovery_signals": 0,
            "frozen_prospective": 0, "collecting": 0, "underpowered": 0,
            "prospective_pass": 0, "prospective_fail": 0, "active_edge": 0,
            "strict_reference": 0, "rejected": 0,
        },
        "candidates": [],
        "writes_active_edge_registry": False,
        "production_authority": False,
        "request_time_history_scan": False,
    }


def llm_edge_prospective_tick(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    """Existing worker phase: outcomes first, then freeze, then only new T0."""
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
            "writes_active_edge_registry": False,
        }
    outcomes = collect_outcomes(engine, now=current)
    freeze = freeze_discovery_signals(engine, now=current)
    opportunities = collect_opportunities(engine, now=current)
    lifecycle = materialize_lifecycle(engine, now=current)
    return {
        "contract_version": LIFECYCLE_CONTRACT_VERSION,
        "status": "OK",
        "outcomes": outcomes,
        "freeze": freeze,
        "opportunities": opportunities,
        "lifecycle_updated_ts": lifecycle.get("updated_ts"),
        "production_authority": False,
        "writes_active_edge_registry": False,
        "may_change_position_manager": False,
        "may_change_cvar_stop_or_size": False,
    }
