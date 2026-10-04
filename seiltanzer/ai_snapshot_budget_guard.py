"""Fail-soft guard for the base AI Verdict snapshot byte budget.

The deterministic management decision is authoritative. Compact report-integrity
and provenance views are useful for explanation, but they must never make
`/api/ai/verdict` unavailable after the underlying v18 snapshot already fit its
hard byte ceiling.
"""
from __future__ import annotations

from typing import Any
from .unified_edge_audit import compact_unified_ensemble
from .operational_edge_compaction import compact_operational_edges, compact_evidence_lineage, FAMILY_ROOTS

_INSTALLED = False


def _is_budget_error(exc: BaseException) -> bool:
    return "snapshot byte budget exceeded" in str(exc).lower()


def _sync_final_bytes(ai_verdict: Any, snapshot: dict[str, Any]) -> int:
    budget = snapshot.setdefault("snapshot_budget", {})
    last = -1
    for _ in range(4):
        size = int(ai_verdict._impl._snapshot_bytes(snapshot))
        budget["final_bytes"] = size
        if size == last:
            return size
        last = size
    return int(ai_verdict._impl._snapshot_bytes(snapshot))


def _drop_explanation_only_contracts(snapshot: dict[str, Any]) -> None:
    snapshot.pop("metric_availability_contract", None)
    snapshot.pop("report_integrity", None)

    # Prospective shadow is research/explanation only. Keep the production EDE
    # aggregate itself, but remove verbose shadow payload if the freeze is under
    # hard byte pressure.
    snapshot.pop("ede_prospective_shadow", None)
    ede = snapshot.get("ede_causal_context")
    if isinstance(ede, dict):
        ede.pop("prospective_shadow", None)
        active = ede.get("active_high_risk")
        if isinstance(active, dict):
            if active.get("signals"):
                active["signals"] = []
                active["serialized_signal_n"] = 0
                active["details_truncated"] = True
            if active.get("matched_groups"):
                active["matched_groups"] = []
                active["details_truncated"] = True


def _compact_input_audit(value: Any) -> dict[str, Any]:
    """Preserve price authority/provenance under the emergency byte guard."""
    audit = value if isinstance(value, dict) else {}
    row_keys = (
        "available", "status", "source", "role", "age_sec", "symbol",
        "reason", "value", "quality", "proxy_quality", "is_proxy",
        "fallback_tier", "production_authority",
    )
    rows: dict[str, Any] = {}
    for name, row in (audit.get("rows") or {}).items():
        if not isinstance(row, dict):
            continue
        rows[str(name)] = {key: row[key] for key in row_keys if key in row}
    result = {"rows": rows}
    for key in (
        "snapshot_utc", "all_required_available", "missing_required",
        "degraded_inputs", "required_count", "available_count", "total_count",
    ):
        if key in audit:
            result[key] = audit[key]
    return result


def _compact_scalars(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {key: row[key] for key in keys if key in row}


def _strict_authoritative_compaction(snapshot: dict[str, Any]) -> None:
    """Keep the completed decision while dropping oversized explanation views.

    The v18 compactor normally gets below the hard ceiling by bounding evidence.
    A production snapshot can still exceed it when several independently bounded
    research contracts are present at once. The legacy decision already exists,
    but the unified selector still consumes the cached expert inputs later.
    Preserve those bounded causal inputs alongside the policy and risk facts;
    discard duplicated research workspaces.
    """
    compact_operational_edges(snapshot)
    manager = snapshot.get("policy_manager")
    if not isinstance(manager, dict):
        manager = {}

    manager_keep = (
        "position_economics", "repeat_intervention_gate", "decision_reliability", "execution_cost_model", "execution_cost_repricing_required",
        "version", "management_decision", "recommendation", "policies",
        "selection_rule", "gate", "inputs", "risk_constraint",
        "management_arbiter", "state_change_attribution",
        "unified_edge_ensemble",
        "expert_registry",
        "mathematical_edge", "active_edge_provisional_weight", "llm_edge_exploratory_weight",
        "market_regime",
        "calibration_contract", "recalculation_triggers",
        "cancellation_boundary", "phase_e_authority_contract",
        "shadow_actions", "extended_actions", "input_audit",
        "scenario_geometry", "raw_optimizer_stability", "stability",
        "risk_tradeoff",
    )
    compact_manager = {
        key: manager[key] for key in manager_keep if key in manager
    }
    lineage = compact_evidence_lineage(manager.get("evidence"))
    if lineage:
        compact_manager["evidence"] = lineage
    if manager.get("unified_edge_ensemble"):
        compact_manager["unified_edge_ensemble"] = compact_unified_ensemble(manager["unified_edge_ensemble"])
    from .management_contract import decision_reliability
    compact_manager["decision_reliability"] = decision_reliability(snapshot)
    compact_manager["input_audit"] = _compact_input_audit(
        manager.get("input_audit"))
    compact_manager["scenario_geometry"] = _compact_scalars(
        manager.get("scenario_geometry"), (
            "scenario_count", "next_rung_r", "p_next_rung_before_stop",
            "rung_first_count", "p_stop_before_next_rung", "stop_first_count",
            "p_unresolved_full_horizon", "unresolved_count", "resolved_count",
            "full_horizon_minutes", "mean_event_minutes_given_resolved",
            "take_first_probability", "stop_or_be_first_probability",
        ))

    root_keep = (
        "captured_ts", "trade_id", "strategy", "trade_geometry", "position_state",
        "trade_identity", "position_execution_units", "position_execution_context_audit",
        "execution_cost_context_audit",
        "validation", "data_quality", "market_state", "hard_risk",
        "risk_constraints", "metric_coverage", "policy_manager", "snapshot_budget",
        "market_regime", "regime", "edge_regime", "edge_family_budget_status",
    ) + FAMILY_ROOTS
    compact_root = {
        key: snapshot[key] for key in root_keep
        if key in snapshot and key != "policy_manager"
    }
    compact_root["policy_manager"] = compact_manager
    snapshot.clear()
    snapshot.update(compact_root)


def _emergency_authoritative_compaction(snapshot: dict[str, Any], ai_verdict: Any) -> None:
    """Bound the transport after an unusually large live option-chain update.

    Keep the legacy decision and the downstream selector's expert inputs, all
    compared policy outcomes, risk inputs and price provenance; remove redundant
    explanations and per-policy Monte Carlo workspaces. This tier is reached
    only when the normal and strict compaction still exceed the byte ceiling.
    """
    compact_operational_edges(snapshot)
    for key in ("validation", "market_state", "data_quality", "metric_coverage"):
        snapshot.pop(key, None)
    manager = snapshot.get("policy_manager") or {}
    lineage = compact_evidence_lineage(manager.get("evidence"))
    if lineage:
        manager["evidence"] = lineage
    else:
        manager.pop("evidence", None)
    if manager.get("unified_edge_ensemble"):
        manager["unified_edge_ensemble"] = compact_unified_ensemble(manager["unified_edge_ensemble"])
    for key in (
        "state_change_attribution", "recalculation_triggers",
        "cancellation_boundary", "phase_e_authority_contract",
        "shadow_actions", "extended_actions", "raw_optimizer_stability",
        "stability", "risk_tradeoff",
    ):
        manager.pop(key, None)
    manager["policies"] = {
        name: _compact_scalars(row, (
            "expected_final_r", "median_final_r", "cvar10_r",
            "p_final_profit", "p_final_loss", "p_giveback_0_25_from_now",
            "p_giveback_0_50_from_now", "p_next_rung_before_stop",
            "p_stop_before_next_rung", "no_event_probability", "eligible",
            "reason", "execution_cost_r", "gross_expected_final_r",
            "expected_final_r_net", "median_final_r_net", "cvar10_r_net", "outcomes_include_execution_costs",
        ))
        for name, row in (manager.get("policies") or {}).items()
    }
    bound = ai_verdict._impl._bounded
    for key in ("management_arbiter", "selection_rule", "gate"):
        if key in manager:
            manager[key] = (ai_verdict._impl._bounded_gate(manager[key])
                            if key == "gate" else bound(manager[key]))
    snapshot["policy_manager"] = manager


def install_ai_snapshot_budget_guard() -> None:
    """Prevent report-integrity byte pressure from becoming an HTTP 500."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import ai_verdict

    original = ai_verdict._enforce_snapshot_budget_with_report_integrity
    base = ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18

    def failsoft(snapshot: dict[str, Any]) -> None:
        try:
            original(snapshot)
            return
        except RuntimeError as exc:
            if not _is_budget_error(exc):
                raise

        # The v18 compactor already succeeded before report-integrity views were
        # restored. Remove only explanation/research replicas first.
        _drop_explanation_only_contracts(snapshot)
        budget = snapshot.setdefault("snapshot_budget", {})
        budget["report_integrity_degraded"] = True
        budget["degrade_reason"] = "BASE_REPORT_INTEGRITY_BYTE_BUDGET"
        size = _sync_final_bytes(ai_verdict, snapshot)
        if size < ai_verdict._impl.SNAPSHOT_LIMIT_BYTES:
            budget["degrade_level"] = "EXPLANATION_ONLY"
            _sync_final_bytes(ai_verdict, snapshot)
            return

        # Defensive retry through the proven v18 allowlist compactor. This keeps
        # management_decision/recommendation/policies/risk constraints while
        # bounding explanatory workspaces.
        try:
            base(snapshot)
            degrade_level = "BASE_RECOMPACTION"
        except RuntimeError as exc:
            if not _is_budget_error(exc):
                raise
            _strict_authoritative_compaction(snapshot)
            try:
                base(snapshot)
                degrade_level = "STRICT_AUTHORITATIVE"
            except RuntimeError as strict_exc:
                if not _is_budget_error(strict_exc):
                    raise
                _emergency_authoritative_compaction(snapshot, ai_verdict)
                degrade_level = "EMERGENCY_AUTHORITATIVE_TRANSPORT"
        budget = snapshot.setdefault("snapshot_budget", {})
        budget["report_integrity_degraded"] = True
        budget["degrade_reason"] = "BASE_REPORT_INTEGRITY_BYTE_BUDGET"
        budget["degrade_level"] = degrade_level
        size = _sync_final_bytes(ai_verdict, snapshot)
        if (size >= ai_verdict._impl.SNAPSHOT_LIMIT_BYTES
                and degrade_level != "EMERGENCY_AUTHORITATIVE_TRANSPORT"):
            _emergency_authoritative_compaction(snapshot, ai_verdict)
            budget = snapshot.setdefault("snapshot_budget", {})
            budget["degrade_level"] = "EMERGENCY_AUTHORITATIVE_TRANSPORT"
            size = _sync_final_bytes(ai_verdict, snapshot)
        if size >= ai_verdict._impl.SNAPSHOT_LIMIT_BYTES:
            raise RuntimeError("AI authoritative snapshot exceeds hard byte budget")

    ai_verdict._enforce_snapshot_budget_with_report_integrity = failsoft
    ai_verdict._impl._enforce_snapshot_budget = failsoft
    _INSTALLED = True
