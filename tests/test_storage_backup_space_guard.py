from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from seiltanzer import storage_backup_space_guard as guard


def _settings(tmp_path):
    db_path = tmp_path / "trades.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, status TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO trades(status) VALUES ('closed')")
        connection.commit()
    finally:
        connection.close()
    return SimpleNamespace(data_dir=str(tmp_path), trades_db=str(db_path))


def test_backup_rejects_before_allocating_when_headroom_is_insufficient(
    tmp_path, monkeypatch,
):
    settings = _settings(tmp_path)
    manager = guard.StorageManager(settings)
    monkeypatch.setenv(guard.BACKUP_HEADROOM_ENV, "1024")
    monkeypatch.setattr(
        guard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=0),
    )

    with pytest.raises(guard.BackupSpaceError, match="backup_space_insufficient"):
        manager.create_backup(kind="local", reason="scheduled")

    assert not list(manager.local_dir.glob(".*.tmp.sqlite3"))
    assert not list(manager.local_dir.glob("*.manifest.json"))
    assert not list(manager.local_dir.glob("*.sqlite3"))


def test_failed_backup_removes_incomplete_temp_copy(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = guard.StorageManager(settings)
    monkeypatch.setenv(guard.BACKUP_HEADROOM_ENV, "0")
    monkeypatch.setattr(
        guard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=10**12),
    )

    def fail_after_temp(self, *, kind="local", reason="scheduled"):
        directory = self._backup_dir(kind)
        (directory / ".simulated.tmp.sqlite3").write_bytes(b"partial")
        raise OSError("simulated backup failure")

    monkeypatch.setattr(guard._BaseStorageManager, "create_backup", fail_after_temp)

    with pytest.raises(OSError, match="simulated backup failure"):
        manager.create_backup(kind="local", reason="scheduled")

    assert not list(manager.local_dir.glob(".*.tmp.sqlite3"))


def test_successful_backup_preserves_canonical_verified_snapshot(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    manager = guard.StorageManager(settings, git_commit="unit-test")
    monkeypatch.setenv(guard.BACKUP_HEADROOM_ENV, "0")

    result = manager.create_backup(kind="local", reason="scheduled")

    assert result.verified is True
    assert result.backup_id
    assert not list(manager.local_dir.glob(".*.tmp.sqlite3"))
    assert len(list(manager.local_dir.glob("*.manifest.json"))) == 1
    assert len(list(manager.local_dir.glob("*.sqlite3"))) == 1
