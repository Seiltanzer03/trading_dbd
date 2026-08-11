"""Fund-grade refinements for the G.1E-0 storage contract."""
from __future__ import annotations

import contextlib
import os
import subprocess
import time
from pathlib import Path

from . import storage_runtime as _s


REFINEMENT_VERSION = "seiltanzer-storage-refinement-v2"

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
    manifest = _s._read_json(manifest_path) or {}
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
    _s._atomic_json(manifest_path, manifest)
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
            if tracked is None:
                continue
            if trade.get("status") != "closed":
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
    _s.StorageManager.reconcile_economic_state = reconcile_only_tracked_positions
    _s.StorageManager._storage_refinement_version = REFINEMENT_VERSION
