"""Safe production restore drill for verified Seiltanzer backups.

The application service already owns the protected backup files.  Running the
verification inside that boundary proves recoverability without weakening file
permissions or granting the CI runner arbitrary sudo access.  The live database
is never used as the restore destination.
"""
from __future__ import annotations

import contextlib
import tempfile
import time
from pathlib import Path
from typing import Any

from .storage_runtime import StorageManager, _atomic_json, _table_counts


RESTORE_DRILL_CONTRACT_VERSION = "seiltanzer-restore-drill-v1"
RESTORE_DRILL_STATE_FILENAME = ".restore_drill_state.json"


def _cleanup_sqlite(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        with contextlib.suppress(FileNotFoundError):
            candidate.unlink()


def run_restore_drill(manager: StorageManager) -> dict[str, Any]:
    """Restore the newest verified local snapshot to a disposable database.

    This deliberately serialises with backup creation/retention so a selected
    snapshot cannot be removed while the drill reads it.  It never stops the
    service, never replaces ``trades.db`` and never mutates the backup itself.
    """
    started = time.time()
    state_path = Path(manager.data_dir) / RESTORE_DRILL_STATE_FILENAME
    destination: Path | None = None
    report: dict[str, Any]

    try:
        with manager._lock:
            latest = manager._last_verified("local")
            if latest is None:
                raise RuntimeError("no verified local backup exists")

            manifest_path = Path(str(latest.get("manifest_path") or ""))
            database_file = str(latest.get("database_file") or "")
            if not manifest_path or not database_file:
                raise RuntimeError("verified backup metadata is incomplete")
            backup_db = manifest_path.parent / database_file

            fd, temp_name = tempfile.mkstemp(
                prefix="seiltanzer-service-restore-drill-", suffix=".sqlite3"
            )
            Path(temp_name).unlink(missing_ok=True)
            # mkstemp reserved a unique path; close its descriptor before SQLite
            # creates the restored file itself.
            import os
            os.close(fd)
            destination = Path(temp_name)

            result = StorageManager.restore_verified_backup(
                backup_db=backup_db,
                manifest_path=manifest_path,
                destination_db=destination,
                preserve_existing=False,
            )
            if result.get("ok") is not True:
                raise RuntimeError("restore contract did not return ok=true")

            restored = _table_counts(destination)
            expected = latest.get("critical_table_counts") or {}
            mismatches: dict[str, dict[str, int | None]] = {}
            for table, expected_count in expected.items():
                if expected_count is None:
                    continue
                actual_count = restored.get(table)
                if actual_count != expected_count:
                    mismatches[str(table)] = {
                        "expected": expected_count,
                        "actual": actual_count,
                    }
            if mismatches:
                raise RuntimeError(f"restored critical-table mismatch: {mismatches}")

            report = {
                "ok": True,
                "restore_drill_contract_version": RESTORE_DRILL_CONTRACT_VERSION,
                "backup_id": latest.get("backup_id"),
                "backup_created_ts": latest.get("created_ts"),
                "backup_sha256": latest.get("database_sha256"),
                "manifest_payload_sha256": result.get("manifest_payload_sha256"),
                "schema_sha256": result.get("schema_sha256"),
                "critical_tables_verified_n": sum(
                    1 for value in expected.values() if value is not None
                ),
                "critical_table_mismatches": {},
                "live_database_replaced": False,
                "drill_destination_kind": "disposable_tempfile",
                "completed_ts": time.time(),
                "duration_sec": max(0.0, time.time() - started),
            }
    except Exception as exc:
        report = {
            "ok": False,
            "restore_drill_contract_version": RESTORE_DRILL_CONTRACT_VERSION,
            "error": str(exc),
            "live_database_replaced": False,
            "completed_ts": time.time(),
            "duration_sec": max(0.0, time.time() - started),
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
    value = _read_json(Path(manager.data_dir) / RESTORE_DRILL_STATE_FILENAME)
    return value if isinstance(value, dict) else None
