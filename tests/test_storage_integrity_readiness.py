from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.storage_readiness import bounded_storage_integrity
from seiltanzer.storage_routes import install_storage_routes


def _db(path: Path, *, payload_bytes: int = 0) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE probe(id INTEGER PRIMARY KEY, payload BLOB)")
        if payload_bytes:
            conn.execute("INSERT INTO probe(payload) VALUES(zeroblob(?))", (payload_bytes,))
        conn.commit()
    finally:
        conn.close()


def test_full_false_route_never_calls_heavy_integrity_path(tmp_path):
    db = tmp_path / "trades.db"
    _db(db)

    def _heavy_integrity(**_kwargs):
        raise AssertionError("full=false must not call StorageManager.integrity")

    storage = SimpleNamespace(db_path=db, integrity=_heavy_integrity)
    app = FastAPI()
    app.state.storage = storage
    app.state.engine = object()
    install_storage_routes(app)

    response = TestClient(app).get("/api/system/storage/integrity?full=false")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["check_kind"] == "quick_check"
    assert body["mode"] == "readiness"
    assert body["operation"] == "sqlite_metadata_probe"


def test_full_true_keeps_explicit_heavy_audit_path(tmp_path):
    calls: list[bool] = []

    def _heavy_integrity(*, full: bool = False):
        calls.append(full)
        return {"ok": True, "check_kind": "integrity_check", "detail": "ok"}

    storage = SimpleNamespace(db_path=tmp_path / "unused.db", integrity=_heavy_integrity)
    app = FastAPI()
    app.state.storage = storage
    app.state.engine = object()
    install_storage_routes(app)

    body = TestClient(app).get("/api/system/storage/integrity?full=true").json()
    assert calls == [True]
    assert body["ok"] is True
    assert body["check_kind"] == "integrity_check"


def test_readiness_probe_is_read_only_and_preserves_contract(tmp_path):
    db = tmp_path / "trades.db"
    _db(db)
    before = db.read_bytes()
    before_mtime = db.stat().st_mtime_ns
    before_names = sorted(path.name for path in tmp_path.iterdir())

    body = bounded_storage_integrity(SimpleNamespace(db_path=db))

    assert body["ok"] is True
    assert body["detail"] == "ok"
    assert body["check_kind"] == "quick_check"
    assert body["storage_contract_version"]
    assert body["checked_ts"] > 0
    assert body["deadline_ms"] > 0
    assert body["lock_timeout_ms"] < body["deadline_ms"]
    assert body["database_size_bytes"] == db.stat().st_size
    assert db.read_bytes() == before
    assert db.stat().st_mtime_ns == before_mtime
    assert sorted(path.name for path in tmp_path.iterdir()) == before_names


def test_missing_and_obviously_corrupt_storage_fail_closed(tmp_path):
    missing = bounded_storage_integrity(SimpleNamespace(db_path=tmp_path / "missing.db"))
    assert missing["ok"] is False
    assert missing["detail"] == "database_missing"

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not-a-sqlite-database" * 64)
    body = bounded_storage_integrity(SimpleNamespace(db_path=corrupt))
    assert body["ok"] is False
    assert body["detail"].startswith("sqlite_error:")


def test_large_database_does_not_turn_readiness_into_size_proportional_scan(tmp_path):
    db = tmp_path / "large.db"
    _db(db, payload_bytes=16 * 1024 * 1024)
    assert db.stat().st_size > 8 * 1024 * 1024

    started = time.monotonic()
    body = bounded_storage_integrity(SimpleNamespace(db_path=db))
    wall_sec = time.monotonic() - started

    assert body["ok"] is True
    assert body["operation"] == "sqlite_metadata_probe"
    # This is intentionally loose: the architectural assertion above is the
    # primary guard; the wall bound only catches accidental large scans/hangs.
    assert body["elapsed_ms"] <= body["deadline_ms"] + 1000
    assert wall_sec < 4.0


def test_readiness_lock_wait_is_bounded_and_fails_closed(tmp_path):
    db = tmp_path / "locked.db"
    _db(db)
    writer = sqlite3.connect(db, timeout=1)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        body = bounded_storage_integrity(SimpleNamespace(db_path=db))
        wall_sec = time.monotonic() - started
    finally:
        writer.rollback()
        writer.close()

    assert body["ok"] is False
    assert body["detail"].startswith("sqlite_error:") or body["detail"] == "readiness_timeout"
    assert wall_sec < 4.0
    assert body["elapsed_ms"] <= body["deadline_ms"] + 1000
