"""Bound the optional LLM provider without weakening deterministic AI policy.

The deterministic policy snapshot/report is authoritative. OpenRouter is only an
explanation layer, so a slow provider must never hold the public HTTP request long
enough for an upstream gateway to return HTML 504. The provider receives a bounded
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


DEFAULT_PROVIDER_TIMEOUT_SEC = 8.0
MIN_PROVIDER_TIMEOUT_SEC = 3.0
MAX_PROVIDER_TIMEOUT_SEC = 8.0
DEFAULT_PROVIDER_CIRCUIT_SEC = 50.0
PROVIDER_SNAPSHOT_LIMIT_BYTES = 30_000
_PROVIDER_TARGET_BYTES = 26_000

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


def _bounded_context(value: Any, *, depth: int = 0) -> Any:
    """Bound explanation-only detail without inventing values."""
    if depth >= 5:
        return "[bounded]"
    if isinstance(value, str):
        return value if len(value) <= 384 else value[:381] + "..."
    if isinstance(value, (list, tuple)):
        return [_bounded_context(item, depth=depth + 1) for item in value[:10]]
    if isinstance(value, dict):
        return {
            key: _bounded_context(value[key], depth=depth + 1)
            for key in sorted(value)[:32]
        }
    return value


def compact_provider_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a small, non-authoritative projection for LLM explanation only."""
    payload = deepcopy(snapshot)
    original_bytes = _json_bytes(payload)

    history = payload.pop("metric_history", None)
    previous_reviews = payload.pop("previous_reviews", None)
    payload["provider_history_summary"] = {
        "metric_history_present": bool(history),
        "previous_review_count": (
            len(previous_reviews) if isinstance(previous_reviews, list) else 0
        ),
    }

    manager = payload.get("policy_manager")
    if isinstance(manager, dict):
        manager.pop("raw_optimizer_stability", None)
        manager.pop("monte_carlo_validation", None)
        manager.pop("scenario_geometry", None)
        for key in ("evidence", "input_audit", "gate"):
            if isinstance(manager.get(key), dict):
                manager[key] = _bounded_context(manager[key])
        payload["policy_manager"] = manager

    if isinstance(payload.get("ede_causal_context"), dict):
        payload["ede_causal_context"] = _bounded_context(
            payload["ede_causal_context"]
        )

    validation = payload.get("validation")
    if isinstance(validation, dict):
        keep = (
            "observations", "resolved_trades", "promotion_allowed",
            "q_calibration", "snapshot_scope", "full_report_endpoint",
        )
        payload["validation"] = {
            key: validation.get(key) for key in keep if key in validation
        }

    if _json_bytes(payload) > _PROVIDER_TARGET_BYTES:
        manager = payload.get("policy_manager") or {}
        keep_manager = (
            "version", "management_decision", "recommendation", "policies",
            "selection_rule", "gate", "evidence", "inputs", "risk_constraint",
            "management_arbiter", "state_change_attribution",
            "counterfactual_attribution", "metric_changes", "input_audit",
            "option_derivative_state", "derived_scenario_ensemble",
            "cancellation_boundary", "recalculation_triggers",
            "phase_e_authority_contract", "decision_inputs",
            "decision_influence", "influence_report",
        )
        compact_manager = {
            key: manager.get(key) for key in keep_manager if key in manager
        }
        for key in ("evidence", "input_audit", "gate", "option_derivative_state"):
            if key in compact_manager:
                compact_manager[key] = _bounded_context(compact_manager[key])
        payload["policy_manager"] = compact_manager

        keep_top = (
            "captured_ts", "trade_id", "strategy", "position_state",
            "trade_geometry", "time_context", "observation", "policy_manager",
            "metric_coverage", "validation", "ede_causal_context",
            "position_management_risk_long", "active_edge_provisional_weight",
            "active_edge", "active_edge_context", "short_horizon_policy",
            "ai_review_mode", "provider_history_summary",
        )
        payload = {key: payload.get(key) for key in keep_top if key in payload}

    if _json_bytes(payload) > PROVIDER_SNAPSHOT_LIMIT_BYTES:
        payload["ede_causal_context"] = _bounded_context(
            payload.get("ede_causal_context") or {}, depth=2
        )
        manager = payload.get("policy_manager") or {}
        for key in ("evidence", "input_audit", "gate", "option_derivative_state"):
            if key in manager:
                manager[key] = _bounded_context(manager[key], depth=2)
        payload["policy_manager"] = manager

    payload["provider_projection"] = {
        "contract_version": "ai-llm-explanation-projection-v1",
        "authority": "EXPLANATION_ONLY",
        "original_snapshot_bytes": original_bytes,
    }
    payload["provider_projection"]["final_bytes"] = _json_bytes(payload)

    if payload["provider_projection"]["final_bytes"] > PROVIDER_SNAPSHOT_LIMIT_BYTES:
        raise RuntimeError("LLM explanation snapshot byte budget exceeded")
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
