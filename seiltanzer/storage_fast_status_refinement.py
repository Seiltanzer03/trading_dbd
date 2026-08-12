"""Bounded request-time storage health for G.1E.2.

`PRAGMA quick_check` remains available on the explicit integrity endpoint and in
startup/backup verification.  A routine status request must not compete with the
live research writer for a database-wide integrity scan.
"""
from __future__ import annotations

import time
import types
from pathlib import Path


CONTRACT_VERSION = "storage-bounded-status-v1"


def _bounded_status(storage, *, engine=None):
    now = time.time()
    local = storage._last_verified("local")
    offhost = storage._last_verified("offhost") if storage.offhost_dir is not None else None
    local_age = None if local is None else max(0.0, now-float(local.get("created_ts") or 0))
    offhost_age = None if offhost is None else max(0.0, now-float(offhost.get("created_ts") or 0))
    startup = storage._startup_integrity or {}
    startup_ok = startup.get("ok") is not False
    if not startup_ok:
        health = "INTEGRITY_WARNING"
    elif local is None or (local_age is not None and local_age > storage.local_interval*2):
        health = "BACKUP_STALE"
    elif storage.offhost_dir is None:
        health = "LOCAL_BACKUP_ONLY"
    elif offhost is None or (offhost_age is not None and offhost_age > storage.offhost_interval*2):
        health = "DISASTER_RECOVERY_DEGRADED"
    else:
        health = "HEALTHY"

    wal = Path(str(storage.db_path) + "-wal")
    shm = Path(str(storage.db_path) + "-shm")
    return {
        "storage_contract_version": "seiltanzer-storage-v1",
        "backup_contract_version": "seiltanzer-backup-v1",
        "recovery_contract_version": "seiltanzer-recovery-v1",
        "bounded_status_contract_version": CONTRACT_VERSION,
        "health": health,
        "database_path": str(storage.db_path),
        "database_exists": storage.db_path.exists(),
        "database_size_bytes": storage.db_path.stat().st_size if storage.db_path.exists() else 0,
        "sqlite_integrity": {
            "ok": startup.get("ok"),
            "detail": startup.get("detail"),
            "checked_ts": startup.get("checked_ts"),
            "check_kind": "startup_cached",
            "fresh_check_endpoint": "/api/system/storage/integrity",
        },
        "previous_shutdown": storage.previous_shutdown,
        "startup_integrity": storage._startup_integrity,
        "last_local_backup": local,
        "last_local_backup_age_sec": local_age,
        "last_offhost_backup": offhost,
        "last_offhost_backup_age_sec": offhost_age,
        "offhost_configured": storage.offhost_dir is not None,
        "rpo_target_sec": storage.local_interval,
        "unresolved_q_observations": None,
        "research_health_decoupled": True,
        "recovery_actions": list(storage._recovery_actions[-50:]),
        "last_error": storage._last_error,
        "background_backup_running": storage._background_running,
        "wal": {
            "exists": wal.exists(),
            "size_bytes": wal.stat().st_size if wal.exists() else 0,
            "shm_exists": shm.exists(),
        },
        "ram_authority": False,
        "persistent_db_authority": True,
        "request_time_integrity_scan": False,
    }


def install_storage_fast_status(app) -> None:
    storage = getattr(app.state, "storage", None)
    if storage is None:
        raise RuntimeError("storage runtime must be installed before bounded status")
    if getattr(storage, "_bounded_status_contract", None) == CONTRACT_VERSION:
        return
    storage.status = types.MethodType(_bounded_status, storage)
    storage._bounded_status_contract = CONTRACT_VERSION
