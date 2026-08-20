"""Bounded read-only SQLite probe used by production readiness.

The full storage audit intentionally remains in ``StorageManager.integrity``.
This module exists so request-time readiness never performs an O(database-size)
``PRAGMA quick_check``/``integrity_check`` against the live production database.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from urllib.parse import quote
from typing import Any

from .storage_runtime import STORAGE_CONTRACT_VERSION


READINESS_DEADLINE_SEC = 2.0
READINESS_LOCK_TIMEOUT_SEC = 0.5


def _size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def bounded_storage_integrity(storage: Any) -> dict[str, Any]:
    """Return a truthful, bounded, read-only storage readiness result.

    This deliberately validates only conditions required to consider storage
    usable *now*: the DB must exist, open read-only, expose a readable SQLite
    schema and contain at least one application table. Latent corruption in
    untouched data pages remains the responsibility of the explicit heavy
    ``full=true`` audit.
    """
    started = time.monotonic()
    db_path = Path(storage.db_path).resolve()
    wal_path = Path(str(db_path) + "-wal")
    db_size = _size(db_path)
    wal_size = _size(wal_path)
    operation = "sqlite_metadata_probe"
    deadline = started + READINESS_DEADLINE_SEC
    detail = "ok"
    ok = False
    timed_out = False
    conn: sqlite3.Connection | None = None

    if not db_path.exists() or not db_path.is_file():
        detail = "database_missing"
    else:
        try:
            # mode=ro makes accidental mutation impossible, including creation of
            # a missing database. query_only is defense in depth for the probe.
            uri_path = quote(str(db_path), safe="/")
            conn = sqlite3.connect(
                f"file:{uri_path}?mode=ro",
                uri=True,
                timeout=READINESS_LOCK_TIMEOUT_SEC,
            )
            conn.execute(f"PRAGMA busy_timeout={int(READINESS_LOCK_TIMEOUT_SEC * 1000)}")
            conn.execute("PRAGMA query_only=ON")

            def _deadline_guard() -> int:
                nonlocal timed_out
                if time.monotonic() >= deadline:
                    timed_out = True
                    return 1
                return 0

            # The probe executes only metadata statements, but retain an SQLite
            # VM deadline as a second bound in addition to the busy timeout.
            conn.set_progress_handler(_deadline_guard, 100)
            schema_version = conn.execute("PRAGMA schema_version").fetchone()
            if schema_version is None:
                detail = "sqlite_schema_unreadable"
            else:
                app_table = conn.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY rowid LIMIT 1"
                ).fetchone()
                if app_table is None:
                    detail = "sqlite_schema_empty"
                else:
                    ok = True
        except sqlite3.DatabaseError as exc:
            if timed_out or time.monotonic() >= deadline:
                detail = "readiness_timeout"
            else:
                detail = f"sqlite_error:{exc}"
        except OSError as exc:
            detail = f"storage_error:{exc}"
        finally:
            if conn is not None:
                conn.close()

    elapsed_ms = int(round((time.monotonic() - started) * 1000.0))
    return {
        "storage_contract_version": STORAGE_CONTRACT_VERSION,
        "checked_ts": time.time(),
        "ok": ok,
        "detail": detail,
        # Keep the existing production_readiness_check.py response contract.
        "check_kind": "quick_check",
        "mode": "readiness",
        "operation": operation,
        "elapsed_ms": elapsed_ms,
        "deadline_ms": int(READINESS_DEADLINE_SEC * 1000),
        "lock_timeout_ms": int(READINESS_LOCK_TIMEOUT_SEC * 1000),
        "database_size_bytes": db_size,
        "wal_size_bytes": wal_size,
    }
