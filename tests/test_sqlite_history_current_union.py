"""Current verified hot outcomes and immutable catalog captures are distinct."""
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import sqlite_history_cutover as cutover
from scripts import sqlite_history_union as union
from scripts.restore_verified_snapshot import manifest_digest
from test_sqlite_history_cutover import _build_cutover_fixture
from test_sqlite_history_partition import identity


def _hot(tmp_path):
    source, proof, raw, pin, client = _build_cutover_fixture(tmp_path, include_post_catalog=True)
    path = tmp_path / 'hot.sqlite3'
    result = cutover.build_hot_cutover_snapshot(
        source, path, source_receipt=proof, catalog_bytes=raw, expected_catalog_sha256=pin)
    return source, path, result, raw, client


def _current_proof(path, result):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = dict(result['hot_restore_proof']['storage_manifest'],
                    database_sha256=digest, database_size_bytes=path.stat().st_size)
    return dict(result['hot_restore_proof'], database_sha256=digest,
                database_size_bytes=path.stat().st_size, verified_local_file=identity(path),
                storage_manifest=manifest, storage_manifest_sha256=manifest_digest(manifest))


def test_current_union_preserves_resolved_hot_prefix_and_appended_rows(tmp_path):
    _, hot, result, raw, client = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            conn.execute("UPDATE passive_market_observations SET resolution_status='resolved' "
                         "WHERE observation_id='obs-pending-110'")
    proof = _current_proof(hot, result)
    destination = tmp_path / 'full-current.sqlite3'
    report = union.hydrate_current_snapshot(
        hot, destination, source_receipt=proof, catalog_bytes=raw, client=client)
    with closing(sqlite3.connect(destination)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone() == (14,)
        assert conn.execute("SELECT resolution_status FROM passive_market_observations "
                            "WHERE observation_id='obs-pending-110'").fetchone() == ('resolved',)
        assert conn.execute("SELECT rowid FROM passive_market_observations "
                            "WHERE observation_id='obs-post-140'").fetchone() == (140,)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert report['current_hot_rows_preserved'] is True
    assert report['original_cold_table_roots_verified'] is True
    assert report['contract'] == 'sqlite-history-current-union-v1'
    assert report['production_authority'] is False
    assert hashlib.sha256(hot.read_bytes()).hexdigest() == proof['database_sha256']


def test_current_union_allows_new_hot_growth_beyond_original_catalog_budget(tmp_path):
    _, hot, result, raw, client = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        # This table is new after the frozen catalog; preserve it byte-for-byte.
        conn.execute('CREATE TABLE newer_history(payload BLOB)')
        conn.execute('INSERT INTO newer_history VALUES(zeroblob(11000000))')
        conn.commit()
    assert hot.stat().st_size > json.loads(raw)['maximum_hydrated_bytes']
    proof = _current_proof(hot, result)
    out = tmp_path / 'current-expanded.sqlite3'
    report = union.hydrate_current_snapshot(
        hot, out, source_receipt=proof, catalog_bytes=raw, client=client)
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute('SELECT length(payload) FROM newer_history').fetchone() == (11000000,)
        assert conn.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone() == (14,)
    assert report['original_cold_table_roots_verified'] is True
    assert report['database_size_bytes'] <= report['effective_output_bound_bytes']
    assert report['catalog_output_bound_bytes'] == 10 * 1024**2


def test_current_union_does_not_hit_old_sqlite_page_cap_during_cold_merge(tmp_path):
    source, proof, raw, _, client = _build_cutover_fixture(tmp_path, include_post_catalog=True)
    catalog = json.loads(raw)
    catalog['maximum_hydrated_bytes'] = source.stat().st_size + 32768
    raw = json.dumps(catalog, sort_keys=True).encode()
    hot = tmp_path / 'hot.sqlite3'
    result = cutover.build_hot_cutover_snapshot(
        source, hot, source_receipt=proof, catalog_bytes=raw,
        expected_catalog_sha256=hashlib.sha256(raw).hexdigest())
    with closing(sqlite3.connect(hot)) as conn:
        conn.execute('CREATE TABLE growth(payload BLOB)')
        conn.execute('INSERT INTO growth VALUES(zeroblob(50000))')
        conn.commit()
    assert hot.stat().st_size < catalog['maximum_hydrated_bytes']
    out = tmp_path / 'with-cold.sqlite3'
    report = union.hydrate_current_snapshot(
        hot, out, source_receipt=_current_proof(hot, result), catalog_bytes=raw, client=client)
    assert out.stat().st_size > catalog['maximum_hydrated_bytes']
    assert report['original_cold_table_roots_verified'] is True
    assert report['current_hot_rows_preserved'] is True


@pytest.mark.parametrize('field,value', [('payload', 'rewritten-input'),
                                       ('symbol', 'WRONG'),
                                       ('observation_id', 'different-capture')])
def test_current_union_refuses_changed_immutable_capture(tmp_path, field, value):
    _, hot, result, raw, client = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            conn.execute(f'UPDATE passive_market_observations SET {field}=? WHERE rowid=110', (value,))
    destination = tmp_path / 'forbidden.sqlite3'
    with pytest.raises(ValueError, match='Conflicting hot/cold history rowid'):
        union.hydrate_current_snapshot(hot, destination, source_receipt=_current_proof(hot, result),
                                       catalog_bytes=raw, client=client)
    assert not destination.exists()


@pytest.mark.parametrize('change', ['unlisted_hole', 'resurrected_retired_row'])
def test_current_union_requires_exact_recorded_retirement_set(tmp_path, change):
    source, hot, result, raw, client = _hot(tmp_path)
    with closing(sqlite3.connect(hot)) as conn:
        with conn:
            if change == 'unlisted_hole':
                conn.execute('DELETE FROM passive_market_observations WHERE rowid=100')
            else:
                conn.execute('ATTACH DATABASE ? AS original', (str(source),))
                conn.execute('INSERT INTO passive_market_observations(rowid,observation_id,symbol,resolution_status,payload) '
                             'SELECT rowid,* FROM original.passive_market_observations WHERE rowid=10')
    destination = tmp_path / 'forbidden.sqlite3'
    with pytest.raises(ValueError, match='retired rowid'):
        union.hydrate_current_snapshot(hot, destination, source_receipt=_current_proof(hot, result),
                                       catalog_bytes=raw, client=client)
    assert not destination.exists()


def test_current_union_independently_checks_complete_original_cold_roots(tmp_path):
    source, proof, raw, _, client = _build_cutover_fixture(tmp_path)
    catalog = json.loads(raw)
    catalog['full_table_proofs']['passive_market_observations']['logical_sha256'] = '0' * 64
    raw = json.dumps(catalog, sort_keys=True).encode()
    manifest = dict(database_sha256=proof['database_sha256'], database_size_bytes=source.stat().st_size,
                    cold_history_catalog_sha256=hashlib.sha256(raw).hexdigest())
    proof = dict(proof, storage_manifest=manifest, storage_manifest_sha256=manifest_digest(manifest))
    destination = tmp_path / 'forbidden.sqlite3'
    with pytest.raises(ValueError, match='original cold typed table root'):
        union.hydrate_current_snapshot(source, destination, source_receipt=proof,
                                       catalog_bytes=raw, client=client)
    assert not destination.exists()


def test_hot_history_state_is_embedded_immutable_and_bound_to_manifest(tmp_path):
    _, hot, result, raw, _ = _hot(tmp_path)
    from seiltanzer.history_storage_state import read_history_state, validate_manifest_history
    with closing(sqlite3.connect(hot)) as conn:
        conn.execute('PRAGMA trusted_schema=OFF')
        state = read_history_state(conn)
        assert state['retired_rowids'] == {'passive_market_observations': [10, 20, 30, 40]}
        assert state['catalog_sha256'] == hashlib.sha256(raw).hexdigest()
        validate_manifest_history(conn, result['hot_restore_proof']['storage_manifest'])
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM hot_history_storage_state')
        manifest = dict(result['hot_restore_proof']['storage_manifest'], cold_history_catalog_sha256='0' * 64)
        with pytest.raises(ValueError, match='history.*pin|pin.*history'):
            validate_manifest_history(conn, manifest)
