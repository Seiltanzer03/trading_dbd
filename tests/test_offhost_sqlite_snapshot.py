import importlib.util
from pathlib import Path
import sqlite3

import pytest

_spec = importlib.util.spec_from_file_location(
    'offhost_sqlite_snapshot', Path(__file__).resolve().parents[1] / 'scripts/offhost_sqlite_snapshot.py')
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def test_replica_is_checkpointed_and_verified(tmp_path):
    database = tmp_path / 'replica.sqlite3'
    conn = sqlite3.connect(database)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE observations(value TEXT)')
    conn.execute("INSERT INTO observations VALUES('retained')")
    conn.commit()
    result = module.verify_replica(database)
    assert result['database_size_bytes'] == database.stat().st_size
    assert len(result['database_sha256']) == 64
    # Reading only the standalone DB must retain the committed row.
    with sqlite3.connect(f'file:{database}?immutable=1', uri=True) as readonly:
        assert readonly.execute('SELECT value FROM observations').fetchone()[0] == 'retained'
    conn.close()


def test_corrupt_replica_fails_closed(tmp_path):
    database = tmp_path / 'replica.sqlite3'
    database.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError):
        module.verify_replica(database)
