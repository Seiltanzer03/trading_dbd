"""Versioned hot state binds actual rejected metadata and exact diagnostics."""
import copy

import pytest

from seiltanzer import history_storage_state as state
from seiltanzer import history_runtime_projection as projection
from seiltanzer.g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION
from test_history_rejected_source_index import rejected_source
from test_history_runtime_rollups import real_payload
from test_g1a_dataset_contract import _insert_resolved


@pytest.fixture
def runtime_state(rejected_source):
    import time
    engine, output = rejected_source
    payload = real_payload(engine)
    index = projection.create_rejected_source_index(output, engine._conn, [1],
        dataset_contract_version=G1_DATASET_CONTRACT_VERSION, deadline=time.monotonic()+5)
    rollups = projection.write_runtime_rollups(output, payload)
    import datetime as dt
    captured = dt.datetime(2026,8,11,15,tzinfo=dt.timezone.utc).timestamp()
    _insert_resolved(engine, 'keep', anchor='keep', captured=captured, target=captured+900)
    for table in ('passive_market_observations', 'g1_dataset_membership'):
        ddl = engine._conn.execute("SELECT sql FROM sqlite_schema WHERE name=?", (table,)).fetchone()[0]
        output.execute(ddl)
    row = engine._conn.execute("SELECT rowid,* FROM passive_market_observations WHERE observation_id='keep'").fetchone()
    columns = [r[1] for r in output.execute('PRAGMA table_info(passive_market_observations)')]
    output.execute('INSERT INTO passive_market_observations(rowid,'+','.join(columns)+') VALUES('+','.join('?' for _ in row)+')', tuple(row))
    for row in engine._conn.execute('SELECT * FROM g1_dataset_membership'):
        output.execute('INSERT INTO g1_dataset_membership VALUES('+','.join('?' for _ in row)+')', tuple(row))
    output.execute('CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY)')
    engine._conn.execute('CREATE TABLE IF NOT EXISTS g1s_observations(observation_id TEXT PRIMARY KEY)')
    projection.install_source_guards(output)
    data = dict(contract='sqlite-history-hot-state-v2', bucket=state.BUCKET,
        catalog_source_database_sha256='1'*64, catalog_sha256='2'*64,
        catalog_object_key=state.catalog_key('1'*64, '2'*64), cutover_source_database_sha256='3'*64,
        retired_rowids={'passive_market_observations':[1]}, source_materialization_watermark=2,
        retained_identity={table:state.identity_proof(output, table, boundary)
                           for table,boundary in (('passive_market_observations',2),('g1s_observations',None))},
        runtime_projection=dict(contract=projection.PROJECTION_CONTRACT,rejected_index=index,rollups=rollups),
        production_authority=False)
    return output, data


def test_v2_state_binds_actual_index_rollups_and_transport_pins(runtime_state):
    conn, data = runtime_state
    state.write_history_state(conn, data)
    assert state.read_history_state(conn) == data
    fields = state.history_manifest_fields(conn)
    assert fields['sqlite_history_state_sha256']
    assert state.validate_manifest_history(conn, fields) == fields


@pytest.mark.parametrize('hazard', ['missing_rollups', 'wrong_membership_hash', 'wrong_retired_ids', 'changed_rollup_pin'])
def test_v2_state_refuses_incomplete_or_conflicting_projection(runtime_state, hazard):
    conn, original = runtime_state
    data = copy.deepcopy(original)
    if hazard == 'missing_rollups':
        conn.execute('DROP TABLE archived_passive_rollups')
    elif hazard == 'wrong_membership_hash':
        conn.execute("UPDATE g1_dataset_membership SET source_record_sha256=?", ('f'*64,))
    elif hazard == 'wrong_retired_ids':
        data['retired_rowids']['passive_market_observations'] = [2]
        data['retained_identity']['passive_market_observations']['max_rowid'] = 3
    else:
        data['runtime_projection']['rollups']['payload_sha256'] = 'f'*64
    with pytest.raises(ValueError):
        state.write_history_state(conn, data)
        state.history_manifest_fields(conn)


def test_full_worker_consumes_only_storage_guards_and_projection(runtime_state):
    conn, data = runtime_state
    state.write_history_state(conn, data)
    conn.execute('CREATE TABLE unrelated(id INTEGER PRIMARY KEY)')
    conn.execute("CREATE TRIGGER unrelated_guard BEFORE INSERT ON unrelated BEGIN SELECT RAISE(ABORT,'unrelated guard'); END")
    state.begin_full_history_hydration(conn)
    assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name='unrelated_guard'").fetchone()[0] == 1
    state.consume_history_state(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    assert not {'hot_history_storage_state','archived_passive_rollups','archived_passive_rejections'} & names
    assert {'passive_market_observations','g1_dataset_membership','unrelated'} <= names


def test_actual_current_union_restores_full_rows_and_consumes_v2_projection(runtime_state, rejected_source, tmp_path):
    import hashlib
    import sqlite3
    from scripts.sqlite_history_catalog import publish_full_catalog
    from scripts.sqlite_history_union import hydrate_current_snapshot
    from scripts.restore_verified_snapshot import manifest_digest
    from test_ede_ledger_archive import ObjectStore
    from test_sqlite_history_partition import identity
    engine, _ = rejected_source
    conn, data = runtime_state
    source, hot = tmp_path/'full.sqlite3', tmp_path/'hot.sqlite3'
    engine._conn.commit()
    with sqlite3.connect(source) as target:
        engine._conn.backup(target)
        target.execute('PRAGMA journal_mode=DELETE')
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    source_proof = dict(immutable_sqlite_read=True, snapshot_restore_verified=True,
        verified_local_file=identity(source),database_sha256=source_sha,database_size_bytes=source.stat().st_size)
    client = ObjectStore()
    catalog = tmp_path/'catalog.json'
    publish_full_catalog(source, catalog, source_receipt=source_proof, client=client, maximum_hydrated_bytes=10*1024**2)
    raw = catalog.read_bytes()
    pin = hashlib.sha256(raw).hexdigest()
    data.update(catalog_source_database_sha256=source_sha, catalog_sha256=pin,
                catalog_object_key=state.catalog_key(source_sha,pin),cutover_source_database_sha256=source_sha)
    state.write_history_state(conn, data)
    conn.commit()
    with sqlite3.connect(hot) as target:
        conn.backup(target)
    manifest = dict(database_sha256=hashlib.sha256(hot.read_bytes()).hexdigest(),
                    database_size_bytes=hot.stat().st_size,**state.history_manifest_fields(conn))
    proof = dict(immutable_sqlite_read=True,snapshot_restore_verified=True,verified_local_file=identity(hot),
                 database_sha256=manifest['database_sha256'],database_size_bytes=manifest['database_size_bytes'],
                 storage_manifest=manifest,storage_manifest_sha256=manifest_digest(manifest))
    full = tmp_path/'union.sqlite3'
    result = hydrate_current_snapshot(hot,full,source_receipt=proof,catalog_bytes=raw,client=client)
    assert result['history_union_verified'] is True
    with sqlite3.connect(full) as restored:
        assert restored.execute('SELECT count(*) FROM passive_market_observations').fetchone() == (2,)
        assert state.read_history_state(restored) is None
        assert not restored.execute("SELECT 1 FROM sqlite_schema WHERE name LIKE 'archived_passive_%'").fetchall()
        assert not restored.execute("SELECT 1 FROM sqlite_schema WHERE name LIKE 'trg_archive_source_%'").fetchall()
