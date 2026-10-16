"""Full-table evidence must detect missing history even with plausible counts."""
import hashlib
import json
import sqlite3

import pytest

from scripts import sqlite_history_partition as partition
from scripts.restore_verified_snapshot import manifest_digest
from test_ede_ledger_archive import ObjectStore
from test_sqlite_history_partition import fixture, identity


def module():
    from scripts import sqlite_history_catalog
    return sqlite_history_catalog


def full_source(tmp_path, *, empty=False):
    path, proof = fixture(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE g1s_observations(id TEXT PRIMARY KEY, observation_id TEXT, payload)')
        if not empty:
            conn.executemany('INSERT INTO g1s_observations(rowid,id,observation_id,payload) VALUES(?,?,?,?)',
                             [(-11,'g-old','old',b'\xff\x00'), (200,'g-next','next',123)])
    proof.update(verified_local_file=identity(path), database_size_bytes=path.stat().st_size,
                 database_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return path, proof


def hot_proof(path, raw):
    manifest = dict(database_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    database_size_bytes=path.stat().st_size,
                    cold_history_catalog_sha256=hashlib.sha256(raw).hexdigest())
    return dict(immutable_sqlite_read=True, snapshot_restore_verified=True,
                verified_local_file=identity(path), database_sha256=manifest['database_sha256'],
                database_size_bytes=manifest['database_size_bytes'], storage_manifest=manifest,
                storage_manifest_sha256=manifest_digest(manifest))


def test_complete_catalog_cloud_hydration_preserves_all_typed_rows_and_reader_join(tmp_path, monkeypatch):
    mod = module(); source, proof = full_source(tmp_path); before = source.read_bytes()
    monkeypatch.setattr(partition, 'MAX_ROWS', 1)
    client = ObjectStore(); output = tmp_path/'catalog.json'
    report = mod.publish_full_catalog(source, output, source_receipt=proof, client=client,
                                      maximum_hydrated_bytes=1024**2)
    raw = output.read_bytes(); catalog = json.loads(raw)
    assert report['catalog_sha256'] == hashlib.sha256(raw).hexdigest()
    assert client.objects[report['object_key']][0] == raw
    assert catalog['expected_table_counts'] == {'passive_market_observations':2, 'g1s_observations':2}
    assert len(catalog['partitions']) == 4
    assert all(p['partition']['source_database_sha256'] == proof['database_sha256'] for p in catalog['partitions'])
    hot = tmp_path/'hot.sqlite3'
    with sqlite3.connect(source) as original, sqlite3.connect(hot) as target:
        original.backup(target)
    with sqlite3.connect(hot) as conn:
        conn.execute('DELETE FROM g1s_observations')
        conn.execute('DELETE FROM passive_market_observations')
    from scripts.sqlite_history_union import hydrate_snapshot
    union = tmp_path/'union.sqlite3'
    restored = hydrate_snapshot(hot, union, source_receipt=hot_proof(hot, raw), catalog_bytes=raw, client=client)
    assert restored['full_selected_tables_verified'] is True
    for path in (source, union):
        with sqlite3.connect(path) as conn:
            mod.verify_full_tables(conn, catalog)
            actual = conn.execute('SELECT g.rowid,p.rowid,g.id FROM g1s_observations g JOIN passive_market_observations p ON p.id=g.observation_id ORDER BY g.rowid').fetchall()
        assert actual == [(-11,-7,'g-old'),(200,91,'g-next')]
    assert source.read_bytes() == before
    assert report['production_authority'] is False and report['data_retirement_authorized'] is False


def test_wrong_full_table_root_rejected_even_when_catalog_counts_match(tmp_path):
    from test_sqlite_history_union import scenario, repin
    hot, proof, raw, client, full = scenario(tmp_path)
    with sqlite3.connect(full) as conn:
        ddl = conn.execute("SELECT sql FROM sqlite_schema WHERE name='passive_market_observations'").fetchone()[0]
    catalog = json.loads(raw)
    catalog['full_table_proofs'] = {'passive_market_observations':dict(
        source_schema_sql=ddl, columns=['id','payload','extra','n'], source_rowid_alias='rowid',
        row_count=3, logical_sha256='0'*64)}
    raw = repin(proof, catalog)
    from scripts.sqlite_history_union import hydrate_snapshot
    out = tmp_path/'bad.sqlite3'
    with pytest.raises(ValueError, match='typed|root|complete'):
        hydrate_snapshot(hot, out, source_receipt=proof, catalog_bytes=raw, client=client)
    assert not out.exists()


@pytest.mark.parametrize('failure',['proof','existing','budget','readback','mutation','coarse_mutation'])
def test_failed_producer_does_not_publish_local_catalog(tmp_path, monkeypatch, failure):
    mod = module(); source, proof = full_source(tmp_path); client = ObjectStore()
    destination = tmp_path/'catalog.json'; before = source.read_bytes()
    if failure == 'proof': proof.pop('verified_local_file')
    if failure == 'existing': destination.write_bytes(b'keep')
    if failure == 'budget': monkeypatch.setattr(mod, 'MAX_PARTITIONS', 1)
    if failure == 'readback':
        original = client.put_object
        def corrupt(**kw):
            original(**kw)
            if '/catalogs/' in kw['Key']: client.objects[kw['Key']] = (b'bad', {})
        client.put_object = corrupt
    if failure in {'mutation','coarse_mutation'}:
        if failure == 'coarse_mutation':
            original_identity = partition._identity
            frozen = original_identity(source)
            monkeypatch.setattr(partition, '_identity', lambda path:frozen if str(path)==str(source) else original_identity(path))
        original = partition.upload_partition
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            with sqlite3.connect(source) as conn: conn.execute("UPDATE g1s_observations SET payload=456 WHERE id='g-next'")
            return result
        monkeypatch.setattr(partition, 'upload_partition', mutate)
    with pytest.raises((ValueError, FileExistsError)):
        mod.publish_full_catalog(source, destination, source_receipt=proof, client=client,
                                 maximum_hydrated_bytes=1024**2)
    if failure == 'existing': assert destination.read_bytes() == b'keep'
    else: assert not destination.exists()
    if failure not in {'mutation','coarse_mutation'}: assert source.read_bytes() == before


def test_empty_selected_table_retains_schema_and_empty_typed_root(tmp_path):
    mod = module(); source, proof = full_source(tmp_path, empty=True); out = tmp_path/'catalog.json'
    mod.publish_full_catalog(source, out, source_receipt=proof, client=ObjectStore(), maximum_hydrated_bytes=1024**2)
    catalog = json.loads(out.read_bytes())
    assert catalog['expected_table_counts']['g1s_observations'] == 0
    assert catalog['full_table_proofs']['g1s_observations']['logical_sha256'] == hashlib.sha256(b'').hexdigest()
    with sqlite3.connect(source) as conn: mod.verify_full_tables(conn, catalog)


@pytest.mark.parametrize('failure',['root','epoch','duplicate','rowids'])
def test_valid_full_catalog_tampering_cannot_publish_hydrated_output(tmp_path, failure):
    mod = module(); source, proof = full_source(tmp_path); client = ObjectStore(); out = tmp_path/'catalog.json'
    mod.publish_full_catalog(source, out, source_receipt=proof, client=client, maximum_hydrated_bytes=1024**2)
    catalog = json.loads(out.read_bytes())
    if failure == 'root': catalog['full_table_proofs']['g1s_observations']['logical_sha256'] = '0'*64
    if failure == 'epoch': catalog['partitions'][0]['partition']['source_database_sha256'] = '0'*64
    if failure == 'duplicate': catalog['partitions'].append(catalog['partitions'][0])
    if failure == 'rowids': catalog['partitions'][0]['partition']['rowids'] = [-11,201]
    raw = json.dumps(catalog, sort_keys=True).encode()
    from scripts.sqlite_history_union import hydrate_snapshot
    restored = tmp_path/'refused.sqlite3'
    with pytest.raises(ValueError):
        hydrate_snapshot(source, restored, source_receipt=hot_proof(source, raw), catalog_bytes=raw, client=client)
    assert not restored.exists()


@pytest.mark.parametrize('failure',['reserve','deadline','json_bytes','row_bytes'])
def test_full_catalog_resource_failure_has_no_published_catalog(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    mod = module(); source, proof = full_source(tmp_path); client = ObjectStore(); out = tmp_path/'catalog.json'
    if failure == 'reserve': monkeypatch.setattr(mod.shutil, 'disk_usage', lambda _:SimpleNamespace(free=1024**3-1))
    if failure == 'deadline': monkeypatch.setattr(mod, 'MAX_SECONDS', 0)
    if failure == 'json_bytes': monkeypatch.setattr(mod, 'MAX_CATALOG_BYTES', 10)
    if failure == 'row_bytes': monkeypatch.setattr(partition, 'MAX_ROW_BYTES', 1)
    with pytest.raises((ValueError,sqlite3.DataError)):
        mod.publish_full_catalog(source, out, source_receipt=proof, client=client, maximum_hydrated_bytes=1024**2)
    assert not out.exists()


def test_entirely_empty_selected_tables_can_be_hydrated(tmp_path):
    mod = module(); source, proof = full_source(tmp_path, empty=True)
    with sqlite3.connect(source) as conn: conn.execute('DELETE FROM passive_market_observations')
    proof.update(verified_local_file=identity(source), database_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    client = ObjectStore(); out = tmp_path/'catalog.json'
    mod.publish_full_catalog(source, out, source_receipt=proof, client=client, maximum_hydrated_bytes=1024**2)
    from scripts.sqlite_history_union import hydrate_snapshot
    raw = out.read_bytes(); restored = tmp_path/'empty.sqlite3'
    report = hydrate_snapshot(source, restored, source_receipt=hot_proof(source, raw), catalog_bytes=raw, client=client)
    assert report['full_selected_tables_verified'] is True
    assert report['expected_table_counts'] == {'g1s_observations':0, 'passive_market_observations':0}
