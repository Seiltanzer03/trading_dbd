"""Review regressions: source proof, immutable ACKs, and current union limits."""
import hashlib
import sqlite3
from contextlib import closing

import pytest

from scripts import sqlite_history_cutover as cutover
from scripts import sqlite_history_union as union
from scripts.restore_verified_snapshot import manifest_digest
from seiltanzer.ai_decision_state import ensure_connection_schema
from test_sqlite_history_cutover import _build_cutover_fixture
from test_sqlite_history_partition import identity


@pytest.mark.parametrize('missing', ['source_receipt', 'expected_catalog_sha256'])
def test_cutover_requires_both_verified_source_and_catalog_pin(tmp_path, missing):
    source, proof, raw, pin, _ = _build_cutover_fixture(tmp_path)
    args = dict(source_receipt=proof, expected_catalog_sha256=pin, catalog_bytes=raw)
    args.pop(missing)
    destination = tmp_path / 'unverified.sqlite3'
    with pytest.raises(ValueError, match='verified local restore proof|catalog SHA-256 pin'):
        cutover.build_hot_cutover_snapshot(source, destination, **args)
    assert not destination.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == proof['database_sha256']


def test_cutover_cannot_disable_catalog_root_verification(tmp_path):
    source, proof, raw, pin, _ = _build_cutover_fixture(tmp_path)
    destination = tmp_path / 'bypassed.sqlite3'
    with pytest.raises(ValueError, match='root verification is mandatory'):
        cutover.build_hot_cutover_snapshot(
            source, destination, source_receipt=proof, catalog_bytes=raw,
            expected_catalog_sha256=pin, verify_source_catalog_roots=False,
        )
    assert not destination.exists()


def test_cutover_rejects_an_unrecognized_existing_dependency_schema(tmp_path):
    source, proof, raw, pin, _ = _build_cutover_fixture(tmp_path)
    with closing(sqlite3.connect(source)) as conn:
        with conn:
            conn.execute('ALTER TABLE g1_q_capture_attempts '
                         'RENAME COLUMN created_observation_id TO unexpected_source_id')
    proof = dict(proof, verified_local_file=identity(source),
                 database_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                 database_size_bytes=source.stat().st_size)
    destination = tmp_path / 'unknown_dependency.sqlite3'
    with pytest.raises(ValueError, match='Unsupported dependency schema.*g1_q_capture_attempts'):
        cutover.build_hot_cutover_snapshot(
            source, destination, source_receipt=proof, catalog_bytes=raw,
            expected_catalog_sha256=pin,
        )
    assert not destination.exists()


def test_cutover_with_no_eligible_retirement_does_not_publish_a_larger_database(tmp_path):
    source, proof, raw, pin, _ = _build_cutover_fixture(tmp_path)
    with closing(sqlite3.connect(source)) as conn:
        with conn:
            conn.executemany('INSERT INTO g1_q_capture_attempts VALUES(?,?)',
                             [(f'protect-{n}', f'obs-retire-{n}') for n in (10, 20, 30, 40)])
    proof = dict(proof, verified_local_file=identity(source),
                 database_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                 database_size_bytes=source.stat().st_size)
    destination = tmp_path/'no-cutover.sqlite3'
    with pytest.raises(ValueError, match='No eligible cold retirement'):
        cutover.build_hot_cutover_snapshot(source, destination, source_receipt=proof,
                                           catalog_bytes=raw, expected_catalog_sha256=pin)
    assert not destination.exists()


@pytest.mark.parametrize('cursor', [30, None])
def test_cutover_never_retires_source_rows_a_materializer_has_not_seen(tmp_path, cursor):
    source, proof, raw, pin, _ = _build_cutover_fixture(tmp_path)
    with closing(sqlite3.connect(source)) as conn:
        with conn:
            if cursor is None:
                conn.execute('DELETE FROM g1s_materialization_state')
            else:
                conn.execute('UPDATE g1s_materialization_state SET source_watermark=?', (cursor,))
    proof = dict(proof, verified_local_file=identity(source),
                 database_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                 database_size_bytes=source.stat().st_size)
    destination = tmp_path/'unprocessed.sqlite3'
    if cursor is None:
        with pytest.raises(ValueError, match='No eligible cold retirement'):
            cutover.build_hot_cutover_snapshot(source, destination, source_receipt=proof,
                                               catalog_bytes=raw, expected_catalog_sha256=pin)
        assert not destination.exists()
    else:
        cutover.build_hot_cutover_snapshot(source, destination, source_receipt=proof,
                                           catalog_bytes=raw, expected_catalog_sha256=pin)
        with closing(sqlite3.connect(destination)) as conn:
            assert conn.execute('SELECT observation_id FROM passive_market_observations WHERE rowid=40').fetchone() == ('obs-retire-40',)


@pytest.mark.parametrize('column,value', [
    ('status', 'not_executed'), ('policy', 'EXIT'), ('note', 'replacement'),
    ('decision_id', 'replacement'), ('ts', 1792280000.0),
])
def test_retained_orphan_ack_cannot_be_rewritten(tmp_path, column, value):
    source, _, _, _, _ = _build_cutover_fixture(tmp_path)
    with closing(sqlite3.connect(source)) as conn:
        with conn:
            ensure_connection_schema(conn)
        conn.execute('PRAGMA trusted_schema=OFF')
        before = conn.execute('SELECT * FROM ai_decision_acks WHERE trade_id=77').fetchone()
        with pytest.raises(sqlite3.IntegrityError, match='requires trades.id'):
            conn.execute(f'UPDATE ai_decision_acks SET {column}=? WHERE trade_id=77', (value,))
        assert conn.execute('SELECT * FROM ai_decision_acks WHERE trade_id=77').fetchone() == before
        # An existing genuine trade remains usable for manual acknowledgement.
        conn.execute('UPDATE ai_decision_acks SET note=? WHERE trade_id=100', ('manual note',))


def test_schema_upgrade_replaces_the_earlier_trade_id_only_update_guard(tmp_path):
    source, _, _, _, _ = _build_cutover_fixture(tmp_path)
    with closing(sqlite3.connect(source)) as conn:
        with conn:
            ensure_connection_schema(conn)
            conn.execute('DROP TRIGGER trg_ai_decision_acks_require_live_trade_update')
            conn.execute('''CREATE TRIGGER trg_ai_decision_acks_require_live_trade_update
                BEFORE UPDATE OF trade_id ON ai_decision_acks
                WHEN NOT EXISTS(SELECT 1 FROM trades WHERE id=NEW.trade_id)
                BEGIN SELECT RAISE(ABORT,'old trade_id-only guard'); END''')
            ensure_connection_schema(conn)
        with pytest.raises(sqlite3.IntegrityError, match='requires trades.id'):
            conn.execute("UPDATE ai_decision_acks SET note='rewrite' WHERE trade_id=77")


def test_current_union_refuses_a_retained_pending_row_that_later_resolved(tmp_path):
    """This safe refusal records an unresolved production-cutover blocker."""
    source, proof, raw, pin, client = _build_cutover_fixture(tmp_path)
    hot = tmp_path / 'hot.sqlite3'
    result = cutover.build_hot_cutover_snapshot(
        source, hot, source_receipt=proof, catalog_bytes=raw,
        expected_catalog_sha256=pin,
    )
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            conn.execute("UPDATE passive_market_observations SET resolution_status='resolved' "
                         "WHERE observation_id='obs-pending-110'")
    digest = hashlib.sha256(hot.read_bytes()).hexdigest()
    manifest = dict(database_sha256=digest, database_size_bytes=hot.stat().st_size,
                    cold_history_catalog_sha256=pin)
    updated = dict(result['hot_restore_proof'], database_sha256=digest,
                   database_size_bytes=hot.stat().st_size, verified_local_file=identity(hot),
                   storage_manifest=manifest, storage_manifest_sha256=manifest_digest(manifest))
    destination = tmp_path / 'must_not_publish.sqlite3'
    with pytest.raises(ValueError, match='Conflicting hot/cold history rowid'):
        union.hydrate_snapshot(hot, destination, source_receipt=updated,
                               catalog_bytes=raw, client=client)
    assert not destination.exists()
    assert hashlib.sha256(hot.read_bytes()).hexdigest() == digest
