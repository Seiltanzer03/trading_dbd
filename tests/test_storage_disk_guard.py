from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from seiltanzer.config import Settings
from seiltanzer import storage_disk_guard
from seiltanzer.storage_disk_guard import install_storage_disk_guard
from seiltanzer.storage_refinement import install_storage_refinement
from seiltanzer.storage_runtime import StorageManager


install_storage_refinement()
install_storage_disk_guard()


def _db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE payload(id INTEGER PRIMARY KEY, value BLOB)")
        conn.execute("INSERT INTO payload(value) VALUES(?)", (b"x" * 64_000,))
        conn.commit()
    finally:
        conn.close()


def test_local_retention_is_bounded_by_bytes_not_snapshot_count(tmp_path, monkeypatch):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)

    first = manager.create_backup(kind="local", reason="seed")
    first_manifest = next(
        item for item in manager.backups()["local"]
        if item["backup_id"] == first.backup_id
    )
    snapshot_bytes = int(first_manifest["database_size_bytes"])
    budget = snapshot_bytes * 4
    monkeypatch.setenv("SEILTANZER_LOCAL_BACKUP_MAX_BYTES", str(budget))

    for index in range(10):
        conn = sqlite3.connect(settings.trades_db)
        try:
            conn.execute("UPDATE payload SET value=? WHERE id=1", (bytes([index]) * 64_000,))
            conn.commit()
        finally:
            conn.close()
        manager.create_backup(kind="local", reason=f"bounded-{index}")
        time.sleep(0.002)

    manifests = manager.backups()["local"]
    used = sum(int(item["database_size_bytes"]) for item in manifests)
    newest_size = int(manifests[0]["database_size_bytes"])
    effective_budget = max(budget, newest_size * 2)

    assert 2 <= len(manifests) < 11
    assert used <= effective_budget
    assert all((manager.local_dir / item["database_file"]).exists() for item in manifests)


def test_stale_partial_backup_is_removed_before_next_snapshot(tmp_path, monkeypatch):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    monkeypatch.setenv("SEILTANZER_BACKUP_TMP_MAX_AGE_SEC", "60")

    stale = manager.local_dir / ".interrupted-local-backup.tmp.sqlite3"
    stale.write_bytes(b"partial")
    old = time.time() - 600
    os.utime(stale, (old, old))

    result = manager.create_backup(kind="local", reason="after-interruption")

    assert not stale.exists()
    assert Path(result.database_path).exists()
    assert Path(result.manifest_path).exists()


def test_low_space_preflight_temporarily_keeps_one_verified_backup(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    manager.create_backup(kind="local", reason="seed-1")
    time.sleep(0.002)
    manager.create_backup(kind="local", reason="seed-2")
    manifests = manager.backups()["local"]
    assert len(manifests) == 2

    real_apply = storage_disk_guard._apply_local_byte_budget
    observed: list[tuple[int, int]] = []

    def record_apply(self, *, minimum_verified=2):
        result = real_apply(self, minimum_verified=minimum_verified)
        kept = len(self._verified_manifests(self.local_dir))
        observed.append((minimum_verified, kept))
        return result

    monkeypatch.setattr(storage_disk_guard, "_apply_local_byte_budget", record_apply)
    monkeypatch.setattr(
        storage_disk_guard,
        "_preflight_minimum_verified",
        lambda self, directory: 1,
    )

    manager.create_backup(kind="local", reason="low-space-replacement")

    assert observed[0] == (1, 1)
    assert observed[-1] == (2, 2)
    assert len(manager.backups()["local"]) == 2


def test_preflight_reduces_floor_only_when_replacement_lacks_headroom(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    live_bytes = Path(settings.trades_db).stat().st_size

    class Stat:
        f_frsize = 1
        f_bavail = live_bytes + storage_disk_guard.MIN_BACKUP_HEADROOM_BYTES - 1

    monkeypatch.setattr(storage_disk_guard.os, "statvfs", lambda directory: Stat())
    assert storage_disk_guard._preflight_minimum_verified(manager, manager.local_dir) == 1

    Stat.f_bavail += 1
    assert storage_disk_guard._preflight_minimum_verified(manager, manager.local_dir) == 2


def test_low_disk_scheduled_backup_fails_before_allocating_temp(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings, git_commit="single-slot-sha")
    seed = manager.create_backup(kind="local", reason="prestart")
    before = {path.name for path in manager.local_dir.iterdir()}

    class Stat:
        f_frsize = 1
        f_bavail = 0

    monkeypatch.setattr(storage_disk_guard.os, "statvfs", lambda _directory: Stat())

    with pytest.raises(OSError, match="single-slot backup headroom") as error:
        manager.create_backup(kind="local", reason="scheduled")

    assert error.value.errno == storage_disk_guard.errno.ENOSPC
    assert {path.name for path in manager.local_dir.iterdir()} == before
    assert Path(seed.database_path).is_file()
    assert not list(manager.local_dir.glob(".*.tmp.sqlite3"))


def test_low_disk_prestart_reuses_only_recent_exact_sha_verified_backup(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings, git_commit="single-slot-sha")
    seed = manager.create_backup(kind="local", reason="prestart")
    before = {path.name for path in manager.local_dir.iterdir()}

    class Stat:
        f_frsize = 1
        f_bavail = 0

    monkeypatch.setattr(storage_disk_guard.os, "statvfs", lambda _directory: Stat())

    reused = manager.create_backup(kind="local", reason="prestart")

    assert reused.backup_id == seed.backup_id
    assert {path.name for path in manager.local_dir.iterdir()} == before
    assert manager._prestart_integrity_ready is True
    assert manager._startup_integrity["backup_reused"] is True
    assert manager._startup_integrity["reason"] == storage_disk_guard.LOW_DISK_REUSE_REASON
    assert manager._startup_integrity["original_backup_created_ts"] == reused.created_ts


def test_low_disk_prestart_never_reuses_backup_from_another_sha(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings, git_commit="old-sha")
    seed = manager.create_backup(kind="local", reason="prestart")
    manager.git_commit = "new-sha"
    manager._prestart_integrity_ready = False
    manager._startup_integrity = None

    class Stat:
        f_frsize = 1
        f_bavail = 0

    monkeypatch.setattr(storage_disk_guard.os, "statvfs", lambda _directory: Stat())

    with pytest.raises(OSError, match="single-slot backup headroom"):
        manager.create_backup(kind="local", reason="prestart")

    assert Path(seed.database_path).is_file()
    assert manager._prestart_integrity_ready is False
    assert manager._startup_integrity is None


def test_restore_drill_headroom_prunes_only_older_verified_backup(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    manager.create_backup(kind="local", reason="seed-1")
    time.sleep(0.002)
    manager.create_backup(kind="local", reason="seed-2")
    newest = manager.backups()["local"][0]
    required = int(newest["database_size_bytes"])
    reserved = (required * 2) + storage_disk_guard.MIN_BACKUP_HEADROOM_BYTES
    locked = required + storage_disk_guard.MIN_BACKUP_HEADROOM_BYTES
    free_values = iter((reserved - 1, locked))

    class Stat:
        f_frsize = 1

        @property
        def f_bavail(self):
            return next(free_values)

    monkeypatch.setattr(storage_disk_guard.os, "statvfs", lambda _directory: Stat())
    with storage_disk_guard.reserve_restore_drill_headroom(
        manager,
        required_bytes=required,
        protected_backup_id=str(newest["backup_id"]),
    ) as report:
        remaining = manager.backups()["local"]
        assert report["exclusive_backup_window"] is True
        assert [item["backup_id"] for item in remaining] == [newest["backup_id"]]

    assert report["pruned"] is True
    assert report["retention"]["removed"] == 1


def test_low_headroom_restore_drill_never_waits_for_active_backup(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    backup = manager.create_backup(kind="local", reason="seed")
    acquired = threading.Event()
    release = threading.Event()

    class Stat:
        f_frsize = 1
        f_bavail = 0

    def hold_backup_lock():
        with manager._lock:
            acquired.set()
            release.wait(timeout=2.0)

    thread = threading.Thread(target=hold_backup_lock, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    monkeypatch.setattr(storage_disk_guard.os, "statvfs", lambda _directory: Stat())
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="while backup is active"):
            with storage_disk_guard.reserve_restore_drill_headroom(
                manager,
                required_bytes=Path(backup.database_path).stat().st_size,
                protected_backup_id=backup.backup_id,
            ):
                raise AssertionError("unreachable")
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        thread.join(timeout=1.0)


def test_failed_low_space_replacement_preserves_newest_verified_backup(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    manager.create_backup(kind="local", reason="seed-1")
    time.sleep(0.002)
    manager.create_backup(kind="local", reason="seed-2")
    monkeypatch.setattr(
        storage_disk_guard,
        "_preflight_minimum_verified",
        lambda self, directory: 1,
    )
    manager.db_path = tmp_path / "missing.sqlite3"

    with pytest.raises(FileNotFoundError):
        manager.create_backup(kind="local", reason="expected-failure")

    manifests = manager.backups()["local"]
    assert len(manifests) == 1
    assert (manager.local_dir / manifests[0]["database_file"]).exists()
