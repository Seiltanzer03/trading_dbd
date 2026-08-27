from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from seiltanzer.config import Settings
from seiltanzer.storage_restore_drill import WRITEBACK_WINDOW_BYTES, run_restore_drill
from seiltanzer.storage_runtime import StorageManager


def _manager_with_backup(tmp_path: Path) -> StorageManager:
    settings = Settings(demo=True, data_dir=str(tmp_path))
    db = Path(settings.trades_db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, payload TEXT)")
        conn.executemany(
            "INSERT INTO trades(id,payload) VALUES(?,?)",
            [(index, "x" * 256) for index in range(1, 2001)],
        )
        conn.commit()
    finally:
        conn.close()
    manager = StorageManager(settings, git_commit="bounded-drill-test")
    manager.create_backup(kind="local", reason="bounded-drill-test")
    return manager


def test_restore_drill_does_not_wait_for_long_storage_manager_lock(tmp_path):
    manager = _manager_with_backup(tmp_path)
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with manager._lock:
            acquired.set()
            release.wait(timeout=3.0)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    try:
        started = time.monotonic()
        report = run_restore_drill(manager)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        thread.join(timeout=1.0)

    assert elapsed < 1.0
    assert report["ok"] is True
    assert report["request_waits_for_backup_manager_lock"] is False
    assert report["repeat_full_integrity_scan_during_drill"] is False
    assert report["source_full_integrity_verified_at_backup_creation"] is True
    assert report["restored_sha256"] == report["backup_sha256"]
    assert report["critical_table_mismatches"] == {}
    assert report["page_cache_pressure_bounded"] is True
    assert report["writeback_window_bytes"] == WRITEBACK_WINDOW_BYTES
    assert isinstance(report["posix_fadvise_available"], bool)
    assert report["headroom_reservation"]["pruned"] is False
    assert report["headroom_reservation"]["exclusive_backup_window"] is False
    assert report["live_database_replaced"] is False


def test_restore_drill_proves_byte_identical_copy_without_general_restore_path(tmp_path, monkeypatch):
    manager = _manager_with_backup(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("full general restore path must not run in bounded readiness drill")

    monkeypatch.setattr(StorageManager, "restore_verified_backup", staticmethod(forbidden))
    report = run_restore_drill(manager)

    assert report["ok"] is True
    assert report["restored_sha256"] == report["backup_sha256"]
    assert "BYTE_IDENTICAL_RESTORE_SHA256" in report["verification_method"]
    assert "BOUNDED_WRITEBACK" in report["verification_method"]
    assert report["critical_table_counts_inherited_by_byte_identity"] is True
    assert report["page_cache_pressure_bounded"] is True
