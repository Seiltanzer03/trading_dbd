"""Safe production restore drill for verified Seiltanzer backups.

The live database is never a restore destination.  A drill selects the newest
verified backup that is schema-complete for the critical tables that actually
belong to the current live SQLite schema.  Production readiness separately
requires the full expected G.1S schema, so this compatibility rule cannot hide
a failed production migration.
"""
from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from . import storage_runtime as _storage
from .storage_runtime import StorageManager, _atomic_json, _table_counts


RESTORE_DRILL_CONTRACT_VERSION = "seiltanzer-restore-drill-v2"
RESTORE_DRILL_STATE_FILENAME = ".restore_drill_state.json"


def _cleanup_sqlite(path: Path) -> None:
    for candidate in (path, Path(str(path)+"-wal"), Path(str(path)+"-shm")):
        with contextlib.suppress(FileNotFoundError):
            candidate.unlink()


def _live_required_tables(manager: StorageManager) -> tuple[str, ...]:
    """Critical contract intersected with the live DB's current schema.

    Unit tests intentionally exercise the durability layer with a minimal
    `trades`-only SQLite database.  A restore drill must still work there.
    Production readiness independently asserts that every expected G.1S table
    exists in a fresh manifest, so absent production schema cannot pass the gate.
    """
    all_critical = tuple(dict.fromkeys(_storage.CRITICAL_TABLES))
    db_path = getattr(manager, "db_path", None)
    if db_path is None:
        return all_critical
    path = Path(db_path)
    if not path.exists():
        return all_critical
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            names = {str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn.close()
    except sqlite3.Error:
        return all_critical
    required = tuple(table for table in all_critical if table in names)
    return required or all_critical


def schema_complete_verified_backup(
    manager: StorageManager, *, required_tables: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Newest verified local backup containing every required current table."""
    required = tuple(required_tables or _live_required_tables(manager))
    for manifest in manager._verified_manifests(manager.local_dir):
        counts = manifest.get("critical_table_counts") or {}
        if all(table in counts and counts.get(table) is not None for table in required):
            return manifest
    return None


def run_restore_drill(manager: StorageManager) -> dict[str, Any]:
    """Restore newest schema-complete verified snapshot to a disposable DB."""
    started = time.time()
    state_path = Path(manager.data_dir)/RESTORE_DRILL_STATE_FILENAME
    destination: Path | None = None
    report: dict[str, Any]
    try:
        with manager._lock:
            required = _live_required_tables(manager)
            latest = schema_complete_verified_backup(manager, required_tables=required)
            if latest is None:
                raise RuntimeError("no verified local backup is schema-complete for current live database")
            manifest_path = Path(str(latest.get("manifest_path") or ""))
            database_file = str(latest.get("database_file") or "")
            if not manifest_path or not database_file:
                raise RuntimeError("verified backup metadata is incomplete")
            backup_db = manifest_path.parent/database_file

            fd, temp_name = tempfile.mkstemp(
                prefix="seiltanzer-service-restore-drill-", suffix=".sqlite3")
            Path(temp_name).unlink(missing_ok=True)
            import os
            os.close(fd)
            destination = Path(temp_name)

            result = StorageManager.restore_verified_backup(
                backup_db=backup_db, manifest_path=manifest_path,
                destination_db=destination, preserve_existing=False)
            if result.get("ok") is not True:
                raise RuntimeError("restore contract did not return ok=true")

            restored = _table_counts(destination)
            expected = latest.get("critical_table_counts") or {}
            mismatches: dict[str, dict[str, int | None]] = {}
            for table in required:
                expected_count = expected.get(table)
                actual_count = restored.get(table)
                if expected_count is None or actual_count != expected_count:
                    mismatches[str(table)] = {"expected": expected_count, "actual": actual_count}
            if mismatches:
                raise RuntimeError(f"restored critical-table mismatch: {mismatches}")

            report = {
                "ok": True,
                "restore_drill_contract_version": RESTORE_DRILL_CONTRACT_VERSION,
                "backup_id": latest.get("backup_id"),
                "backup_created_ts": latest.get("created_ts"),
                "backup_reason": latest.get("reason"),
                "backup_sha256": latest.get("database_sha256"),
                "manifest_payload_sha256": result.get("manifest_payload_sha256"),
                "schema_sha256": result.get("schema_sha256"),
                "critical_tables_verified_n": len(required),
                "critical_tables_verified": list(required),
                "critical_table_mismatches": {},
                "schema_complete_current_contract": True,
                "live_database_replaced": False,
                "drill_destination_kind": "disposable_tempfile",
                "completed_ts": time.time(),
                "duration_sec": max(0.0, time.time()-started),
            }
    except Exception as exc:
        report = {
            "ok": False,
            "restore_drill_contract_version": RESTORE_DRILL_CONTRACT_VERSION,
            "error": str(exc),
            "live_database_replaced": False,
            "completed_ts": time.time(),
            "duration_sec": max(0.0, time.time()-started),
        }
        _atomic_json(state_path, report)
        manager._last_restore_drill = report
        raise
    finally:
        if destination is not None:
            _cleanup_sqlite(destination)
    _atomic_json(state_path, report)
    manager._last_restore_drill = report
    return report


def last_restore_drill(manager: StorageManager) -> dict[str, Any] | None:
    cached = getattr(manager, "_last_restore_drill", None)
    if isinstance(cached, dict):
        return dict(cached)
    from .storage_runtime import _read_json
    value = _read_json(Path(manager.data_dir)/RESTORE_DRILL_STATE_FILENAME)
    return value if isinstance(value, dict) else None
