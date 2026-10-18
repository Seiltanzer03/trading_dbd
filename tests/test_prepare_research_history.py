"""Actual research preparation chooses a complete, verified reader database."""
import hashlib
import importlib
import json
import sqlite3
from contextlib import closing

import pytest

from test_sqlite_history_current_union import _hot, _current_proof
from test_verify_immutable_snapshot import create_snapshot


def test_plain_verified_snapshot_requires_no_cloud_access_or_second_copy(tmp_path):
    database, manifest = create_snapshot(tmp_path)
    class NoCloud:
        def get_object(self, **kwargs):
            raise AssertionError('Plain snapshot should not read cloud history')
    prep = importlib.import_module('scripts.prepare_research_history')
    result = prep.prepare_research_snapshot(database, manifest, tmp_path/'unused.sqlite3', client=NoCloud())
    assert result['database'] == str(database.resolve())
    assert result['manifest'] == str(manifest.resolve())
    assert result['history_union_prepared'] is False
    assert not (tmp_path/'unused.sqlite3').exists()


def test_research_readers_receive_complete_history_and_current_hot_outcomes(tmp_path):
    _, hot, report, _, client = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            conn.execute("UPDATE passive_market_observations SET resolution_status='resolved' WHERE rowid=110")
    proof = _current_proof(hot, report)
    manifest = tmp_path/'hot.manifest.json'; manifest.write_text(json.dumps(proof['storage_manifest']))
    prep = importlib.import_module('scripts.prepare_research_history')
    result = prep.prepare_research_snapshot(hot, manifest, tmp_path/'research.sqlite3', client=client)
    assert result['history_union_prepared'] is True
    with closing(sqlite3.connect(result['database'])) as conn:
        assert conn.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone() == (14,)
        assert conn.execute('SELECT resolution_status FROM passive_market_observations WHERE rowid=110').fetchone() == ('resolved',)
        assert conn.execute("SELECT 1 FROM sqlite_schema WHERE name='hot_history_storage_state'").fetchone() is None
    output_manifest = json.loads(__import__('pathlib').Path(result['manifest']).read_text())
    assert output_manifest['source'] == 'VERIFIED_CURRENT_HOT_COLD_UNION'
    assert output_manifest['raw_hot_database_sha256'] == proof['database_sha256']
    assert output_manifest['production_authority'] is False
    assert 'cold_history_catalog_sha256' not in output_manifest
    assert hashlib.sha256(hot.read_bytes()).hexdigest() == proof['database_sha256']


def test_bad_manifest_history_pin_fails_before_catalog_fetch(tmp_path):
    _, hot, result, _, _ = _hot(tmp_path)
    manifest = tmp_path/'bad.json'
    manifest.write_text(json.dumps(dict(result['hot_restore_proof']['storage_manifest'], cold_history_catalog_sha256='0'*64)))
    class NoCloud:
        def get_object(self, **kwargs):
            raise AssertionError('Invalid pin reached cloud')
    prep = importlib.import_module('scripts.prepare_research_history')
    with pytest.raises(ValueError, match='history pin'):
        prep.prepare_research_snapshot(hot, manifest, tmp_path/'forbidden.sqlite3', client=NoCloud())
    assert not (tmp_path/'forbidden.sqlite3').exists()


def test_research_output_manifest_collision_is_not_overwritten(tmp_path):
    _, hot, result, _, client = _hot(tmp_path)
    manifest = tmp_path/'hot.json'; manifest.write_text(json.dumps(result['hot_restore_proof']['storage_manifest']))
    destination = tmp_path/'research.sqlite3'
    collision = tmp_path/'research.sqlite3.manifest.json'; collision.write_text('keep unrelated file')
    prep = importlib.import_module('scripts.prepare_research_history')
    with pytest.raises(FileExistsError):
        prep.prepare_research_snapshot(hot, manifest, destination, client=client)
    assert collision.read_text() == 'keep unrelated file'
    assert not destination.exists()
