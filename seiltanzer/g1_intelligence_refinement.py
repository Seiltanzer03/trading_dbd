"""Compatibility/integrity refinement for G.1E model-readiness presentation.

G.1C intentionally reports readiness per semantic Q scope (native/direct/inverse)
rather than one pooled aggregate.  The cockpit must preserve that contract while
still giving a human-readable "how much more evidence" summary.
"""
from __future__ import annotations

from typing import Any

from . import g1_intelligence_runtime as _g1e


REFINEMENT_VERSION = "g1e-semantic-readiness-v1"


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _deficits(required: dict, observed: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, req in (required or {}).items():
        try:
            out[str(key)] = max(0, int(req) - int((observed or {}).get(key, 0)))
        except (TypeError, ValueError):
            continue
    return out


def _best_scope(scopes: list[dict]) -> dict:
    """Pick only a presentation representative; never pool scope evidence."""
    if not scopes:
        return {}

    def score(scope: dict) -> tuple:
        deficits = _deficits(scope.get("required") or {}, scope.get("observed") or {})
        missing_kinds = sum(1 for value in deficits.values() if value > 0)
        total_gap = sum(deficits.values())
        # Prefer ready, then the closest independently valid semantic scope.
        return (
            0 if bool(scope.get("ready")) else 1,
            missing_kinds,
            total_gap,
            str(scope.get("scope_key") or ""),
        )

    return min(scopes, key=score)


def readiness_item(item: dict | None) -> dict:
    item = item or {}
    scopes = [scope for scope in (item.get("scopes") or []) if isinstance(scope, dict)]
    representative = _best_scope(scopes)
    # Older aggregate G.1C responses already expose required/observed directly.
    required = representative.get("required") or item.get("required") or {}
    observed = representative.get("observed") or item.get("observed") or {}
    deficits = _deficits(required, observed)

    raw_blockers = item.get("blockers") or representative.get("blockers") or []
    if isinstance(raw_blockers, dict):
        blockers = [str(code) for code, count in raw_blockers.items() if _int(count) > 0]
        blocker_counts = {str(code): _int(count) for code, count in raw_blockers.items()}
    else:
        blockers = [str(code) for code in raw_blockers]
        blocker_counts = {code: 1 for code in blockers}

    return {
        "status": item.get("status") or representative.get("status") or "INSUFFICIENT_EVIDENCE",
        "ready": bool(item.get("ready") or representative.get("ready")),
        "family": item.get("family") or representative.get("family"),
        "required": required,
        "observed": observed,
        "deficits": deficits,
        "blockers": blockers,
        "blocker_counts": blocker_counts,
        "explanations": [_g1e.HUMAN_EXPLANATIONS.get(code, code) for code in blockers],
        "semantic_scope_n": _int(item.get("scope_n")) or len(scopes),
        "ready_scope_n": _int(item.get("ready_scope_n")),
        "representative_scope_key": representative.get("scope_key"),
        "semantic_pooling": False,
        "refinement_contract_version": REFINEMENT_VERSION,
    }


def install_g1_intelligence_refinement() -> None:
    if getattr(_g1e.IntelligenceRuntime, "_g1e_readiness_refinement", None) == REFINEMENT_VERSION:
        return
    _g1e.IntelligenceRuntime._readiness_item = staticmethod(readiness_item)
    _g1e.IntelligenceRuntime._g1e_readiness_refinement = REFINEMENT_VERSION
