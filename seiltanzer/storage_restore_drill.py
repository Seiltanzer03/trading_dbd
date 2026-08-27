"""Safe, bounded production restore drill for verified Seiltanzer backups.

The live database is never a restore destination.  A drill selects the newest
verified backup that is schema-complete for the critical tables that actually
belong to the current live SQLite schema and restores it into a disposable file.

Backup creation already performs a full SQLite integrity check before publishing
a verified manifest.  The drill therefore proves that the *same verified bytes*
are still readable and can be restored byte-for-byte: it validates the manifest,
streams the backup into a disposable destination while hashing the source, hashes
the restored destination, verifies the schema identity and confirms every
required table is present.  Re-running full ``PRAGMA integrity_check`` on both
source and destination would scan the same large database multiple additional
times and made production acceptance exceed 90 seconds on the small VPS without
adding independent evidence.

The production database is several times larger than host RAM.  Sequentially
restoring and re-reading a multi-GB snapshot must therefore also avoid turning
Linux page cache into an implicit second copy of the database.  The drill keeps
dirty writeback bounded and drops completed sequential ranges from page cache
where POSIX fadvise is available.  This changes resource behaviour only; the
byte-identical restore and independent destination hash remain mandatory.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from . import storage_runtime as _storage
from .storage_disk_guard import reserve_restore_drill_headroom
from .storage_runtime import (
    BACKUP_CONTRACT_VERSION,
    StorageManager,
    _atomic_json,
    _read_json,
)


RESTORE_DRILL_CONTRACT_VERSION = "seiltanzer-restore-drill-v4-byte-identical-headroom"
RESTORE_DRILL_STATE_FILENAME = ".restore_drill_state.json"
COPY_CHUNK_BYTES = 4 * 1024 * 1024
WRITEBACK_WINDOW_BYTES = 64 * 1024 * 1024


def _cleanup_sqlite(path: Path) -> None:
    for candidate in (path, Path(str(path)+"-wal"), Path(str(path)+"-shm")):
        with contextlib.suppress(FileNotFoundError):
            candidate.unlink()


def _manifest_payload_sha256(manifest: dict[str, Any]) -> str:
    """Same canonical payload identity used by ``storage_refinement``."""
    payload = {key: value for key, value in manifest.items()
               if key != "manifest_payload_sha256"}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_identity(database_path: Path) -> tuple[int, str, set[str]]:
    """Read only schema pages; do not perform another whole-database scan."""
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=10)
    try:
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        rows = conn.execute(
            "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
        ).fetchall()
        names = {str(row[1]) for row in rows if str(row[0]) == "table"}
        encoded = json.dumps(
            rows, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return user_version, hashlib.sha256(encoded).hexdigest(), names
    finally:
        conn.close()


def _advise_sequential(fh: Any) -> bool:
    fadvise = getattr(os, "posix_fadvise", None)
    advice = getattr(os, "POSIX_FADV_SEQUENTIAL", None)
    if fadvise is None or advice is None:
        return False
    try:
        fadvise(fh.fileno(), 0, 0, advice)
        return True
    except OSError:
        return False


def _drop_file_cache(fh: Any, offset: int, length: int) -> bool:
    """Release an already-consumed clean range from the kernel page cache."""
    if length <= 0:
        return False
    fadvise = getattr(os, "posix_fadvise", None)
    advice = getattr(os, "POSIX_FADV_DONTNEED", None)
    if fadvise is None or advice is None:
        return False
    try:
        fadvise(fh.fileno(), max(0, int(offset)), int(length), advice)
        return True
    except OSError:
        return False


def _sync_data(fh: Any) -> None:
    fdatasync = getattr(os, "fdatasync", None)
    if fdatasync is not None:
        fdatasync(fh.fileno())
    else:
        os.fsync(fh.fileno())


def _sha256_streaming_no_cache(path: Path) -> tuple[str, bool]:
    """Hash restored bytes and report whether every cache eviction succeeded."""
    digest = hashlib.sha256()
    offset = 0
    cache_eviction_verified = True
    with path.open("rb") as fh:
        _advise_sequential(fh)
        while True:
            chunk = fh.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            cache_eviction_verified = (
                _drop_file_cache(fh, offset, len(chunk))
                and cache_eviction_verified
            )
            offset += len(chunk)
    return digest.hexdigest(), cache_eviction_verified


def _copy_source_with_sha256(
    source: Path, destination: Path,
) -> tuple[str, int, bool]:
    """Perform the actual restore copy while hashing exactly the bytes read.

    Dirty destination pages are synced in a bounded window instead of allowing a
    multi-GB disposable restore to accumulate in page cache until one final
    fsync.  Completed source/destination ranges are marked DONTNEED when Linux
    supports POSIX fadvise so the live service keeps its own hot working set.
    """
    digest = hashlib.sha256()
    copied = 0
    writeback_start = 0
    pending_writeback = 0
    cache_eviction_verified = True
    with source.open("rb") as src, destination.open("xb") as dst:
        _advise_sequential(src)
        _advise_sequential(dst)
        while True:
            chunk = src.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            source_offset = copied
            digest.update(chunk)
            dst.write(chunk)
            copied += len(chunk)
            pending_writeback += len(chunk)
            cache_eviction_verified = (
                _drop_file_cache(src, source_offset, len(chunk))
                and cache_eviction_verified
            )

            if pending_writeback >= WRITEBACK_WINDOW_BYTES:
                dst.flush()
                _sync_data(dst)
                cache_eviction_verified = (
                    _drop_file_cache(dst, writeback_start, pending_writeback)
                    and cache_eviction_verified
                )
                writeback_start = copied
                pending_writeback = 0

        dst.flush()
        os.fsync(dst.fileno())
        if pending_writeback:
            cache_eviction_verified = (
                _drop_file_cache(dst, writeback_start, pending_writeback)
                and cache_eviction_verified
            )
    return digest.hexdigest(), copied, cache_eviction_verified


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


def _restore_verified_bytes_for_drill(
    *, backup_db: Path, manifest_path: Path, destination: Path,
    required_tables: tuple[str, ...],
) -> dict[str, Any]:
    """Restore one immutable verified backup with bounded independent checks.

    This is intentionally a drill-specific path.  The general disaster-recovery
    method keeps its conservative full-integrity behaviour; production readiness
    does not need to repeat those whole-file checks twice on the same freshly
    verified pre-start snapshot.
    """
    manifest = _read_json(manifest_path)
    if not manifest or manifest.get("verified") is not True:
        raise ValueError("backup manifest is missing or unverified")
    if manifest.get("backup_contract_version") != BACKUP_CONTRACT_VERSION:
        raise ValueError("backup contract mismatch")
    if str(manifest.get("sqlite_integrity") or "").lower() != "ok":
        raise ValueError("backup was not published from a successful full integrity check")

    expected_manifest_sha = str(manifest.get("manifest_payload_sha256") or "")
    if expected_manifest_sha:
        actual_manifest_sha = _manifest_payload_sha256(manifest)
        if actual_manifest_sha != expected_manifest_sha:
            raise ValueError("backup manifest SHA256 mismatch")

    expected_sha = str(manifest.get("database_sha256") or "")
    if not expected_sha:
        raise ValueError("backup SHA256 missing")
    if not backup_db.is_file():
        raise FileNotFoundError(str(backup_db))

    expected_size = manifest.get("database_size_bytes")
    source_sha, copied_bytes, copy_cache_eviction_verified = (
        _copy_source_with_sha256(backup_db, destination)
    )
    if source_sha != expected_sha:
        raise ValueError("backup SHA256 mismatch")
    if expected_size is not None and copied_bytes != int(expected_size):
        raise ValueError("restored byte count mismatch")

    # A separate read of the restored file proves the filesystem destination is
    # byte-identical to the manifest-approved source, not merely that the source
    # stream had the expected digest.  The scan drops consumed cache ranges so a
    # disposable multi-GB file cannot displace the live service working set.
    restored_sha, hash_cache_eviction_verified = _sha256_streaming_no_cache(
        destination
    )
    if restored_sha != expected_sha:
        raise RuntimeError("restored database SHA256 mismatch")

    user_version, schema_sha, restored_tables = _schema_identity(destination)
    manifest_user_version = manifest.get("sqlite_user_version")
    if manifest_user_version is not None and user_version != int(manifest_user_version):
        raise ValueError("restored SQLite user_version mismatch")
    expected_schema_sha = str(manifest.get("schema_sha256") or "")
    if expected_schema_sha and schema_sha != expected_schema_sha:
        raise ValueError("restored schema SHA256 mismatch")

    missing = [table for table in required_tables if table not in restored_tables]
    if missing:
        raise RuntimeError(f"restored critical tables missing: {missing}")

    # Exact file identity means all table contents/counts equal the verified
    # backup manifest by construction.  Re-counting every large table would add
    # no independent information and was the main unbounded acceptance cost.
    expected_counts = manifest.get("critical_table_counts") or {}
    if any(expected_counts.get(table) is None for table in required_tables):
        raise RuntimeError("verified backup lacks required critical-table counts")

    return {
        "ok": True,
        "manifest_payload_sha256": expected_manifest_sha or None,
        "schema_sha256": schema_sha,
        "sqlite_user_version": user_version,
        "restored_sha256": restored_sha,
        "copied_bytes": copied_bytes,
        "verification_method": (
            "MANIFEST_FULL_INTEGRITY_PROVENANCE+SOURCE_SHA256+"
            "BYTE_IDENTICAL_RESTORE_SHA256+SCHEMA_IDENTITY+TABLE_PRESENCE+"
            "BOUNDED_WRITEBACK"
        ),
        "source_full_integrity_verified_at_backup_creation": True,
        "repeat_full_integrity_scan_during_drill": False,
        "critical_table_counts_inherited_by_byte_identity": True,
        "page_cache_pressure_bounded": (
            copy_cache_eviction_verified and hash_cache_eviction_verified
        ),
        "writeback_window_bytes": WRITEBACK_WINDOW_BYTES,
        "posix_fadvise_available": bool(
            getattr(os, "posix_fadvise", None)
            and getattr(os, "POSIX_FADV_DONTNEED", None) is not None
        ),
    }


def run_restore_drill(manager: StorageManager) -> dict[str, Any]:
    """Restore newest schema-complete verified snapshot to a disposable DB.

    With normal headroom the manager-wide backup lock is not held.  Under low
    headroom it is acquired non-blocking and held only across the disposable
    copy, preventing a concurrent replacement backup from consuming the reserved
    space.  Readiness therefore never waits behind scheduled backup I/O.
    """
    started = time.time()
    state_path = Path(manager.data_dir)/RESTORE_DRILL_STATE_FILENAME
    destination: Path | None = None
    report: dict[str, Any]
    try:
        required = _live_required_tables(manager)
        latest = schema_complete_verified_backup(manager, required_tables=required)
        if latest is None:
            raise RuntimeError("no verified local backup is schema-complete for current live database")
        manifest_path = Path(str(latest.get("manifest_path") or ""))
        database_file = str(latest.get("database_file") or "")
        if not str(manifest_path) or not database_file:
            raise RuntimeError("verified backup metadata is incomplete")
        backup_db = manifest_path.parent/database_file
        backup_bytes = int(
            latest.get("database_size_bytes") or backup_db.stat().st_size
        )
        with reserve_restore_drill_headroom(
            manager,
            required_bytes=backup_bytes,
            protected_backup_id=str(latest.get("backup_id") or ""),
        ) as headroom:
            fd, temp_name = tempfile.mkstemp(
                prefix="seiltanzer-service-restore-drill-", suffix=".sqlite3")
            os.close(fd)
            Path(temp_name).unlink(missing_ok=True)
            destination = Path(temp_name)

            result = _restore_verified_bytes_for_drill(
                backup_db=backup_db,
                manifest_path=manifest_path,
                destination=destination,
                required_tables=required,
            )
            if result.get("ok") is not True:
                raise RuntimeError("restore drill contract did not return ok=true")

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
            "critical_table_counts_inherited_by_byte_identity": True,
            "schema_complete_current_contract": True,
            "verification_method": result.get("verification_method"),
            "source_full_integrity_verified_at_backup_creation": True,
            "repeat_full_integrity_scan_during_drill": False,
            "request_waits_for_backup_manager_lock": False,
            "restored_sha256": result.get("restored_sha256"),
            "copied_bytes": result.get("copied_bytes"),
            "page_cache_pressure_bounded": result.get("page_cache_pressure_bounded"),
            "writeback_window_bytes": result.get("writeback_window_bytes"),
            "posix_fadvise_available": result.get("posix_fadvise_available"),
            "headroom_reservation": headroom,
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
            "request_waits_for_backup_manager_lock": False,
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
    value = _read_json(Path(manager.data_dir)/RESTORE_DRILL_STATE_FILENAME)
    return value if isinstance(value, dict) else None
