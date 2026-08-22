"""Bound the optional LLM provider without weakening deterministic AI policy.

The deterministic policy snapshot/report is authoritative. OpenRouter is only an
explanation layer, so a slow provider must never hold the public HTTP request long
enough for an upstream gateway to return HTML 504. The provider receives a small
explanation projection rather than longitudinal/debug replicas that do not own the
management action. A timeout still falls back to the established deterministic
report.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable


DEFAULT_PROVIDER_TIMEOUT_SEC = 6.0
MIN_PROVIDER_TIMEOUT_SEC = 3.0
MAX_PROVIDER_TIMEOUT_SEC = 8.0
DEFAULT_PROVIDER_CIRCUIT_SEC = 50.0
PROVIDER_SNAPSHOT_LIMIT_BYTES = 18_000

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="seiltanzer-ai-provider")
_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT_OPEN_UNTIL = 0.0
_INSTALLED = False


def provider_timeout_sec() -> float:
    raw = os.environ.get("AI_PROVIDER_TIMEOUT_SEC", str(DEFAULT_PROVIDER_TIMEOUT_SEC))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_PROVIDER_TIMEOUT_SEC
    return min(MAX_PROVIDER_TIMEOUT_SEC, max(MIN_PROVIDER_TIMEOUT_SEC, value))


def provider_circuit_sec() -> float:
    raw = os.environ.get("AI_PROVIDER_CIRCUIT_SEC", str(DEFAULT_PROVIDER_CIRCUIT_SEC))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_PROVIDER_CIRCUIT_SEC
    return min(120.0, max(15.0, value))


def _json_bytes(value: Any) -> int:
    return len(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8"))


def _pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    return {key: deepcopy(source[key]) for key in keys if key in source}


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Bound explanation-only detail without inventing values."""
    if depth >= 4:
        return "[bounded]"
    if isinstance(value, str):
        return value if len(value) <= 192 else value[:189] + "..."
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {
            key: _bounded(value[key], depth=depth + 1)
            for key in sorted(value)[:20]
        }
    return value


def _compact_policies(source: Any) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    fields = (
        "name", "close_fraction", "expected_final_r", "median_final_r", "cvar10_r",
        "expected_final_r_net", "cvar10_r_net", "p_final_profit", "p_final_loss",
        "p_giveback_0_25_from_now", "p_giveback_0_50_from_now",
        "p_next_rung_before_stop", "p_stop_before_next_rung", "next_rung_r",
        "expected_event_minutes", "no_event_probability", "gross_expected_final_r",
        "execution_cost_r", "expected_future_r_on_remaining", "expected_total_trade_r",
        "eligible", "reason",
    )
    return {
        name: _bounded(_pick(row, fields))
        for name, row in source.items() if isinstance(row, dict)
    }


def _compact_evidence(source: Any) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    keys = (
        "live_price", "atr_regime", "iv_surface", "correlation", "strike_oi_gex",
        "option_barrier", "option_derivative_state", "cone_rnd", "levels",
        "data_quality", "adverse_confirmations", "supportive_contradictions",
        "context_observations", "uncertainty_flags", "decision_roles",
        "confirmation_independence", "adverse_confirmation_families",
        "supportive_confirmation_families", "mixed_confirmation_families",
        "adverse_confirmation_count",
    )
    return {key: _bounded(source[key]) for key in keys if key in source}


def _compact_input_audit(source: Any, *, row_limit: int = 20) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    out = _pick(source, (
        "all_required_available", "missing_required", "degraded_inputs",
        "required_count", "available_count", "total_count",
    ))
    rows = source.get("rows")
    if isinstance(rows, dict):
        row_fields = (
            "available", "status", "source", "role", "age_sec", "symbol",
            "reason", "quality", "proxy_quality", "is_proxy", "fallback_tier",
        )
        out["rows"] = {
            name: _bounded(_pick(row, row_fields))
            for name, row in list(rows.items())[:row_limit]
            if isinstance(row, dict)
        }
    return _bounded(out)


def _compact_ede(source: Any) -> dict[str, Any]:
    source = source if isinstance(source, dict) else {}
    keys = (
        "contract_version", "instrument", "snapshot_ts", "observation_t0",
        "data_maturity", "edge_maturity", "confidence_context",
        "candidate", "candidate_id", "candidate_applies", "application_reason",
        "position_relation", "family_evidence", "current_features", "families",
        "production_authority", "production_directional_authority", "auto_promotion",
        "may_trigger_exit_or_close",
    )
    selected = {key: source[key] for key in keys if key in source}
    return _bounded(selected if selected else source)


def _compact_recommendation(source: Any) -> dict[str, Any]:
    return _bounded(_pick(source, (
        "policy", "action_ru", "raw_optimizer_policy", "selected_policy",
        "close_fraction", "remaining_fraction", "remaining_management",
        "next_rung_r", "automatic_execution_allowed", "reason",
    )))


def _compact_management_decision(source: Any) -> dict[str, Any]:
    return _bounded(_pick(source, (
        "decision_id", "policy", "execution_status", "instruction_ru", "continuity",
        "close_fraction", "remaining_fraction", "next_rung_r", "last_ack_status",
        "automatic_execution_allowed", "arbiter_reason",
    )))


def _compact_scalar_contract(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    return _bounded(_pick(source, keys))


def _minimal_provider_projection(snapshot: dict[str, Any], original_bytes: int) -> dict[str, Any]:
    """Hard-bounded projection retaining every field that can own/report the action.

    A large canonical snapshot is valid input, not provider unavailability. This
    projection deliberately drops only duplicated research/debug workspaces. It is
    also used as a deterministic second tier when the richer explanation projection
    exceeds the transport budget.
    """
    manager = snapshot.get("policy_manager") or {}
    manager = manager if isinstance(manager, dict) else {}
    observation = snapshot.get("observation") or {}
    observation = observation if isinstance(observation, dict) else {}

    compact_manager = {
        "recommendation": _compact_recommendation(manager.get("recommendation")),
        "management_decision": _compact_management_decision(manager.get("management_decision")),
        "policies": _compact_policies(manager.get("policies")),
        "selection_rule": _compact_scalar_contract(manager.get("selection_rule"), (
            "cvar_floor_r", "minimum_net_advantage_r", "min_net_advantage_r",
            "eligible", "winner", "raw_policy", "selected_policy",
        )),
        "inputs": _compact_scalar_contract(manager.get("inputs"), (
            "r0", "sigma_R", "drift_R", "skew_R", "term_slope", "horizon_minutes",
            "chain_age_sec", "chain_status", "proxy_quality", "stop_r", "be_r",
            "active_stop_r", "current_r", "remaining_fraction",
        )),
        "risk_constraint": _bounded(manager.get("risk_constraint") or {}),
        "gate": _compact_scalar_contract(manager.get("gate"), (
            "status", "raw_policy", "provisional_policy", "selected_policy",
            "automatic_execution_allowed", "reason", "reasons",
            "independent_confirmation_count", "required_confirmation_count",
        )),
        "stability": _compact_scalar_contract(manager.get("stability"), (
            "status", "stable", "winner", "selected_share", "required_share",
            "decision_uncertain", "checks",
        )),
        "raw_optimizer_stability": _compact_scalar_contract(
            manager.get("raw_optimizer_stability"),
            ("status", "stable", "winner", "selected_share", "required_share",
             "decision_uncertain", "checks")),
        "risk_tradeoff": _compact_scalar_contract(manager.get("risk_tradeoff"), (
            "expected_delta_vs_hold_r", "cvar_improvement_vs_hold_r",
            "expected_r_sacrifice", "cvar_gain_r", "status", "reason",
        )),
        "scenario_geometry": _compact_scalar_contract(manager.get("scenario_geometry"), (
            "scenario_count", "next_rung_r", "p_next_rung_before_stop",
            "rung_first_count", "p_stop_before_next_rung", "stop_first_count",
            "p_unresolved_full_horizon", "unresolved_count", "resolved_count",
            "full_horizon_minutes", "mean_event_minutes_given_resolved",
            "take_first_probability", "stop_or_be_first_probability",
        )),
        "evidence": _compact_evidence(manager.get("evidence")),
        "input_audit": _compact_input_audit(manager.get("input_audit"), row_limit=12),
        "management_arbiter": _bounded(manager.get("management_arbiter") or {}),
        "cancellation_boundary": _bounded(manager.get("cancellation_boundary") or {}),
        "counterfactual_attribution": _bounded(manager.get("counterfactual_attribution") or {}),
    }

    exact_levels = observation.get("exact_levels") or {}
    payload: dict[str, Any] = {
        "captured_ts": snapshot.get("captured_ts"),
        "trade_id": snapshot.get("trade_id"),
        "strategy": _compact_scalar_contract(snapshot.get("strategy"), (
            "symbol", "instrument", "direction", "setup", "setup_id", "timeframe",
        )),
        "position_state": _bounded(snapshot.get("position_state") or {}),
        "trade_geometry": _bounded(snapshot.get("trade_geometry") or {}),
        "observation": {
            "exact_levels": _bounded(exact_levels),
            **_compact_scalar_contract(observation, ("symbol", "price", "captured_ts")),
        },
        "metric_coverage": _bounded(snapshot.get("metric_coverage") or {}),
        "position_management_risk_long": _bounded(
            snapshot.get("position_management_risk_long") or {}),
        "active_edge_provisional_weight": _bounded(
            snapshot.get("active_edge_provisional_weight") or {}),
        "active_edge": _bounded(snapshot.get("active_edge") or {}),
        "policy_manager": compact_manager,
        "ede_causal_context": _compact_ede(snapshot.get("ede_causal_context")),
        "provider_history_summary": {
            "metric_history_present": bool(snapshot.get("metric_history")),
            "previous_review_count": len(snapshot.get("previous_reviews") or [])
            if isinstance(snapshot.get("previous_reviews"), list) else 0,
        },
        "provider_projection": {
            "contract_version": "ai-llm-explanation-projection-v2",
            "authority": "EXPLANATION_ONLY",
            "canonical_snapshot_unchanged": True,
            "compaction_tier": "minimal",
            "truncated_debug_workspaces": True,
            "original_snapshot_bytes": original_bytes,
            "final_bytes": 0,
        },
    }

    # Optional context is removed in a stable priority order until the immutable
    # transport budget is met. Action/policies/geometry/risk inputs are never in
    # this prune list.
    prune_order = (
        "active_edge", "ede_causal_context", "position_management_risk_long",
        "metric_coverage", "trade_geometry",
    )
    for key in prune_order:
        if _json_bytes(payload) <= PROVIDER_SNAPSHOT_LIMIT_BYTES:
            break
        payload.pop(key, None)

    for key in (
        "counterfactual_attribution", "cancellation_boundary", "management_arbiter",
        "evidence", "input_audit", "raw_optimizer_stability", "risk_tradeoff",
    ):
        if _json_bytes(payload) <= PROVIDER_SNAPSHOT_LIMIT_BYTES:
            break
        compact_manager.pop(key, None)

    # Final emergency projection still keeps the exact selected action and all
    # policy Expected/CVaR values needed by post-provider integrity validation.
    if _json_bytes(payload) > PROVIDER_SNAPSHOT_LIMIT_BYTES:
        compact_manager.clear()
        compact_manager.update({
            "recommendation": _compact_recommendation(manager.get("recommendation")),
            "management_decision": _compact_management_decision(manager.get("management_decision")),
            "policies": _compact_policies(manager.get("policies")),
            "selection_rule": _compact_scalar_contract(manager.get("selection_rule"), (
                "cvar_floor_r", "minimum_net_advantage_r", "eligible", "winner",
            )),
            "inputs": _compact_scalar_contract(manager.get("inputs"), (
                "r0", "sigma_R", "drift_R", "skew_R", "term_slope", "horizon_minutes",
                "chain_age_sec", "chain_status", "proxy_quality",
            )),
            "gate": _compact_scalar_contract(manager.get("gate"), (
                "status", "automatic_execution_allowed", "reason", "reasons",
            )),
            "scenario_geometry": _compact_scalar_contract(manager.get("scenario_geometry"), (
                "scenario_count", "next_rung_r", "p_next_rung_before_stop",
                "p_stop_before_next_rung", "p_unresolved_full_horizon",
                "full_horizon_minutes", "mean_event_minutes_given_resolved",
            )),
        })
        for key in list(payload):
            if key not in {
                "captured_ts", "trade_id", "strategy", "position_state", "observation",
                "policy_manager", "provider_history_summary", "provider_projection",
            }:
                payload.pop(key, None)
        payload["position_state"] = _bounded(payload.get("position_state") or {}, depth=2)
        payload["observation"] = _bounded(payload.get("observation") or {}, depth=2)

    # With bounded strings/lists and the small immutable action surface above this
    # should be unreachable for real snapshots. Do not mislabel a valid trade as
    # provider-unavailable if it ever is reached: retain only absolute essentials.
    if _json_bytes(payload) > PROVIDER_SNAPSHOT_LIMIT_BYTES:
        payload = {
            "captured_ts": snapshot.get("captured_ts"),
            "trade_id": snapshot.get("trade_id"),
            "policy_manager": {
                "recommendation": _compact_recommendation(manager.get("recommendation")),
                "management_decision": _compact_management_decision(manager.get("management_decision")),
                "policies": _compact_policies(manager.get("policies")),
            },
            "provider_projection": {
                "contract_version": "ai-llm-explanation-projection-v2",
                "authority": "EXPLANATION_ONLY",
                "canonical_snapshot_unchanged": True,
                "compaction_tier": "essential",
                "truncated_debug_workspaces": True,
                "original_snapshot_bytes": original_bytes,
                "final_bytes": 0,
            },
        }

    for _ in range(2):
        payload["provider_projection"]["final_bytes"] = _json_bytes(payload)
    return payload


def compact_provider_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return an explanation-only projection while preserving action semantics."""
    original_bytes = _json_bytes(snapshot)
    manager = snapshot.get("policy_manager") or {}
    manager = manager if isinstance(manager, dict) else {}

    manager_fields = (
        "version", "management_decision", "recommendation", "selection_rule",
        "inputs", "risk_constraint", "management_arbiter", "stability",
        "state_change_attribution", "counterfactual_attribution", "metric_changes",
        "cancellation_boundary", "recalculation_triggers", "first_touch_clock",
        "derived_scenario_ensemble", "execution_cost_sensitivity",
        "calibration_contract", "derivative_switch_thresholds", "shadow_policy_contract",
        "phase_e_authority_contract", "decision_inputs", "decision_influence",
        "influence_report", "option_derivative_state",
    )
    compact_manager = _pick(manager, manager_fields)
    compact_manager["policies"] = _compact_policies(manager.get("policies"))
    compact_manager["evidence"] = _compact_evidence(manager.get("evidence"))
    compact_manager["input_audit"] = _compact_input_audit(manager.get("input_audit"))
    compact_manager["gate"] = _bounded(manager.get("gate") or {})

    payload = _pick(snapshot, (
        "captured_ts", "trade_id", "strategy", "position_state", "trade_geometry",
        "time_context", "observation", "metric_coverage", "validation",
        "position_management_risk_long", "active_edge_provisional_weight",
        "active_edge", "active_edge_context", "short_horizon_policy", "ai_review_mode",
    ))
    payload["policy_manager"] = compact_manager
    payload["ede_causal_context"] = _compact_ede(snapshot.get("ede_causal_context"))
    previous_reviews = snapshot.get("previous_reviews")
    payload["provider_history_summary"] = {
        "metric_history_present": bool(snapshot.get("metric_history")),
        "previous_review_count": len(previous_reviews) if isinstance(previous_reviews, list) else 0,
    }

    for key in (
        "time_context", "observation", "metric_coverage", "validation",
        "active_edge_context", "short_horizon_policy",
    ):
        if key in payload:
            payload[key] = _bounded(payload[key])

    payload["provider_projection"] = {
        "contract_version": "ai-llm-explanation-projection-v2",
        "authority": "EXPLANATION_ONLY",
        "canonical_snapshot_unchanged": True,
        "compaction_tier": "rich",
        "truncated_debug_workspaces": False,
        "original_snapshot_bytes": original_bytes,
        "final_bytes": 0,
    }

    if _json_bytes(payload) > PROVIDER_SNAPSHOT_LIMIT_BYTES:
        return _minimal_provider_projection(snapshot, original_bytes)

    for _ in range(2):
        payload["provider_projection"]["final_bytes"] = _json_bytes(payload)
    if _json_bytes(payload) > PROVIDER_SNAPSHOT_LIMIT_BYTES:
        return _minimal_provider_projection(snapshot, original_bytes)
    return payload


def _circuit_remaining(now: float | None = None) -> float:
    now = time.monotonic() if now is None else float(now)
    with _CIRCUIT_LOCK:
        return max(0.0, float(_CIRCUIT_OPEN_UNTIL) - now)


def _open_circuit() -> None:
    global _CIRCUIT_OPEN_UNTIL
    with _CIRCUIT_LOCK:
        _CIRCUIT_OPEN_UNTIL = max(
            float(_CIRCUIT_OPEN_UNTIL), time.monotonic() + provider_circuit_sec())


def _close_circuit() -> None:
    global _CIRCUIT_OPEN_UNTIL
    with _CIRCUIT_LOCK:
        _CIRCUIT_OPEN_UNTIL = 0.0


def bounded_provider_call(
    fn: Callable[[dict[str, Any]], dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    timeout_sec: float | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> dict[str, Any]:
    """Call one provider with a hard wall-clock budget and stale-work circuit."""
    timeout = provider_timeout_sec() if timeout_sec is None else float(timeout_sec)
    production_pool = executor is None
    if production_pool:
        remaining = _circuit_remaining()
        if remaining > 0:
            raise RuntimeError(f"provider_circuit_open_{remaining:.1f}s")
    pool = executor or _EXECUTOR
    future = pool.submit(fn, snapshot)
    try:
        result = future.result(timeout=max(0.001, timeout))
        if production_pool:
            _close_circuit()
        return result
    except FutureTimeout as exc:
        future.cancel()
        if production_pool:
            _open_circuit()
        raise RuntimeError(f"provider_timeout_after_{timeout:.1f}s") from exc


def install_ai_provider_guard() -> None:
    """Patch the FastAPI module's imported provider call before app creation."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import app as app_module

    original = app_module.request_verdict

    def guarded_request_verdict(snapshot: dict[str, Any]) -> dict[str, Any]:
        provider_snapshot = compact_provider_snapshot(snapshot)
        return bounded_provider_call(original, provider_snapshot)

    guarded_request_verdict.__name__ = getattr(original, "__name__", "request_verdict")
    guarded_request_verdict.__doc__ = (
        "Gateway-safe OpenRouter explanation call; deterministic policy remains authoritative."
    )
    app_module.request_verdict = guarded_request_verdict
    _INSTALLED = True
