"""Explicit authoritative SQLite identity for production and diagnostics."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path


DATABASE_AUTHORITY_VERSION = "database-authority-v1"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.flush(); os.fsync(fh.fileno())
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def install_database_authority(app) -> dict:
    if getattr(app.state, "database_authority", None):
        return app.state.database_authority
    engine = app.state.engine
    settings = app.state.settings
    conn, lock = engine.passive._conn, engine.passive._lock
    db_path = Path(settings.trades_db).resolve()
    with lock, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS database_authority(
                id INTEGER PRIMARY KEY CHECK(id=1),
                database_uuid TEXT NOT NULL UNIQUE,
                contract_version TEXT NOT NULL,
                created_ts REAL NOT NULL,
                authoritative_path TEXT NOT NULL,
                last_open_ts REAL NOT NULL
            )""")
        row = conn.execute("SELECT * FROM database_authority WHERE id=1").fetchone()
        if row is None:
            database_uuid = str(uuid.uuid4())
            created = time.time()
            conn.execute(
                "INSERT INTO database_authority(id,database_uuid,contract_version,created_ts,"
                "authoritative_path,last_open_ts) VALUES(1,?,?,?,?,?)",
                (database_uuid, DATABASE_AUTHORITY_VERSION, created, str(db_path), time.time()))
        else:
            database_uuid = str(row["database_uuid"])
            created = float(row["created_ts"])
            conn.execute(
                "UPDATE database_authority SET contract_version=?,authoritative_path=?,last_open_ts=? "
                "WHERE id=1", (DATABASE_AUTHORITY_VERSION, str(db_path), time.time()))
    parent_legacy = db_path.parent.parent / "trades.db" if db_path.parent.name == "data" else None
    legacy = []
    if parent_legacy is not None and parent_legacy.resolve() != db_path and parent_legacy.exists():
        legacy.append({"path": str(parent_legacy.resolve()), "classification": "NON_AUTHORITATIVE_LEGACY"})
    payload = {
        "contract_version": DATABASE_AUTHORITY_VERSION,
        "database_uuid": database_uuid, "created_ts": created,
        "authoritative_database_path": str(db_path), "last_open_ts": time.time(),
        "legacy_databases": legacy,
    }
    authority_file = Path(settings.data_dir).resolve() / "database_authority.json"
    _atomic_json(authority_file, payload)
    app.state.database_authority = payload
    app.state.database_authority_file = str(authority_file)
    app.add_api_route(
        "/api/system/database-authority",
        lambda: dict(app.state.database_authority),
        methods=["GET"], name="database_authority")
    return payload
