from pathlib import Path
from types import SimpleNamespace

import errno
import sqlite3

import pytest

from seiltanzer import storage_runtime as storage
from seiltanzer import storage_single_slot_rotation as rotation
from seiltanzer import storage_sparse_backup_guard as sparse


def _source(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO trades(payload) VALUES ('authoritative')")
        conn.commit()
    finally:
        conn.close()


def _manager(tmp_path: Path) -> storage.StorageManager:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "trades.db"
    _source(source)
    manager = storage.StorageManager(
        SimpleNamespace(data_dir=str(data), trades_db=str(source)),
        git_commit="sparse-guard-test-sha",
    )
    return manager


def test_sparse_guarded_backup_is_verified_without_source_write(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    source_sha = storage._sha256(manager.db_path)
    plenty = sparse._guard.MIN_BACKUP_HEADROOM_BYTES + 512 * 1024 * 1024
    monkeypatch.setattr(sparse, "_available_bytes", lambda _directory: plenty)

    result = sparse._create_sparse_guarded_backup(
        manager, kind="local", reason="prestart"
    )

    backup = Path(result.database_path)
    manifest = storage._read_json(Path(result.manifest_path))
    assert result.verified is True
    assert storage._sqlite_integrity(backup, full=True) == (True, "ok")
    assert storage._sha256(manager.db_path) == source_sha
    assert manager._prestart_integrity_ready is True
    assert manifest is not None
    assert manifest["verified"] is True
    assert manifest["snapshot_mode"] == "sqlite_backup_sparse_guarded"
    assert manifest["git_commit"] == "sparse-guard-test-sha"
    assert manifest["filesystem_allocated_bytes"] == sparse._allocated_bytes(backup)
    assert manifest["database_size_bytes"] == backup.stat().st_size
    assert manifest["database_sha256"] == storage._sha256(backup)


def test_sparse_guard_aborts_and_removes_temp_before_protected_headroom(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path)
    high = (
        sparse._guard.MIN_BACKUP_HEADROOM_BYTES
        + sparse.PROGRESS_MARGIN_BYTES
        + 1024 * 1024
    )
    low = sparse._guard.MIN_BACKUP_HEADROOM_BYTES + sparse.PROGRESS_MARGIN_BYTES - 1
    calls = {"n": 0}

    def available(_directory):
        calls["n"] += 1
        return high if calls["n"] == 1 else low

    monkeypatch.setattr(sparse, "_available_bytes", available)
    with pytest.raises(OSError) as exc:
        sparse._create_sparse_guarded_backup(
            manager, kind="local", reason="prestart"
        )
    assert exc.value.errno == errno.ENOSPC
    assert list(manager.local_dir.glob(".*.tmp.sqlite3")) == []
    assert manager._verified_manifests(manager.local_dir) == []
    assert storage._sqlite_integrity(manager.db_path, full=False) == (True, "ok")


def test_single_slot_never_uses_sparse_logical_size_as_reclaimable_space(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path)
    result = manager.create_backup(kind="local", reason="prestart")
    backup = Path(result.database_path)
    logical = backup.stat().st_size
    assert logical > 0

    # Reproduce production semantics: a backup can report a multi-GiB logical
    # st_size while unlink returns only its allocated blocks.  The old code used
    # st_size here and could destroy the last recovery point before discovering
    # that statvfs barely changed.
    monkeypatch.setattr(rotation, "_allocated_bytes", lambda _path: 4096)
    available = 100_000
    required = available + 8192
    slot = rotation._validated_single_slot(
        manager,
        manager.local_dir,
        required_bytes=required,
        available_bytes=available,
    )

    assert slot is None
    assert backup.is_file()
    assert Path(result.manifest_path).is_file()


def test_allocated_bytes_is_never_greater_than_logical_for_sparse_probe(tmp_path):
    probe = tmp_path / "sparse-probe.bin"
    with probe.open("wb") as handle:
        handle.seek(32 * 1024 * 1024 - 1)
        handle.write(b"\0")
    assert probe.stat().st_size == 32 * 1024 * 1024
    assert sparse._allocated_bytes(probe) <= probe.stat().st_size
