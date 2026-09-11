import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

_spec = importlib.util.spec_from_file_location(
    'verify_immutable_snapshot', Path(__file__).resolve().parents[1] / 'scripts/verify_immutable_snapshot.py')
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def create_snapshot(tmp_path):
    database = tmp_path / 'snapshot.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE evidence(value TEXT)')
        conn.execute("INSERT INTO evidence VALUES('retained')")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    manifest = tmp_path / 'snapshot.db.manifest.json'
    manifest.write_text(json.dumps({'database_size_bytes': database.stat().st_size,
                                    'database_sha256': digest, 'source': 'TEST'}))
    return database, manifest


def test_verified_input_is_made_read_only(tmp_path):
    database, manifest = create_snapshot(tmp_path)
    assert module.verify(database, manifest)['source'] == 'TEST'
    assert database.stat().st_mode & 0o222 == 0


def test_hash_or_mutable_sidecar_fails_closed(tmp_path):
    database, manifest = create_snapshot(tmp_path)
    payload = json.loads(manifest.read_text()); payload['database_sha256'] = '0' * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match='SHA256'):
        module.verify(database, manifest)
    database, manifest = create_snapshot(tmp_path / 'again') if False else (database, manifest)
    Path(str(database) + '-wal').touch()
    with pytest.raises(RuntimeError, match='sidecars'):
        module.verify(database, manifest)
