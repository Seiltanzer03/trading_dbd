from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from seiltanzer.config import Settings
from seiltanzer.storage_fast_status_refinement import install_storage_fast_status
from seiltanzer.storage_runtime import StorageManager


def test_bounded_storage_status_never_calls_integrity_scan(tmp_path, monkeypatch):
    settings = Settings(demo=False, data_dir=str(tmp_path))
    conn = sqlite3.connect(settings.trades_db)
    conn.execute("CREATE TABLE IF NOT EXISTS probe(id INTEGER PRIMARY KEY)")
    conn.commit(); conn.close()

    storage = StorageManager(settings)
    storage._startup_integrity = {
        "ok": True, "detail": "ok", "checked_ts": 123.0,
        "contract_version": "seiltanzer-storage-v1",
    }
    app = SimpleNamespace(state=SimpleNamespace(storage=storage))
    install_storage_fast_status(app)

    monkeypatch.setattr(storage, "integrity", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("request-time integrity scan")))
    body = storage.status(engine=object())
    assert body["request_time_integrity_scan"] is False
    assert body["research_health_decoupled"] is True
    assert body["sqlite_integrity"]["check_kind"] == "startup_cached"
    assert body["database_path"] == str(storage.db_path)
