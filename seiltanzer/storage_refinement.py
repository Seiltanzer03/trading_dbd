"""Fund-grade refinements for the G.1E-0 storage contract."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from . import storage_runtime as _s


REFINEMENT_VERSION = "seiltanzer-storage-refinement-v3"

# Exact tables that carry economic/research identity today. Missing tables on an
# old DB are still reported as None, but current production manifests should see
# these names rather than aliases from the design draft.
CRITICAL_TABLES = (
    "trades", "account", "option_forecasts", "ai_verdicts",
    "policy_shadow_reviews", "decision_snapshots", "decision_path_points",
    "decision_replays", "human_decisions", "experiment_registry",
    "trade_market_path", "position_management_events", "management_decisions",
    "passive_market_observations", "passive_market_path", "passive_market_bars",
    "virtual_position_observations", "passive_collector_state",
    "g1_q_capture_attempts", "g1_dataset_membership", "g1_contract_errors",
    "g1_dataset_cuts", "g1_dataset_cut_members", "g1c_fit_runs",
    "g1c_shadow_models", "g1c_shadow_predictions", "g1c_contract_errors",
    "g1e_intelligence_snapshots",
)

_ORIGINAL_INIT = _s.StorageManager.__init__
_ORIGINAL_CREATE = _s.StorageManager.create_backup
_ORIGINAL_RESTORE = _s.StorageManager.restore_verified_backup


def _detect_git_commit() -> str:
    env = (os.environ.get("GITHUB_SHA") or os.environ.get("GIT_COMMIT") or "").strip()
    if env:
        return env
    repo = Path(__file__).resolve().parents[1]
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, timeout=3,
        ).strip()
    except Exception:
        return "unknown"


def _manifest_hash(manifest: dict) -> str:
    payload = {k: v for k, v in manifest.items() if k != "manifest_payload_sha256"}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_identity(database_path: Path) -> tuple[int, str]:
    conn = sqlite3.connect(str(database_path), timeout=30)
    try:
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        rows = conn.execute(
            "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
        ).fetchall()
        encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return user_version, hashlib.sha256(encoded).hexdigest()
    finally:
        conn.close()


def init_with_identity(self, settings, *, git_commit: str | None = None):
    _ORIGINAL_INIT(self, settings, git_commit=git_commit or _detect_git_commit())


def apply_retention_exact(self, kind: str) -> None:
    """24h dense + 14 daily + 8 weekly + 12 monthly recovery points."""
    directory = self._backup_dir(kind)
    manifests = self._verified_manifests(directory)
    if not manifests:
        return
    # Local cadence is 15m => 96 snapshots / 24h. Off-host cadence is 1h => 24.
    dense_n = 96 if kind == "local" else 24
    keep: set[str] = {str(m.get("backup_id")) for m in manifests[:dense_n]}
    now = time.time()
    daily: set[str] = set()
    weekly: set[str] = set()
    monthly: set[str] = set()

    for manifest in manifests[dense_n:]:
        ts = float(manifest.get("created_ts") or 0.0)
        if ts <= 0:
            continue
        age_days = max(0.0, (now - ts) / 86400.0)
        bid = str(manifest.get("backup_id"))
        tm = time.gmtime(ts)
        if age_days <= 14.0:
            key = time.strftime("%Y-%m-%d", tm)
            if key not in daily and len(daily) < 14:
                daily.add(key)
                keep.add(bid)
        elif age_days <= 70.0:
            key = time.strftime("%Y-W%W", tm)
            if key not in weekly and len(weekly) < 8:
                weekly.add(key)
                keep.add(bid)
        elif age_days <= 366.0:
            key = time.strftime("%Y-%m", tm)
            if key not in monthly and len(monthly) < 12:
                monthly.add(key)
                keep.add(bid)

    for manifest in manifests:
        bid = str(manifest.get("backup_id"))
        if bid in keep:
            continue
        manifest_path = Path(str(manifest.get("manifest_path") or ""))
        db_name = manifest.get("database_file")
        if db_name:
            with contextlib.suppress(FileNotFoundError):
                (directory / str(db_name)).unlink()
        if str(manifest_path):
            with contextlib.suppress(FileNotFoundError):
                manifest_path.unlink()


def create_backup_honest_encryption(self, *, kind: str = "local", reason: str = "scheduled"):
    result = _ORIGINAL_CREATE(self, kind=kind, reason=reason)
    manifest_path = Path(result.manifest_path)
    database_path = Path(result.database_path)
    manifest = _s._read_json(manifest_path) or {}

    previous = [
        item for item in self._verified_manifests(self._backup_dir(kind))
        if str(item.get("backup_id")) != result.backup_id
    ]
    user_version, schema_sha = _schema_identity(database_path)
    manifest["previous_backup_id"] = previous[0].get("backup_id") if previous else None
    manifest["sqlite_user_version"] = user_version
    manifest["schema_sha256"] = schema_sha

    if kind == "offhost":
        encrypted = os.environ.get(
            "SEILTANZER_OFFHOST_ENCRYPTION_VERIFIED", ""
        ).lower() in {"1", "true", "yes"}
        manifest["encryption_status"] = (
            "verified_external_target" if encrypted else "external_target_not_verified"
        )
        manifest["encryption_verified"] = encrypted
    else:
        manifest["encryption_status"] = "local_filesystem_permissions_only"
        manifest["encryption_verified"] = False
    manifest["storage_refinement_version"] = REFINEMENT_VERSION
    manifest["manifest_payload_sha256"] = _manifest_hash(manifest)
    _s._atomic_json(manifest_path, manifest)
    return result


def restore_verified_backup_refined(*, backup_db, manifest_path, destination_db,
                                    preserve_existing=True):
    manifest_path = Path(manifest_path)
    manifest = _s._read_json(manifest_path)
    if not manifest:
        raise ValueError("backup manifest is missing")
    expected_manifest_sha = str(manifest.get("manifest_payload_sha256") or "")
    if not expected_manifest_sha or _manifest_hash(manifest) != expected_manifest_sha:
        raise ValueError("backup manifest SHA256 mismatch")
    result = _ORIGINAL_RESTORE(
        backup_db=backup_db,
        manifest_path=manifest_path,
        destination_db=destination_db,
        preserve_existing=preserve_existing,
    )
    user_version, schema_sha = _schema_identity(Path(destination_db))
    if user_version != int(manifest.get("sqlite_user_version") or 0):
        raise ValueError("restored SQLite user_version mismatch")
    if schema_sha != str(manifest.get("schema_sha256") or ""):
        raise ValueError("restored schema SHA256 mismatch")
    result["manifest_payload_sha256"] = expected_manifest_sha
    result["schema_sha256"] = schema_sha
    result["previous_backup_id"] = manifest.get("previous_backup_id")
    result["storage_refinement_version"] = REFINEMENT_VERSION
    return result


def reconcile_only_tracked_positions(self, engine) -> list[dict]:
    """Repair a close crash gap without inventing ledgers for historical trades.

    `Journal.add_closed()` is legitimate historical/backfill data and was never an
    actively managed PositionLedger state. Only a trade that already had a
    position event before the crash is eligible for cross-ledger reconciliation.
    """
    actions: list[dict] = []
    for trade in engine.journal.list_trades():
        trade_id = int(trade["id"])
        try:
            with engine.position._lock:
                tracked = engine.position._conn.execute(
                    "SELECT 1 FROM position_management_events WHERE trade_id=? LIMIT 1",
                    (trade_id,),
                ).fetchone()
            if tracked is None or trade.get("status") != "closed":
                continue
            state = engine.position.state(trade)
            remaining = float(state.get("remaining_position_fraction") or 0.0)
            if remaining <= 1e-12:
                continue
            engine.position.terminal_exit(
                trade,
                event_type="MANUAL_EXIT",
                execution_price=None,
                execution_r=trade.get("result_r"),
            )
            actions.append({
                "trade_id": trade_id,
                "action": "RECOVER_CLOSED_POSITION_REMAINDER",
                "remaining_before": remaining,
                "result_r": trade.get("result_r"),
                "recovery_contract_version": _s.RECOVERY_CONTRACT_VERSION,
                "preexisting_position_ledger": True,
            })
        except Exception as exc:
            actions.append({
                "trade_id": trade_id,
                "action": "RECOVERY_ERROR",
                "error": str(exc),
                "recovery_contract_version": _s.RECOVERY_CONTRACT_VERSION,
            })
    self._recovery_actions = actions
    return actions


def install_storage_refinement() -> None:
    if getattr(_s.StorageManager, "_storage_refinement_version", None) == REFINEMENT_VERSION:
        return
    _s.CRITICAL_TABLES = CRITICAL_TABLES
    _s.StorageManager.__init__ = init_with_identity
    _s.StorageManager._apply_retention = apply_retention_exact
    _s.StorageManager.create_backup = create_backup_honest_encryption
    _s.StorageManager.restore_verified_backup = staticmethod(restore_verified_backup_refined)
    _s.StorageManager.reconcile_economic_state = reconcile_only_tracked_positions
    _s.StorageManager._storage_refinement_version = REFINEMENT_VERSION
