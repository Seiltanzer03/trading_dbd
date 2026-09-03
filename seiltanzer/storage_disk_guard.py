"""Disk-budget guard for verified SQLite backups.

The durability contract requires a 15 minute local RPO, but that does not imply
keeping 24 hours of full database copies on the same small filesystem.  This
module keeps the newest verified recovery points dense within a byte budget,
retains sparse older anchors while space permits, and removes stale temporary
artifacts left by interrupted/ENOSPC backup attempts.
"""
from __future__ import annotations

import contextlib
import errno
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

from . import storage_refinement as _refinement
from . import storage_runtime as _s


DISK_GUARD_VERSION = "seiltanzer-storage-disk-guard-v4-compact-backup"
DEFAULT_LOCAL_BACKUP_MAX_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_DENSE_BUDGET_FRACTION = 0.75
MIN_VERIFIED_LOCAL_BACKUPS = 2
MIN_BACKUP_HEADROOM_BYTES = 1024 * 1024 * 1024
DEFAULT_TMP_MAX_AGE_SEC = 5 * 60
COMPACT_BACKUP_MARGIN_BYTES = 16 * 1024 * 1024
LOW_DISK_REUSE_REASON = "LOW_DISK_CURRENT_SHA_VERIFIED_BACKUP_REUSE"
LOW_DISK_COMPACT_REASON = "LOW_DISK_COMPACT_SQLITE_SNAPSHOT"


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


def _available_bytes(directory: Path) -> int:
    if callable(getattr(os, "statvfs", None)):
        stat = os.statvfs(directory)
        return max(0, int(stat.f_bavail) * int(stat.f_frsize))
    import shutil
    return max(0, int(shutil.disk_usage(directory).free))


def _preflight_minimum_verified(self, directory: Path) -> int:
    """Preserve one recovery point when two would prevent a replacement copy."""
    try:
        live_bytes = max(1, int(self.db_path.stat().st_size))
        free_bytes = _available_bytes(directory)
    except (OSError, AttributeError):
        # Unknown capacity must not reduce the normal two-backup durability floor.
        return MIN_VERIFIED_LOCAL_BACKUPS
    required_bytes = live_bytes + MIN_BACKUP_HEADROOM_BYTES
    return 1 if free_bytes < required_bytes else MIN_VERIFIED_LOCAL_BACKUPS


def _compact_snapshot_plan(source: Path) -> dict[str, int]:
    """Measure whether a read-only VACUUM INTO snapshot can fit safely.

    The normal online backup is intentionally preferred because it is a direct
    page-for-page recovery point. This planner is used only when that copy cannot
    fit. ``freelist_count`` measures pages SQLite can omit while rebuilding the
    same logical database. A source quick_check is mandatory before the fallback
    is allowed.
    """
    source = source.resolve()
    conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        if quick_rows != ["ok"]:
            raise RuntimeError(
                "source quick_check failed before compact backup: "
                + "; ".join(quick_rows[:20])
            )
        page_size = max(1, int(conn.execute("PRAGMA page_size").fetchone()[0]))
        page_count = max(1, int(conn.execute("PRAGMA page_count").fetchone()[0]))
        freelist_count = max(0, int(conn.execute("PRAGMA freelist_count").fetchone()[0]))
    finally:
        conn.close()
    freelist_count = min(freelist_count, page_count)
    logical_bytes = page_count * page_size
    logical_used_bytes = max(page_size, (page_count - freelist_count) * page_size)
    reclaimable_bytes = freelist_count * page_size
    # VACUUM rebuilds using the same page size and normally cannot require more
    # than the currently allocated non-freelist pages. Keep an extra filesystem
    # cushion anyway; post-copy headroom is checked again before promotion.
    estimated_snapshot_bytes = logical_used_bytes + COMPACT_BACKUP_MARGIN_BYTES
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "logical_database_bytes": logical_bytes,
        "logical_used_bytes": logical_used_bytes,
        "reclaimable_bytes": reclaimable_bytes,
        "estimated_snapshot_bytes": estimated_snapshot_bytes,
    }


def _refine_compact_manifest(self, result: _s.BackupResult, *, kind: str) -> None:
    """Apply the same refinement identity as the canonical backup wrapper."""
    manifest_path = Path(result.manifest_path)
    database_path = Path(result.database_path)
    manifest = _s._read_json(manifest_path) or {}
    previous = [
        item
        for item in self._verified_manifests(self._backup_dir(kind))
        if str(item.get("backup_id")) != result.backup_id
    ]
    user_version, schema_sha = _refinement._schema_identity(database_path)
    manifest["previous_backup_id"] = previous[0].get("backup_id") if previous else None
    manifest["sqlite_user_version"] = user_version
    manifest["schema_sha256"] = schema_sha
    if kind == "offhost":
        encrypted = _refinement._truthy_env("SEILTANZER_OFFHOST_ENCRYPTION_VERIFIED")
        target_verified = _refinement._truthy_env("SEILTANZER_OFFHOST_TARGET_VERIFIED")
        manifest["encryption_status"] = (
            "verified_external_target" if encrypted else "external_target_not_verified"
        )
        manifest["encryption_verified"] = encrypted
        manifest["offhost_target_verified"] = target_verified
    else:
        manifest["encryption_status"] = "local_filesystem_permissions_only"
        manifest["encryption_verified"] = False
        manifest["offhost_target_verified"] = False
    manifest["storage_refinement_version"] = _refinement.REFINEMENT_VERSION
    manifest["manifest_payload_sha256"] = _refinement._manifest_hash(manifest)
    _s._atomic_json(manifest_path, manifest)


def _create_compact_backup(
    self,
    *,
    kind: str,
    reason: str,
    plan: dict[str, int],
):
    """Create and fully verify a compact SQLite recovery point without source writes."""
    directory = self._backup_dir(kind)
    directory.mkdir(parents=True, exist_ok=True)
    created = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created))
    backup_id = f"{stamp}-{kind}-{int(created * 1000) % 1000000:06d}"
    final_db = directory / f"{backup_id}.sqlite3"
    manifest_path = directory / f"{backup_id}.manifest.json"
    temp_db = directory / f".{backup_id}.tmp.sqlite3"
    with contextlib.suppress(FileNotFoundError):
        temp_db.unlink()

    # mode=ro makes the source-side safety property explicit. VACUUM INTO writes
    # only the new destination database and obtains a transactionally consistent
    # snapshot of committed source state, including committed WAL contents.
    src = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        src.execute("PRAGMA busy_timeout=30000")
        src.execute("VACUUM INTO ?", (str(temp_db),))
    finally:
        src.close()

    if not temp_db.is_file():
        raise RuntimeError("compact SQLite backup did not create its destination")
    size = int(temp_db.stat().st_size)
    remaining = _available_bytes(directory)
    if remaining < MIN_BACKUP_HEADROOM_BYTES:
        with contextlib.suppress(FileNotFoundError):
            temp_db.unlink()
        raise OSError(
            errno.ENOSPC,
            "compact backup would consume protected headroom: "
            f"remaining={remaining} required={MIN_BACKUP_HEADROOM_BYTES}",
        )

    startup_source = self.db_path if reason == "prestart" else None
    ok, integrity_detail, sha, counts, source_check = _s._verify_backup_snapshot(
        temp_db,
        startup_source=startup_source,
    )
    if not ok:
        with contextlib.suppress(FileNotFoundError):
            temp_db.unlink()
        raise RuntimeError(f"compact backup integrity failed: {integrity_detail}")
    if source_check is not None and not source_check[0]:
        with contextlib.suppress(FileNotFoundError):
            temp_db.unlink()
        raise RuntimeError(f"source startup integrity failed: {source_check[1]}")
    if source_check is not None:
        self._startup_integrity = {
            "checked_ts": time.time(),
            "ok": source_check[0],
            "detail": source_check[1],
            "check_kind": "quick_check",
            "verification_scope": "pre_engine_source",
            "contract_version": _s.STORAGE_CONTRACT_VERSION,
            "backup_compacted": True,
            "reason": LOW_DISK_COMPACT_REASON,
        }
        self._prestart_integrity_ready = True

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
        "database_size_bytes": size,
        "sqlite_integrity": integrity_detail,
        "critical_table_counts": counts,
        "git_commit": self.git_commit,
        "verified": True,
        "encryption_status": (
            "external_target_managed" if kind == "offhost" else "filesystem_permissions"
        ),
        "snapshot_mode": "vacuum_into_compact",
        "disk_guard_version": DISK_GUARD_VERSION,
        "compact_plan": dict(plan),
        "free_after_snapshot_bytes": remaining,
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
    _refine_compact_manifest(self, result, kind=kind)
    self._recovery_actions.append({
        "ts": time.time(),
        "action": "create_compact_verified_backup",
        "backup_id": backup_id,
        "reason": LOW_DISK_COMPACT_REASON,
        "database_size_bytes": size,
        "reclaimable_source_bytes": int(plan.get("reclaimable_bytes") or 0),
        "free_after_snapshot_bytes": remaining,
    })
    self._apply_retention(kind)
    return result


def _reuse_current_prestart_backup(self, directory: Path):
    """Reuse only a recent verified backup produced by this exact code SHA.

    A small production disk may have room for one full SQLite recovery point,
    but not for the old and replacement copies at the same time. A repeated
    restart of the *same* release must not fill the filesystem rebuilding the
    recovery point it created minutes earlier. This path performs a fresh
    quick-check of the authoritative DB and preserves the original manifest
    timestamp/provenance; it never relabels an old backup as new.
    """
    manifests = self._verified_manifests(directory)
    if not manifests:
        return None
    newest = manifests[0]
    created_ts = float(newest.get("created_ts") or 0.0)
    max_age = max(1, int(getattr(self, "local_interval", 0) or _s.LOCAL_BACKUP_INTERVAL_SEC))
    if created_ts <= 0 or time.time() - created_ts > max_age:
        return None
    if str(newest.get("git_commit") or "") != str(self.git_commit):
        return None
    database_file = Path(str(newest.get("database_file") or ""))
    if database_file.is_absolute():
        backup_db = database_file.resolve()
    else:
        backup_db = (directory / database_file).resolve()
    if backup_db.parent != directory.resolve() or not backup_db.is_file():
        return None
    try:
        declared_size = int(newest.get("database_size_bytes") or 0)
    except (TypeError, ValueError):
        return None
    if declared_size <= 0 or backup_db.stat().st_size != declared_size:
        return None
    source_ok, source_detail = _s._sqlite_integrity(self.db_path, full=False)
    if not source_ok:
        return None

    checked_ts = time.time()
    backup_id = str(newest.get("backup_id") or "")
    self._startup_integrity = {
        "checked_ts": checked_ts,
        "ok": True,
        "detail": source_detail,
        "check_kind": "quick_check",
        "verification_scope": "pre_engine_source",
        "backup_reused": True,
        "backup_id": backup_id,
        "reason": LOW_DISK_REUSE_REASON,
        "original_backup_created_ts": created_ts,
    }
    self._prestart_integrity_ready = True
    self._recovery_actions.append({
        "ts": checked_ts,
        "action": "reuse_recent_exact_sha_prestart_backup",
        "backup_id": backup_id,
        "reason": LOW_DISK_REUSE_REASON,
    })
    manifest_path = Path(str(newest.get("manifest_path") or ""))
    return _s.BackupResult(
        backup_id=backup_id,
        kind=str(newest.get("kind") or "local"),
        database_path=str(backup_db),
        manifest_path=str(manifest_path),
        verified=True,
        created_ts=created_ts,
        sha256=str(newest.get("database_sha256") or ""),
    )


@contextlib.contextmanager
def reserve_restore_drill_headroom(
    self, *, required_bytes: int, protected_backup_id: str,
) -> Iterator[dict[str, Any]]:
    """Reserve disposable-copy space without dropping the newest recovery point.

    This reuses the same one-backup low-space floor as snapshot replacement. It
    never waits behind an active backup: readiness either reserves space now or
    fails visibly, while the live database and newest verified backup remain.
    """
    directory = self._backup_dir("local")
    copy_bytes = max(1, int(required_bytes))
    locked_required = copy_bytes + MIN_BACKUP_HEADROOM_BYTES
    unlocked_required = (copy_bytes * 2) + MIN_BACKUP_HEADROOM_BYTES

    def free_bytes() -> int:
        stat = os.statvfs(directory)
        return max(0, int(stat.f_bavail) * int(stat.f_frsize))

    before = free_bytes()
    if before >= unlocked_required:
        yield {
            "disk_guard_version": DISK_GUARD_VERSION,
            "pruned": False,
            "required_bytes": unlocked_required,
            "free_before_bytes": before,
            "free_after_bytes": before,
            "exclusive_backup_window": False,
        }
        return

    if not self._lock.acquire(blocking=False):
        raise RuntimeError("restore drill headroom unavailable while backup is active")
    try:
        manifests = self._verified_manifests(directory)
        newest_id = str(manifests[0].get("backup_id") or "") if manifests else ""
        if not newest_id or newest_id != str(protected_backup_id):
            raise RuntimeError("restore drill cannot prune its protected newest backup")
        retention = _apply_local_byte_budget(self, minimum_verified=1)
        after = free_bytes()
        if after < locked_required:
            raise OSError(
                errno.ENOSPC,
                "insufficient headroom after preserving newest verified backup",
            )
        yield {
            "disk_guard_version": DISK_GUARD_VERSION,
            "pruned": True,
            "required_bytes": locked_required,
            "free_before_bytes": before,
            "free_after_bytes": after,
            "exclusive_backup_window": True,
            "retention": retention,
        }
    finally:
        self._lock.release()


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
            compact_plan: dict[str, int] | None = None
            if kind == "local":
                required = max(1, int(self.db_path.stat().st_size)) + MIN_BACKUP_HEADROOM_BYTES
                available = _available_bytes(directory)
                if available < required:
                    reused = (
                        _reuse_current_prestart_backup(self, directory)
                        if reason == "prestart" else None
                    )
                    if reused is not None:
                        return reused
                    compact_plan = _compact_snapshot_plan(self.db_path)
                    compact_required = (
                        int(compact_plan["estimated_snapshot_bytes"])
                        + MIN_BACKUP_HEADROOM_BYTES
                    )
                    if (
                        int(compact_plan["reclaimable_bytes"]) <= 0
                        or available < compact_required
                    ):
                        raise OSError(
                            errno.ENOSPC,
                            "insufficient single-slot backup headroom while preserving "
                            "verified recovery point: "
                            f"available={available} raw_required={required} "
                            f"compact_required={compact_required} "
                            f"reclaimable={compact_plan['reclaimable_bytes']}",
                        )
            before = {p.name for p in directory.iterdir()}
            try:
                if compact_plan is not None:
                    result = _create_compact_backup(
                        self,
                        kind=kind,
                        reason=reason,
                        plan=compact_plan,
                    )
                else:
                    result = original_create(self, kind=kind, reason=reason)
            except Exception:
                # Remove only artifacts created by this failed attempt. Existing
                # verified snapshots and the authoritative live DB are untouched.
                for path in directory.iterdir():
                    if path.name in before:
                        continue
                    if (
                        path.name.endswith(".tmp.sqlite3")
                        or path.name.endswith(".manifest.json")
                        or path.name.endswith(".sqlite3")
                    ):
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
