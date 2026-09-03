"""Fail-closed replacement of the only verified local SQLite backup.

The production disk is intentionally allowed to keep a single full recovery point
when two database-sized copies do not fit. ``storage_disk_guard`` preserves that
point and raises ENOSPC before allocating a second copy. This refinement handles
only that exact ENOSPC: it freshly verifies the existing slot by SHA256, runs a
read-only quick_check of the authoritative database, proves that reclaiming the
old slot creates enough room for a replacement plus the existing 1 GiB operating
headroom, removes only the verified backup pair, and immediately retries the
canonical backup implementation.

This is the in-process equivalent of the existing production single-slot recovery
workflow. It never deletes or rewrites the authoritative trades.db.
"""
from __future__ import annotations

import contextlib
import errno
import os
import time
from pathlib import Path
from typing import Any

from . import storage_runtime as _s
from .storage_disk_guard import MIN_BACKUP_HEADROOM_BYTES


SINGLE_SLOT_ROTATION_VERSION = "storage-single-slot-rotation-v1"
SINGLE_SLOT_ROTATION_REASON = "LOW_DISK_VERIFIED_SINGLE_SLOT_REPLACEMENT"
_ELIGIBLE_REASONS = {"prestart", "scheduled", "clean_shutdown"}
_GUARD_ENOSPC_FRAGMENT = "insufficient single-slot backup headroom"


def _available_bytes(directory: Path) -> int:
    stat = os.statvfs(directory)
    return max(0, int(stat.f_bavail) * int(stat.f_frsize))


def _confined_file(directory: Path, raw: str) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (directory / candidate).resolve()
    root = directory.resolve()
    if resolved.parent != root:
        return None
    return resolved


def _validated_single_slot(
    self: Any,
    directory: Path,
    *,
    required_bytes: int,
    available_bytes: int,
) -> dict[str, Any] | None:
    """Return one replacement-safe slot, or ``None`` without deleting anything."""
    manifests = self._verified_manifests(directory)
    if len(manifests) != 1:
        return None
    manifest = manifests[0]
    backup_id = str(manifest.get("backup_id") or "")
    backup_db = _confined_file(directory, str(manifest.get("database_file") or ""))
    manifest_path = _confined_file(directory, str(manifest.get("manifest_path") or ""))
    if not backup_id or backup_db is None or manifest_path is None:
        return None
    if not backup_db.is_file() or not manifest_path.is_file():
        return None

    try:
        declared_size = int(manifest.get("database_size_bytes") or 0)
    except (TypeError, ValueError):
        return None
    actual_size = int(backup_db.stat().st_size)
    if declared_size <= 0 or actual_size != declared_size:
        raise RuntimeError("single-slot verified backup size mismatch")

    # Do not sacrifice the current recovery point unless its space is sufficient
    # to make the canonical replacement possible. This keeps an impossible
    # rotation fail-closed with the old backup still present.
    if available_bytes + actual_size < required_bytes:
        return None

    expected_sha = str(manifest.get("database_sha256") or "").lower()
    if len(expected_sha) != 64:
        raise RuntimeError("single-slot verified backup has no SHA256")
    actual_sha = _s._sha256(backup_db)
    if actual_sha.lower() != expected_sha:
        raise RuntimeError("single-slot verified backup SHA256 mismatch")

    source_ok, source_detail = _s._sqlite_integrity(self.db_path, full=False)
    if not source_ok:
        raise RuntimeError(
            "authoritative trades.db quick_check failed before single-slot rotation: "
            f"{source_detail}"
        )

    return {
        "backup_id": backup_id,
        "backup_db": backup_db,
        "manifest_path": manifest_path,
        "backup_bytes": actual_size,
        "source_integrity": source_detail,
        "database_sha256": actual_sha,
    }


def install_storage_single_slot_rotation() -> None:
    """Install after ``install_storage_disk_guard`` and before ``prepare_storage``."""
    manager_cls = _s.StorageManager
    if (
        getattr(manager_cls, "_storage_single_slot_rotation_version", None)
        == SINGLE_SLOT_ROTATION_VERSION
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

            # The disk guard released the same RLock when it raised. Reacquire it
            # so no other in-process backup can race validation/deletion/retry.
            with self._lock:
                directory = self._backup_dir("local")
                required = max(1, int(self.db_path.stat().st_size)) + MIN_BACKUP_HEADROOM_BYTES
                available = _available_bytes(directory)
                if available >= required:
                    return guarded_create(self, kind=kind, reason=reason)

                slot = _validated_single_slot(
                    self,
                    directory,
                    required_bytes=required,
                    available_bytes=available,
                )
                if slot is None:
                    raise original_error

                backup_db = slot["backup_db"]
                manifest_path = slot["manifest_path"]
                backup_id = str(slot["backup_id"])
                reclaimed = int(slot["backup_bytes"])

                # Same ordering as the existing recovery workflow: remove the
                # duplicate database copy first, then its manifest. A crash in
                # between cannot turn a missing file into a usable verified slot.
                backup_db.unlink()
                manifest_path.unlink()
                with contextlib.suppress(OSError, AttributeError):
                    os.sync()
                after = _available_bytes(directory)
                if after < required:
                    # This can only happen if an external reader still holds the
                    # unlinked inode. Surface it immediately; never touch trades.db.
                    raise OSError(
                        errno.ENOSPC,
                        "single-slot verified backup was validated and unlinked but "
                        f"filesystem did not release required headroom: available={after} "
                        f"required={required}",
                    )

                self._recovery_actions.append({
                    "ts": time.time(),
                    "action": "replace_verified_single_slot_backup",
                    "reason": SINGLE_SLOT_ROTATION_REASON,
                    "backup_id": backup_id,
                    "reclaimed_bytes": reclaimed,
                    "free_before_bytes": available,
                    "free_after_bytes": after,
                    "required_bytes": required,
                    "source_integrity": slot["source_integrity"],
                    "authoritative_db_deleted": False,
                })

                try:
                    result = guarded_create(self, kind=kind, reason=reason)
                except Exception as exc:
                    self._recovery_actions.append({
                        "ts": time.time(),
                        "action": "replace_verified_single_slot_backup_failed",
                        "reason": SINGLE_SLOT_ROTATION_REASON,
                        "replaced_backup_id": backup_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "authoritative_db_deleted": False,
                    })
                    raise

                self._recovery_actions.append({
                    "ts": time.time(),
                    "action": "replace_verified_single_slot_backup_complete",
                    "reason": SINGLE_SLOT_ROTATION_REASON,
                    "replaced_backup_id": backup_id,
                    "new_backup_id": result.backup_id,
                    "authoritative_db_deleted": False,
                })
                return result

    manager_cls.create_backup = create_backup
    manager_cls._storage_single_slot_rotation_version = SINGLE_SLOT_ROTATION_VERSION
