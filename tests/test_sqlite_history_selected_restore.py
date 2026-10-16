import hashlib
import json
import sqlite3

import pytest

from scripts import sqlite_history_catalog as catalog
from test_sqlite_history_catalog import full_source
from test_ede_ledger_archive import ObjectStore


def module():
    from scripts import sqlite_history_selected_restore
    return sqlite_history_selected_restore


def cloud(tmp_path, empty=False):
    source, proof = full_source(tmp_path, empty=empty)
    client = ObjectStore(); path = tmp_path/'catalog.json'
    catalog.publish_full_catalog(source, path, source_receipt=proof, client=client, maximum_hydrated_bytes=1024**2)
    raw = path.read_bytes(); source.unlink()
    return client, raw, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('empty',[False,True])
def test_reconstitutes_entire_selected_history_without_original_database(tmp_path, empty):
    client, raw, pin = cloud(tmp_path, empty=empty); out=tmp_path/'restored.sqlite3'
    report=module().restore_catalog_tables(client,out,catalog_bytes=raw,expected_catalog_sha256=pin)
    with sqlite3.connect(out) as conn:
        catalog.verify_full_tables(conn,json.loads(raw))
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}=={'g1s_observations','passive_market_observations'}
        rows=conn.execute('SELECT g.rowid,p.rowid FROM g1s_observations g JOIN passive_market_observations p ON p.id=g.observation_id ORDER BY g.rowid').fetchall()
        assert rows==([] if empty else [(-11,-7),(200,91)])
    assert report['full_selected_tables_verified'] is True
    assert report['full_database_integrity_verified'] is False
    assert report['database_sha256']==hashlib.sha256(out.read_bytes()).hexdigest()
    assert report['production_authority'] is False and report['data_retirement_authorized'] is False
    assert 'full_restore_verified' not in report and 'history_union_verified' not in report


@pytest.mark.parametrize('failure',['pin','root','duplicate_json','missing_object','corrupt_object','existing','space','deadline','output_bound'])
def test_refuses_invalid_or_unbounded_restore_without_publication(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    mod=module();client,raw,pin=cloud(tmp_path);out=tmp_path/'out.sqlite3'
    if failure=='pin':pin='0'*64
    if failure=='root':
        value=json.loads(raw);value['full_table_proofs']['g1s_observations']['logical_sha256']='0'*64
        raw=json.dumps(value).encode();pin=hashlib.sha256(raw).hexdigest()
    if failure=='duplicate_json':
        raw=raw[:-1]+b',"partitions":[]}';pin=hashlib.sha256(raw).hexdigest()
    if failure=='missing_object':client.objects.clear()
    if failure=='corrupt_object':client.corrupt=True
    if failure=='existing':out.write_bytes(b'keep')
    if failure=='space':monkeypatch.setattr(mod.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024**3-1))
    if failure=='deadline':monkeypatch.setattr(mod,'MAX_SECONDS',0)
    if failure=='output_bound':
        value=json.loads(raw);value['maximum_hydrated_bytes']=4096
        raw=json.dumps(value).encode();pin=hashlib.sha256(raw).hexdigest()
    with pytest.raises((ValueError,FileExistsError,KeyError,OSError,EOFError,sqlite3.DatabaseError)):
        mod.restore_catalog_tables(client,out,catalog_bytes=raw,expected_catalog_sha256=pin)
    if failure=='existing':assert out.read_bytes()==b'keep'
    else:assert not out.exists()


@pytest.mark.parametrize('failure',['boolean_count','too_many_parts','non_table_schema','shadowed_rowid','wrong_columns','scope'])
def test_pinned_catalog_still_requires_strict_schema_and_bounds(tmp_path, failure):
    client,raw,_=cloud(tmp_path);value=json.loads(raw)
    proof=value['full_table_proofs']['g1s_observations']
    if failure=='boolean_count':value['expected_table_counts']['g1s_observations']=True
    if failure=='too_many_parts':value['partitions']*=1025
    if failure=='non_table_schema':proof['source_schema_sql']='CREATE VIEW g1s_observations AS SELECT 1'
    if failure=='shadowed_rowid':proof['columns']=['rowid']
    if failure=='wrong_columns':proof['columns']=[123]
    if failure=='scope':value['production_authority']=True
    # Keep partition descriptors consistent to exercise the final schema check.
    for receipt in value['partitions']:
        if receipt['partition']['table']=='g1s_observations':
            for key in ('source_schema_sql','columns'):receipt['partition'][key]=proof[key]
    raw=json.dumps(value).encode();out=tmp_path/'bad.sqlite3'
    with pytest.raises(ValueError):
        module().restore_catalog_tables(client,out,catalog_bytes=raw,expected_catalog_sha256=hashlib.sha256(raw).hexdigest())
    assert not out.exists()


def test_both_empty_selected_schemas_are_reconstituted(tmp_path):
    source,proof=full_source(tmp_path,empty=True)
    from test_sqlite_history_partition import identity
    with sqlite3.connect(source) as conn:conn.execute('DELETE FROM passive_market_observations')
    proof.update(verified_local_file=identity(source),database_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    client=ObjectStore();path=tmp_path/'empty.json'
    catalog.publish_full_catalog(source,path,source_receipt=proof,client=client,maximum_hydrated_bytes=1024**2)
    raw=path.read_bytes();source.unlink();out=tmp_path/'empty.sqlite3'
    report=module().restore_catalog_tables(client,out,catalog_bytes=raw,expected_catalog_sha256=hashlib.sha256(raw).hexdigest())
    assert report['expected_table_counts']=={'g1s_observations':0,'passive_market_observations':0}
    assert report['partition_count']==0 and report['full_database_integrity_verified'] is False
