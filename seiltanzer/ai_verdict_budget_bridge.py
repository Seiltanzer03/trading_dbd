"""Budget bridge for post-snapshot enrichers.

Post-snapshot enrichers such as Active Edge must resolve byte-budget enforcement
from the public ``ai_verdict`` facade. Presentation enrichment is never allowed
to make the deterministic verdict endpoint unavailable: verbose explanatory
payload is degraded before any decision-bearing snapshot fields are sacrificed.
"""
from __future__ import annotations

from typing import Any


def _compact_active_edge_context(snapshot: dict[str, Any]) -> None:
    """Drop verbose Active Edge rows while preserving aggregate decision facts."""
    ede = snapshot.get("ede_causal_context")
    if isinstance(ede, dict):
        active = ede.get("active_high_risk")
        if isinstance(active, dict):
            if active.get("signals"):
                active["signals"] = []
                active["serialized_signal_n"] = 0
                active["details_truncated"] = True
            if active.get("matched_groups"):
                active["matched_groups"] = []
                # Keep matched_group_n: it is the aggregate fact used by reports.
                active["details_truncated"] = True


def _drop_duplicate_integrity_views(snapshot: dict[str, Any]) -> None:
    manager = snapshot.get("policy_manager")
    if not isinstance(manager, dict):
        return
    # Root report_integrity preserves these same facts. The manager replicas are
    # presentation conveniences and are the first thing to drop under pressure.
    for key in (
        "scenario_geometry", "raw_optimizer_stability", "stability",
        "risk_tradeoff", "monte_carlo_validation", "active_edge_provisional_weight",
    ):
        manager.pop(key, None)


def _trim_static_availability_explanation(snapshot: dict[str, Any]) -> None:
    """Keep per-input provenance while removing duplicated static prose."""
    contract = snapshot.get("metric_availability_contract")
    if not isinstance(contract, dict):
        return
    for key in (
        "goal", "fallback_order", "fallback_rule", "required_provenance_fields",
        "primary_unavailable_semantics",
    ):
        contract.pop(key, None)
    contract["static_semantics_compacted"] = True


def _snapshot_size(ai_verdict: Any, snapshot: dict[str, Any]) -> int | None:
    impl = getattr(ai_verdict, "_impl", None)
    measure = getattr(impl, "_snapshot_bytes", None)
    return int(measure(snapshot)) if callable(measure) else None


def _target_bytes(ai_verdict: Any) -> int | None:
    impl = getattr(ai_verdict, "_impl", None)
    value = getattr(impl, "SNAPSHOT_TARGET_BYTES", None)
    return int(value) if value is not None else None


def _limit_bytes(ai_verdict: Any) -> int | None:
    impl = getattr(ai_verdict, "_impl", None)
    value = getattr(impl, "SNAPSHOT_LIMIT_BYTES", None)
    return int(value) if value is not None else None


def _sync_final_bytes(ai_verdict: Any, snapshot: dict[str, Any]) -> int | None:
    """Keep snapshot_budget.final_bytes equal to the final serialized payload."""
    budget = snapshot.setdefault("snapshot_budget", {})
    # Setting the field changes serialized size; converge in a few deterministic
    # iterations so tests/telemetry always report the exact final byte count.
    last = None
    for _ in range(4):
        size = _snapshot_size(ai_verdict, snapshot)
        if size is None:
            return None
        budget["final_bytes"] = size
        if size == last:
            return size
        last = size
    return _snapshot_size(ai_verdict, snapshot)


def _fit_post_enrichment_target(
    ai_verdict: Any,
    snapshot: dict[str, Any],
    *,
    already_compacted: bool = False,
) -> bool:
    """Compact only duplicated/verbose presentation fields after a successful pass."""
    size = _snapshot_size(ai_verdict, snapshot)
    target = _target_bytes(ai_verdict)
    limit = _limit_bytes(ai_verdict)
    compacted = bool(already_compacted)
    if size is None:
        if compacted:
            snapshot.setdefault("snapshot_budget", {})["late_enrichment_compacted"] = True
        return True

    if target is not None and size > target:
        _compact_active_edge_context(snapshot)
        _drop_duplicate_integrity_views(snapshot)
        compacted = True
        size = _snapshot_size(ai_verdict, snapshot)
        if size is not None and size > target:
            _trim_static_availability_explanation(snapshot)
            compacted = True
            size = _snapshot_size(ai_verdict, snapshot)

    if compacted:
        snapshot.setdefault("snapshot_budget", {})["late_enrichment_compacted"] = True
    size = _sync_final_bytes(ai_verdict, snapshot)
    return size is None or limit is None or size < limit


def enforce_public_snapshot_budget(snapshot: dict[str, Any]) -> None:
    """Enforce the public budget without allowing enrichment-only HTTP 500s.

    Normal path uses the integrity-aware facade enforcer. If a late enrichment
    pushes an otherwise-valid snapshot over the target, only duplicated/verbose
    presentation fields are removed. If it exceeds the hard ceiling, retry with
    compact Active Edge context. Only an exceptional final fallback removes the
    compact presentation contracts and delegates to the proven v18 decision
    snapshot compactor; policy/CVaR/arbiter inputs remain untouched.
    """
    from . import ai_verdict

    enforce = getattr(ai_verdict, "_enforce_snapshot_budget_with_report_integrity", None)
    if not callable(enforce):
        enforce = getattr(ai_verdict, "_enforce_snapshot_budget", None)
    if not callable(enforce):
        raise RuntimeError("AI snapshot budget enforcer unavailable")

    try:
        enforce(snapshot)
        if _fit_post_enrichment_target(ai_verdict, snapshot):
            return
    except RuntimeError as exc:
        if "snapshot byte budget exceeded" not in str(exc).lower():
            raise

    _compact_active_edge_context(snapshot)
    _drop_duplicate_integrity_views(snapshot)
    try:
        enforce(snapshot)
        if _fit_post_enrichment_target(
            ai_verdict, snapshot, already_compacted=True
        ):
            return
    except RuntimeError as exc:
        if "snapshot byte budget exceeded" not in str(exc).lower():
            raise

    # Last-resort compatibility path. An explanatory contract must never take
    # down /api/ai/verdict. Preserve the authoritative policy snapshot and mark
    # that presentation provenance was omitted because of the byte ceiling.
    snapshot.pop("metric_availability_contract", None)
    snapshot.pop("report_integrity", None)
    _compact_active_edge_context(snapshot)
    _drop_duplicate_integrity_views(snapshot)
    base = getattr(ai_verdict, "_BASE_ENFORCE_SNAPSHOT_BUDGET_V18", None)
    if not callable(base):
        raise RuntimeError("AI base snapshot budget enforcer unavailable")
    base(snapshot)
    budget = snapshot.setdefault("snapshot_budget", {})
    budget["report_integrity_degraded"] = True
    budget["degrade_reason"] = "LATE_ENRICHMENT_BYTE_BUDGET"
    _sync_final_bytes(ai_verdict, snapshot)
