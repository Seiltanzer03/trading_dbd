"""Low-priority runtime materializer for EDE v1.3 prospective shadow.

The full selective discovery stays outside the request/decision path. This
materializer only consumes the latest frozen v1.3 audit and immutable G1S rows,
then appends shadow prediction/resolution events. It is intentionally bounded
and may lag/fail without affecting production decisions.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .prospective import ProspectiveFeatureAdapter
from .selective import SELECTIVE_HORIZONS
from .shadow import (
    ShadowLedger,
    create_shadow_predictions,
    resolve_shadow_predictions,
    shadow_ledger_path,
    shadow_summary,
)

SHADOW_RUNTIME_VERSION = "g1s-ede-shadow-runtime-v1.3"
SHADOW_RUNTIME_INTERVAL_SEC = 60.0


def latest_v13_audit_path(engine: Any) -> Path:
    override = os.environ.get("SEILTANZER_EDE_V13_LATEST_AUDIT")
    if override:
        return Path(override)
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research" / "ede_v13_latest_audit.json"


def _load_latest_audit(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("contract_version")) != "g1s-ede-production-audit-v1.3":
        return None
    if not isinstance(payload.get("selective_search"), dict):
        return None
    if not isinstance(payload.get("frozen_evidence"), dict):
        return None
    return payload


def materialize_runtime_shadow(engine: Any, *, now: float | None = None) -> dict[str, Any]:
    """One bounded shadow pass; safe to call from the low-priority worker."""
    current = float(now or time.time())
    last = float(getattr(engine, "_ede_shadow_runtime_last_ts", 0.0) or 0.0)
    if current-last < SHADOW_RUNTIME_INTERVAL_SEC:
        return {
            "contract_version": SHADOW_RUNTIME_VERSION,
            "refreshed": False, "reason": "INTERVAL_NOT_DUE",
            "next_due_ts": last + SHADOW_RUNTIME_INTERVAL_SEC,
        }
    engine._ede_shadow_runtime_last_ts = current

    audit_path = latest_v13_audit_path(engine)
    audit = _load_latest_audit(audit_path)
    if audit is None:
        return {
            "contract_version": SHADOW_RUNTIME_VERSION,
            "refreshed": False, "reason": "V13_AUDIT_UNAVAILABLE",
            "audit_path": str(audit_path),
        }
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {
            "contract_version": SHADOW_RUNTIME_VERSION,
            "refreshed": False, "reason": "G1S_RUNTIME_UNAVAILABLE",
        }

    adapter = ProspectiveFeatureAdapter(runtime)
    rows = adapter.rows(resolved_only=False, strict=False)
    resolved_rows = [
        row for row in rows
        if row.get("outcome_available")
        and int(row.get("horizon_minutes") or 0) in SELECTIVE_HORIZONS
        and row.get("resolved_ts") is not None
        and float(row["resolved_ts"]) <= current+1e-6]
    pending_rows = [
        row for row in rows
        if not row.get("outcome_available")
        and int(row.get("horizon_minutes") or 0) in SELECTIVE_HORIZONS
        and float(row.get("captured_ts") or 0.0) <= current+1e-6]

    ledger = ShadowLedger(shadow_ledger_path(engine))
    resolution = resolve_shadow_predictions(
        ledger, resolved_rows=resolved_rows, asof_ts=current)
    prediction_creation = create_shadow_predictions(
        ledger,
        frozen_evidence=audit["frozen_evidence"],
        selective_report=audit["selective_search"],
        resolved_rows=resolved_rows,
        pending_rows=pending_rows,
        created_ts=current)
    summary = shadow_summary(ledger, cutoff_ts=current)
    return {
        "contract_version": SHADOW_RUNTIME_VERSION,
        "refreshed": True,
        "audit_path": str(audit_path),
        "resolved_rows_seen": len(resolved_rows),
        "pending_rows_seen": len(pending_rows),
        "resolution": resolution,
        "prediction_creation": prediction_creation,
        "summary": {
            "prediction_count": summary.get("prediction_count", 0),
            "resolved_count": summary.get("resolved_count", 0),
            "pending_count": summary.get("pending_count", 0),
            "candidate_count": summary.get("candidate_count", 0),
        },
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }
