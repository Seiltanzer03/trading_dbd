"""Fail-before-write disk guard for verified SQLite backup creation.

The authoritative database remains untouched. Hidden ``.*.tmp.sqlite3`` files are
incomplete backup copies with no verified manifest; they are safe to remove once
this process owns the backup lock. The production recovery workflow applies the
same classification while the service is stopped.
"""
from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path

from .storage_runtime import (
    StorageManager as _BaseStorageManager,
    install_storage_runtime as _install_storage_runtime,
)


DEFAULT_BACKUP_HEADROOM_BYTES = 1024 * 1024 * 1024
BACKUP_HEADROOM_ENV = "SEILTANZER_BACKUP_HEADROOM_BYTES"


class BackupSpaceError(RuntimeError):
    """A verified snapshot cannot fit without consuming operating headroom."""


class StorageManager(_BaseStorageManager):
    """StorageManager with bounded disk use before and during backup creation."""

    @staticmethod
    def _backup_headroom_bytes() -> int:
        raw = os.environ.get(
            BACKUP_HEADROOM_ENV,
            str(DEFAULT_BACKUP_HEADROOM_BYTES),
        )
        value = int(raw)
        if value < 0:
            raise ValueError(f"{BACKUP_HEADROOM_ENV} must be >= 0")
        return value

    @staticmethod
    def _cleanup_incomplete_backup_temps(directory: Path) -> int:
        """Remove only unverified hidden SQLite backup temps in this directory."""
        root = directory.resolve()
        removed = 0
        for candidate in directory.glob(".*.tmp.sqlite3"):
            # Never follow a symlink and never leave the configured backup root.
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve()
            if resolved.parent != root or not resolved.is_file():
                continue
            with contextlib.suppress(FileNotFoundError):
                resolved.unlink()
                removed += 1
        return removed

    def _assert_backup_space(self, directory: Path, *, kind: str, reason: str) -> None:
        source_bytes = self.db_path.stat().st_size
        reserve_bytes = self._backup_headroom_bytes()
        required_bytes = source_bytes + reserve_bytes
        free_bytes = int(shutil.disk_usage(directory).free)
        if free_bytes < required_bytes:
            raise BackupSpaceError(
                "backup_space_insufficient "
                f"kind={kind} reason={reason} free_bytes={free_bytes} "
                f"required_bytes={required_bytes} source_bytes={source_bytes} "
                f"headroom_bytes={reserve_bytes}"
            )

    def create_backup(self, *, kind: str = "local", reason: str = "scheduled"):
        """Reject impossible snapshots before allocation and clean failed temps."""
        # The base implementation also uses this RLock; holding it here makes
        # temp classification deterministic and remains safe because it is reentrant.
        with self._lock:
            if not self.db_path.exists():
                raise FileNotFoundError(str(self.db_path))
            directory = self._backup_dir(kind)
            directory.mkdir(parents=True, exist_ok=True)

            # A previous crash/out-of-space attempt may have left an incomplete
            # hidden copy. It has no verified manifest and only steals headroom.
            self._cleanup_incomplete_backup_temps(directory)
            self._assert_backup_space(directory, kind=kind, reason=reason)
            try:
                return super().create_backup(kind=kind, reason=reason)
            except Exception:
                # sqlite3.Connection.backup can fail after allocating a partial
                # destination. Never let that disposable temp consume the disk.
                self._cleanup_incomplete_backup_temps(directory)
                raise


def prepare_storage(settings, *, git_commit: str | None = None) -> StorageManager:
    """Preserve fail-closed prestart backup semantics without disk exhaustion."""
    manager = StorageManager(settings, git_commit=git_commit)
    if manager.db_path.exists():
        manager.create_backup(kind="local", reason="prestart")
    return manager


def install_storage_runtime(app, manager: StorageManager | None = None):
    """Delegate runtime wiring to the canonical implementation."""
    return _install_storage_runtime(app, manager)
