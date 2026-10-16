"""Whole-catalog off-host hydration cannot silently shrink a SQL cohort."""
import hashlib
import json
import sqlite3

import pytest

from scripts import sqlite_history_partition as partition
from scripts.restore_verified_snapshot import manifest_digest
from test_ede_ledger_archive import ObjectStore
from test_sqlite_history_partition import fixture,identity,values


def union_module():
    from scripts import sqlite_history_union
    return sqlite_history_union


def scenario(tmp_path,*,overlap=False):
    full,full_proof=fixture(tmp_path)
    raw=tmp_path/'cold.sqlite3'
    piece=partition.export_partition(full,raw,table='passive_market_observations',rowids=[-7],source_receipt=full_proof)
    client=ObjectStore();cloud=partition.upload_partition(client,raw,receipt=piece)
    hot=tmp_path/'hot.sqlite3'
    source=sqlite3.connect(full);target=sqlite3.connect(hot)
    try:source.backup(target)
    finally:target.close();source.close()
    with sqlite3.connect(hot) as conn:
        if not overlap:conn.execute('DELETE FROM passive_market_observations WHERE rowid=-7')
        conn.execute('INSERT INTO passive_market_observations(rowid,id,payload,extra,n) VALUES(150,?,?,?,?)',('new','newer hot fact',b'new',2.5))
    catalog=dict(contract='sqlite-history-catalog-v1',partitions=[cloud],
                 expected_table_counts={'passive_market_observations':3},maximum_hydrated_bytes=1024**2)
    raw_catalog=json.dumps(catalog,sort_keys=True).encode()
    manifest=dict(database_sha256=hashlib.sha256(hot.read_bytes()).hexdigest(),
                  database_size_bytes=hot.stat().st_size,
                  cold_history_catalog_sha256=hashlib.sha256(raw_catalog).hexdigest())
    proof=dict(snapshot_restore_verified=True,immutable_sqlite_read=True,
               verified_local_file=identity(hot),database_sha256=manifest['database_sha256'],
               database_size_bytes=manifest['database_size_bytes'],storage_manifest=manifest,
               storage_manifest_sha256=manifest_digest(manifest))
    return hot,proof,raw_catalog,client,full


def repin(proof,catalog):
    raw=json.dumps(catalog,sort_keys=True).encode()
    proof['storage_manifest']['cold_history_catalog_sha256']=hashlib.sha256(raw).hexdigest()
    proof['storage_manifest_sha256']=manifest_digest(proof['storage_manifest'])
    return raw


def refresh_hot_proof(hot,proof):
    proof.update(verified_local_file=identity(hot),database_sha256=hashlib.sha256(hot.read_bytes()).hexdigest(),
                 database_size_bytes=hot.stat().st_size)
    proof['storage_manifest'].update(database_sha256=proof['database_sha256'],database_size_bytes=proof['database_size_bytes'])
    proof['storage_manifest_sha256']=manifest_digest(proof['storage_manifest'])


@pytest.mark.parametrize('overlap',[False,True])
def test_exact_cold_union_retains_typed_old_and_new_hot_rows(tmp_path,overlap):
    hot,proof,raw,client,full=scenario(tmp_path,overlap=overlap)
    before=hot.read_bytes();out=tmp_path/'all.sqlite3';mod=union_module()
    report=mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    actual=values(out);old=values(full)
    assert actual[:2]==old and actual[-1][0]==150
    assert len(actual)==3 and hot.read_bytes()==before
    assert report['history_union_verified'] is True
    assert report['database_sha256']==hashlib.sha256(out.read_bytes()).hexdigest()
    assert report['production_authority'] is False and report['data_retirement_authorized'] is False
    assert 'full_restore_verified' not in report


@pytest.mark.parametrize('kind',['missing_pin','changed_bytes','changed_manifest','missing_object','wrong_count','duplicate_receipt'])
def test_incomplete_or_inconsistent_catalog_refuses_without_output(tmp_path,kind):
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module();out=tmp_path/'all.sqlite3'
    if kind=='missing_pin':proof['storage_manifest'].pop('cold_history_catalog_sha256')
    if kind=='changed_bytes':raw+=b' '
    if kind=='changed_manifest':proof['storage_manifest']['other']='changed'
    if kind=='missing_object':client.objects.clear()
    if kind in {'wrong_count','duplicate_receipt'}:
        catalog=json.loads(raw)
        if kind=='wrong_count':catalog['expected_table_counts']['passive_market_observations']=4
        else:catalog['partitions']*=2
        raw=repin(proof,catalog)
    before=hot.read_bytes()
    with pytest.raises((ValueError,KeyError)):
        mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    assert hot.read_bytes()==before and not out.exists()


@pytest.mark.parametrize('conflict',['rowid','primary_key'])
def test_conflicting_hot_state_is_never_replaced(tmp_path,conflict):
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module();out=tmp_path/'all.sqlite3'
    with sqlite3.connect(hot) as conn:
        if conflict=='rowid':conn.execute("INSERT INTO passive_market_observations(rowid,id,payload) VALUES(-7,'different','keep')")
        else:conn.execute("INSERT INTO passive_market_observations(rowid,id,payload) VALUES(151,'old','keep')")
    proof.update(verified_local_file=identity(hot),database_sha256=hashlib.sha256(hot.read_bytes()).hexdigest())
    proof['storage_manifest']['database_sha256']=proof['database_sha256']
    proof['storage_manifest_sha256']=manifest_digest(proof['storage_manifest'])
    before=hot.read_bytes()
    with pytest.raises((ValueError,sqlite3.IntegrityError)):
        mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    assert hot.read_bytes()==before and not out.exists()


def test_target_insert_triggers_are_not_replayed_as_history(tmp_path):
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module()
    with sqlite3.connect(hot) as conn:
        conn.execute('CREATE TABLE effects(v)')
        conn.execute('CREATE TRIGGER side_effect AFTER INSERT ON passive_market_observations BEGIN INSERT INTO effects VALUES(1); END')
    proof.update(verified_local_file=identity(hot),database_sha256=hashlib.sha256(hot.read_bytes()).hexdigest(),database_size_bytes=hot.stat().st_size)
    proof['storage_manifest'].update(database_sha256=proof['database_sha256'],database_size_bytes=proof['database_size_bytes'])
    proof['storage_manifest_sha256']=manifest_digest(proof['storage_manifest'])
    out=tmp_path/'all.sqlite3'
    with pytest.raises(ValueError,match='trigger'):
        mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    assert not out.exists()


@pytest.mark.parametrize('header',[
    'AFTER INSERT /* valid SQL comment */ ON passive_market_observations',
    'AFTER INSERT ON PASSIVE_MARKET_OBSERVATIONS'])
def test_sqlite_trigger_guard_cannot_be_evaded_by_spelling_or_comments(tmp_path,header):
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module();out=tmp_path/'all.sqlite3'
    with sqlite3.connect(hot) as conn:
        conn.execute('CREATE TABLE effects(v)')
        conn.execute('INSERT INTO effects VALUES(42)')
        conn.execute('CREATE TRIGGER side_effect '+header+' BEGIN DELETE FROM effects; END')
    refresh_hot_proof(hot,proof)
    before=hot.read_bytes()
    with pytest.raises(ValueError,match='trigger'):
        mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    assert not out.exists() and hot.read_bytes()==before


def test_low_worker_space_or_existing_destination_never_overwrites(tmp_path,monkeypatch):
    from types import SimpleNamespace
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module();out=tmp_path/'all.sqlite3'
    out.write_bytes(b'keep')
    with pytest.raises(FileExistsError):mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    assert out.read_bytes()==b'keep'
    monkeypatch.setattr(mod.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024**3-1))
    with pytest.raises(ValueError):mod.hydrate_snapshot(hot,tmp_path/'fresh.sqlite3',source_receipt=proof,catalog_bytes=raw,client=client)
    assert not (tmp_path/'fresh.sqlite3').exists()


@pytest.mark.parametrize('kind',['source_change','copy_timeout','total_timeout','foreign_key'])
def test_interrupted_or_invalid_snapshot_is_not_published(tmp_path,monkeypatch,kind):
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module();out=tmp_path/'all.sqlite3'
    if kind=='source_change':
        restore=mod.archive.restore_from_cloud
        def changed(*args,**kwargs):
            result=restore(*args,**kwargs)
            with sqlite3.connect(hot) as conn:conn.execute("UPDATE passive_market_observations SET payload='changed' WHERE rowid=150")
            return result
        monkeypatch.setattr(mod.archive,'restore_from_cloud',changed)
    elif kind=='copy_timeout':monkeypatch.setattr(mod,'COPY_SECONDS',0)
    elif kind=='total_timeout':monkeypatch.setattr(mod,'MAX_SECONDS',0)
    else:
        with sqlite3.connect(hot) as conn:
            conn.execute('CREATE TABLE child(parent TEXT REFERENCES passive_market_observations(id))')
            conn.execute("INSERT INTO child VALUES('missing')")
        refresh_hot_proof(hot,proof)
    with pytest.raises(ValueError):
        mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    assert not out.exists()
    assert not list(tmp_path.glob('.history-union-*'))


def test_update_only_guard_is_preserved_and_not_replayed(tmp_path):
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module();out=tmp_path/'all.sqlite3'
    with sqlite3.connect(hot) as conn:
        conn.execute("CREATE TRIGGER immutable_t0 BEFORE UPDATE ON passive_market_observations BEGIN SELECT RAISE(ABORT,'frozen'); END")
    refresh_hot_proof(hot,proof)
    mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    with sqlite3.connect(out) as conn:
        with pytest.raises(sqlite3.IntegrityError,match='frozen'):
            conn.execute("UPDATE passive_market_observations SET payload='changed'")


def test_each_partition_merge_has_bounded_sql_deadline(tmp_path,monkeypatch):
    hot,proof,raw,client,_=scenario(tmp_path);mod=union_module();out=tmp_path/'all.sqlite3'
    monkeypatch.setattr(mod,'MERGE_SECONDS',0,raising=False)
    with pytest.raises(ValueError,match='deadline'):
        mod.hydrate_snapshot(hot,out,source_receipt=proof,catalog_bytes=raw,client=client)
    assert not out.exists()
