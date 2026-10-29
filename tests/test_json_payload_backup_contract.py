"""A physical coded backup is explicit; restore must retain exact original bytes."""
import gzip
import hashlib
import io
import json
import sqlite3

import pytest

from scripts.history_storage_loader import history_state
from seiltanzer import sqlite_json_payloads as codec
from test_upload_verified_snapshot import _ObjectStore, module as uploader
from test_restore_verified_snapshot import MODULE as restorer

CODED_CONTRACT = 'trading-dbd-offhost-backup-v2-json-payloads'


class Store(_ObjectStore):
    def get_object(self, **kwargs):
        if 'Range' not in kwargs and not kwargs['Key'].endswith('.manifest.json'):
            return {'Body':io.BytesIO(self.object)}
        return super().get_object(**kwargs)


def coded_source(tmp_path):
    source = tmp_path/'coded.db'
    text = '{"r":1.25,"audit":"'+'original; '*3000+'"}'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE passive_market_observations(features_json TEXT)')
        conn.execute('CREATE TABLE g1s_observations(frozen_features_json TEXT)')
        codec.install_marker(conn)
        conn.execute('INSERT INTO passive_market_observations VALUES(?)',(codec.pack_text(text),))
        fields = history_state.history_manifest_fields(conn)
    raw = source.read_bytes()
    manifest = dict(database_sha256=hashlib.sha256(raw).hexdigest(),database_size_bytes=len(raw),
        git_commit='a'*40,backup_id='coded-fixture',critical_table_counts={'passive_market_observations':1},**fields)
    path = tmp_path/'source.json';path.write_text(json.dumps(manifest))
    return source,path,text


def test_coded_offhost_backup_has_v2_contract_and_exact_physical_restore(tmp_path,monkeypatch):
    import boto3
    source,manifest,text = coded_source(tmp_path)
    store = Store();monkeypatch.setattr(boto3,'client',lambda *a,**kw:store)
    key = 'backups/v1/daily-slot-0/snapshot.sqlite3.gz'
    result = uploader.upload(source,manifest,bucket=codec_bucket(),key=key)
    assert result['backup_contract'] == CODED_CONTRACT
    assert store.metadata['contract'] == CODED_CONTRACT
    assert result[codec.MANIFEST_FIELD] == history_state.snapshot_history_manifest(source)[codec.MANIFEST_FIELD]
    assert gzip.decompress(store.object) == source.read_bytes()
    output = tmp_path/'restored.db'
    restored = restorer.restore(bucket=codec_bucket(),key=key,destination=output,
        result_path=tmp_path/'restored.json',client=store)
    assert restored['full_restore_verified'] is True
    assert output.read_bytes() == source.read_bytes()
    with sqlite3.connect(output) as conn:
        assert codec.install_reader(conn) is True
        assert conn.execute('SELECT features_json FROM passive_market_observations').fetchone()[0] == text


def codec_bucket():
    return 'trading-dbd-backups-2026'


@pytest.mark.parametrize('hazard',['pin_removed','version_downgraded'])
def test_coded_manifest_cannot_downgrade_to_plain_reader_contract(tmp_path,monkeypatch,hazard):
    import boto3
    source,manifest,_ = coded_source(tmp_path)
    store = Store();monkeypatch.setattr(boto3,'client',lambda *a,**kw:store)
    key = 'backups/v1/daily-slot-0/snapshot.sqlite3.gz'
    uploader.upload(source,manifest,bucket=codec_bucket(),key=key)
    payload = json.loads(store.manifest)
    payload['backup_contract'] = CODED_CONTRACT
    if hazard == 'pin_removed':payload.pop(codec.MANIFEST_FIELD)
    else:payload['backup_contract'] = restorer.CONTRACT
    store.manifest=json.dumps(payload).encode()
    output = tmp_path/'restored.db'
    with pytest.raises((ValueError,RuntimeError)):
        restorer.restore(bucket=codec_bucket(),key=key,destination=output,
            result_path=tmp_path/'result.json',client=store)
    assert not output.exists()


def test_local_coded_backup_uses_new_contract_and_restores_exact_bytes(tmp_path):
    from seiltanzer import storage_runtime as storage
    from seiltanzer import storage_refinement as refined
    from types import SimpleNamespace
    source,_,text=coded_source(tmp_path)
    manager=storage.StorageManager(SimpleNamespace(data_dir=tmp_path,trades_db=source),git_commit='a'*40)
    # Exercise the same refinement over the real backup owner; no synthetic manifest.
    result=refined.create_backup_honest_encryption(manager,reason='scheduled')
    data=json.loads(__import__('pathlib').Path(result.manifest_path).read_text())
    assert data['backup_contract_version']=='seiltanzer-backup-v2-json-payloads'
    output=tmp_path/'local-restored.db'
    restored=refined.restore_verified_backup_refined(backup_db=result.database_path,
        manifest_path=result.manifest_path,destination_db=output)
    assert restored['ok'] is True
    assert output.read_bytes()==__import__('pathlib').Path(result.database_path).read_bytes()


def test_local_coded_restore_refuses_downgraded_reader_contract_before_destination(tmp_path):
    from seiltanzer import storage_runtime as storage
    from seiltanzer import storage_refinement as refined
    source,manifest,_=coded_source(tmp_path)
    payload=json.loads(manifest.read_text());payload.update(verified=True,backup_contract_version=storage.BACKUP_CONTRACT_VERSION)
    payload['manifest_payload_sha256']=refined._manifest_hash(payload)
    manifest.write_text(json.dumps(payload));output=tmp_path/'forbidden.db'
    with pytest.raises(ValueError,match='contract'):
        storage.StorageManager.restore_verified_backup(backup_db=source,manifest_path=manifest,destination_db=output)
    assert not output.exists()


@pytest.mark.parametrize('path',['sparse','quiescent'])
def test_low_disk_coded_backup_remains_explicit_and_restorable(tmp_path,monkeypatch,path):
    from types import SimpleNamespace
    from pathlib import Path
    from seiltanzer import storage_runtime as storage, storage_sparse_backup_guard as sparse
    from seiltanzer import storage_refinement as refined
    source,_,_=coded_source(tmp_path);before=source.read_bytes()
    manager=storage.StorageManager(SimpleNamespace(data_dir=tmp_path,trades_db=source))
    monkeypatch.setattr(sparse,'_available_bytes',lambda _:10*1024**3)
    create=(sparse._create_sparse_guarded_backup if path=='sparse'
            else sparse._create_quiescent_sparse_clone_backup)
    result=create(manager,kind='local',reason='prestart')
    payload=json.loads(Path(result.manifest_path).read_text())
    assert payload['backup_contract_version']=='seiltanzer-backup-v2-json-payloads'
    assert payload[codec.MANIFEST_FIELD]==history_state.snapshot_history_manifest(source)[codec.MANIFEST_FIELD]
    output=tmp_path/'restored.db'
    restored=refined.restore_verified_backup_refined(backup_db=result.database_path,
        manifest_path=result.manifest_path,destination_db=output)
    assert restored['ok'] is True
    assert output.read_bytes()==Path(result.database_path).read_bytes()
    assert source.read_bytes()==before


@pytest.mark.parametrize('entry',['plan','create'])
def test_coded_database_cannot_enter_rowid_changing_vacuum_backup(tmp_path,entry):
    from types import SimpleNamespace
    from seiltanzer import storage_runtime as storage, storage_disk_guard as guard
    source,_,_=coded_source(tmp_path);before=source.read_bytes()
    manager=storage.StorageManager(SimpleNamespace(data_dir=tmp_path,trades_db=source))
    with pytest.raises(OSError):
        if entry=='plan':guard._compact_snapshot_plan(source)
        else:guard._create_compact_backup(manager,kind='local',reason='prestart',plan={})
    assert source.read_bytes()==before
    assert list(manager.local_dir.glob('*.manifest.json'))==[]
