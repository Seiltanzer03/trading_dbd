"""LLM Edge Researcher v1.3 PR C: bounded automation and parity refinements.

This module finishes the lifecycle without creating a second research engine:
- the existing low-priority EDE/G1S worker remains the only execution loop;
- only materialized state is exposed to HTTP/AI/UI;
- automatic proposals require both new resolved evidence and a long provider interval;
- semantic duplicates are rejected before a new evaluation can be created;
- prospectively validated LLM edge keeps the exact existing 30%/40% STRICT_REFERENCE cap.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from typing import Any, Callable

from . import llm_edge_evaluator as _evaluator
from . import llm_edge_lifecycle as _lifecycle
from . import llm_edge_prospective_evaluation as _prospective
from . import llm_edge_researcher as _researcher
from . import llm_validated_active_edge_bridge as _validated_bridge
from . import research_llm_cost_guard as _cost_guard
from .edge_discovery.active_edge_policy import (
    STRICT_REFERENCE,
    _strict_reference_qualified,
)

PR_C_CONTRACT_VERSION = "llm-edge-researcher-v1.3-pr-c"
AUTOMATION_CONTRACT_VERSION = "llm-edge-research-automation-v1"
RESEARCH_QUALITY_CONTRACT_VERSION = "llm-edge-research-quality-v1"
PROMOTION_PARITY_CONTRACT_VERSION = "llm-edge-active-parity-v1"

AUTO_MIN_NEW_RESOLVED_T0 = 100
AUTO_MIN_PROVIDER_INTERVAL_SEC = 12 * 60 * 60
AUTO_MAX_HYPOTHESES = 5

_STATE_TABLE = "llm_edge_research_automation_state"
_DEDUPE_TABLE = "llm_edge_hypothesis_dedup_events"
_AUTO_LOCK = threading.Lock()
_INSTALLED = False


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    den = float(denominator)
    if den <= 0:
        return None
    return float(numerator) / den


def _ensure_storage(runtime: Any) -> None:
    """Worker/startup storage only; HTTP readers never call this."""
    _researcher._ensure_tables(runtime)
    _evaluator._ensure_tables(runtime)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {_STATE_TABLE}(
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                contract_version TEXT NOT NULL,
                last_provider_call_ts REAL,
                last_automatic_run_ts REAL,
                last_automatic_run_id TEXT,
                last_run_resolved_ts REAL NOT NULL DEFAULT 0,
                last_run_resolved_observation_id TEXT NOT NULL DEFAULT '',
                automatic_provider_attempts INTEGER NOT NULL DEFAULT 0,
                automatic_provider_failures INTEGER NOT NULL DEFAULT 0,
                automatic_cache_hits INTEGER NOT NULL DEFAULT 0,
                automatic_orchestrations INTEGER NOT NULL DEFAULT 0,
                last_status TEXT,
                last_error TEXT,
                updated_ts REAL NOT NULL
            )"""
        )
        runtime._conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {_DEDUPE_TABLE}(
                event_id TEXT PRIMARY KEY,
                hypothesis_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                rejected_ts REAL NOT NULL,
                contract_version TEXT NOT NULL
            )"""
        )
        runtime._conn.execute(
            f"""CREATE TRIGGER IF NOT EXISTS {_DEDUPE_TABLE}_immutable_update
                BEFORE UPDATE ON {_DEDUPE_TABLE}
                BEGIN SELECT RAISE(ABORT,'immutable llm edge dedupe row'); END"""
        )
        runtime._conn.execute(
            f"""CREATE TRIGGER IF NOT EXISTS {_DEDUPE_TABLE}_immutable_delete
                BEFORE DELETE ON {_DEDUPE_TABLE}
                BEGIN SELECT RAISE(ABORT,'immutable llm edge dedupe row'); END"""
        )
        row = runtime._conn.execute(
            f"SELECT 1 FROM {_STATE_TABLE} WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            latest_run = runtime._conn.execute(
                "SELECT created_ts FROM llm_edge_research_runs "
                "ORDER BY created_ts DESC LIMIT 1"
            ).fetchone()
            latest_run_ts = float(latest_run[0]) if latest_run is not None else 0.0
            boundary = runtime._conn.execute(
                """SELECT r.resolved_ts,o.observation_id
                   FROM g1s_resolutions r
                   JOIN g1s_observations o USING(observation_id)
                   WHERE o.horizon_minutes IN (15,30,60,120,240)
                     AND r.resolved_ts<=?
                   ORDER BY r.resolved_ts DESC,o.observation_id DESC LIMIT 1""",
                (latest_run_ts,),
            ).fetchone()
            cursor_ts = float(boundary[0]) if boundary is not None else 0.0
            cursor_id = str(boundary[1]) if boundary is not None else ""
            runtime._conn.execute(
                f"""INSERT INTO {_STATE_TABLE}(
                     singleton_id,contract_version,last_provider_call_ts,
                     last_run_resolved_ts,last_run_resolved_observation_id,
                     updated_ts
                   ) VALUES(1,?,?,?,?,?)""",
                (
                    AUTOMATION_CONTRACT_VERSION,
                    latest_run_ts if latest_run_ts > 0 else None,
                    cursor_ts,
                    cursor_id,
                    time.time(),
                ),
            )


def _automation_state(runtime: Any) -> dict[str, Any]:
    with runtime._lock:
        row = runtime._conn.execute(
            f"SELECT * FROM {_STATE_TABLE} WHERE singleton_id=1"
        ).fetchone()
    return {} if row is None else dict(row)


def _update_state(runtime: Any, **values: Any) -> None:
    allowed = {
        "last_provider_call_ts", "last_automatic_run_ts", "last_automatic_run_id",
        "last_run_resolved_ts", "last_run_resolved_observation_id",
        "automatic_provider_attempts", "automatic_provider_failures",
        "automatic_cache_hits", "automatic_orchestrations",
        "last_status", "last_error",
    }
    updates = {key: value for key, value in values.items() if key in allowed}
    updates["contract_version"] = AUTOMATION_CONTRACT_VERSION
    updates["updated_ts"] = time.time()
    assignments = ",".join(f"{key}=?" for key in updates)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            f"UPDATE {_STATE_TABLE} SET {assignments} WHERE singleton_id=1",
            tuple(updates.values()),
        )


def _resolved_evidence_since_cursor(
    runtime: Any, state: dict[str, Any]
) -> tuple[int, tuple[float, str] | None]:
    ts = float(state.get("last_run_resolved_ts") or 0.0)
    observation_id = str(state.get("last_run_resolved_observation_id") or "")
    with runtime._lock:
        count = int(runtime._conn.execute(
            """SELECT COUNT(*)
               FROM g1s_resolutions r
               JOIN g1s_observations o USING(observation_id)
               WHERE o.horizon_minutes IN (15,30,60,120,240)
                 AND (r.resolved_ts>? OR
                      (r.resolved_ts=? AND o.observation_id>?))""",
            (ts, ts, observation_id),
        ).fetchone()[0])
        latest = runtime._conn.execute(
            """SELECT r.resolved_ts,o.observation_id
               FROM g1s_resolutions r
               JOIN g1s_observations o USING(observation_id)
               WHERE o.horizon_minutes IN (15,30,60,120,240)
                 AND (r.resolved_ts>? OR
                      (r.resolved_ts=? AND o.observation_id>?))
               ORDER BY r.resolved_ts DESC,o.observation_id DESC LIMIT 1""",
            (ts, ts, observation_id),
        ).fetchone()
    return count, (
        None if latest is None else (float(latest[0]), str(latest[1]))
    )


def _record_dedupe(
    runtime: Any, *, hypothesis_id: str, observation_id: str, rejected_ts: float
) -> None:
    event_id = "llm-edge-dedupe-" + _sha({
        "hypothesis_id": hypothesis_id,
        "observation_id": observation_id,
        "contract": PR_C_CONTRACT_VERSION,
    })[:24]
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            f"""INSERT OR IGNORE INTO {_DEDUPE_TABLE}(
                 event_id,hypothesis_id,observation_id,reason,rejected_ts,contract_version
               ) VALUES(?,?,?,?,?,?)""",
            (
                event_id, str(hypothesis_id), str(observation_id),
                "DUPLICATE_HYPOTHESIS", float(rejected_ts),
                PR_C_CONTRACT_VERSION,
            ),
        )


def _deduplicating_provider(
    runtime: Any,
    provider: Callable[[dict[str, Any], str, int], dict[str, Any]],
    duplicate_rejections: list[str],
) -> Callable[[dict[str, Any], str, int], dict[str, Any]]:
    """Reject previously tested semantic IDs before the base proposer persists a run."""
    def call(summary: dict[str, Any], model: str, limit: int) -> dict[str, Any]:
        response = provider(summary, model, limit)
        raw = response.get("hypotheses") if isinstance(response, dict) else None
        if not isinstance(raw, list):
            return response
        with runtime._lock:
            existing_ids = {
                str(row[0]) for row in runtime._conn.execute(
                    "SELECT hypothesis_id FROM llm_edge_hypotheses"
                ).fetchall()
            }
        novel: list[Any] = []
        now = time.time()
        observation_id = str(summary.get("observation_id") or "")
        for index, item in enumerate(raw[:limit]):
            hypothesis, rejection = _researcher._validate_hypothesis(
                item, summary, index=index
            )
            if rejection or hypothesis is None:
                novel.append(item)
                continue
            hypothesis_id = str(hypothesis["hypothesis_id"])
            if hypothesis_id in existing_ids:
                duplicate_rejections.append(f"{index}:DUPLICATE_HYPOTHESIS")
                _record_dedupe(
                    runtime,
                    hypothesis_id=hypothesis_id,
                    observation_id=observation_id,
                    rejected_ts=now,
                )
                continue
            existing_ids.add(hypothesis_id)
            novel.append(item)
        return {"hypotheses": novel}
    return call


_BASE_PROPOSE = _researcher.propose_edge_hypotheses


def propose_edge_hypotheses(
    runtime: Any,
    observation_id: str | None = None,
    *,
    max_hypotheses: int = 5,
    provider: Callable[[dict[str, Any], str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Base proposer plus cross-run semantic dedupe; all original allowlists remain."""
    duplicate_rejections: list[str] = []
    selected_provider = provider or _researcher._provider
    result = _BASE_PROPOSE(
        runtime,
        observation_id,
        max_hypotheses=max_hypotheses,
        provider=_deduplicating_provider(
            runtime, selected_provider, duplicate_rejections
        ),
    )
    if not duplicate_rejections:
        return result
    output = dict(result)
    output["rejections"] = list(output.get("rejections") or []) + duplicate_rejections
    output["duplicate_hypothesis_n"] = len(duplicate_rejections)
    output["duplicate_reason"] = "DUPLICATE_HYPOTHESIS"
    return output


def _automatic_research_tick(
    engine: Any, *, now: float
) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {"status": "UNAVAILABLE", "reason": "G1S_RUNTIME_UNAVAILABLE"}
    _ensure_storage(runtime)
    if not _lifecycle.researcher_enabled():
        return {"status": "DISABLED", "reason": "LLM_EDGE_RESEARCHER_ENABLED_FALSE"}
    if not _AUTO_LOCK.acquire(blocking=False):
        return {"status": "SKIPPED", "reason": "RESEARCH_JOB_ALREADY_RUNNING"}
    try:
        state = _automation_state(runtime)
        new_resolved, latest = _resolved_evidence_since_cursor(runtime, state)
        last_provider = float(state.get("last_provider_call_ts") or 0.0)
        elapsed = math.inf if last_provider <= 0 else max(0.0, now - last_provider)
        evidence_due = new_resolved >= AUTO_MIN_NEW_RESOLVED_T0
        time_due = elapsed >= AUTO_MIN_PROVIDER_INTERVAL_SEC
        if not (evidence_due and time_due):
            return {
                "status": "NOT_DUE",
                "new_resolved_t0_since_last_run": new_resolved,
                "required_new_resolved_t0": AUTO_MIN_NEW_RESOLVED_T0,
                "seconds_since_last_provider_call": None if not math.isfinite(elapsed) else elapsed,
                "minimum_provider_interval_sec": AUTO_MIN_PROVIDER_INTERVAL_SEC,
                "evidence_gate_met": evidence_due,
                "time_gate_met": time_due,
                "max_hypotheses": AUTO_MAX_HYPOTHESES,
            }

        result = propose_edge_hypotheses(
            runtime,
            max_hypotheses=AUTO_MAX_HYPOTHESES,
            provider=_cost_guard.guarded_edge_researcher_provider,
        )
        state = _automation_state(runtime)
        attempts = int(state.get("automatic_provider_attempts") or 0)
        failures = int(state.get("automatic_provider_failures") or 0)
        cache_hits = int(state.get("automatic_cache_hits") or 0)
        orchestrations = int(state.get("automatic_orchestrations") or 0)

        provider_called = bool(result.get("provider_called"))
        if provider_called:
            attempts += 1
        if bool(result.get("cache_hit")):
            cache_hits += 1
        if provider_called and str(result.get("status")) == "UNAVAILABLE":
            failures += 1

        updates: dict[str, Any] = {
            "automatic_provider_attempts": attempts,
            "automatic_provider_failures": failures,
            "automatic_cache_hits": cache_hits,
            "last_status": str(result.get("status") or "UNKNOWN"),
            "last_error": (
                str(result.get("reason") or "")[:300]
                if str(result.get("status")) == "UNAVAILABLE" else None
            ),
        }
        if provider_called:
            updates["last_provider_call_ts"] = now

        run_id = result.get("run_id")
        evaluation = None
        freeze = None
        if run_id and str(result.get("status")) in {"OK", "NO_VALID_HYPOTHESES"}:
            evaluation = _evaluator.evaluate_edge_research_run(runtime, str(run_id))
            freeze = _lifecycle.freeze_discovery_signals(engine, now=now)
            orchestrations += 1
            updates.update({
                "automatic_orchestrations": orchestrations,
                "last_automatic_run_ts": now,
                "last_automatic_run_id": str(run_id),
            })
            if latest is not None:
                updates["last_run_resolved_ts"] = float(latest[0])
                updates["last_run_resolved_observation_id"] = str(latest[1])

        _update_state(runtime, **updates)
        return {
            "status": "RAN" if run_id else "SKIPPED",
            "proposal": result,
            "evaluation": evaluation,
            "freeze": freeze,
            "new_resolved_t0_since_last_run": new_resolved,
            "required_new_resolved_t0": AUTO_MIN_NEW_RESOLVED_T0,
            "minimum_provider_interval_sec": AUTO_MIN_PROVIDER_INTERVAL_SEC,
            "max_hypotheses": AUTO_MAX_HYPOTHESES,
            "provider_called": provider_called,
            "production_authority": False,
            "may_change_cvar_stop_or_size": False,
        }
    except Exception as exc:
        try:
            _update_state(
                runtime,
                last_status="ERROR",
                last_error=f"{type(exc).__name__}: {str(exc)[:260]}",
            )
        except Exception:
            pass
        return {
            "status": "ERROR",
            "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
            "production_authority": False,
            "worker_failure_isolated": True,
        }
    finally:
        _AUTO_LOCK.release()


def _strict_from_promotion(payload: dict[str, Any]) -> bool:
    return _strict_reference_qualified({
        "q_value": payload.get("discovery_q_value"),
        "primary_improvement": payload.get("discovery_effect"),
        "fold_positive": payload.get("discovery_fold_positive"),
    })


_BASE_PROMOTION_PAYLOAD = _prospective._promotion_payload


def _promotion_payload_with_parity(state: dict[str, Any]) -> dict[str, Any]:
    payload = dict(_BASE_PROMOTION_PAYLOAD(state))
    frozen = (state.get("validation") or {}).get("frozen_spec") or {}
    payload.update({
        "candidate_source": "LLM_EDGE_RESEARCHER",
        "prospective_candidate_id": str(state.get("candidate_id") or ""),
        "evaluation_id": frozen.get("source_evaluation_id"),
        "prospective_epoch_id": frozen.get("prospective_epoch_id"),
        "discovery_fold_positive": state.get("fold_positive"),
        "strict_reference": dict(STRICT_REFERENCE),
        "promotion_parity_contract": PROMOTION_PARITY_CONTRACT_VERSION,
    })
    payload["strict_reference_qualified"] = _strict_from_promotion(payload)
    return payload


_BASE_VALIDATED_ROWS = _validated_bridge._validated_rows
_BASE_AUGMENT_CONTEXT = _validated_bridge._augment_context


def _validated_rows_with_strict(
    engine: Any, snapshot: dict[str, Any], integration: Any
) -> list[dict[str, Any]]:
    rows = _BASE_VALIDATED_ROWS(engine, snapshot, integration)
    promotions = {
        str(item.get("candidate_id") or ""): item
        for item in _validated_bridge.active_promotions(engine)
    }
    for row in rows:
        payload = promotions.get(str(row.get("candidate_id") or ""), {})
        row["strict_reference_qualified"] = bool(
            payload.get("strict_reference_qualified", False)
        )
    return rows


def _researcher_context(engine: Any) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {
            "active_derived_n": 0,
            "prospective_confirmed_n": 0,
            "collecting_n": 0,
            "top_active_candidates": [],
            "status": "UNAVAILABLE",
            "production_authority": False,
        }
    try:
        payload = _lifecycle.read_materialized_lifecycle(runtime)
    except Exception:
        payload = {}
    summary = payload.get("researcher") or {}
    candidates = payload.get("candidates") or []
    active = [
        item for item in candidates
        if item.get("active_edge_eligible") is True
        or str(item.get("active_edge_status") or "").startswith("PROMOTED")
    ]
    return {
        "contract_version": PR_C_CONTRACT_VERSION,
        "status": payload.get("status", "INITIALIZING"),
        "active_derived_n": int(summary.get("active_edge") or 0),
        "prospective_confirmed_n": int(summary.get("prospective_pass") or 0),
        "collecting_n": int(summary.get("collecting") or 0),
        "discovery_n": int(summary.get("discovery_signals") or 0),
        "rejected_n": int(summary.get("rejected") or 0),
        "top_active_candidates": [
            {
                "candidate_id": item.get("candidate_id"),
                "name": item.get("name"),
                "target": item.get("target"),
                "horizon": item.get("horizon"),
                "state": item.get("state"),
            }
            for item in active[:3]
        ],
        "research_states_have_trading_weight": False,
        "active_weight_source": "EXISTING_ACTIVE_EDGE_AGGREGATOR_ONLY",
        "production_authority": False,
    }


def _augment_context_with_strict_and_researcher(
    engine: Any, snapshot: dict[str, Any], context: dict[str, Any], integration: Any
) -> dict[str, Any]:
    result = dict(_BASE_AUGMENT_CONTEXT(engine, snapshot, context, integration))
    if result.get("measurement_available") is False:
        result["validated_strict_directional_n"] = 0
        result["edge_researcher"] = _researcher_context(engine)
        return result

    validated = _validated_rows_with_strict(engine, snapshot, integration)
    strict_rows = [
        row for row in validated
        if row.get("conditions_match_current_t0") is True
        and row.get("strict_reference_qualified") is True
    ]
    strict_supporting = sum(
        row.get("position_relation") == "SUPPORTS_POSITION" for row in strict_rows
    )
    strict_opposing = sum(
        row.get("position_relation") == "OPPOSES_POSITION" for row in strict_rows
    )
    if strict_rows:
        result["strict_supporting_position_n"] = (
            int(result.get("strict_supporting_position_n") or 0) + strict_supporting
        )
        result["strict_opposing_position_n"] = (
            int(result.get("strict_opposing_position_n") or 0) + strict_opposing
        )
        result["matched_strict_reference_signal_n"] = (
            int(result.get("matched_strict_reference_signal_n") or 0) + len(strict_rows)
        )
        result["strict_reference_signal_n"] = (
            int(result.get("strict_reference_signal_n") or 0)
            + sum(row.get("strict_reference_qualified") is True for row in validated)
        )
        result["strict_net_position_vote"] = (
            int(result["strict_supporting_position_n"])
            - int(result["strict_opposing_position_n"])
        )
        strict_directional = (
            int(result["strict_supporting_position_n"])
            + int(result["strict_opposing_position_n"])
        )
        result["strict_net_position_vote_ratio"] = (
            result["strict_net_position_vote"] / strict_directional
            if strict_directional else None
        )
        groups = [dict(item) for item in result.get("matched_groups") or []]
        for row in strict_rows:
            target = str(row.get("target_id") or "UNKNOWN")
            horizon = int(row.get("horizon_minutes") or 0)
            group = next((
                item for item in groups
                if str(item.get("target_id") or "") == target
                and int(item.get("signal_horizon_minutes") or 0) == horizon
            ), None)
            if group is None:
                continue
            group["strict_matched_n"] = int(group.get("strict_matched_n") or 0) + 1
            relation = str(row.get("position_relation") or "")
            if relation == "SUPPORTS_POSITION":
                group["strict_supporting_n"] = int(
                    group.get("strict_supporting_n") or 0
                ) + 1
            elif relation == "OPPOSES_POSITION":
                group["strict_opposing_n"] = int(
                    group.get("strict_opposing_n") or 0
                ) + 1
            supporting = int(group.get("strict_supporting_n") or 0)
            opposing = int(group.get("strict_opposing_n") or 0)
            group["strict_net_vote"] = supporting - opposing
            group["strict_net_vote_ratio"] = (
                (supporting - opposing) / (supporting + opposing)
                if supporting + opposing else None
            )
        result["matched_groups"] = groups

    result["validated_strict_directional_n"] = strict_supporting + strict_opposing
    result["edge_researcher"] = _researcher_context(engine)
    return result


def _upgrade_weight_profile_strict_only(
    context: dict[str, Any], profile: dict[str, Any], weight_module: Any
) -> dict[str, Any]:
    """Prospective validation grants eligibility, not STRICT_REFERENCE authority."""
    validated_directional = max(
        0,
        int(context.get("validated_supporting_position_n") or 0)
        + int(context.get("validated_opposing_position_n") or 0),
    )
    output = dict(profile)
    output.update({
        "prospective_validated_directional_n": validated_directional,
        "prospective_validated_strict_directional_n": max(
            0, int(context.get("validated_strict_directional_n") or 0)
        ),
        "validated_directional_share": (
            validated_directional
            / max(1, int(output.get("matched_directional_signal_n") or 0))
        ),
        "authority_grade_directional_share": float(
            output.get("strict_directional_share") or 0.0
        ),
        "prospective_calibration_pending": False
        if validated_directional else output.get("prospective_calibration_pending", True),
        "validated_promotion_bridge": PROMOTION_PARITY_CONTRACT_VERSION,
        "strict_reference_required_for_40pct_cap": True,
    })
    return output


def _quality_snapshot(runtime: Any, payload: dict[str, Any]) -> dict[str, Any]:
    researcher = payload.get("researcher") or {}
    with runtime._lock:
        evaluation_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_evaluations"
        ).fetchone()[0])
        dedupe_n = int(runtime._conn.execute(
            f"SELECT COUNT(*) FROM {_DEDUPE_TABLE}"
        ).fetchone()[0])
        run_rows = runtime._conn.execute(
            "SELECT rejections_json FROM llm_edge_research_runs"
        ).fetchall()
    rejection_n = 0
    duplicate_in_run_n = 0
    for row in run_rows:
        try:
            rejections = json.loads(str(row[0] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            rejections = []
        rejection_n += len(rejections)
        duplicate_in_run_n += sum(
            "DUPLICATE_HYPOTHESIS" in str(item) for item in rejections
        )
    duplicate_total = dedupe_n + duplicate_in_run_n
    hypotheses = int(researcher.get("hypotheses") or 0)
    discovery = int(researcher.get("discovery_signals") or 0)
    confirmed = int(researcher.get("prospective_pass") or 0)
    failed = int(researcher.get("prospective_fail") or 0)
    candidates = payload.get("candidates") or []
    prospective_samples = [
        int((item.get("prospective") or {}).get("matched_n") or 0)
        for item in candidates
    ]
    prospective_samples.sort()
    median_sample = None
    if prospective_samples:
        middle = len(prospective_samples) // 2
        median_sample = (
            float(prospective_samples[middle])
            if len(prospective_samples) % 2
            else (
                prospective_samples[middle - 1] + prospective_samples[middle]
            ) / 2.0
        )
    state = _automation_state(runtime)
    provider_failures = int(state.get("automatic_provider_failures") or 0)
    provider_calls = int(researcher.get("proposal_runs") or 0) + provider_failures
    auto_attempts = int(state.get("automatic_provider_attempts") or 0)
    auto_cache_hits = int(state.get("automatic_cache_hits") or 0)
    return {
        "contract_version": RESEARCH_QUALITY_CONTRACT_VERSION,
        "hypotheses_total": hypotheses,
        "evaluations_total": evaluation_n,
        "discovery_signal_rate": _ratio(discovery, hypotheses),
        "prospective_pass_rate": _ratio(confirmed, confirmed + failed),
        "llm_discovery_to_prospective_survival_rate": _ratio(confirmed, discovery),
        "duplicate_rate": _ratio(duplicate_total, hypotheses + duplicate_total),
        "duplicate_rejections": duplicate_total,
        "rejection_rate": _ratio(rejection_n + dedupe_n, hypotheses + rejection_n + dedupe_n),
        "median_prospective_sample": median_sample,
        "provider_calls": provider_calls,
        "automatic_provider_attempts": auto_attempts,
        "cache_hit_rate": _ratio(auto_cache_hits, max(1, auto_attempts + auto_cache_hits)),
        "cache_hit_rate_scope": "AUTOMATIC_ORCHESTRATOR_ONLY",
        "publication_bias_guard": "FAILED_AND_REJECTED_ARTIFACTS_RETAINED",
        "production_authority": False,
    }


_BASE_MATERIALIZE = _lifecycle.materialize_lifecycle


def materialize_lifecycle(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return _BASE_MATERIALIZE(engine, now=now)
    _ensure_storage(runtime)
    payload = dict(_BASE_MATERIALIZE(engine, now=now))
    state = _automation_state(runtime)
    new_resolved, _latest = _resolved_evidence_since_cursor(runtime, state)
    last_provider = state.get("last_provider_call_ts")
    current = float(time.time() if now is None else now)
    seconds_since = (
        None if last_provider is None
        else max(0.0, current - float(last_provider))
    )
    automation = {
        "contract_version": AUTOMATION_CONTRACT_VERSION,
        "enabled": _lifecycle.researcher_enabled(),
        "manual_post_only": False,
        "new_resolved_t0_since_last_run": new_resolved,
        "required_new_resolved_t0": AUTO_MIN_NEW_RESOLVED_T0,
        "minimum_provider_interval_sec": AUTO_MIN_PROVIDER_INTERVAL_SEC,
        "seconds_since_last_provider_call": seconds_since,
        "evidence_gate_met": new_resolved >= AUTO_MIN_NEW_RESOLVED_T0,
        "time_gate_met": (
            last_provider is None
            or seconds_since is not None
            and seconds_since >= AUTO_MIN_PROVIDER_INTERVAL_SEC
        ),
        "max_automatic_hypotheses": AUTO_MAX_HYPOTHESES,
        "hard_max_hypotheses": _researcher.MAX_HYPOTHESES,
        "automatic_provider_attempts": int(
            state.get("automatic_provider_attempts") or 0
        ),
        "automatic_provider_failures": int(
            state.get("automatic_provider_failures") or 0
        ),
        "automatic_orchestrations": int(
            state.get("automatic_orchestrations") or 0
        ),
        "last_automatic_run_id": state.get("last_automatic_run_id"),
        "last_automatic_run_ts": state.get("last_automatic_run_ts"),
        "last_status": state.get("last_status"),
        "last_error": state.get("last_error"),
        "worker_priority": "LOWEST_AFTER_OUTCOMES_AND_PROMOTION",
        "heavy_evaluation_concurrency": 1,
    }
    payload["automation"] = automation
    payload["research_quality"] = _quality_snapshot(runtime, payload)
    promotions = _prospective.active_promotions(engine)
    strict_active = sum(
        bool(item.get("strict_reference_qualified")) for item in promotions
    )
    if isinstance(payload.get("researcher"), dict):
        payload["researcher"]["strict_reference"] = strict_active
    payload["pr_c_contract_version"] = PR_C_CONTRACT_VERSION
    payload["request_time_history_scan"] = False
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            """INSERT INTO llm_edge_lifecycle_materialized(
                 singleton_id,payload_json,updated_ts
               ) VALUES(1,?,?)
               ON CONFLICT(singleton_id) DO UPDATE SET
                 payload_json=excluded.payload_json,updated_ts=excluded.updated_ts""",
            (
                _canonical(payload),
                float(payload.get("updated_ts") or current),
            ),
        )
    return payload


def _materialized_status(runtime: Any) -> dict[str,Any]:
    payload = _lifecycle.read_materialized_lifecycle(runtime)
    summary = payload.get("researcher") or {}
    return {
        "contract_version": _researcher.CONTRACT_VERSION,
        "pr_c_contract_version": PR_C_CONTRACT_VERSION,
        "status": payload.get("status", "INITIALIZING"),
        "run_n": int(summary.get("proposal_runs") or 0),
        "hypothesis_n": int(summary.get("hypotheses") or 0),
        "automation": payload.get("automation") or {},
        "research_quality": payload.get("research_quality") or {},
        "prompt_version": _researcher.PROMPT_VERSION,
        "max_conditions": _researcher.MAX_CONDITIONS,
        "max_hypotheses_per_run": _researcher.MAX_HYPOTHESES,
        "numeric_thresholds_fit_by_llm": False,
        "future_outcomes_visible_to_llm": False,
        "writes_active_edge_registry": False,
        "request_time_history_scan": False,
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
    }


def _materialized_evaluator_status(runtime: Any) -> dict[str, Any]:
    payload = _lifecycle.read_materialized_lifecycle(runtime)
    quality = payload.get("research_quality") or {}
    return {
        "contract_version": _evaluator.CONTRACT_VERSION,
        "measurement_contract": _evaluator.MEASUREMENT_CONTRACT,
        "status": payload.get("status", "INITIALIZING"),
        "evaluation_n": int(quality.get("evaluations_total") or 0),
        "cutoff_frozen_at_proposal_time": True,
        "future_resolutions_excluded": True,
        "numeric_thresholds_fit_train_only": True,
        "llm_scores_or_selects_results": False,
        "prospective_confirmation": False,
        "writes_active_edge_registry": False,
        "request_time_history_scan": False,
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
    }


_BASE_TICK = _lifecycle.llm_edge_prospective_tick


def llm_edge_research_tick(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    current = float(time.time() if now is None else now)
    # Existing future outcomes/checkpoints/promotion always run first.
    base = _BASE_TICK(engine, now=current)
    automatic = _automatic_research_tick(engine, now=current)
    if automatic.get("status") in {"RAN", "ERROR"}:
        try:
            materialize_lifecycle(engine, now=current)
        except Exception:
            pass
    return {
        **base,
        "automation": automatic,
        "pr_c_contract_version": PR_C_CONTRACT_VERSION,
        "research_order": [
            "PROSPECTIVE_OUTCOMES",
            "CHECKPOINTS_AND_PROMOTION",
            "EXISTING_DISCOVERY_FREEZE",
            "NEW_T0_OPPORTUNITIES",
            "AUTOMATIC_LLM_PROPOSAL_LAST",
        ],
    }


_BASE_COST_GUARD_STATUS = _cost_guard.cost_guard_status


def _cost_guard_status() -> dict[str, Any]:
    base = dict(_BASE_COST_GUARD_STATUS())
    base.update({
        "edge_researcher_manual_post_only": False,
        "edge_researcher_automatic_enabled": True,
        "edge_researcher_automatic_min_provider_interval_sec":
            AUTO_MIN_PROVIDER_INTERVAL_SEC,
        "edge_researcher_automatic_min_new_resolved_t0":
            AUTO_MIN_NEW_RESOLVED_T0,
        "edge_researcher_automatic_max_hypotheses": AUTO_MAX_HYPOTHESES,
        "edge_researcher_manual_min_provider_interval_sec":
            base.get("edge_researcher_min_provider_interval_sec"),
    })
    return base


def install_llm_edge_pr_c() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # Provider/proposer: preserve the Phase-1 contract and add only semantic
    # cross-run dedupe. Routes import this symbol after package initialization.
    _researcher.propose_edge_hypotheses = propose_edge_hypotheses
    _researcher.edge_researcher_status = _materialized_status
    _evaluator.edge_evaluator_status = _materialized_evaluator_status

    # Prospective PASS remains the eligibility gate. STRICT_REFERENCE is still
    # the existing common threshold and is the only route from the 30% to 40% cap.
    _prospective._promotion_payload = _promotion_payload_with_parity
    _validated_bridge._validated_rows = _validated_rows_with_strict
    _validated_bridge._augment_context = _augment_context_with_strict_and_researcher
    _validated_bridge._upgrade_weight_profile = _upgrade_weight_profile_strict_only

    # Materialization and automatic work stay inside the existing low-priority
    # worker. HTTP routes continue to read the one prebuilt lifecycle row.
    _lifecycle.materialize_lifecycle = materialize_lifecycle
    _lifecycle.llm_edge_prospective_tick = llm_edge_research_tick

    _cost_guard.cost_guard_status = _cost_guard_status
    _INSTALLED = True
