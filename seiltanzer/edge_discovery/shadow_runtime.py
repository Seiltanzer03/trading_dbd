"""Low-priority runtime materializer for EDE v1.3 prospective shadow.

The full selective discovery stays outside the request/decision path. This
materializer only consumes the latest frozen v1.3 audit and immutable G1S rows,
then appends shadow prediction/resolution events. It is intentionally bounded
and may lag/fail without affecting production decisions.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .baseline_rows import baseline_eligible_rows
from .prospective_v13 import ProspectiveFeatureAdapter
from .selective import SELECTIVE_HORIZONS
from .shadow import (
    ShadowLedger,
    create_shadow_predictions,
    resolve_shadow_predictions,
    shadow_ledger_path,
    shadow_summary,
)
from .shadow_cache import write_shadow_summary_cache
from ..production_resource_guard import memory_pressure_state

SHADOW_RUNTIME_VERSION = "g1s-ede-shadow-runtime-v1.3.2"
SHADOW_RUNTIME_INTERVAL_SEC = 60.0
_SHADOW_READ_BUDGET_SEC = 30.0
_SHADOW_READ_RESERVE_BYTES = 1024 ** 3
AUDIT_CONTRACT_VERSIONS = {
    "g1s-ede-production-audit-v1.3",
    "g1s-ede-production-audit-v1.3.1",
    "g1s-ede-production-audit-v1.3.3",
}


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
    if str(payload.get("contract_version")) not in AUDIT_CONTRACT_VERSIONS:
        return None
    if not isinstance(payload.get("selective_search"), dict):
        return None
    if not isinstance(payload.get("frozen_evidence"), dict):
        return None
    return payload


def _run_llm_edge_lifecycle(engine: Any, current: float) -> dict[str, Any]:
    """Reuse this existing worker phase; Researcher failure can never break EDE/G1S."""
    try:
        from seiltanzer.llm_edge_lifecycle import llm_edge_prospective_tick
        return llm_edge_prospective_tick(engine, now=current)
    except Exception as exc:
        return {
            "status": "ERROR",
            "reason": "LLM_EDGE_PROSPECTIVE_TICK_ERROR",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "production_authority": False,
            "writes_active_edge_registry": False,
        }


def _shadow_read_has_headroom(database: Path) -> bool:
    capacity = os.statvfs(database)
    return capacity.f_bavail * capacity.f_frsize >= _SHADOW_READ_RESERVE_BYTES


def _shadow_feature_rows(runtime: Any, *, available_asof: float) -> list[dict[str, Any]]:
    """Stream raw payloads from an owned WAL snapshot, never the live writer.

    The adapter deliberately fetches atomically on shared live connections.
    An explicit read-only transaction enables its existing 32-row streaming
    path without weakening consistency or truncating the derived cohort.
    """
    with runtime._lock:
        database = next((row[2] for row in runtime._conn.execute(
            "PRAGMA database_list").fetchall() if row[1] == "main"), "")
    if not database:
        # In-memory/demo runtimes cannot be reopened by filename; retain their
        # established atomic extraction instead of creating a different DB.
        return ProspectiveFeatureAdapter(runtime, available_asof=available_asof).rows(
            resolved_only=False, strict=False)

    database = Path(database)
    deadline = time.monotonic() + _SHADOW_READ_BUDGET_SEC
    refusal = None
    last_capacity_check = -float("inf")

    def interrupted() -> int:
        nonlocal refusal, last_capacity_check
        now = time.monotonic()
        if now >= deadline:
            refusal = "SHADOW_READ_TIME_BUDGET_EXCEEDED"
        elif now - last_capacity_check >= 0.25:
            last_capacity_check = now
            if not _shadow_read_has_headroom(database):
                refusal = "SHADOW_READ_DISK_BUDGET_EXCEEDED"
            elif memory_pressure_state().get("pause_background"):
                refusal = "SHADOW_READ_MEMORY_BUDGET_EXCEEDED"
        return int(refusal is not None)

    def check_read_budget() -> None:
        # SQLite callbacks do not run while Python decodes/transforms a batch.
        if interrupted():
            raise RuntimeError(refusal)

    if interrupted():
        raise RuntimeError(refusal)
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=0.5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA cache_size=-2048")
        connection.set_progress_handler(interrupted, 1000)
        connection.execute("BEGIN")
        reader = SimpleNamespace(_conn=connection, _lock=threading.RLock(),
                                 _consistent_read_snapshot=True,
                                 _read_budget_check=check_read_budget)
        rows = ProspectiveFeatureAdapter(reader, available_asof=available_asof).rows(
            resolved_only=False, strict=False)
        # Also check Python-side work before any shadow ledger/cache publication.
        last_capacity_check = -float("inf")
        if interrupted():
            raise RuntimeError(refusal)
        return rows
    except sqlite3.OperationalError as exc:
        if refusal is not None:
            raise RuntimeError(refusal) from exc
        raise
    finally:
        # Includes failed decoding, interrupted SQL and downstream exceptions.
        # The live runtime connection and all observations remain untouched.
        connection.close()


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

    # Prospective Researcher work is higher priority than creating new shadow
    # diagnostics and is deliberately executed before any EDE audit/history read.
    llm_edge_lifecycle = _run_llm_edge_lifecycle(engine, current)

    audit_path = latest_v13_audit_path(engine)
    audit = _load_latest_audit(audit_path)
    if audit is None:
        return {
            "contract_version": SHADOW_RUNTIME_VERSION,
            "refreshed": False, "reason": "V13_AUDIT_UNAVAILABLE",
            "audit_path": str(audit_path),
            "llm_edge_lifecycle": llm_edge_lifecycle,
        }
    runtime = getattr(engine, "short_horizon", None)
    if runtime is None:
        return {
            "contract_version": SHADOW_RUNTIME_VERSION,
            "refreshed": False, "reason": "G1S_RUNTIME_UNAVAILABLE",
            "llm_edge_lifecycle": llm_edge_lifecycle,
        }

    rows = _shadow_feature_rows(runtime, available_asof=current)
    resolved_rows_all = [
        row for row in rows
        if row.get("outcome_available")
        and int(row.get("horizon_minutes") or 0) in SELECTIVE_HORIZONS
        and row.get("resolved_ts") is not None
        and float(row["resolved_ts"]) <= current+1e-6]
    resolved_rows, baseline_row_gate = baseline_eligible_rows(resolved_rows_all)
    rule_active_since = float(
        (audit.get("frozen_evidence") or {}).get("frozen_at") or current)
    pending_rows = [
        row for row in rows
        if not row.get("outcome_available")
        and int(row.get("horizon_minutes") or 0) in SELECTIVE_HORIZONS
        and rule_active_since <= float(row.get("captured_ts") or 0.0) <= current+1e-6]

    ledger = ShadowLedger(shadow_ledger_path(engine))
    # Outcome resolution does not need the baseline feature gate. A prediction
    # that existed before the target must remain resolvable even if an optional
    # sanity-baseline field at T0 was missing.
    resolution = resolve_shadow_predictions(
        ledger, resolved_rows=resolved_rows_all, asof_ts=current)
    prediction_creation = create_shadow_predictions(
        ledger,
        frozen_evidence=audit["frozen_evidence"],
        selective_report=audit["selective_search"],
        resolved_rows=resolved_rows,
        pending_rows=pending_rows,
        created_ts=current)
    summary = shadow_summary(ledger, cutoff_ts=current)
    summary_cache = write_shadow_summary_cache(
        engine, summary=summary, cutoff_ts=current)
    return {
        "contract_version": SHADOW_RUNTIME_VERSION,
        "refreshed": True,
        "audit_path": str(audit_path),
        "rule_active_since": rule_active_since,
        "rule_must_preexist_t0": True,
        "resolved_rows_seen": len(resolved_rows_all),
        "baseline_scoreable_resolved_rows": len(resolved_rows),
        "baseline_row_gate": baseline_row_gate,
        "pending_rows_seen": len(pending_rows),
        "resolution": resolution,
        "prediction_creation": prediction_creation,
        "summary_cache": summary_cache,
        "summary": {
            "prediction_count": summary.get("prediction_count", 0),
            "resolved_count": summary.get("resolved_count", 0),
            "pending_count": summary.get("pending_count", 0),
            "candidate_count": summary.get("candidate_count", 0),
        },
        "llm_edge_lifecycle": llm_edge_lifecycle,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }
