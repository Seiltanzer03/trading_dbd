"""Budget bridge for post-snapshot enrichers.

Post-snapshot enrichers such as Active Edge must resolve byte-budget enforcement
from the public ``ai_verdict`` facade.  Presentation enrichment is never allowed
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
    # Root report_integrity preserves these same facts.  The manager replicas are
    # presentation conveniences and are the first thing to drop under pressure.
    for key in (
        "scenario_geometry", "raw_optimizer_stability", "stability",
        "risk_tradeoff", "monte_carlo_validation", "active_edge_provisional_weight",
    ):
        manager.pop(key, None)


def enforce_public_snapshot_budget(snapshot: dict[str, Any]) -> None:
    """Enforce the public budget without allowing enrichment-only HTTP 500s.

    Normal path uses the integrity-aware facade enforcer.  If a late enrichment
    pushes an otherwise-valid snapshot over the hard ceiling, compact that late
    context and retry.  Only an exceptional final fallback removes the compact
    presentation contracts and delegates to the proven v18 decision snapshot
    compactor; policy/CVaR/arbiter inputs remain untouched.
    """
    from . import ai_verdict

    enforce = getattr(ai_verdict, "_enforce_snapshot_budget_with_report_integrity", None)
    if not callable(enforce):
        enforce = getattr(ai_verdict, "_enforce_snapshot_budget", None)
    if not callable(enforce):
        raise RuntimeError("AI snapshot budget enforcer unavailable")

    try:
        enforce(snapshot)
        return
    except RuntimeError as exc:
        if "snapshot byte budget exceeded" not in str(exc).lower():
            raise

    _compact_active_edge_context(snapshot)
    _drop_duplicate_integrity_views(snapshot)
    try:
        enforce(snapshot)
        budget = snapshot.setdefault("snapshot_budget", {})
        budget["late_enrichment_compacted"] = True
        return
    except RuntimeError as exc:
        if "snapshot byte budget exceeded" not in str(exc).lower():
            raise

    # Last-resort compatibility path.  An explanatory contract must never take
    # down /api/ai/verdict.  Preserve the authoritative policy snapshot and mark
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
