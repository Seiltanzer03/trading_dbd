"""Disk-budget guard for verified SQLite backups.

The durability contract requires a 15 minute local RPO, but that does not imply
keeping 24 hours of full database copies on the same small filesystem.  This
module keeps the newest verified recovery points dense within a byte budget,
retains sparse older anchors while space permits, and removes stale temporary
artifacts left by interrupted/ENOSPC backup attempts.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

from . import storage_runtime as _s


DISK_GUARD_VERSION = "seiltanzer-storage-disk-guard-v2"
DEFAULT_LOCAL_BACKUP_MAX_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_DENSE_BUDGET_FRACTION = 0.75
MIN_VERIFIED_LOCAL_BACKUPS = 2
MIN_BACKUP_HEADROOM_BYTES = 512 * 1024 * 1024
DEFAULT_TMP_MAX_AGE_SEC = 5 * 60


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _manifest_size(directory: Path, manifest: dict[str, Any]) -> int:
    try:
        declared = int(manifest.get("database_size_bytes") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > 0:
        return declared
    db_name = str(manifest.get("database_file") or "")
    if db_name:
        with contextlib.suppress(OSError):
            return max(1, int((directory / db_name).stat().st_size))
    return 1


def _unlink_manifest_pair(directory: Path, manifest: dict[str, Any]) -> None:
    db_name = str(manifest.get("database_file") or "")
    if db_name:
        with contextlib.suppress(FileNotFoundError):
            (directory / db_name).unlink()
    manifest_path = Path(str(manifest.get("manifest_path") or ""))
    if manifest_path.name:
        with contextlib.suppress(FileNotFoundError):
            manifest_path.unlink()


def _cleanup_stale_temp(directory: Path, *, max_age_sec: int | None = None) -> int:
    """Delete only hidden SQLite backup temporaries older than the safety window."""
    max_age = max_age_sec if max_age_sec is not None else _positive_int_env(
        "SEILTANZER_BACKUP_TMP_MAX_AGE_SEC", DEFAULT_TMP_MAX_AGE_SEC)
    now = time.time()
    removed = 0
    for path in directory.glob(".*.tmp.sqlite3"):
        try:
            if now - path.stat().st_mtime < max_age:
                continue
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            # Backup creation must surface its own error; stale cleanup is best effort.
            continue
    return removed


def _retention_priority(manifests: list[dict[str, Any]], *, dense_ids: set[str]) -> list[dict[str, Any]]:
    """Pick newest daily/weekly/monthly anchors before optional dense fill."""
    now = time.time()
    daily: set[str] = set()
    weekly: set[str] = set()
    monthly: set[str] = set()
    sparse: list[dict[str, Any]] = []
    for manifest in manifests:
        bid = str(manifest.get("backup_id") or "")
        if not bid or bid in dense_ids:
            continue
        ts = float(manifest.get("created_ts") or 0.0)
        if ts <= 0:
            continue
        age_days = max(0.0, (now - ts) / 86400.0)
        tm = time.gmtime(ts)
        if age_days <= 14.0:
            key = time.strftime("%Y-%m-%d", tm)
            bucket = daily
        elif age_days <= 70.0:
            key = time.strftime("%Y-W%W", tm)
            bucket = weekly
        elif age_days <= 366.0:
            key = time.strftime("%Y-%m", tm)
            bucket = monthly
        else:
            continue
        if key not in bucket:
            bucket.add(key)
            sparse.append(manifest)
    return sparse


def _preflight_minimum_verified(self, directory: Path) -> int:
    """Preserve one recovery point when two would prevent a replacement copy."""
    try:
        live_bytes = max(1, int(self.db_path.stat().st_size))
        stat = os.statvfs(directory)
        free_bytes = max(0, int(stat.f_bavail) * int(stat.f_frsize))
    except OSError:
        # Unknown capacity must not reduce the normal two-backup durability floor.
        return MIN_VERIFIED_LOCAL_BACKUPS
    required_bytes = live_bytes + MIN_BACKUP_HEADROOM_BYTES
    return 1 if free_bytes < required_bytes else MIN_VERIFIED_LOCAL_BACKUPS


def _apply_local_byte_budget(
    self, *, minimum_verified: int = MIN_VERIFIED_LOCAL_BACKUPS
) -> dict[str, Any]:
    directory = self._backup_dir("local")
    manifests = self._verified_manifests(directory)
    if not manifests:
        return {"kept": 0, "removed": 0, "budget_bytes": 0, "used_bytes": 0}

    minimum_verified = max(
        1, min(int(minimum_verified), MIN_VERIFIED_LOCAL_BACKUPS)
    )

    configured_budget = _positive_int_env(
        "SEILTANZER_LOCAL_BACKUP_MAX_BYTES", DEFAULT_LOCAL_BACKUP_MAX_BYTES)
    newest_size = _manifest_size(directory, manifests[0])
    # Normal retention keeps two restore points. During a low-space preflight the
    # caller may temporarily keep only the newest verified point so a replacement
    # snapshot can be created; successful post-backup retention restores two.
    budget = (
        newest_size
        if minimum_verified == 1
        else max(configured_budget, newest_size * minimum_verified)
    )
    dense_budget = max(
        newest_size * minimum_verified,
        int(budget * DEFAULT_DENSE_BUDGET_FRACTION),
    )

    keep: set[str] = set()
    used = 0

    def add(manifest: dict[str, Any], *, ceiling: int, force: bool = False) -> bool:
        nonlocal used
        bid = str(manifest.get("backup_id") or "")
        if not bid or bid in keep:
            return False
        size = _manifest_size(directory, manifest)
        if not force and used + size > ceiling:
            return False
        keep.add(bid)
        used += size
        return True

    # Minimum safety floor first, then as much dense recent history as the dense
    # share permits.
    for manifest in manifests[:minimum_verified]:
        add(manifest, ceiling=budget, force=True)
    for manifest in manifests[minimum_verified:]:
        add(manifest, ceiling=dense_budget)

    # Spend the remaining quarter preferentially on sparse older recovery anchors.
    dense_ids = set(keep)
    for manifest in _retention_priority(manifests, dense_ids=dense_ids):
        add(manifest, ceiling=budget)

    # Any residual bytes go back to the newest omitted snapshots.
    for manifest in manifests:
        add(manifest, ceiling=budget)

    removed = 0
    for manifest in manifests:
        bid = str(manifest.get("backup_id") or "")
        if bid in keep:
            continue
        _unlink_manifest_pair(directory, manifest)
        removed += 1

    return {
        "kept": len(keep),
        "removed": removed,
        "budget_bytes": budget,
        "configured_budget_bytes": configured_budget,
        "used_bytes": used,
        "newest_backup_bytes": newest_size,
        "disk_guard_version": DISK_GUARD_VERSION,
    }


def install_storage_disk_guard() -> None:
    """Install after ``install_storage_refinement`` and before ``prepare_storage``."""
    manager_cls = _s.StorageManager
    if getattr(manager_cls, "_storage_disk_guard_version", None) == DISK_GUARD_VERSION:
        return

    original_create = manager_cls.create_backup
    original_retention = manager_cls._apply_retention

    def apply_retention(self, kind: str) -> None:
        if kind != "local":
            original_retention(self, kind)
            return
        _apply_local_byte_budget(self)

    def create_backup(self, *, kind: str = "local", reason: str = "scheduled"):
        directory = self._backup_dir(kind)
        directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            # Preflight pruning ensures a normal scheduled backup does not wait until
            # after another full copy has already consumed the last free bytes.
            if kind == "local":
                _apply_local_byte_budget(
                    self,
                    minimum_verified=_preflight_minimum_verified(self, directory),
                )
            _cleanup_stale_temp(directory)
            before = {p.name for p in directory.iterdir()}
            try:
                result = original_create(self, kind=kind, reason=reason)
            except Exception:
                # Remove only artifacts created by this failed attempt. Existing
                # verified snapshots and the authoritative live DB are untouched.
                for path in directory.iterdir():
                    if path.name in before:
                        continue
                    if path.name.endswith(".tmp.sqlite3") or path.name.endswith(".manifest.json") or path.name.endswith(".sqlite3"):
                        with contextlib.suppress(FileNotFoundError, OSError):
                            path.unlink()
                _cleanup_stale_temp(directory, max_age_sec=0)
                raise
            if kind == "local":
                _apply_local_byte_budget(self)
            return result

    manager_cls._apply_retention = apply_retention
    manager_cls.create_backup = create_backup
    manager_cls._storage_disk_guard_version = DISK_GUARD_VERSION
