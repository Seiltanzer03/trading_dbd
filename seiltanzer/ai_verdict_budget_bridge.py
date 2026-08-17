"""Budget bridge for post-snapshot enrichers.

This module is intentionally tiny: enrichers such as Active Edge must resolve
byte-budget enforcement from the public ai_verdict facade, not retain a stale
v18 function captured before report-integrity extensions were installed.
"""
from __future__ import annotations

from typing import Any


def enforce_public_snapshot_budget(snapshot: dict[str, Any]) -> None:
    from . import ai_verdict

    enforce = getattr(ai_verdict, "_enforce_snapshot_budget_with_report_integrity", None)
    if not callable(enforce):
        enforce = getattr(ai_verdict, "_enforce_snapshot_budget", None)
    if not callable(enforce):
        raise RuntimeError("AI snapshot budget enforcer unavailable")
    enforce(snapshot)
