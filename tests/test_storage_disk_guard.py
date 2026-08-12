from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from seiltanzer.config import Settings
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
