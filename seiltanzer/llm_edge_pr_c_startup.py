"""Cheap startup upgrade for the already-materialized LLM Edge lifecycle.

PR C GET endpoints must expose their versioned contract immediately after app
startup without waiting for the low-priority research worker.  This module does
not evaluate candidates, scan feature history, call an LLM, or run FDR.  It only
preserves the existing materialized lifecycle payload and adds PR-C scheduler /
quality metadata from its tiny durable singleton.
"""
from __future__ import annotations

import json
import time
from typing import Any

from . import llm_edge_lifecycle as _lifecycle
from . import llm_edge_pr_c as _prc

STARTUP_CONTRACT_VERSION = "llm-edge-pr-c-startup-materialization-v1"


def _automation_bootstrap(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": _prc.AUTOMATION_CONTRACT_VERSION,
        "enabled": _lifecycle.researcher_enabled(),
        "manual_post_only": False,
        # The worker owns the live evidence count.  Startup deliberately does
        # not scan G1S history just to populate a presentation field.
        "new_resolved_t0_since_last_run": None,
        "required_new_resolved_t0": _prc.AUTO_MIN_NEW_RESOLVED_T0,
        "minimum_provider_interval_sec": _prc.AUTO_MIN_PROVIDER_INTERVAL_SEC,
        "seconds_since_last_provider_call": None,
        "evidence_gate_met": None,
        "time_gate_met": None,
        "max_automatic_hypotheses": _prc.AUTO_MAX_HYPOTHESES,
        "hard_max_hypotheses": _prc._researcher.MAX_HYPOTHESES,
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
        "startup_contract_only": True,
    }


def _quality_bootstrap(
    previous: dict[str, Any] | None, state: dict[str, Any]
) -> dict[str, Any]:
    quality = dict(previous or {})
    quality.setdefault("contract_version", _prc.RESEARCH_QUALITY_CONTRACT_VERSION)
    quality.setdefault("hypotheses_total", None)
    quality.setdefault("evaluations_total", None)
    quality.setdefault("discovery_signal_rate", None)
    quality.setdefault("prospective_pass_rate", None)
    quality.setdefault("llm_discovery_to_prospective_survival_rate", None)
    quality.setdefault("duplicate_rate", None)
    quality.setdefault("duplicate_rejections", None)
    quality.setdefault("rejection_rate", None)
    quality.setdefault("median_prospective_sample", None)
    quality.setdefault("provider_calls", None)
    quality.setdefault(
        "automatic_provider_attempts",
        int(state.get("automatic_provider_attempts") or 0),
    )
    quality.setdefault("cache_hit_rate", None)
    quality.setdefault("cache_hit_rate_scope", "AUTOMATIC_ORCHESTRATOR_ONLY")
    quality.setdefault(
        "publication_bias_guard", "FAILED_AND_REJECTED_ARTIFACTS_RETAINED"
    )
    quality["production_authority"] = False
    return quality


def initialize_pr_c_materialized_state(
    runtime: Any, *, now: float | None = None
) -> dict[str, Any]:
    """Upgrade one existing materialized row at startup, preserving its truth.

    DDL and the tiny automation singleton are startup-only.  Candidate counts,
    journals and details come exclusively from the previous materialized row;
    this function never reconstructs them from history.
    """
    _prc._ensure_storage(runtime)
    previous = _lifecycle.read_materialized_lifecycle(runtime)
    payload = dict(previous if isinstance(previous, dict) else {})
    state = _prc._automation_state(runtime)
    current = float(time.time() if now is None else now)

    payload.setdefault("status", "INITIALIZING")
    payload.setdefault("researcher", {
        "proposal_runs": 0,
        "hypotheses": 0,
        "discovery_signals": 0,
        "frozen_prospective": 0,
        "collecting": 0,
        "underpowered": 0,
        "prospective_pass": 0,
        "prospective_fail": 0,
        "active_edge": 0,
        "strict_reference": 0,
        "rejected": 0,
    })
    payload.setdefault("candidates", [])
    payload["automation"] = _automation_bootstrap(state)
    payload["research_quality"] = _quality_bootstrap(
        payload.get("research_quality"), state
    )
    payload["pr_c_contract_version"] = _prc.PR_C_CONTRACT_VERSION
    payload["pr_c_startup_contract_version"] = STARTUP_CONTRACT_VERSION
    payload["request_time_history_scan"] = False
    payload["production_authority"] = False
    payload["updated_ts"] = current

    payload_json = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    with runtime._lock:
        with runtime._conn:
            runtime._conn.execute(
                """INSERT INTO llm_edge_lifecycle_materialized(
                     singleton_id,payload_json,updated_ts
                   ) VALUES(1,?,?)
                   ON CONFLICT(singleton_id) DO UPDATE SET
                     payload_json=excluded.payload_json,
                     updated_ts=excluded.updated_ts""",
                (
                    payload_json,
                    current,
                ),
            )
        _lifecycle.publish_materialized_lifecycle_cache(runtime, payload_json)
    return payload
