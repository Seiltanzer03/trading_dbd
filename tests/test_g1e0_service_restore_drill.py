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


def _request(path: str, *, method: str = "GET", host: str = "127.0.0.1") -> Request:
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "client": (host, 12345),
        "server": ("127.0.0.1", 8790),
        "scheme": "http",
        "query_string": b"",
    })


def _route(app: FastAPI, path: str):
    return next(route for route in app.routes if getattr(route, "path", None) == path)


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


def test_service_restore_drill_reports_failed_cache_eviction(tmp_path, monkeypatch):
    manager, _ = _manager_with_backup(tmp_path)
    monkeypatch.setattr(
        "seiltanzer.storage_restore_drill._drop_file_cache",
        lambda *_args: False,
    )

    report = run_restore_drill(manager)

    assert report["ok"] is True
    assert report["page_cache_pressure_bounded"] is False


def test_production_readiness_requires_verified_cache_eviction():
    readiness = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "production_readiness_check.py"
    ).read_text(encoding="utf-8")
    assert 'drill.get("page_cache_pressure_bounded") is True' in readiness


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

    body = _route(app, "/api/system/storage/restore-drill").endpoint(
        _request("/api/system/storage/restore-drill", method="POST")
    )

    assert body["ok"] is True
    assert body["live_database_replaced"] is False
    assert live_db.read_bytes() == live_before


def test_restore_drill_status_is_fast_semantic_readback_before_and_after_success(tmp_path):
    manager, _ = _manager_with_backup(tmp_path)
    app = FastAPI()
    app.state.storage = manager
    install_storage_routes(app)
    status_route = _route(app, "/api/system/storage/restore-drill/status")

    before = status_route.endpoint(_request("/api/system/storage/restore-drill/status"))
    assert before == {
        "restore_drill_contract_version": RESTORE_DRILL_CONTRACT_VERSION,
        "status": "NEVER_RUN",
        "last_restore_drill": None,
    }

    report = run_restore_drill(manager)
    after = status_route.endpoint(_request("/api/system/storage/restore-drill/status"))
    assert after["status"] == "PASS"
    assert after["last_restore_drill"] == report
    assert after["last_restore_drill"]["live_database_replaced"] is False


def test_restore_drill_status_reports_failed_attempt_without_scanning_storage(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _minimal_db(Path(settings.trades_db))
    manager = StorageManager(settings)
    app = FastAPI()
    app.state.storage = manager
    install_storage_routes(app)

    with pytest.raises(RuntimeError):
        run_restore_drill(manager)

    body = _route(app, "/api/system/storage/restore-drill/status").endpoint(
        _request("/api/system/storage/restore-drill/status")
    )
    assert body["status"] == "FAIL"
    assert body["last_restore_drill"]["ok"] is False
    assert body["last_restore_drill"]["live_database_replaced"] is False


def test_restore_drill_routes_reject_non_loopback_client(tmp_path):
    manager, _ = _manager_with_backup(tmp_path)
    app = FastAPI()
    app.state.storage = manager
    install_storage_routes(app)

    for path, method in (
        ("/api/system/storage/restore-drill", "POST"),
        ("/api/system/storage/restore-drill/status", "GET"),
    ):
        route = _route(app, path)
        with pytest.raises(HTTPException) as exc:
            route.endpoint(_request(path, method=method, host="203.0.113.10"))
        assert exc.value.status_code == 403
