"""Retirement may reuse old cold bytes, never overwrite new current outcomes."""
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


def _proof(source, proof, pin):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = dict(database_sha256=digest, database_size_bytes=source.stat().st_size,
                    cold_history_catalog_sha256=pin)
    return dict(proof, database_sha256=digest, database_size_bytes=source.stat().st_size,
                verified_local_file=identity(source), storage_manifest=manifest,
                storage_manifest_sha256=manifest_digest(manifest))


def test_current_compaction_keeps_updated_outcome_and_only_retires_exact_cold_rows(tmp_path):
    source, proof, raw, pin, client = _build_cutover_fixture(
        tmp_path, include_post_catalog=True, include_mutable_outcome=True)
    with closing(sqlite3.connect(source)) as conn:
        with conn:
            conn.execute("UPDATE passive_market_observations SET resolution_status='resolved' WHERE rowid=110")
            # A current mutable field may also differ on an IFD row. It must stay hot.
            conn.execute('UPDATE passive_market_observations SET resolved_ts=123.0 WHERE rowid=20')
    proof = _proof(source, proof, pin)
    hot = tmp_path/'current-hot.sqlite3'
    result = cutover.build_current_hot_cutover_snapshot(
        source, hot, source_receipt=proof, catalog_bytes=raw,
        expected_catalog_sha256=pin, client=client)
    assert result['retired_row_count'] == 3
    assert result['source_catalog_verification'] == 'CURRENT_CAPTURE_AND_EXACT_RETIREMENT'
    assert result['data_retirement_authorized'] is False
    with closing(sqlite3.connect(hot)) as conn:
        assert conn.execute('SELECT resolution_status FROM passive_market_observations WHERE rowid=110').fetchone() == ('resolved',)
        assert conn.execute('SELECT resolved_ts FROM passive_market_observations WHERE rowid=20').fetchone() == (123.0,)
    full = tmp_path/'reconstituted.sqlite3'
    union.hydrate_current_snapshot(hot, full, source_receipt=result['hot_restore_proof'],
                                   catalog_bytes=raw, client=client)
    with closing(sqlite3.connect(full)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone() == (14,)
        assert conn.execute('SELECT resolution_status FROM passive_market_observations WHERE rowid=110').fetchone() == ('resolved',)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == proof['database_sha256']


@pytest.mark.parametrize('change', ['capture', 'hole', 'g1s', 'source_hash'])
def test_current_compaction_refuses_unproven_current_source(tmp_path, change):
    source, proof, raw, pin, client = _build_cutover_fixture(tmp_path)
    with closing(sqlite3.connect(source)) as conn:
        with conn:
            if change == 'capture':
                conn.execute("UPDATE passive_market_observations SET payload='rewritten' WHERE rowid=10")
            elif change == 'hole':
                conn.execute('DELETE FROM passive_market_observations WHERE rowid=10')
            elif change == 'g1s':
                conn.execute("UPDATE g1s_observations SET payload='rewritten' WHERE rowid=5")
    proof = _proof(source, proof, pin)
    if change == 'source_hash':
        proof['database_sha256'] = '0'*64
        proof['storage_manifest']['database_sha256'] = '0'*64
        proof['storage_manifest_sha256'] = manifest_digest(proof['storage_manifest'])
    hot = tmp_path/'rejected.sqlite3'
    with pytest.raises(ValueError, match='capture|coverage|source SHA'):
        cutover.build_current_hot_cutover_snapshot(
            source, hot, source_receipt=proof, catalog_bytes=raw,
            expected_catalog_sha256=pin, client=client)
    assert not hot.exists()


def test_current_compaction_checks_complete_cold_root_before_any_publication(tmp_path):
    source, proof, raw, _, client = _build_cutover_fixture(tmp_path)
    catalog = json.loads(raw)
    catalog['full_table_proofs']['passive_market_observations']['logical_sha256'] = '0'*64
    raw = json.dumps(catalog, sort_keys=True).encode()
    pin = hashlib.sha256(raw).hexdigest()
    hot = tmp_path/'bad-root.sqlite3'
    with pytest.raises(ValueError, match='original cold typed table root'):
        cutover.build_current_hot_cutover_snapshot(
            source, hot, source_receipt=_proof(source, proof, pin), catalog_bytes=raw,
            expected_catalog_sha256=pin, client=client)
    assert not hot.exists()
    assert not list(tmp_path.glob('.retirement-proof-*'))
    assert not list(tmp_path.glob('.cutover-work-*'))


@pytest.mark.parametrize('pin', [None, '', '0'*64])
def test_current_compaction_rejects_bad_explicit_pin_before_cloud_access(tmp_path, pin):
    source, proof, raw, actual_pin, _ = _build_cutover_fixture(tmp_path)
    class NoCloud:
        def get_object(self, **_):
            pytest.fail('invalid explicit pin reached cloud')
    with pytest.raises(ValueError, match='pin'):
        cutover.build_current_hot_cutover_snapshot(
            source, tmp_path/'bad-pin.sqlite3', source_receipt=_proof(source, proof, actual_pin),
            catalog_bytes=raw, expected_catalog_sha256=pin, client=NoCloud())
