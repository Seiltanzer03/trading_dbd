"""Low-disk verified SQLite backup paths for constrained production storage.

The normal SQLite backup API is preferred.  On production the authoritative
database can be much larger logically than the remaining filesystem budget, so
prestart additionally supports a quiescent file-level sparse clone.  That clone
preserves source holes, copies a stable WAL sidecar, checkpoints only the clone,
and is promoted only after the same integrity/SHA/schema verification as a normal
backup.  The authoritative trades.db is always opened read-only here.
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


SPARSE_GUARD_VERSION = "storage-sparse-backup-guard-v2-quiescent-clone"
SPARSE_GUARD_REASON = "LOW_DISK_SPARSE_AWARE_SQLITE_BACKUP"
QUIESCENT_CLONE_REASON = "LOW_DISK_QUIESCENT_SPARSE_CLONE"
PROGRESS_MARGIN_BYTES = 64 * 1024 * 1024
COPY_CHUNK_BYTES = 8 * 1024 * 1024
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
    """Return filesystem blocks consumed, not a sparse file's logical size."""
    stat = path.stat()
    blocks = getattr(stat, "st_blocks", None)
    if blocks is None:
        return max(1, int(stat.st_size))
    return max(0, int(blocks) * 512)


def _file_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    """Identity + mutation signature; missing sidecars are represented explicitly."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


def _write_all_at(fd: int, data: bytes, offset: int) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        amount = os.pwrite(fd, view[written:], offset + written)
        if amount <= 0:
            raise OSError(errno.EIO, "short pwrite while creating sparse backup")
        written += amount


def _guard_copy_headroom(directory: Path, abort_floor: int) -> int:
    free = _available_bytes(directory)
    if free < abort_floor:
        raise OSError(
            errno.ENOSPC,
            "quiescent sparse clone stopped before protected headroom: "
            f"available={free} abort_floor={abort_floor}",
        )
    return free


def _copy_sparse_file(
    source: Path,
    destination: Path,
    *,
    directory: Path,
    abort_floor: int,
) -> dict[str, int | str]:
    """Copy exact bytes while preserving filesystem holes when the FS exposes them."""
    source = source.resolve()
    destination = destination.resolve()
    source_fd = os.open(source, os.O_RDONLY)
    destination_fd = -1
    lowest_free = _available_bytes(directory)
    method = "seek_hole"
    try:
        source_stat = os.fstat(source_fd)
        logical_size = int(source_stat.st_size)
        destination_fd = os.open(
            destination,
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.ftruncate(destination_fd, logical_size)

        use_seek_hole = all(
            hasattr(os, name) for name in ("SEEK_DATA", "SEEK_HOLE")
        )
        first_data: int | None = None
        if use_seek_hole and logical_size:
            try:
                first_data = os.lseek(source_fd, 0, os.SEEK_DATA)
            except OSError as exc:
                if exc.errno == errno.ENXIO:
                    first_data = None
                elif exc.errno in {
                    errno.EINVAL,
                    getattr(errno, "ENOTSUP", errno.EINVAL),
                    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
                }:
                    use_seek_hole = False
                else:
                    raise

        if use_seek_hole:
            position = first_data
            while position is not None and position < logical_size:
                try:
                    hole = min(
                        logical_size,
                        int(os.lseek(source_fd, position, os.SEEK_HOLE)),
                    )
                except OSError as exc:
                    if exc.errno in {
                        errno.EINVAL,
                        getattr(errno, "ENOTSUP", errno.EINVAL),
                        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
                    }:
                        raise RuntimeError(
                            "filesystem stopped exposing SEEK_HOLE mid-copy"
                        ) from exc
                    raise

                cursor = int(position)
                while cursor < hole:
                    chunk = os.pread(
                        source_fd,
                        min(COPY_CHUNK_BYTES, hole - cursor),
                        cursor,
                    )
                    if not chunk:
                        raise OSError(errno.EIO, "unexpected EOF in sparse data extent")
                    _write_all_at(destination_fd, chunk, cursor)
                    cursor += len(chunk)
                    lowest_free = min(
                        lowest_free,
                        _guard_copy_headroom(directory, abort_floor),
                    )

                if hole >= logical_size:
                    break
                try:
                    position = int(os.lseek(source_fd, hole, os.SEEK_DATA))
                except OSError as exc:
                    if exc.errno == errno.ENXIO:
                        position = None
                    else:
                        raise
        else:
            # Portable fail-safe: pre-size a sparse destination and materialize only
            # chunks containing non-zero bytes.  This may allocate more than
            # SEEK_HOLE but never changes the source and remains headroom-guarded.
            method = "zero_scan"
            cursor = 0
            while cursor < logical_size:
                chunk = os.pread(
                    source_fd,
                    min(COPY_CHUNK_BYTES, logical_size - cursor),
                    cursor,
                )
                if not chunk:
                    raise OSError(errno.EIO, "unexpected EOF in sparse zero scan")
                if chunk.count(0) != len(chunk):
                    _write_all_at(destination_fd, chunk, cursor)
                cursor += len(chunk)
                lowest_free = min(
                    lowest_free,
                    _guard_copy_headroom(directory, abort_floor),
                )

        os.fsync(destination_fd)
    except Exception:
        if destination_fd >= 0:
            with contextlib.suppress(OSError):
                os.close(destination_fd)
            destination_fd = -1
        with contextlib.suppress(FileNotFoundError):
            destination.unlink()
        raise
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)

    return {
        "logical_size_bytes": int(destination.stat().st_size),
        "filesystem_allocated_bytes": _allocated_bytes(destination),
        "lowest_free_bytes": int(lowest_free),
        "copy_method": method,
    }


def _verify_source_quiescent(
    source: Path,
    wal: Path,
    expected_db: tuple[int, int, int, int, int],
    expected_wal: tuple[int, int, int, int, int] | None,
) -> None:
    if _file_signature(source) != expected_db or _file_signature(wal) != expected_wal:
        raise RuntimeError(
            "authoritative database changed during quiescent sparse clone"
        )


def _new_backup_paths(self: Any, kind: str) -> tuple[float, str, Path, Path, Path]:
    directory = self._backup_dir(kind)
    directory.mkdir(parents=True, exist_ok=True)
    created = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created))
    backup_id = f"{stamp}-{kind}-{int(created * 1000) % 1000000:06d}"
    final_db = directory / f"{backup_id}.sqlite3"
    manifest_path = directory / f"{backup_id}.manifest.json"
    temp_db = directory / f".{backup_id}.tmp.sqlite3"
    return created, backup_id, final_db, manifest_path, temp_db


def _finalize_verified_backup(
    self: Any,
    *,
    kind: str,
    reason: str,
    created: float,
    backup_id: str,
    temp_db: Path,
    final_db: Path,
    manifest_path: Path,
    snapshot_mode: str,
    extra_manifest: dict[str, Any],
    startup_reason: str,
):
    startup_source = self.db_path if reason == "prestart" else None
    ok, integrity_detail, sha, counts, source_check = _s._verify_backup_snapshot(
        temp_db,
        startup_source=startup_source,
    )
    if not ok:
        raise RuntimeError(f"backup integrity failed: {integrity_detail}")
    if source_check is not None and not source_check[0]:
        raise RuntimeError(f"source startup integrity failed: {source_check[1]}")

    logical_size = int(temp_db.stat().st_size)
    allocated_size = _allocated_bytes(temp_db)
    remaining = _available_bytes(self._backup_dir(kind))
    if remaining < _guard.MIN_BACKUP_HEADROOM_BYTES:
        raise OSError(
            errno.ENOSPC,
            "verified backup would consume protected headroom: "
            f"available={remaining} required={_guard.MIN_BACKUP_HEADROOM_BYTES}",
        )

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
        "snapshot_mode": snapshot_mode,
        "disk_guard_version": _guard.DISK_GUARD_VERSION,
        "sparse_guard_version": SPARSE_GUARD_VERSION,
        "free_after_snapshot_bytes": remaining,
        **extra_manifest,
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
    _guard._refine_compact_manifest(self, result, kind=kind)

    if source_check is not None:
        self._startup_integrity = {
            "checked_ts": time.time(),
            "ok": True,
            "detail": source_check[1],
            "check_kind": "quick_check",
            "verification_scope": "pre_engine_source",
            "contract_version": _s.STORAGE_CONTRACT_VERSION,
            "backup_sparse_guarded": True,
            "reason": startup_reason,
        }
        self._prestart_integrity_ready = True
    return result


def _create_quiescent_sparse_clone_backup(
    self: Any,
    *,
    kind: str,
    reason: str,
):
    """Clone a stopped-writer SQLite database without materializing source holes."""
    if kind != "local" or reason != "prestart":
        raise ValueError("quiescent sparse clone is prestart/local only")

    directory = self._backup_dir(kind)
    protected = _guard.MIN_BACKUP_HEADROOM_BYTES
    abort_floor = protected + PROGRESS_MARGIN_BYTES
    before_free = _available_bytes(directory)
    if before_free < abort_floor:
        raise OSError(
            errno.ENOSPC,
            "quiescent sparse clone cannot start while preserving headroom: "
            f"available={before_free} required={abort_floor}",
        )

    source = self.db_path.resolve()
    source_wal = Path(str(source) + "-wal")
    source_ok, source_detail = _s._sqlite_integrity(source, full=False)
    if not source_ok:
        raise RuntimeError(
            f"authoritative trades.db quick_check failed before sparse clone: {source_detail}"
        )

    source_signature = _file_signature(source)
    if source_signature is None:
        raise FileNotFoundError(str(source))
    wal_signature = _file_signature(source_wal)
    source_allocated = _allocated_bytes(source)
    wal_allocated = 0 if wal_signature is None else _allocated_bytes(source_wal)
    print(
        "SPARSE_CLONE_SOURCE "
        f"logical={source_signature[2]} allocated={source_allocated} "
        f"wal_logical={0 if wal_signature is None else wal_signature[2]} "
        f"wal_allocated={wal_allocated} free={before_free}",
        flush=True,
    )

    created, backup_id, final_db, manifest_path, temp_db = _new_backup_paths(
        self, kind
    )
    clone_wal = Path(str(temp_db) + "-wal")
    clone_shm = Path(str(temp_db) + "-shm")
    for path in (temp_db, clone_wal, clone_shm):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    main_copy: dict[str, int | str] | None = None
    wal_copy: dict[str, int | str] | None = None
    try:
        main_copy = _copy_sparse_file(
            source,
            temp_db,
            directory=directory,
            abort_floor=abort_floor,
        )
        _verify_source_quiescent(
            source, source_wal, source_signature, wal_signature
        )

        if wal_signature is not None:
            wal_size = int(wal_signature[2])
            current_free = _available_bytes(directory)
            if current_free - wal_size < abort_floor:
                raise OSError(
                    errno.ENOSPC,
                    "WAL clone cannot preserve protected headroom: "
                    f"available={current_free} wal_size={wal_size} "
                    f"abort_floor={abort_floor}",
                )
            wal_copy = _copy_sparse_file(
                source_wal,
                clone_wal,
                directory=directory,
                abort_floor=abort_floor,
            )
            _verify_source_quiescent(
                source, source_wal, source_signature, wal_signature
            )

        # A copied WAL is replayed/checkpointed only against the clone.  This turns
        # the recovery point back into the existing single-file .sqlite3 contract.
        if clone_wal.is_file() and clone_wal.stat().st_size:
            current_free = _available_bytes(directory)
            wal_size = int(clone_wal.stat().st_size)
            if current_free - wal_size < protected:
                raise OSError(
                    errno.ENOSPC,
                    "clone WAL checkpoint could consume protected headroom: "
                    f"available={current_free} wal_size={wal_size} required={protected}",
                )
            clone = sqlite3.connect(str(temp_db), timeout=30)
            try:
                clone.execute("PRAGMA busy_timeout=30000")
                check = str(clone.execute("PRAGMA quick_check").fetchone()[0])
                if check != "ok":
                    raise RuntimeError(
                        f"cloned database quick_check before WAL checkpoint failed: {check}"
                    )
                checkpoint = clone.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is not None and int(checkpoint[0]) != 0:
                    raise RuntimeError(
                        f"clone WAL checkpoint remained busy: {checkpoint}"
                    )
                clone.commit()
            finally:
                clone.close()

        for sidecar in (clone_wal, clone_shm):
            with contextlib.suppress(FileNotFoundError):
                sidecar.unlink()
        _guard_copy_headroom(directory, protected)
        _verify_source_quiescent(
            source, source_wal, source_signature, wal_signature
        )

        result = _finalize_verified_backup(
            self,
            kind=kind,
            reason=reason,
            created=created,
            backup_id=backup_id,
            temp_db=temp_db,
            final_db=final_db,
            manifest_path=manifest_path,
            snapshot_mode="quiescent_sparse_clone_checkpointed",
            extra_manifest={
                "source_logical_size_bytes": int(source_signature[2]),
                "source_filesystem_allocated_bytes": source_allocated,
                "source_wal_present": wal_signature is not None,
                "source_wal_size_bytes": 0 if wal_signature is None else int(wal_signature[2]),
                "main_copy": dict(main_copy or {}),
                "wal_copy": dict(wal_copy or {}),
                "free_before_snapshot_bytes": before_free,
            },
            startup_reason=QUIESCENT_CLONE_REASON,
        )
        _verify_source_quiescent(
            source, source_wal, source_signature, wal_signature
        )
    except Exception:
        for path in (temp_db, clone_wal, clone_shm, final_db, manifest_path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        raise

    self._recovery_actions.append(
        {
            "ts": time.time(),
            "action": "create_quiescent_sparse_clone_verified_backup",
            "reason": QUIESCENT_CLONE_REASON,
            "backup_id": backup_id,
            "source_logical_size_bytes": int(source_signature[2]),
            "source_filesystem_allocated_bytes": source_allocated,
            "backup_logical_size_bytes": int(Path(result.database_path).stat().st_size),
            "backup_filesystem_allocated_bytes": _allocated_bytes(
                Path(result.database_path)
            ),
            "wal_present": wal_signature is not None,
            "authoritative_db_deleted": False,
            "authoritative_db_written": False,
        }
    )
    self._apply_retention(kind)
    return result


def _create_sparse_guarded_backup(self: Any, *, kind: str, reason: str):
    """Canonical SQLite backup with real free-space checks on every step."""
    directory = self._backup_dir(kind)
    protected = _guard.MIN_BACKUP_HEADROOM_BYTES
    abort_floor = protected + PROGRESS_MARGIN_BYTES
    before_free = _available_bytes(directory)
    if before_free < abort_floor:
        raise OSError(
            errno.ENOSPC,
            "sparse-aware backup cannot start while preserving protected headroom: "
            f"available={before_free} required={abort_floor}",
        )

    created, backup_id, final_db, manifest_path, temp_db = _new_backup_paths(
        self, kind
    )
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
            self.db_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=30,
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

    try:
        result = _finalize_verified_backup(
            self,
            kind=kind,
            reason=reason,
            created=created,
            backup_id=backup_id,
            temp_db=temp_db,
            final_db=final_db,
            manifest_path=manifest_path,
            snapshot_mode="sqlite_backup_sparse_guarded",
            extra_manifest={
                "free_before_snapshot_bytes": before_free,
                "lowest_progress_free_bytes": lowest_free,
            },
            startup_reason=SPARSE_GUARD_REASON,
        )
    except Exception:
        for path in (temp_db, final_db, manifest_path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        raise

    self._recovery_actions.append(
        {
            "ts": time.time(),
            "action": "create_sparse_guarded_verified_backup",
            "reason": SPARSE_GUARD_REASON,
            "backup_id": backup_id,
            "logical_size_bytes": int(Path(result.database_path).stat().st_size),
            "filesystem_allocated_bytes": _allocated_bytes(Path(result.database_path)),
            "free_before_bytes": before_free,
            "free_after_bytes": _available_bytes(directory),
            "lowest_progress_free_bytes": lowest_free,
            "authoritative_db_deleted": False,
        }
    )
    self._apply_retention(kind)
    return result


def install_storage_sparse_backup_guard() -> None:
    """Install after disk guard and before legacy single-slot rotation."""
    manager_cls = _s.StorageManager
    if (
        getattr(manager_cls, "_storage_sparse_backup_guard_version", None)
        == SPARSE_GUARD_VERSION
    ):
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

            with self._lock:
                try:
                    if reason == "prestart":
                        return _create_quiescent_sparse_clone_backup(
                            self,
                            kind=kind,
                            reason=reason,
                        )
                    return _create_sparse_guarded_backup(
                        self,
                        kind=kind,
                        reason=reason,
                    )
                except OSError as sparse_error:
                    # Change the signature so the older unlink-first wrapper cannot
                    # consume a guarded failure and destroy the last recovery point.
                    if sparse_error.errno == errno.ENOSPC:
                        raise OSError(
                            errno.ENOSPC,
                            "sparse-aware backup could not preserve protected headroom: "
                            f"{sparse_error}",
                        ) from sparse_error
                    raise

    manager_cls.create_backup = create_backup
    manager_cls._storage_sparse_backup_guard_version = SPARSE_GUARD_VERSION
