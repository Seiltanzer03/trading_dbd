from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/production_ede_v13_audit.py"
SPEC = importlib.util.spec_from_file_location("production_ede_v13_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_read_only_runtime_does_not_create_wal_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "snapshot.sqlite3"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE sample(value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (7)")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    sidecars = [Path(str(database) + suffix) for suffix in ("-wal", "-shm")]
    assert not any(path.exists() for path in sidecars)

    runtime = MODULE.ReadOnlyRuntime(database)
    try:
        assert runtime._conn.execute("SELECT value FROM sample").fetchone()[0] == 7
    finally:
        runtime.close()

    assert not any(path.exists() for path in sidecars)
