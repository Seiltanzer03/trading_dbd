from __future__ import annotations

import sqlite3
from pathlib import Path

from seiltanzer.config import Settings
from seiltanzer.storage_refinement import install_storage_refinement
from seiltanzer.storage_runtime import StorageManager

install_storage_refinement()


def _db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO trades(id,status) VALUES(1,'open')")
        conn.commit()
    finally:
        conn.close()


def test_offhost_directory_without_verified_separation_or_encryption_is_degraded(tmp_path, monkeypatch):
    data = tmp_path / "data"
    offhost = tmp_path / "remote-mount"
    data.mkdir()
    monkeypatch.setenv("SEILTANZER_OFFHOST_BACKUP_DIR", str(offhost))
    monkeypatch.delenv("SEILTANZER_OFFHOST_TARGET_VERIFIED", raising=False)
    monkeypatch.delenv("SEILTANZER_OFFHOST_ENCRYPTION_VERIFIED", raising=False)
    settings = Settings(demo=True, data_dir=str(data))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    manager.create_backup(kind="local", reason="test")
    manager.create_backup(kind="offhost", reason="test")

    status = manager.status()
    assert status["health"] == "DISASTER_RECOVERY_DEGRADED"
    assert status["offhost_target_verified"] is False
    assert status["offhost_encryption_verified"] is False
    assert status["disaster_recovery_verified"] is False
    assert status["rpo_scope"] == "offhost_configured_but_not_fully_verified"


def test_verified_separate_encrypted_offhost_target_can_be_healthy(tmp_path, monkeypatch):
    data = tmp_path / "data"
    offhost = tmp_path / "remote-mount"
    data.mkdir()
    monkeypatch.setenv("SEILTANZER_OFFHOST_BACKUP_DIR", str(offhost))
    monkeypatch.setenv("SEILTANZER_OFFHOST_TARGET_VERIFIED", "true")
    monkeypatch.setenv("SEILTANZER_OFFHOST_ENCRYPTION_VERIFIED", "true")
    settings = Settings(demo=True, data_dir=str(data))
    _db(Path(settings.trades_db))
    manager = StorageManager(settings)
    manager.create_backup(kind="local", reason="test")
    off = manager.create_backup(kind="offhost", reason="test")

    status = manager.status()
    assert status["health"] == "HEALTHY"
    assert status["disaster_recovery_verified"] is True
    assert status["rpo_scope"] == "separate_verified_offhost"
    manifest = next(item for item in manager.backups()["offhost"] if item["backup_id"] == off.backup_id)
    assert manifest["offhost_target_verified"] is True
    assert manifest["encryption_verified"] is True
