"""History pins survive the actual replica/backup/restore transport contracts."""
import copy
import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from seiltanzer import history_storage_state as state
from test_sqlite_history_current_union import _hot
from test_offhost_sqlite_snapshot import module as replica
from test_restore_verified_snapshot import Client, MODULE as restore, _fixture


def test_actual_replica_manifest_preserves_embedded_history_pins(tmp_path):
    _, hot, result, _, _ = _hot(tmp_path)
    manifest = replica.verify_replica(hot)
    for key in state.PIN_FIELDS:
        assert manifest[key] == result['hot_restore_proof']['storage_manifest'][key]


def test_renumbered_hot_rowids_cannot_receive_a_valid_backup_pin(tmp_path):
    _, hot, _, _, _ = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            conn.execute('UPDATE passive_market_observations SET rowid=111 WHERE rowid=110')
    with pytest.raises(ValueError, match='rowid'):
        replica.verify_replica(hot)


def test_marked_hot_database_refuses_vacuum_fallback_without_source_writes(tmp_path):
    from types import SimpleNamespace
    from seiltanzer import storage_disk_guard as guard
    from seiltanzer.storage_runtime import StorageManager
    _, hot, result, _, _ = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            conn.execute('CREATE TABLE padding(payload BLOB)')
            conn.executemany('INSERT INTO padding VALUES(?)', [(b'X'*16384,)]*30)
        with conn:
            conn.execute('DELETE FROM padding')
    manager = StorageManager(SimpleNamespace(data_dir=str(tmp_path/'compact-runtime'), trades_db=str(hot)),
                             git_commit='history-compact-test')
    digest = hashlib.sha256(hot.read_bytes()).hexdigest()
    with pytest.raises(OSError, match='rowid-preserving'):
        guard._compact_snapshot_plan(hot)
    # Direct callers cannot bypass the planner either.
    with pytest.raises(OSError, match='rowid-preserving'):
        guard._create_compact_backup(manager, kind='local', reason='history-test', plan={})
    assert hashlib.sha256(hot.read_bytes()).hexdigest() == digest
    assert not list(manager.local_dir.glob('*.sqlite3'))


def test_backup_refinement_carries_the_embedded_catalog_pin(tmp_path):
    from seiltanzer.config import Settings
    from seiltanzer.storage_refinement import install_storage_refinement
    from seiltanzer.storage_runtime import StorageManager
    install_storage_refinement()
    _, hot, result, _, _ = _hot(tmp_path)
    settings = Settings(demo=True, data_dir=str(tmp_path/'runtime'))
    Path(settings.trades_db).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.trades_db).write_bytes(hot.read_bytes())
    manager = StorageManager(settings, git_commit='history-test')
    backup = manager.create_backup(kind='local', reason='history-pin')
    manifest = json.loads(Path(backup.manifest_path).read_text())
    for key in state.PIN_FIELDS:
        assert manifest[key] == result['hot_restore_proof']['storage_manifest'][key]


def test_uploader_rejects_bad_history_pin_before_any_cloud_call(tmp_path, monkeypatch):
    import boto3
    from test_upload_verified_snapshot import module as upload
    _, hot, result, _, _ = _hot(tmp_path)
    manifest = dict(result['hot_restore_proof']['storage_manifest'], cold_history_catalog_sha256='0'*64)
    path = tmp_path/'upload-manifest.json'; path.write_text(json.dumps(manifest))
    def forbidden_client(*args, **kwargs):
        raise AssertionError('Unverified history reached cloud client creation')
    monkeypatch.setattr(boto3, 'client', forbidden_client)
    with pytest.raises(ValueError, match='history pin'):
        upload.upload(hot, path, bucket=state.BUCKET, key='unused')


def test_immutable_input_check_rejects_a_mismatched_history_pin(tmp_path):
    from scripts.verify_immutable_snapshot import verify
    _, hot, result, _, _ = _hot(tmp_path)
    manifest = dict(result['hot_restore_proof']['storage_manifest'], cold_history_catalog_sha256='0'*64)
    path = tmp_path/'immutable-manifest.json'; path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='history pin'):
        verify(hot, path)


@pytest.mark.parametrize('bad_pin', [False, True])
def test_actual_cloud_restore_revalidates_embedded_history_pin(tmp_path, bad_pin):
    _, hot, result, _, _ = _hot(tmp_path)
    key, _, manifest = _fixture(tmp_path)
    raw = hot.read_bytes()
    compressed = gzip.compress(raw)
    manifest.update(result['hot_restore_proof']['storage_manifest'])
    manifest.update(database_size_bytes=len(raw), database_sha256=hashlib.sha256(raw).hexdigest(),
                    compressed_size_bytes=len(compressed),
                    compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                    critical_table_counts={'trades': 1})
    if bad_pin:
        manifest['cold_history_catalog_sha256'] = '0' * 64
        with pytest.raises(ValueError, match='history pin'):
            restore.restore(bucket=state.BUCKET, key=key, destination=tmp_path/'restored.sqlite3',
                            result_path=tmp_path/'proof.json',
                            client=Client(key=key, archive=compressed, manifest=manifest))
    else:
        proof = restore.restore(bucket=state.BUCKET, key=key, destination=tmp_path/'restored.sqlite3',
                                result_path=tmp_path/'proof.json',
                                client=Client(key=key, archive=compressed, manifest=manifest))
        assert proof['full_restore_verified'] is True
        with closing(sqlite3.connect(tmp_path/'restored.sqlite3')) as conn:
            assert state.history_manifest_fields(conn) == {
                field: manifest[field] for field in state.PIN_FIELDS}


@pytest.mark.parametrize('mutation', ['bucket', 'unordered', 'boolean_id', 'source_key', 'authority'])
def test_state_rejects_false_authority_or_invalid_storage_identity(tmp_path, mutation):
    _, hot, _, _, _ = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        document = copy.deepcopy(state.read_history_state(conn))
    if mutation == 'bucket': document['bucket'] = 'unapproved-bucket'
    if mutation == 'unordered': document['retired_rowids']['passive_market_observations'] = [20, 10]
    if mutation == 'boolean_id': document['retired_rowids']['passive_market_observations'] = [True]
    if mutation == 'source_key': document['catalog_object_key'] = 'other/source.json'
    if mutation == 'authority': document['production_authority'] = True
    with pytest.raises(ValueError):
        state.canonical_state(document)


def test_state_duplicate_json_is_not_silently_accepted(tmp_path):
    _, hot, _, _, _ = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        document = state.read_history_state(conn)
    raw = state.canonical_state(document).decode()
    duplicate = '{"contract":"wrong",' + raw[1:]
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.execute(f'CREATE TABLE "{state.TABLE}"(id INTEGER,payload_json TEXT,payload_sha256 TEXT)')
        conn.execute(f'INSERT INTO "{state.TABLE}" VALUES(1,?,?)',
                     (duplicate, hashlib.sha256(duplicate.encode()).hexdigest()))
        with pytest.raises(ValueError, match='Duplicate'):
            state.read_history_state(conn)


def test_state_refuses_oversize_payload_before_fetching_it(monkeypatch):
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.execute(f'CREATE TABLE "{state.TABLE}"(id INTEGER,payload_json TEXT,payload_sha256 TEXT)')
        conn.execute(f'INSERT INTO "{state.TABLE}" VALUES(1,?,?)', ('x'*101, '0'*64))
        monkeypatch.setattr(state, 'MAX_BYTES', 100)
        with pytest.raises(ValueError, match='size or cardinality'):
            state.read_history_state(conn)


def test_offhost_state_loader_requires_only_standard_library():
    script = "from scripts.history_storage_loader import history_state; import sys; assert 'numpy' not in sys.modules; assert history_state.BUCKET=='trading-dbd-backups-2026'"
    subprocess.run([sys.executable, '-S', '-c', script],
                   cwd=Path(__file__).resolve().parents[1], check=True, timeout=10)
