from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from seiltanzer.config import Settings
from seiltanzer.storage_restore_drill import (
    RESTORE_DRILL_CONTRACT_VERSION,
    last_restore_drill,
    run_restore_drill,
)
from seiltanzer.storage_routes import install_storage_routes
from seiltanzer.storage_runtime import StorageManager


def _minimal_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, status TEXT, result_r REAL)")
        conn.execute("INSERT INTO trades(id,status,result_r) VALUES(1,'closed',1.25)")
        conn.commit()
    finally:
        conn.close()


def _manager_with_backup(tmp_path: Path) -> tuple[StorageManager, Path]:
    settings = Settings(demo=True, data_dir=str(tmp_path))
    live_db = Path(settings.trades_db)
    _minimal_db(live_db)
    manager = StorageManager(settings, git_commit="restore-drill-test")
    manager.create_backup(kind="local", reason="restore-drill-test")
    return manager, live_db


def test_service_restore_drill_restores_disposable_copy_without_touching_live_db(tmp_path):
    manager, live_db = _manager_with_backup(tmp_path)
    live_before = live_db.read_bytes()

    report = run_restore_drill(manager)

    assert report["ok"] is True
    assert report["restore_drill_contract_version"] == RESTORE_DRILL_CONTRACT_VERSION
    assert report["live_database_replaced"] is False
    assert report["drill_destination_kind"] == "disposable_tempfile"
    assert report["critical_table_mismatches"] == {}
    assert report["critical_tables_verified_n"] >= 1
    assert live_db.read_bytes() == live_before
    assert last_restore_drill(manager) == report


def test_service_restore_drill_fails_closed_without_verified_backup(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    live_db = Path(settings.trades_db)
    _minimal_db(live_db)
    manager = StorageManager(settings)
    live_before = live_db.read_bytes()

    with pytest.raises(RuntimeError, match="no verified local backup"):
        run_restore_drill(manager)

    status = last_restore_drill(manager)
    assert status is not None
    assert status["ok"] is False
    assert status["live_database_replaced"] is False
    assert live_db.read_bytes() == live_before


def test_service_restore_drill_rejects_tampered_backup_and_preserves_live_db(tmp_path):
    manager, live_db = _manager_with_backup(tmp_path)
    live_before = live_db.read_bytes()
    latest = manager.backups()["local"][0]
    backup_db = Path(latest["manifest_path"]).parent / latest["database_file"]
    with backup_db.open("ab") as fh:
        fh.write(b"tamper")

    with pytest.raises(ValueError, match="SHA256"):
        run_restore_drill(manager)

    status = last_restore_drill(manager)
    assert status is not None
    assert status["ok"] is False
    assert status["live_database_replaced"] is False
    assert live_db.read_bytes() == live_before


def test_restore_drill_route_runs_inside_service_boundary(tmp_path):
    manager, live_db = _manager_with_backup(tmp_path)
    live_before = live_db.read_bytes()
    app = FastAPI()
    app.state.storage = manager
    install_storage_routes(app)

    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/system/storage/restore-drill"
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/system/storage/restore-drill",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8790),
        "scheme": "http",
        "query_string": b"",
    })
    body = route.endpoint(request)

    assert body["ok"] is True
    assert body["live_database_replaced"] is False
    assert live_db.read_bytes() == live_before


def test_restore_drill_route_rejects_non_loopback_client(tmp_path):
    manager, _ = _manager_with_backup(tmp_path)
    app = FastAPI()
    app.state.storage = manager
    install_storage_routes(app)
    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/system/storage/restore-drill"
    )
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/system/storage/restore-drill",
        "headers": [],
        "client": ("203.0.113.10", 5555),
        "server": ("127.0.0.1", 8790),
        "scheme": "http",
        "query_string": b"",
    })

    with pytest.raises(HTTPException) as exc:
        route.endpoint(request)
    assert exc.value.status_code == 403
