"""A compact page image must retain the actual SQLite records and identities."""
import hashlib
import sqlite3
import stat
import time

import pytest

from scripts import sqlite_history_partition as archive
from scripts import sqlite_page_repack as owner


def source_fixture(tmp_path):
    source = tmp_path / 'original.db'
    with sqlite3.connect(source) as conn:
        conn.executescript('''
            PRAGMA user_version=17;
            PRAGMA application_id=12345;
            CREATE TABLE wide(id TEXT PRIMARY KEY, value, payload TEXT);
            CREATE TABLE counter(id INTEGER PRIMARY KEY AUTOINCREMENT, parent TEXT REFERENCES wide(id));
            CREATE INDEX wide_value ON wide(value);
            CREATE TABLE audit(n INTEGER);
            CREATE TRIGGER write_audit AFTER INSERT ON counter BEGIN INSERT INTO audit VALUES(new.id); END;
            CREATE VIEW projected AS SELECT id,value FROM wide;
        ''')
        # Real hidden-rowid holes, actual mixed SQLite storage classes and large
        # overflow payloads; rowid renumbering would corrupt existing links.
        conn.executemany('INSERT INTO wide(rowid,id,value,payload) VALUES(?,?,?,?)',
            [(7 + 3*i, str(i), [None, b'\x00\xff', -7, 1.25, 'text'][i % 5], 'z' * 65537)
             for i in range(40)])
        conn.execute("INSERT INTO counter(id,parent) VALUES(900,'0')")
        conn.execute('DELETE FROM counter')
    proof = dict(immutable_sqlite_read=True, immutable_snapshot_verified=True,
        verified_local_file=archive._identity(source),
        database_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        database_size_bytes=source.stat().st_size)
    return source, proof


def run_repack(tmp_path, **kwargs):
    source, proof = source_fixture(tmp_path)
    before = source.read_bytes()
    destination = tmp_path / 'compact.db'
    result = owner.repack_snapshot(source, destination, source_receipt=proof, **kwargs)
    assert source.read_bytes() == before
    return source, destination, result


def test_repack_preserves_rows_types_rowids_schema_sequence_and_headers(tmp_path):
    source, compact, result = run_repack(tmp_path)
    assert result['all_typed_table_roots_verified'] is True
    assert result['all_rows_preserved'] is True
    assert result['data_retirement_authorized'] is False
    assert result['production_authority'] is False
    assert result['reclaimed_bytes'] == source.stat().st_size - compact.stat().st_size > 0
    with sqlite3.connect(source) as old, sqlite3.connect(compact) as new:
        assert new.execute('PRAGMA page_size').fetchone() == (1024,)
        for pragma in ('user_version', 'application_id', 'encoding'):
            assert new.execute('PRAGMA ' + pragma).fetchone() == old.execute('PRAGMA ' + pragma).fetchone()
        assert old.execute('SELECT rowid,* FROM wide ORDER BY rowid').fetchall() == new.execute(
            'SELECT rowid,* FROM wide ORDER BY rowid').fetchall()
        assert old.execute('SELECT rowid,* FROM sqlite_sequence').fetchall() == new.execute(
            'SELECT rowid,* FROM sqlite_sequence').fetchall()
        new.execute("INSERT INTO counter(parent) VALUES('1')")
        assert new.execute('SELECT id FROM counter').fetchone() == (901,)
        assert new.execute('SELECT n FROM audit ORDER BY rowid DESC LIMIT 1').fetchone() == (901,)
        assert new.execute('SELECT count(*) FROM projected').fetchone() == (40,)


@pytest.mark.parametrize('mutation', ['hash', 'identity', 'size', 'unverified'])
def test_missing_or_wrong_source_proof_never_publishes(tmp_path, mutation):
    source, proof = source_fixture(tmp_path)
    if mutation == 'hash': proof['database_sha256'] = '0' * 64
    if mutation == 'identity': proof['verified_local_file'] = {}
    if mutation == 'size': proof['database_size_bytes'] -= 1
    if mutation == 'unverified': proof['immutable_snapshot_verified'] = False
    output = tmp_path / 'result.db'
    with pytest.raises(ValueError): owner.repack_snapshot(source, output, source_receipt=proof)
    assert not output.exists()


def test_output_collision_preserves_existing_bytes(tmp_path):
    source, proof = source_fixture(tmp_path)
    output = tmp_path / 'result.db'; output.write_bytes(b'owned by someone else')
    with pytest.raises(FileExistsError): owner.repack_snapshot(source, output, source_receipt=proof)
    assert output.read_bytes() == b'owned by someone else'


def test_source_sidecar_is_refused(tmp_path):
    source, proof = source_fixture(tmp_path)
    (tmp_path / 'original.db-wal').write_bytes(b'active')
    with pytest.raises(ValueError): owner.repack_snapshot(source, tmp_path / 'result.db', source_receipt=proof)


def test_expired_deadline_and_capacity_refuse_without_output(tmp_path, monkeypatch):
    source, proof = source_fixture(tmp_path)
    output = tmp_path / 'result.db'
    with pytest.raises(ValueError): owner.repack_snapshot(source, output, source_receipt=proof, deadline=time.monotonic()-1)
    monkeypatch.setattr(owner.shutil, 'disk_usage', lambda _: type('Space', (), {'free': 0})())
    with pytest.raises(ValueError): owner.repack_snapshot(source, output, source_receipt=proof)
    assert not output.exists()


def test_different_typed_root_is_not_a_verified_repack(tmp_path, monkeypatch):
    source, proof = source_fixture(tmp_path)
    actual = archive._logical_hash
    def tamper(conn, table, *args, **kwargs):
        root = actual(conn, table, *args, **kwargs)
        if conn.execute('PRAGMA page_size').fetchone()[0] == 1024: return '0'*64
        return root
    monkeypatch.setattr(archive, '_logical_hash', tamper)
    output = tmp_path / 'result.db'
    with pytest.raises(ValueError, match='typed'): owner.repack_snapshot(source, output, source_receipt=proof)
    assert not output.exists()
    assert not list(tmp_path.glob('.page-repack-*'))


def test_unsupported_schema_is_not_silently_skipped(tmp_path):
    source, _ = source_fixture(tmp_path)
    with sqlite3.connect(source) as conn: conn.execute('CREATE TABLE unsupported(k TEXT PRIMARY KEY) WITHOUT ROWID')
    proof = dict(immutable_sqlite_read=True, immutable_snapshot_verified=True,
        verified_local_file=archive._identity(source), database_size_bytes=source.stat().st_size,
        database_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='Unsupported'): owner.repack_snapshot(source, tmp_path / 'result.db', source_receipt=proof)


def test_nonshrinking_image_is_not_published(tmp_path):
    source, proof = source_fixture(tmp_path)
    with pytest.raises(ValueError, match='smaller'):
        owner.repack_snapshot(source, tmp_path / 'result.db', source_receipt=proof, page_size=4096)


def test_source_mutation_during_copy_refuses_output(tmp_path, monkeypatch):
    source, proof = source_fixture(tmp_path)
    actual = owner._tables
    def mutate(conn):
        tables = actual(conn)
        with sqlite3.connect(source) as writer:
            writer.execute("INSERT INTO wide(id,payload) VALUES('new legitimate row','new')")
        return tables
    monkeypatch.setattr(owner, '_tables', mutate)
    with pytest.raises(ValueError, match='changed'):
        owner.repack_snapshot(source, tmp_path / 'result.db', source_receipt=proof)
    assert not (tmp_path / 'result.db').exists()
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT payload FROM wide WHERE id='new legitimate row'").fetchone() == ('new',)


def test_destination_created_at_publication_survives(tmp_path, monkeypatch):
    source, proof = source_fixture(tmp_path)
    actual = archive._publish
    def collision(work, destination):
        destination.write_bytes(b'another owner')
        actual(work, destination)
    monkeypatch.setattr(archive, '_publish', collision)
    output = tmp_path / 'result.db'
    with pytest.raises(FileExistsError): owner.repack_snapshot(source, output, source_receipt=proof)
    assert output.read_bytes() == b'another owner'
    assert not list(tmp_path.glob('.page-repack-*'))


def test_existing_cold_history_marker_and_pins_survive(tmp_path):
    from scripts.history_storage_loader import history_state as state
    source, _ = source_fixture(tmp_path)
    with sqlite3.connect(source) as conn:
        for table in ('passive_market_observations', 'g1s_observations'):
            conn.execute('CREATE TABLE '+table+'(observation_id TEXT PRIMARY KEY)')
            conn.execute('INSERT INTO '+table+'(rowid,observation_id) VALUES(9,?)', (table,))
        data = dict(contract=state.CONTRACT, bucket=state.BUCKET,
            catalog_source_database_sha256='1'*64, catalog_sha256='2'*64,
            catalog_object_key=state.catalog_key('1'*64, '2'*64),
            cutover_source_database_sha256='3'*64,
            retired_rowids={'passive_market_observations': [2]},
            source_materialization_watermark=9,
            retained_identity={t:state.identity_proof(conn,t,9)
                for t in ('passive_market_observations','g1s_observations')},
            production_authority=False)
        state.write_history_state(conn, data)
        pins = state.history_manifest_fields(conn)
    proof = dict(immutable_sqlite_read=True, immutable_snapshot_verified=True,
        verified_local_file=archive._identity(source), database_size_bytes=source.stat().st_size,
        database_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    output = tmp_path / 'result.db'
    result = owner.repack_snapshot(source, output, source_receipt=proof)
    assert {k:result[k] for k in state.PIN_FIELDS} == pins
    with sqlite3.connect(output) as conn: assert state.read_history_state(conn) == data


def test_directory_fsync_failure_retires_only_owned_publication(tmp_path, monkeypatch):
    source, proof = source_fixture(tmp_path)
    before = source.read_bytes()
    actual = owner.os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(owner.os.fstat(fd).st_mode): raise OSError('directory fsync failed')
        return actual(fd)
    monkeypatch.setattr(owner.os, 'fsync', fail_directory)
    output = tmp_path / 'result.db'
    with pytest.raises(OSError, match='directory fsync'):
        owner.repack_snapshot(source, output, source_receipt=proof)
    assert source.read_bytes() == before
    assert not output.exists()
    assert not list(tmp_path.glob('.page-repack-*'))


def test_replacement_inode_survives_postpublication_failure(tmp_path, monkeypatch):
    source, proof = source_fixture(tmp_path)
    actual = archive._publish
    def replaced(work, destination):
        actual(work, destination)
        destination.unlink()
        # Keep the original inode open to prevent inode reuse in the fixture.
        destination.write_bytes(b'replaced by another owner')
        raise OSError('after publication')
    monkeypatch.setattr(archive, '_publish', replaced)
    output = tmp_path / 'result.db'
    with pytest.raises(OSError): owner.repack_snapshot(source, output, source_receipt=proof)
    assert output.read_bytes() == b'replaced by another owner'


def test_v2_runtime_projection_and_pins_survive(tmp_path):
    from scripts.history_storage_loader import history_state as state
    projection = state._runtime_projection()
    source, _ = source_fixture(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.executescript('''
            CREATE TABLE passive_market_observations(observation_id TEXT PRIMARY KEY);
            CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY);
            CREATE TABLE g1_dataset_membership(observation_id TEXT,dataset_contract_version TEXT,
                source_record_sha256 TEXT,forecast_eval_eligible INTEGER,q_to_p_eligible INTEGER);
        ''')
        conn.execute("INSERT INTO passive_market_observations(rowid,observation_id) VALUES(2,'kept')")
        conn.execute(projection.DDL)
        conn.execute('INSERT INTO archived_passive_rejections VALUES(?,?,?,?,?,?,?,?,?)',
            (1,'old','TEST',1.,2.,1,'feature','insufficient_future_data','f'*64))
        for sql in projection._guards().values(): conn.execute(sql)
        conn.execute('INSERT INTO g1_dataset_membership VALUES(?,?,?,?,?)',('old','dataset','f'*64,0,0))
        index = projection._index_proof(conn,'dataset')
        statistics = dict.fromkeys(projection.STATISTIC_FIELDS,0)
        statistics.update(raw_n=1,resolution_counts={'insufficient_future_data':1},
            feature_version_counts={'feature':1},current_f32_n=1,sigma_missing_n=1)
        payload = dict(contract=projection.ROLLUP_CONTRACT,source_contract_version='feature',
            measurement_contract_version='measurement',dataset_contract_version='dataset',
            statistics=statistics,measurement=dict.fromkeys(projection.MEASUREMENT_FIELDS,0),
            evidence_eligible_n=0,production_authority=False)
        rollups = projection.write_runtime_rollups(conn,payload)
        projection.install_source_guards(conn)
        data = dict(contract=state.RUNTIME_CONTRACT,bucket=state.BUCKET,
            catalog_source_database_sha256='1'*64,catalog_sha256='2'*64,
            catalog_object_key=state.catalog_key('1'*64,'2'*64),cutover_source_database_sha256='3'*64,
            retired_rowids={'passive_market_observations':[1]},source_materialization_watermark=2,
            retained_identity={t:state.identity_proof(conn,t,boundary)
                for t,boundary in (('passive_market_observations',2),('g1s_observations',None))},
            runtime_projection=dict(contract=projection.PROJECTION_CONTRACT,rejected_index=index,rollups=rollups),
            production_authority=False)
        state.write_history_state(conn,data)
        pins = state.history_manifest_fields(conn)
    proof = dict(immutable_sqlite_read=True,immutable_snapshot_verified=True,
        verified_local_file=archive._identity(source),database_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        database_size_bytes=source.stat().st_size)
    result = owner.repack_snapshot(source,tmp_path/'v2.db',source_receipt=proof)
    with sqlite3.connect(tmp_path/'v2.db') as conn:
        assert state.read_history_state(conn) == data
        assert state.history_manifest_fields(conn) == pins
    assert {k:result[k] for k in state.PIN_FIELDS} == pins
