"""Sparse-aware fail-closed SQLite backup fallback for constrained production disk.

The canonical storage guard budgets a raw backup by logical SQLite file size.  A
verified SQLite snapshot may, however, be a sparse file: ``st_size`` can be many
GiB while the filesystem allocation is only a small fraction of that.  Production
proved this distinction when unlinking a logical ~6 GiB recovery point released
only ~69 MiB.

This refinement never predicts success from logical size and never deletes an
existing recovery point.  When the normal guard refuses a local backup, it retries
the same SQLite online-backup algorithm into a hidden temp while checking *actual*
filesystem free space on every SQLite progress callback.  The attempt aborts and
removes its temp before the protected 1 GiB operating reserve can be consumed.
Only a fully integrity/SHA/schema-verified snapshot is promoted.
"""
from __future__ import annotations

import contextlib
import errno
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import storage_disk_guard as _guard
from . import storage_runtime as _s


SPARSE_GUARD_VERSION = "storage-sparse-backup-guard-v1"
SPARSE_GUARD_REASON = "LOW_DISK_SPARSE_AWARE_SQLITE_BACKUP"
PROGRESS_MARGIN_BYTES = 64 * 1024 * 1024
_ELIGIBLE_REASONS = {
    "prestart",
    "scheduled",
    "clean_shutdown",
    "g1m-schema-identity",
    "g1s-schema-identity",
}
_GUARD_ENOSPC_FRAGMENT = "insufficient single-slot backup headroom"


def _available_bytes(directory: Path) -> int:
    stat = os.statvfs(directory)
    return max(0, int(stat.f_bavail) * int(stat.f_frsize))


def _allocated_bytes(path: Path) -> int:
    """Return filesystem blocks consumed, not the sparse file's logical size."""
    stat = path.stat()
    blocks = getattr(stat, "st_blocks", None)
    if blocks is None:
        return max(1, int(stat.st_size))
    return max(0, int(blocks) * 512)


def _create_sparse_guarded_backup(self: Any, *, kind: str, reason: str):
    directory = self._backup_dir(kind)
    directory.mkdir(parents=True, exist_ok=True)
    protected = _guard.MIN_BACKUP_HEADROOM_BYTES
    abort_floor = protected + PROGRESS_MARGIN_BYTES
    before_free = _available_bytes(directory)
    if before_free < abort_floor:
        raise OSError(
            errno.ENOSPC,
            "sparse-aware backup cannot start while preserving protected headroom: "
            f"available={before_free} required={abort_floor}",
        )

    created = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created))
    backup_id = f"{stamp}-{kind}-{int(created * 1000) % 1000000:06d}"
    final_db = directory / f"{backup_id}.sqlite3"
    manifest_path = directory / f"{backup_id}.manifest.json"
    temp_db = directory / f".{backup_id}.tmp.sqlite3"
    with contextlib.suppress(FileNotFoundError):
        temp_db.unlink()

    lowest_free = before_free

    def progress(_status: int, _remaining: int, _total: int) -> None:
        nonlocal lowest_free
        free = _available_bytes(directory)
        lowest_free = min(lowest_free, free)
        if free < abort_floor:
            raise OSError(
                errno.ENOSPC,
                "sparse-aware SQLite backup stopped before protected headroom: "
                f"available={free} abort_floor={abort_floor}",
            )

    src = None
    dst = None
    try:
        src = sqlite3.connect(
            self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30
        )
        dst = sqlite3.connect(str(temp_db), timeout=30)
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dst, pages=256, progress=progress, sleep=0.01)
        dst.commit()
    except Exception:
        if dst is not None:
            with contextlib.suppress(Exception):
                dst.close()
            dst = None
        if src is not None:
            with contextlib.suppress(Exception):
                src.close()
            src = None
        with contextlib.suppress(FileNotFoundError):
            temp_db.unlink()
        raise
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()

    if not temp_db.is_file():
        raise RuntimeError("sparse-aware SQLite backup did not create its destination")

    after_copy_free = _available_bytes(directory)
    if after_copy_free < protected:
        with contextlib.suppress(FileNotFoundError):
            temp_db.unlink()
        raise OSError(
            errno.ENOSPC,
            "sparse-aware backup consumed protected headroom: "
            f"available={after_copy_free} required={protected}",
        )

    startup_source = self.db_path if reason == "prestart" else None
    try:
        ok, integrity_detail, sha, counts, source_check = _s._verify_backup_snapshot(
            temp_db, startup_source=startup_source
        )
        if not ok:
            raise RuntimeError(f"sparse-aware backup integrity failed: {integrity_detail}")
        if source_check is not None and not source_check[0]:
            raise RuntimeError(f"source startup integrity failed: {source_check[1]}")

        logical_size = int(temp_db.stat().st_size)
        allocated_size = _allocated_bytes(temp_db)
        os.replace(temp_db, final_db)
        manifest = {
            "backup_contract_version": _s.BACKUP_CONTRACT_VERSION,
            "retention_contract_version": _s.RETENTION_CONTRACT_VERSION,
            "backup_id": backup_id,
            "kind": kind,
            "reason": reason,
            "created_ts": created,
            "source_db": str(self.db_path),
            "database_file": final_db.name,
            "database_sha256": sha,
            "database_size_bytes": logical_size,
            "filesystem_allocated_bytes": allocated_size,
            "sqlite_integrity": integrity_detail,
            "critical_table_counts": counts,
            "git_commit": self.git_commit,
            "verified": True,
            "encryption_status": (
                "external_target_managed" if kind == "offhost" else "filesystem_permissions"
            ),
            "snapshot_mode": "sqlite_backup_sparse_guarded",
            "disk_guard_version": _guard.DISK_GUARD_VERSION,
            "sparse_guard_version": SPARSE_GUARD_VERSION,
            "free_before_snapshot_bytes": before_free,
            "free_after_snapshot_bytes": after_copy_free,
            "lowest_progress_free_bytes": lowest_free,
        }
        _s._atomic_json(manifest_path, manifest)
        result = _s.BackupResult(
            backup_id=backup_id,
            kind=kind,
            database_path=str(final_db),
            manifest_path=str(manifest_path),
            verified=True,
            created_ts=created,
            sha256=sha,
        )
        # Reuse the existing manifest identity refinement.  Despite its historical
        # name, it applies schema identity/encryption lineage to any BackupResult.
        _guard._refine_compact_manifest(self, result, kind=kind)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temp_db.unlink()
        with contextlib.suppress(FileNotFoundError):
            final_db.unlink()
        with contextlib.suppress(FileNotFoundError):
            manifest_path.unlink()
        raise

    if source_check is not None:
        self._startup_integrity = {
            "checked_ts": time.time(),
            "ok": True,
            "detail": source_check[1],
            "check_kind": "quick_check",
            "verification_scope": "pre_engine_source",
            "contract_version": _s.STORAGE_CONTRACT_VERSION,
            "backup_sparse_guarded": True,
            "reason": SPARSE_GUARD_REASON,
        }
        self._prestart_integrity_ready = True

    self._recovery_actions.append({
        "ts": time.time(),
        "action": "create_sparse_guarded_verified_backup",
        "reason": SPARSE_GUARD_REASON,
        "backup_id": backup_id,
        "logical_size_bytes": logical_size,
        "filesystem_allocated_bytes": allocated_size,
        "free_before_bytes": before_free,
        "free_after_bytes": after_copy_free,
        "lowest_progress_free_bytes": lowest_free,
        "authoritative_db_deleted": False,
    })
    self._apply_retention(kind)
    return result


def install_storage_sparse_backup_guard() -> None:
    """Install after disk guard and before legacy single-slot rotation."""
    manager_cls = _s.StorageManager
    if getattr(manager_cls, "_storage_sparse_backup_guard_version", None) == SPARSE_GUARD_VERSION:
        return

    guarded_create = manager_cls.create_backup

    def create_backup(self, *, kind: str = "local", reason: str = "scheduled"):
        try:
            return guarded_create(self, kind=kind, reason=reason)
        except OSError as original_error:
            eligible = (
                kind == "local"
                and reason in _ELIGIBLE_REASONS
                and original_error.errno == errno.ENOSPC
                and _GUARD_ENOSPC_FRAGMENT in str(original_error)
            )
            if not eligible:
                raise
            # Never sacrifice an existing recovery point to *predict* filesystem
            # reclaim from st_size.  Try the canonical online backup while actual
            # free blocks are continuously guarded instead.
            with self._lock:
                try:
                    return _create_sparse_guarded_backup(
                        self, kind=kind, reason=reason
                    )
                except OSError as sparse_error:
                    # Deliberately replace the disk-guard signature so the older
                    # unlink-first single-slot wrapper cannot consume this failure.
                    if sparse_error.errno == errno.ENOSPC:
                        raise OSError(
                            errno.ENOSPC,
                            "sparse-aware backup could not preserve protected headroom: "
                            f"{sparse_error}",
                        ) from sparse_error
                    raise

    manager_cls.create_backup = create_backup
    manager_cls._storage_sparse_backup_guard_version = SPARSE_GUARD_VERSION
