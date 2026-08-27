from __future__ import annotations

import os
import sqlite3
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
    free_values = iter((reserved - 1, reserved))

    class Stat:
        f_frsize = 1

        @property
        def f_bavail(self):
            return next(free_values)

    monkeypatch.setattr(storage_disk_guard.os, "statvfs", lambda _directory: Stat())
    report = storage_disk_guard.reserve_restore_drill_headroom(
        manager,
        required_bytes=required,
        protected_backup_id=str(newest["backup_id"]),
    )

    remaining = manager.backups()["local"]
    assert report["pruned"] is True
    assert report["retention"]["removed"] == 1
    assert [item["backup_id"] for item in remaining] == [newest["backup_id"]]


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
