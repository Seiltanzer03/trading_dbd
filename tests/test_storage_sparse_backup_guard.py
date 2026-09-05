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


def test_sparse_file_clone_preserves_bytes_and_holes(tmp_path):
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    logical = 32 * 1024 * 1024
    with source.open("wb") as handle:
        handle.write(b"HEAD")
        handle.seek(logical - 4)
        handle.write(b"TAIL")

    before_sha = storage._sha256(source)
    result = sparse._copy_sparse_file(
        source,
        destination,
        directory=tmp_path,
        abort_floor=1,
    )

    assert destination.stat().st_size == logical
    assert storage._sha256(destination) == before_sha
    assert result["logical_size_bytes"] == logical
    assert result["filesystem_allocated_bytes"] == sparse._allocated_bytes(destination)
    if getattr(destination.stat(), "st_blocks", None) is not None:
        assert sparse._allocated_bytes(destination) < logical
        if result["copy_method"] == "seek_hole":
            assert sparse._allocated_bytes(destination) <= (
                sparse._allocated_bytes(source) + 1024 * 1024
            )



def test_quiescent_sparse_clone_replays_wal_only_into_backup(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    writer = sqlite3.connect(manager.db_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO trades(payload) VALUES ('committed-in-wal')")
        writer.commit()

        source_sha = storage._sha256(manager.db_path)
        source_signature = sparse._file_signature(manager.db_path)
        wal = Path(str(manager.db_path) + "-wal")
        wal_signature = sparse._file_signature(wal)
        assert wal_signature is not None
        plenty = sparse._guard.MIN_BACKUP_HEADROOM_BYTES + 4 * 1024 * 1024 * 1024
        monkeypatch.setattr(sparse, "_available_bytes", lambda _directory: plenty)

        result = sparse._create_quiescent_sparse_clone_backup(
            manager,
            kind="local",
            reason="prestart",
        )

        backup = Path(result.database_path)
        manifest = storage._read_json(Path(result.manifest_path))
        clone = sqlite3.connect(backup)
        try:
            rows = [
                row[0]
                for row in clone.execute(
                    "SELECT payload FROM trades ORDER BY id"
                ).fetchall()
            ]
            assert rows == ["authoritative", "committed-in-wal"]
            assert clone.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            clone.close()

        assert storage._sha256(manager.db_path) == source_sha
        assert sparse._file_signature(manager.db_path) == source_signature
        assert sparse._file_signature(wal) == wal_signature
        assert not Path(str(backup) + "-wal").exists()
        assert manifest is not None
        assert manifest["verified"] is True
        assert manifest["snapshot_mode"] == "quiescent_sparse_clone_checkpointed"
        assert manifest["source_wal_present"] is True
        assert manifest["database_sha256"] == storage._sha256(backup)
        assert manager._prestart_integrity_ready is True
    finally:
        writer.close()


def test_quiescent_clone_aborts_if_source_signature_changes(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    plenty = sparse._guard.MIN_BACKUP_HEADROOM_BYTES + 4 * 1024 * 1024 * 1024
    monkeypatch.setattr(sparse, "_available_bytes", lambda _directory: plenty)
    original = sparse._verify_source_quiescent
    calls = {"n": 0}

    def changed(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(
                "authoritative database changed during quiescent sparse clone"
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(sparse, "_verify_source_quiescent", changed)

    with pytest.raises(RuntimeError, match="changed during quiescent sparse clone"):
        sparse._create_quiescent_sparse_clone_backup(
            manager,
            kind="local",
            reason="prestart",
        )

    assert manager._verified_manifests(manager.local_dir) == []
    assert list(manager.local_dir.glob(".*.tmp.sqlite3")) == []
    assert storage._sqlite_integrity(manager.db_path, full=False) == (True, "ok")
