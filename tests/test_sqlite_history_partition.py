"""Offline history partitions retain physical values, never grant retirement."""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts import restore_verified_snapshot as verifier


def archive_module():
    from scripts import sqlite_history_partition
    return sqlite_history_partition


def identity(path):
    info=path.stat()
    return dict(path=str(path.resolve()),device=info.st_dev,inode=info.st_ino,
                size=info.st_size,mtime_ns=info.st_mtime_ns,ctime_ns=info.st_ctime_ns)


def fixture(tmp_path):
    path=tmp_path/'source.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE passive_market_observations(id TEXT PRIMARY KEY, payload TEXT, extra, n REAL)')
        conn.execute("INSERT INTO passive_market_observations(rowid,id,payload,extra,n) VALUES(-7,'old',CAST(X'FF0061' AS TEXT),X'00FF',1.2345678901234567)")
        conn.execute('INSERT INTO passive_market_observations(rowid,id,payload,extra,n) VALUES(91,?,?,?,?)',
                     ('next',' { "unchanged": [1, 2] }\n',9223372036854775807,None))
    proof=dict(immutable_sqlite_read=True,snapshot_restore_verified=True,
               database_size_bytes=path.stat().st_size,
               database_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
               verified_local_file=identity(path))
    return path,proof


def values(path):
    with sqlite3.connect(path) as conn:
        conn.text_factory=bytes
        return conn.execute('SELECT rowid,typeof(payload),payload,typeof(extra),extra,n FROM passive_market_observations ORDER BY rowid').fetchall()


def test_roundtrip_preserves_rowids_storage_types_and_opaque_json(tmp_path):
    source,proof=fixture(tmp_path); before=source.read_bytes(); mod=archive_module()
    out=tmp_path/'partition.sqlite3'
    receipt=mod.export_partition(source,out,table='passive_market_observations',rowids=[-7,91],source_receipt=proof)
    restored=tmp_path/'restored.sqlite3'; mod.restore_partition(out,restored,receipt=receipt)
    assert values(restored)==values(source)
    assert source.read_bytes()==before
    assert receipt['row_count']==2
    assert receipt['production_authority'] is False
    assert receipt['data_retirement_authorized'] is False


@pytest.mark.parametrize('change',['missing','path','inode','bytes','unverified','wal'])
def test_unverified_or_changed_source_refused_before_output(tmp_path,change):
    source,proof=fixture(tmp_path);mod=archive_module()
    if change=='missing': proof.pop('verified_local_file')
    if change=='path': proof['verified_local_file']['path']=str(tmp_path/'other')
    if change=='inode': proof['verified_local_file']['inode']+=1
    if change=='bytes': source.write_bytes(source.read_bytes()+b'changed')
    if change=='unverified': proof['snapshot_restore_verified']=False
    if change=='wal': Path(str(source)+'-wal').write_bytes(b'active')
    out=tmp_path/'partition.sqlite3'
    with pytest.raises(ValueError):mod.export_partition(source,out,table='passive_market_observations',rowids=[91],source_receipt=proof)
    assert not out.exists()


@pytest.mark.parametrize('rowids',[[91,91],[1],list(range(501)),[True]])
def test_missing_duplicate_or_excessive_selection_is_not_partial_success(tmp_path,rowids):
    source,proof=fixture(tmp_path);mod=archive_module();out=tmp_path/'partition.sqlite3'
    with pytest.raises(ValueError):mod.export_partition(source,out,table='passive_market_observations',rowids=rowids,source_receipt=proof)
    assert not out.exists()


def test_corrupt_partition_or_existing_destination_never_overwrites(tmp_path):
    source,proof=fixture(tmp_path);mod=archive_module();part=tmp_path/'partition.sqlite3'
    receipt=mod.export_partition(source,part,table='passive_market_observations',rowids=[91],source_receipt=proof)
    target=tmp_path/'keep.sqlite3';target.write_bytes(b'keep')
    with pytest.raises((ValueError,FileExistsError)):mod.restore_partition(part,target,receipt=receipt)
    assert target.read_bytes()==b'keep'
    part.write_bytes(part.read_bytes()[:-1]+b'!')
    with pytest.raises(ValueError):mod.restore_partition(part,tmp_path/'fresh.sqlite3',receipt=receipt)
    assert not (tmp_path/'fresh.sqlite3').exists()


def test_unsupported_table_refused(tmp_path):
    source,proof=fixture(tmp_path);mod=archive_module()
    with pytest.raises(ValueError):mod.export_partition(source,tmp_path/'p.sqlite3',table='trades',rowids=[91],source_receipt=proof)


def test_local_file_identity_is_emitted_only_after_real_restore_verification(tmp_path):
    from test_restore_verified_snapshot import _live_fixture,Client
    key,packed,manifest=_live_fixture(tmp_path);out=tmp_path/'verified.sqlite3'
    receipt=verifier.restore(bucket=manifest['bucket'],key=key,destination=out,
        result_path=tmp_path/'result.json',snapshot_drill=True,
        client=Client(key=key,archive=packed,manifest=manifest))
    assert receipt.get('verified_local_file')==identity(out)


def test_case_insensitive_rowid_column_does_not_shadow_original_hidden_rowid(tmp_path):
    source,proof=fixture(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute('ALTER TABLE passive_market_observations ADD COLUMN ROWID INTEGER')
        conn.execute('UPDATE passive_market_observations SET ROWID=100')
    proof['verified_local_file']=identity(source);proof['database_size_bytes']=source.stat().st_size
    proof['database_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    mod=archive_module();part=tmp_path/'partition.sqlite3'
    receipt=mod.export_partition(source,part,table='passive_market_observations',rowids=[-7,91],source_receipt=proof)
    assert receipt['source_rowid_alias']=='_rowid_'
    out=tmp_path/'out.sqlite3';mod.restore_partition(part,out,receipt=receipt)
    with sqlite3.connect(out) as conn:
        assert conn.execute('SELECT _rowid_,ROWID FROM passive_market_observations ORDER BY _rowid_').fetchall()==[(-7,100),(91,100)]


def test_zero_budget_and_oversize_rows_publish_nothing(tmp_path,monkeypatch):
    source,proof=fixture(tmp_path);mod=archive_module()
    monkeypatch.setattr(mod,'MAX_SECONDS',0)
    with pytest.raises(ValueError):mod.export_partition(source,tmp_path/'timeout.sqlite3',table='passive_market_observations',rowids=[91],source_receipt=proof)
    assert not (tmp_path/'timeout.sqlite3').exists()
    monkeypatch.setattr(mod,'MAX_SECONDS',30);monkeypatch.setattr(mod,'MAX_ROW_BYTES',1)
    with pytest.raises(ValueError):mod.export_partition(source,tmp_path/'large.sqlite3',table='passive_market_observations',rowids=[91],source_receipt=proof)
    assert not (tmp_path/'large.sqlite3').exists()


def test_source_changed_during_copy_does_not_publish_partition(tmp_path,monkeypatch):
    source,proof=fixture(tmp_path);mod=archive_module();original=mod._logical_hash
    def changed(*args,**kw):
        value=original(*args,**kw)
        if args[1]=='partition_rows':
            with source.open('ab') as f:f.write(b'changed')
        return value
    monkeypatch.setattr(mod,'_logical_hash',changed)
    with pytest.raises(ValueError,match='changed'):mod.export_partition(source,tmp_path/'out.sqlite3',table='passive_market_observations',rowids=[91],source_receipt=proof)
    assert not (tmp_path/'out.sqlite3').exists()


def test_private_cloud_full_get_roundtrip_retains_typed_values_without_authority(tmp_path):
    from test_ede_ledger_archive import ObjectStore
    source,proof=fixture(tmp_path);mod=archive_module();part=tmp_path/'partition.sqlite3'
    receipt=mod.export_partition(source,part,table='passive_market_observations',rowids=[-7,91],source_receipt=proof)
    client=ObjectStore();cloud=mod.upload_partition(client,part,receipt=receipt)
    assert cloud['verified_full_get'] is True
    assert cloud['object_key'].startswith('research/sqlite-history/v1/'+proof['database_sha256']+'/passive_market_observations/')
    assert cloud['production_authority'] is False and cloud['data_retirement_authorized'] is False
    out=tmp_path/'restored.sqlite3';mod.restore_from_cloud(client,cloud,out)
    assert values(source)==values(out)


def test_cloud_corruption_refuses_receipt_and_keeps_original(tmp_path):
    from test_ede_ledger_archive import ObjectStore
    source,proof=fixture(tmp_path);mod=archive_module();part=tmp_path/'partition.sqlite3'
    receipt=mod.export_partition(source,part,table='passive_market_observations',rowids=[91],source_receipt=proof)
    client=ObjectStore();client.corrupt=True
    with pytest.raises((ValueError,OSError,EOFError)):
        mod.upload_partition(client,part,receipt=receipt)
    assert not any(k.endswith('.manifest.json') for k in client.objects)
    assert source.exists() and part.exists()


def test_missing_cloud_object_never_changes_existing_destination(tmp_path):
    from test_ede_ledger_archive import ObjectStore
    source,proof=fixture(tmp_path);mod=archive_module();part=tmp_path/'partition.sqlite3'
    receipt=mod.export_partition(source,part,table='passive_market_observations',rowids=[91],source_receipt=proof)
    client=ObjectStore();cloud=mod.upload_partition(client,part,receipt=receipt)
    del client.objects[cloud['object_key']]
    target=tmp_path/'out.sqlite3';target.write_bytes(b'keep')
    with pytest.raises(FileExistsError):mod.restore_from_cloud(client,cloud,target)
    assert target.read_bytes()==b'keep'


def test_verifier_never_mints_token_for_mutation_during_capacity_scan(tmp_path,monkeypatch):
    from test_restore_verified_snapshot import _live_fixture,Client
    key,packed,manifest=_live_fixture(tmp_path);out=tmp_path/'verified.sqlite3'
    def mutate(_connection):
        with sqlite3.connect(out) as writer:writer.execute("UPDATE evidence SET value='changed'")
        return {'status':'TEST_MUTATION_ONLY'}
    monkeypatch.setattr(verifier,'collect_capacity',mutate)
    with pytest.raises(RuntimeError,match='changed'):
        verifier.restore(bucket=manifest['bucket'],key=key,destination=out,
            result_path=tmp_path/'result.json',snapshot_drill=True,storage_report=True,
            client=Client(key=key,archive=packed,manifest=manifest))
    assert not (tmp_path/'result.json').exists()


def test_restored_mtime_does_not_hide_source_mutation(tmp_path):
    import os
    source,proof=fixture(tmp_path);mod=archive_module();before=source.stat()
    with sqlite3.connect(source) as writer:writer.execute("UPDATE passive_market_observations SET id='late' WHERE id='next'")
    os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert source.stat().st_size==before.st_size
    with pytest.raises(ValueError):mod.export_partition(source,tmp_path/'out.sqlite3',table='passive_market_observations',rowids=[91],source_receipt=proof)
    assert not (tmp_path/'out.sqlite3').exists()


def test_restore_applies_row_bound_before_materializing_and_publishing(tmp_path,monkeypatch):
    source,proof=fixture(tmp_path);mod=archive_module();limit=mod.MAX_ROW_BYTES
    with sqlite3.connect(source) as writer:
        writer.execute('UPDATE passive_market_observations SET extra=? WHERE id=?',(b'x'*(limit+1024**2),'next'))
    proof.update(verified_local_file=identity(source),database_size_bytes=source.stat().st_size,
                 database_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(mod,'MAX_ROW_BYTES',limit*2)
    part=tmp_path/'partition.sqlite3'
    receipt=mod.export_partition(source,part,table='passive_market_observations',rowids=[91],source_receipt=proof)
    monkeypatch.setattr(mod,'MAX_ROW_BYTES',limit)
    with pytest.raises((ValueError,sqlite3.DataError)):
        mod.restore_partition(part,tmp_path/'out.sqlite3',receipt=receipt)
    assert not (tmp_path/'out.sqlite3').exists()
