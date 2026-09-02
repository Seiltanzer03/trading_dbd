from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from seiltanzer import storage_runtime


def _manager(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    settings = SimpleNamespace(data_dir=str(data_dir), trades_db=str(db_path))
    return storage_runtime.StorageManager(settings, git_commit="test-sha"), db_path


def test_scheduled_backup_refuses_copy_without_headroom(tmp_path, monkeypatch):
    manager, db_path = _manager(tmp_path)
    monkeypatch.setattr(
        storage_runtime.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1, used=1, free=0),
    )

    with pytest.raises(RuntimeError, match="insufficient disk headroom"):
        manager.create_backup(kind="local", reason="scheduled")

    assert db_path.exists()
    assert not list(manager.local_dir.glob(".*.tmp.sqlite3"))
    assert not list(manager.local_dir.glob("*.sqlite3"))


def test_failed_sqlite_copy_removes_partial_temp(tmp_path, monkeypatch):
    manager, db_path = _manager(tmp_path)
    monkeypatch.setattr(
        storage_runtime.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**12, used=0, free=10**12),
    )
    real_connect = storage_runtime.sqlite3.connect

    class FailingSource:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, *args, **kwargs):
            return self._conn.execute(*args, **kwargs)

        def backup(self, _dst, **_kwargs):
            raise OSError("simulated disk-full copy failure")

        def close(self):
            self._conn.close()

    def connect(path, *args, **kwargs):
        conn = real_connect(path, *args, **kwargs)
        if str(path) == str(db_path):
            return FailingSource(conn)
        return conn

    monkeypatch.setattr(storage_runtime.sqlite3, "connect", connect)

    with pytest.raises(OSError, match="simulated disk-full"):
        manager.create_backup(kind="local", reason="scheduled")

    assert db_path.exists()
    assert not list(manager.local_dir.glob(".*.tmp.sqlite3"))
    assert not list(manager.local_dir.glob("*.sqlite3"))
