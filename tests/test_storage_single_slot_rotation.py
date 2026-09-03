from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from pathlib import Path

import pytest

from seiltanzer.config import Settings
from seiltanzer.storage_disk_guard import MIN_BACKUP_HEADROOM_BYTES, install_storage_disk_guard
from seiltanzer.storage_refinement import install_storage_refinement
from seiltanzer.storage_runtime import StorageManager
from seiltanzer import storage_single_slot_rotation as rotation


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


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextlib.contextmanager
def _installed_rotation():
    original_create = StorageManager.create_backup
    marker_name = "_storage_single_slot_rotation_version"
    had_marker = hasattr(StorageManager, marker_name)
    original_marker = getattr(StorageManager, marker_name, None)
    if had_marker:
        delattr(StorageManager, marker_name)
    try:
        rotation.install_storage_single_slot_rotation()
        yield
    finally:
        StorageManager.create_backup = original_create
        if had_marker:
            setattr(StorageManager, marker_name, original_marker)
        elif hasattr(StorageManager, marker_name):
            delattr(StorageManager, marker_name)


def _single_slot_stat(seed_db: Path, *, low_free: int, high_free: int):
    class Stat:
        f_frsize = 1

        @property
        def f_bavail(self):
            return low_free if seed_db.exists() else high_free

    return Stat


def test_scheduled_backup_replaces_only_verified_slot_when_it_unlocks_headroom(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    live = Path(settings.trades_db)
    _db(live)
    manager = StorageManager(settings, git_commit="same-sha")
    seed = manager.create_backup(kind="local", reason="prestart")
    seed_db = Path(seed.database_path)
    seed_manifest = Path(seed.manifest_path)
    live_sha = _sha(live)
    required = live.stat().st_size + MIN_BACKUP_HEADROOM_BYTES
    low_free = required - seed_db.stat().st_size + 1
    Stat = _single_slot_stat(seed_db, low_free=low_free, high_free=required + 4096)
    monkeypatch.setattr(rotation.os, "statvfs", lambda _directory: Stat())
    # The wrapped disk guard uses the same os module object.

    with _installed_rotation():
        result = manager.create_backup(kind="local", reason="scheduled")

    assert result.backup_id != seed.backup_id
    assert Path(result.database_path).is_file()
    assert Path(result.manifest_path).is_file()
    assert not seed_db.exists()
    assert not seed_manifest.exists()
    assert _sha(live) == live_sha
    manifests = manager.backups()["local"]
    assert [item["backup_id"] for item in manifests] == [result.backup_id]
    actions = [item["action"] for item in manager._recovery_actions]
    assert "replace_verified_single_slot_backup" in actions
    assert "replace_verified_single_slot_backup_complete" in actions


def test_prestart_can_replace_old_sha_slot_without_touching_authoritative_db(
    tmp_path, monkeypatch
):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    live = Path(settings.trades_db)
    _db(live)
    manager = StorageManager(settings, git_commit="old-sha")
    seed = manager.create_backup(kind="local", reason="prestart")
    seed_db = Path(seed.database_path)
    live_sha = _sha(live)
    manager.git_commit = "new-sha"
    manager._prestart_integrity_ready = False
    manager._startup_integrity = None
    required = live.stat().st_size + MIN_BACKUP_HEADROOM_BYTES
    low_free = required - seed_db.stat().st_size + 1
    Stat = _single_slot_stat(seed_db, low_free=low_free, high_free=required + 4096)
    monkeypatch.setattr(rotation.os, "statvfs", lambda _directory: Stat())

    with _installed_rotation():
        result = manager.create_backup(kind="local", reason="prestart")

    newest = manager.backups()["local"][0]
    assert result.backup_id != seed.backup_id
    assert newest["backup_id"] == result.backup_id
    assert newest["git_commit"] == "new-sha"
    assert manager._prestart_integrity_ready is True
    assert manager._startup_integrity["ok"] is True
    assert _sha(live) == live_sha


def test_tampered_verified_slot_is_never_deleted(tmp_path, monkeypatch):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    live = Path(settings.trades_db)
    _db(live)
    manager = StorageManager(settings, git_commit="sha")
    seed = manager.create_backup(kind="local", reason="seed")
    seed_db = Path(seed.database_path)
    seed_manifest = Path(seed.manifest_path)
    with seed_db.open("r+b") as handle:
        handle.seek(max(0, seed_db.stat().st_size - 32))
        original = handle.read(1)
        handle.seek(-1, 1)
        handle.write(bytes([original[0] ^ 0x01]))
    required = live.stat().st_size + MIN_BACKUP_HEADROOM_BYTES
    low_free = required - seed_db.stat().st_size + 1
    Stat = _single_slot_stat(seed_db, low_free=low_free, high_free=required + 4096)
    monkeypatch.setattr(rotation.os, "statvfs", lambda _directory: Stat())

    with _installed_rotation(), pytest.raises(RuntimeError, match="SHA256 mismatch"):
        manager.create_backup(kind="local", reason="scheduled")

    assert seed_db.is_file()
    assert seed_manifest.is_file()


def test_failed_source_quick_check_preserves_verified_slot(tmp_path, monkeypatch):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    live = Path(settings.trades_db)
    _db(live)
    manager = StorageManager(settings, git_commit="sha")
    seed = manager.create_backup(kind="local", reason="seed")
    seed_db = Path(seed.database_path)
    seed_manifest = Path(seed.manifest_path)
    required = live.stat().st_size + MIN_BACKUP_HEADROOM_BYTES
    low_free = required - seed_db.stat().st_size + 1
    Stat = _single_slot_stat(seed_db, low_free=low_free, high_free=required + 4096)
    monkeypatch.setattr(rotation.os, "statvfs", lambda _directory: Stat())
    monkeypatch.setattr(
        rotation._s,
        "_sqlite_integrity",
        lambda _path, full=False: (False, "simulated source failure"),
    )

    with _installed_rotation(), pytest.raises(RuntimeError, match="quick_check failed"):
        manager.create_backup(kind="local", reason="scheduled")

    assert seed_db.is_file()
    assert seed_manifest.is_file()


def test_slot_is_preserved_when_reclaim_cannot_make_enough_space(tmp_path, monkeypatch):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    live = Path(settings.trades_db)
    _db(live)
    manager = StorageManager(settings, git_commit="sha")
    seed = manager.create_backup(kind="local", reason="seed")
    seed_db = Path(seed.database_path)
    seed_manifest = Path(seed.manifest_path)

    class Stat:
        f_frsize = 1
        f_bavail = 0

    monkeypatch.setattr(rotation.os, "statvfs", lambda _directory: Stat())

    with _installed_rotation(), pytest.raises(OSError, match="single-slot backup headroom"):
        manager.create_backup(kind="local", reason="scheduled")

    assert seed_db.is_file()
    assert seed_manifest.is_file()
