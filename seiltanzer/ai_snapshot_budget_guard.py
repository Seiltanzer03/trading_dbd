"""Fail-soft guard for the base AI Verdict snapshot byte budget.

The deterministic management decision is authoritative. Compact report-integrity
and provenance views are useful for explanation, but they must never make
`/api/ai/verdict` unavailable after the underlying v18 snapshot already fit its
hard byte ceiling.
"""
from __future__ import annotations

from typing import Any

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
            return

        # Defensive retry through the proven v18 allowlist compactor. This keeps
        # management_decision/recommendation/policies/risk constraints while
        # bounding explanatory workspaces.
        base(snapshot)
        budget = snapshot.setdefault("snapshot_budget", {})
        budget["report_integrity_degraded"] = True
        budget["degrade_reason"] = "BASE_REPORT_INTEGRITY_BYTE_BUDGET"
        size = _sync_final_bytes(ai_verdict, snapshot)
        if size >= ai_verdict._impl.SNAPSHOT_LIMIT_BYTES:
            raise RuntimeError("AI authoritative snapshot exceeds hard byte budget")

    ai_verdict._enforce_snapshot_budget_with_report_integrity = failsoft
    ai_verdict._impl._enforce_snapshot_budget = failsoft
    _INSTALLED = True
