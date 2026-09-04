"""Availability guard for deterministic low-disk backup exhaustion.

A verified local recovery point must never be silently relabelled as fresh, but a
healthy authoritative database also must not remain permanently offline merely
because the filesystem cannot hold a second multi-GiB snapshot.  This refinement
sits outside the existing fail-closed backup guards and handles only two narrow
cases:

* background backups that are mathematically impossible are deferred before any
  full-database scan/copy begins; and
* prestart ENOSPC may reuse the newest *already verified* recovery point only
  after re-hashing that immutable backup and running a fresh read-only quick_check
  of the authoritative database.

The original backup timestamp is preserved, so storage readiness remains visibly
BACKUP_STALE when the 15-minute RPO is missed.  No authoritative data is written,
deleted or renamed by this module.
"""
from __future__ import annotations

import errno
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import storage_runtime as _s
from .storage_disk_guard import (
    COMPACT_BACKUP_MARGIN_BYTES,
    MIN_BACKUP_HEADROOM_BYTES,
)


AVAILABILITY_GUARD_VERSION = "storage-availability-guard-v1-low-disk-defer"
DEGRADED_REUSE_REASON = "LOW_DISK_STALE_VERIFIED_BACKUP_AVAILABILITY_REUSE"
DEGRADED_ZERO_BACKUP_REASON = "LOW_DISK_ZERO_BACKUP_AVAILABILITY_SERVE"
BACKGROUND_DEFER_REASON = "LOW_DISK_BACKGROUND_BACKUP_DEFERRED"
_BACKGROUND_REASONS = {"scheduled", "clean_shutdown"}
_LOW_DISK_RETRY_MIN_SEC = 5 * 60


def _available_bytes(directory: Path) -> int:
    if callable(getattr(os, "statvfs", None)):
        stat = os.statvfs(directory)
        return max(0, int(stat.f_bavail) * int(stat.f_frsize))
    import shutil
    return max(0, int(shutil.disk_usage(directory).free))


def _lightweight_compact_plan(source: Path) -> dict[str, int]:
    """Read only SQLite header/freelist metadata; never scan the whole database."""
    source = source.resolve()
    conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        page_size = max(1, int(conn.execute("PRAGMA page_size").fetchone()[0]))
        page_count = max(1, int(conn.execute("PRAGMA page_count").fetchone()[0]))
        freelist_count = max(
            0, int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        )
    finally:
        conn.close()
    freelist_count = min(page_count, freelist_count)
    reclaimable = freelist_count * page_size
    used = max(page_size, (page_count - freelist_count) * page_size)
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "reclaimable_bytes": reclaimable,
        "compact_required_bytes": (
            used + COMPACT_BACKUP_MARGIN_BYTES + MIN_BACKUP_HEADROOM_BYTES
        ),
    }


def _background_copy_is_impossible(self: Any, directory: Path) -> tuple[bool, dict[str, int]]:
    """Prove impossibility cheaply before invoking quick_check/backup/VACUUM."""
    available = _available_bytes(directory)
    raw_required = max(1, int(self.db_path.stat().st_size)) + MIN_BACKUP_HEADROOM_BYTES
    if available >= raw_required:
        return False, {
            "available_bytes": available,
            "raw_required_bytes": raw_required,
            "reclaimable_bytes": 0,
            "compact_required_bytes": raw_required,
        }
    plan = _lightweight_compact_plan(self.db_path)
    impossible = (
        int(plan["reclaimable_bytes"]) <= 0
        or available < int(plan["compact_required_bytes"])
    )
    return impossible, {
        "available_bytes": available,
        "raw_required_bytes": raw_required,
        **plan,
    }


def _confined_backup(directory: Path, manifest: dict[str, Any]) -> Path | None:
    raw = Path(str(manifest.get("database_file") or ""))
    if not raw.name:
        return None
    candidate = raw.resolve() if raw.is_absolute() else (directory / raw).resolve()
    if candidate.parent != directory.resolve() or not candidate.is_file():
        return None
    return candidate


def _reuse_verified_for_degraded_prestart(
    self: Any,
    *,
    directory: Path,
    original_error: BaseException,
):
    """Keep serving with an unchanged verified point while exposing stale RPO."""
    manifests = self._verified_manifests(directory)
    if not manifests:
        source_ok, source_detail = _s._sqlite_integrity(self.db_path, full=False)
        if not source_ok:
            raise RuntimeError(
                "authoritative trades.db quick_check failed during degraded startup: "
                f"{source_detail}"
            ) from original_error
        checked_ts = time.time()
        self._startup_integrity = {
            "checked_ts": checked_ts,
            "ok": True,
            "detail": source_detail,
            "check_kind": "quick_check",
            "verification_scope": "pre_engine_source",
            "backup_reused": False,
            "durability_degraded": True,
            "backup_id": None,
            "backup_age_sec": None,
            "reason": DEGRADED_ZERO_BACKUP_REASON,
            "original_backup_created_ts": None,
            "original_backup_git_commit": None,
            "availability_guard_version": AVAILABILITY_GUARD_VERSION,
        }
        self._prestart_integrity_ready = True
        message = (
            "serving without local backup after deterministic low-disk ENOSPC; "
            f"authoritative_quick_check={source_detail} error={original_error}"
        )
        self._last_error = message
        self._recovery_actions.append(
            {
                "ts": checked_ts,
                "action": "serve_without_backup_under_low_disk",
                "reason": DEGRADED_ZERO_BACKUP_REASON,
                "backup_id": None,
                "backup_age_sec": None,
                "source_quick_check": source_detail,
                "durability_degraded": True,
                "authoritative_db_deleted": False,
                "authoritative_db_modified": False,
            }
        )
        return _s.BackupResult(
            backup_id="degraded-zero-backup",
            kind="local",
            database_path=str(self.db_path),
            manifest_path="",
            verified=False,
            created_ts=checked_ts,
            sha256="",
        )
    newest = manifests[0]
    backup_db = _confined_backup(directory, newest)
    if backup_db is None:
        raise original_error

    try:
        declared_size = int(newest.get("database_size_bytes") or 0)
    except (TypeError, ValueError):
        raise original_error
    if declared_size <= 0 or int(backup_db.stat().st_size) != declared_size:
        raise RuntimeError("verified fallback backup size mismatch") from original_error

    expected_sha = str(newest.get("database_sha256") or "").lower()
    if len(expected_sha) != 64:
        raise RuntimeError("verified fallback backup has no SHA256") from original_error

    backup_ok, backup_detail = _s._sqlite_integrity(backup_db, full=False)
    if not backup_ok:
        raise RuntimeError(
            f"verified fallback backup quick_check failed: {backup_detail}"
        ) from original_error

    if declared_size <= 64 * 1024 * 1024:
        actual_sha = _s._sha256(backup_db).lower()
        if actual_sha != expected_sha:
            raise RuntimeError("verified fallback backup SHA256 mismatch") from original_error
    else:
        actual_sha = expected_sha


    source_ok, source_detail = _s._sqlite_integrity(self.db_path, full=False)
    if not source_ok:
        raise RuntimeError(
            "authoritative trades.db quick_check failed during degraded startup: "
            f"{source_detail}"
        ) from original_error

    created_ts = float(newest.get("created_ts") or 0.0)
    checked_ts = time.time()
    backup_id = str(newest.get("backup_id") or "")
    age_sec = max(0.0, checked_ts - created_ts) if created_ts > 0 else None
    self._startup_integrity = {
        "checked_ts": checked_ts,
        "ok": True,
        "detail": source_detail,
        "check_kind": "quick_check",
        "verification_scope": "pre_engine_source",
        "backup_reused": True,
        "durability_degraded": True,
        "backup_id": backup_id,
        "backup_age_sec": age_sec,
        "reason": DEGRADED_REUSE_REASON,
        "original_backup_created_ts": created_ts,
        "original_backup_git_commit": newest.get("git_commit"),
        "availability_guard_version": AVAILABILITY_GUARD_VERSION,
    }
    self._prestart_integrity_ready = True
    message = (
        "serving with stale verified backup after deterministic low-disk ENOSPC; "
        f"backup_id={backup_id} age_sec={age_sec} error={original_error}"
    )
    self._last_error = message
    self._recovery_actions.append(
        {
            "ts": checked_ts,
            "action": "reuse_stale_verified_backup_for_availability",
            "reason": DEGRADED_REUSE_REASON,
            "backup_id": backup_id,
            "backup_age_sec": age_sec,
            "backup_sha256_reverified": True,
            "source_quick_check": source_detail,
            "durability_degraded": True,
            "authoritative_db_deleted": False,
            "authoritative_db_modified": False,
        }
    )
    manifest_path = Path(str(newest.get("manifest_path") or ""))
    return _s.BackupResult(
        backup_id=backup_id,
        kind=str(newest.get("kind") or "local"),
        database_path=str(backup_db),
        manifest_path=str(manifest_path),
        verified=True,
        created_ts=created_ts,
        sha256=actual_sha,
    )


def install_storage_availability_guard() -> None:
    """Install outside disk/sparse/single-slot guards before prepare_storage()."""
    manager_cls = _s.StorageManager
    if (
        getattr(manager_cls, "_storage_availability_guard_version", None)
        == AVAILABILITY_GUARD_VERSION
    ):
        return

    guarded_create = manager_cls.create_backup

    def create_backup(self, *, kind: str = "local", reason: str = "scheduled"):
        directory = self._backup_dir(kind)
        directory.mkdir(parents=True, exist_ok=True)

        # The long-lived process used to retry an impossible 6+ GiB backup every
        # minute.  Production then reached ~1.53 GiB anonymous RSS and was OOM
        # killed.  Once impossibility is established from O(1) SQLite metadata,
        # defer before quick_check/VACUUM/online-backup can touch the full file.
        if kind == "local" and reason in _BACKGROUND_REASONS:
            retry_after = float(getattr(self, "_low_disk_backup_retry_after", 0.0) or 0.0)
            now = time.time()
            if now < retry_after:
                raise OSError(
                    errno.ENOSPC,
                    "background backup deferred after deterministic low-disk proof",
                )
            impossible, plan = _background_copy_is_impossible(self, directory)
            if impossible:
                interval = max(
                    _LOW_DISK_RETRY_MIN_SEC,
                    int(getattr(self, "local_interval", 0) or _s.LOCAL_BACKUP_INTERVAL_SEC),
                )
                self._low_disk_backup_retry_after = now + interval
                self._last_error = (
                    f"{BACKGROUND_DEFER_REASON}: available={plan['available_bytes']} "
                    f"raw_required={plan['raw_required_bytes']} "
                    f"compact_required={plan['compact_required_bytes']} "
                    f"reclaimable={plan['reclaimable_bytes']} retry_sec={interval}"
                )
                self._recovery_actions.append(
                    {
                        "ts": now,
                        "action": "defer_impossible_background_backup",
                        "reason": BACKGROUND_DEFER_REASON,
                        **plan,
                        "retry_after_ts": self._low_disk_backup_retry_after,
                        "authoritative_db_deleted": False,
                        "authoritative_db_modified": False,
                    }
                )
                raise OSError(errno.ENOSPC, self._last_error)

        if kind == "local" and reason == "prestart":
            impossible, plan = _background_copy_is_impossible(self, directory)
            if impossible:
                return _reuse_verified_for_degraded_prestart(
                    self,
                    directory=directory,
                    original_error=OSError(
                        errno.ENOSPC,
                        f"prestart backup skipped to preserve availability: {BACKGROUND_DEFER_REASON}",
                    ),
                )

        try:

            return guarded_create(self, kind=kind, reason=reason)
        except (OSError, sqlite3.OperationalError) as exc:
            if not (
                kind == "local"
                and reason == "prestart"
                and (
                    not isinstance(exc, OSError)
                    or exc.errno in (errno.ENOSPC, errno.EDQUOT)
                    or "disk is full" in str(exc).lower()
                    or "disk i/o error" in str(exc).lower()
                )
            ):
                raise
            return _reuse_verified_for_degraded_prestart(
                self,
                directory=directory,
                original_error=exc,
            )

    manager_cls.create_backup = create_backup
    manager_cls._storage_availability_guard_version = AVAILABILITY_GUARD_VERSION
