"""Tests for rowid-preserving hot/cold SQLite compaction and full hydration."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import sqlite_history_catalog as catalog_mod
from scripts import sqlite_history_cutover as cutover_mod
from scripts import sqlite_history_partition as partition_mod
from scripts import sqlite_history_union as union_mod
from seiltanzer.ai_decision_state import PRE_TOMBSTONE_DELETE_CUTOFF_TS
from test_ede_ledger_archive import ObjectStore
from test_sqlite_history_partition import identity


def _build_cutover_fixture(tmp_path, *, include_post_catalog=False, include_mutable_outcome=False):
    from contextlib import closing
    source = tmp_path / 'source.sqlite3'
    with closing(sqlite3.connect(source)) as conn:
        conn.execute('PRAGMA page_size=4096')
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                opened_at REAL,
                deleted_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ai_decision_acks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL REFERENCES trades(id),
                decision_id TEXT NOT NULL,
                policy TEXT NOT NULL,
                status TEXT NOT NULL,
                ts REAL NOT NULL,
                note TEXT
            )
            """
        )
        conn.execute("INSERT INTO trades(id, symbol, opened_at, deleted_at) VALUES (100, 'BTCUSDT', 1787000000.0, NULL)")
        # Simulate pre-tombstone orphan ACKs (trade_id 77, 96) + live ACK (trade_id 100)
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.executemany(
            'INSERT INTO ai_decision_acks(id, trade_id, decision_id, policy, status, ts, note) VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                (1, 77, 'dec-77', 'warn_only', 'executed', PRE_TOMBSTONE_DELETE_CUTOFF_TS - 5000.0, 'old-77'),
                (2, 96, 'dec-96', 'warn_only', 'not_executed', PRE_TOMBSTONE_DELETE_CUTOFF_TS - 1000.0, 'old-96'),
                (3, 100, 'dec-100', 'warn_only', 'executed', PRE_TOMBSTONE_DELETE_CUTOFF_TS + 1000.0, 'live-100'),
            ],
        )
        conn.execute(
            """
            CREATE TABLE passive_market_observations (
                observation_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                resolution_status TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE g1s_observations (
                observation_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE g1_q_capture_attempts (
                attempt_id TEXT PRIMARY KEY,
                created_observation_id TEXT
            )
            """
        )
        conn.execute('CREATE TABLE g1s_materialization_state '
                     '(materializer TEXT PRIMARY KEY,source_watermark INTEGER NOT NULL)')
        conn.execute("INSERT INTO g1s_materialization_state VALUES('fixed_horizon_t0',120)")
        conn.execute(
            """
            CREATE TABLE g1_dataset_cut_members (
                cut_id TEXT NOT NULL,
                observation_id TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE g1c_shadow_predictions (
                prediction_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE virtual_position_observations (
                virtual_id TEXT PRIMARY KEY,
                passive_observation_id TEXT NOT NULL,
                resolution_status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE g1_dataset_membership (
                observation_id TEXT PRIMARY KEY,
                forecast_eval_eligible INTEGER NOT NULL DEFAULT 0,
                q_to_p_eligible INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Populate passive_market_observations with explicit non-contiguous rowids
        # and large payloads so physical compaction measurably shrinks the file.
        blob_text = 'X' * 16384
        passive_rows = [
            # Unreferenced insufficient_future_data rows (should be retired)
            (10, 'obs-retire-10', 'BTCUSDT', 'insufficient_future_data', blob_text + '-10'),
            (20, 'obs-retire-20', 'ETHUSDT', 'insufficient_future_data', blob_text + '-20'),
            (30, 'obs-retire-30', 'SOLUSDT', 'insufficient_future_data', blob_text + '-30'),
            (40, 'obs-retire-40', 'BNBUSDT', 'insufficient_future_data', blob_text + '-40'),
            # Referenced insufficient_future_data rows (must stay hot!)
            (50, 'obs-qcap-50', 'BTCUSDT', 'insufficient_future_data', 'keep-qcap'),
            (60, 'obs-cut-60', 'BTCUSDT', 'insufficient_future_data', 'keep-cut'),
            (70, 'obs-g1c-70', 'BTCUSDT', 'insufficient_future_data', 'keep-g1c'),
            (80, 'obs-vp-pending-80', 'BTCUSDT', 'insufficient_future_data', 'keep-vp-pending'),
            (90, 'obs-eligible-90', 'BTCUSDT', 'insufficient_future_data', 'keep-eligible'),
            # Resolved & pending rows (must stay hot!)
            (100, 'obs-resolved-100', 'BTCUSDT', 'resolved', 'keep-resolved'),
            (110, 'obs-pending-110', 'ETHUSDT', 'pending', 'keep-pending'),
            # MAX(rowid) in catalog (must stay hot even if insufficient_future_data!)
            (120, 'obs-max-120', 'SOLUSDT', 'insufficient_future_data', 'keep-max-rowid'),
        ]
        conn.executemany(
            'INSERT INTO passive_market_observations(rowid, observation_id, symbol, resolution_status, payload) VALUES (?, ?, ?, ?, ?)',
            passive_rows,
        )
        conn.executemany(
            'INSERT INTO g1s_observations(rowid, observation_id, symbol, payload) VALUES (?, ?, ?, ?)',
            [
                (5, 'obs-retire-10', 'BTCUSDT', 'g1s-10'),
                (15, 'obs-resolved-100', 'BTCUSDT', 'g1s-100'),
                (25, 'obs-max-120', 'SOLUSDT', 'g1s-120'),
            ],
        )
        conn.execute("INSERT INTO g1_q_capture_attempts VALUES ('att-1', 'obs-qcap-50')")
        conn.execute("INSERT INTO g1_dataset_cut_members VALUES ('cut-1', 'obs-cut-60')")
        conn.execute("INSERT INTO g1c_shadow_predictions VALUES ('pred-1', 'obs-g1c-70')")
        conn.execute("INSERT INTO virtual_position_observations VALUES ('vp-1', 'obs-vp-pending-80', 'pending')")
        conn.execute("INSERT INTO g1_dataset_membership VALUES ('obs-eligible-90', 1, 0)")
        conn.commit()

    if include_mutable_outcome:
        with closing(sqlite3.connect(source)) as conn:
            with conn:
                conn.execute('ALTER TABLE passive_market_observations ADD COLUMN resolved_ts REAL')
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    source_receipt = dict(
        immutable_sqlite_read=True,
        snapshot_restore_verified=True,
        verified_local_file=identity(source),
        database_size_bytes=source.stat().st_size,
        database_sha256=source_sha,
    )
    client = ObjectStore()
    catalog_path = tmp_path / 'catalog.json'
    catalog_mod.publish_full_catalog(
        source,
        catalog_path,
        source_receipt=source_receipt,
        client=client,
        maximum_hydrated_bytes=10 * 1024**2,
    )
    catalog_bytes = catalog_path.read_bytes()
    catalog_sha = hashlib.sha256(catalog_bytes).hexdigest()

    if include_post_catalog:
        with closing(sqlite3.connect(source)) as conn:
            conn.execute('PRAGMA journal_mode=DELETE')
            conn.executemany(
                'INSERT INTO passive_market_observations(rowid, observation_id, symbol, resolution_status, payload) VALUES (?, ?, ?, ?, ?)',
                [
                    (130, 'obs-post-130', 'BTCUSDT', 'insufficient_future_data', 'post-catalog-ifd-must-stay-hot'),
                    (140, 'obs-post-140', 'ETHUSDT', 'pending', 'post-catalog-pending-must-stay-hot'),
                ],
            )
            conn.execute(
                'INSERT INTO g1s_observations(rowid, observation_id, symbol, payload) VALUES (?, ?, ?, ?)',
                (35, 'obs-post-130', 'BTCUSDT', 'g1s-post-130'),
            )
            conn.commit()
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        source_receipt = dict(
            immutable_sqlite_read=True,
            snapshot_restore_verified=True,
            verified_local_file=identity(source),
            database_size_bytes=source.stat().st_size,
            database_sha256=source_sha,
        )

    return source, source_receipt, catalog_bytes, catalog_sha, client


@pytest.mark.parametrize('include_post_catalog', [False, True])
def test_hot_cutover_preserves_rowids_shrinks_file_fixes_fk_and_hydrates_back_to_exact_roots(
    tmp_path, include_post_catalog
):
    source, source_receipt, catalog_bytes, catalog_sha, client = _build_cutover_fixture(
        tmp_path, include_post_catalog=include_post_catalog
    )
    hot_path = tmp_path / 'hot_compacted.sqlite3'
    report = cutover_mod.build_hot_cutover_snapshot(
        source,
        hot_path,
        catalog_bytes=catalog_bytes,
        expected_catalog_sha256=catalog_sha,
        source_receipt=source_receipt,
    )

    assert report['cutover_verified'] is True
    assert report['retired_row_count'] == 4  # rowids 10, 20, 30, 40
    assert report['reclaimed_bytes'] > 40_000
    assert report['hot_database_size_bytes'] < report['source_database_size_bytes']
    assert report['freelist_count'] == 0
    assert report['quick_check'] == 'ok'
    assert report['foreign_key_violations'] == 0
    assert report['hot_max_rowid'] == (140 if include_post_catalog else 120)

    # Verify surviving rowids in hot_path are preserved 100% without VACUUM renumbering
    with sqlite3.connect(hot_path) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        surviving_passive = conn.execute(
            'SELECT rowid, observation_id FROM passive_market_observations ORDER BY rowid'
        ).fetchall()
        expected_hot_passive = [
            (50, 'obs-qcap-50'),
            (60, 'obs-cut-60'),
            (70, 'obs-g1c-70'),
            (80, 'obs-vp-pending-80'),
            (90, 'obs-eligible-90'),
            (100, 'obs-resolved-100'),
            (110, 'obs-pending-110'),
            (120, 'obs-max-120'),
        ]
        if include_post_catalog:
            expected_hot_passive += [
                (130, 'obs-post-130'),
                (140, 'obs-post-140'),
            ]
        assert surviving_passive == expected_hot_passive

        surviving_g1s = conn.execute(
            'SELECT rowid, observation_id FROM g1s_observations ORDER BY rowid'
        ).fetchall()
        expected_g1s = [
            (5, 'obs-retire-10'),
            (15, 'obs-resolved-100'),
            (25, 'obs-max-120'),
        ]
        if include_post_catalog:
            expected_g1s.append((35, 'obs-post-130'))
        assert surviving_g1s == expected_g1s

        # Verify all 3 ai_decision_acks rows survived and trade_lifecycle_identities has 77, 96, 100
        acks = conn.execute('SELECT id, trade_id, decision_id FROM ai_decision_acks ORDER BY id').fetchall()
        assert acks == [(1, 77, 'dec-77'), (2, 96, 'dec-96'), (3, 100, 'dec-100')]
        identities = conn.execute(
            'SELECT trade_id, lifecycle_origin FROM trade_lifecycle_identities ORDER BY trade_id'
        ).fetchall()
        assert identities == [
            (77, 'pre_tombstone_hard_deleted_parent'),
            (96, 'pre_tombstone_hard_deleted_parent'),
            (100, 'trades_row_present'),
        ]

    # Now hydrate the compacted hot DB with cold partitions from ObjectStore and prove 100% reconstitution
    hydrated_path = tmp_path / 'hydrated.sqlite3'
    union_report = union_mod.hydrate_snapshot(
        hot_path,
        hydrated_path,
        source_receipt=report['hot_restore_proof'],
        catalog_bytes=catalog_bytes,
        client=client,
    )
    assert union_report['history_union_verified'] is True
    assert union_report['full_selected_tables_verified'] is True
    assert union_report['typed_values_verified'] is True

    # Compare hydrated passive_market_observations and g1s_observations to original source DB
    from contextlib import closing
    with closing(sqlite3.connect(source)) as src_conn, closing(sqlite3.connect(hydrated_path)) as hyd_conn:
        for table in ('passive_market_observations', 'g1s_observations'):
            cols = [r[1] for r in src_conn.execute(f'PRAGMA table_xinfo("{table}")').fetchall()]
            assert (
                partition_mod._logical_hash(src_conn, f'"{table}"', cols, 'rowid')
                == partition_mod._logical_hash(hyd_conn, f'"{table}"', cols, 'rowid')
            )


def test_post_cutover_hot_writes_advance_rowid_and_still_hydrate_cleanly(tmp_path):
    from contextlib import closing
    from scripts.restore_verified_snapshot import manifest_digest
    source, source_receipt, catalog_bytes, catalog_sha, client = _build_cutover_fixture(
        tmp_path, include_post_catalog=False
    )
    hot_path = tmp_path / 'hot_compacted.sqlite3'
    report = cutover_mod.build_hot_cutover_snapshot(
        source,
        hot_path,
        catalog_bytes=catalog_bytes,
        expected_catalog_sha256=catalog_sha,
        source_receipt=source_receipt,
    )
    with closing(sqlite3.connect(hot_path)) as conn:
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute(
            "INSERT INTO passive_market_observations(observation_id, symbol, resolution_status, payload) "
            "VALUES ('obs-live-after-cutover', 'BTCUSDT', 'pending', 'new-live-row')"
        )
        new_rowid = conn.execute(
            "SELECT rowid FROM passive_market_observations WHERE observation_id='obs-live-after-cutover'"
        ).fetchone()[0]
        assert new_rowid > 120
        conn.commit()

    hot_sha = hashlib.sha256(hot_path.read_bytes()).hexdigest()
    hot_size = hot_path.stat().st_size
    manifest = dict(
        database_sha256=hot_sha,
        database_size_bytes=hot_size,
        cold_history_catalog_sha256=catalog_sha,
    )
    updated_proof = dict(
        immutable_sqlite_read=True,
        snapshot_restore_verified=True,
        verified_local_file=identity(hot_path),
        database_sha256=hot_sha,
        database_size_bytes=hot_size,
        storage_manifest=manifest,
        storage_manifest_sha256=manifest_digest(manifest),
    )
    hydrated_path = tmp_path / 'hydrated_after_write.sqlite3'
    union_report = union_mod.hydrate_snapshot(
        hot_path,
        hydrated_path,
        source_receipt=updated_proof,
        catalog_bytes=catalog_bytes,
        client=client,
    )
    assert union_report['history_union_verified'] is True
    assert union_report['expected_table_counts']['passive_market_observations'] == 13


def test_cutover_refuses_bad_catalog_pin_or_existing_destination(tmp_path):
    source, source_receipt, catalog_bytes, catalog_sha, _ = _build_cutover_fixture(tmp_path)
    existing = tmp_path / 'existing.sqlite3'
    existing.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        cutover_mod.build_hot_cutover_snapshot(
            source, existing, catalog_bytes=catalog_bytes, expected_catalog_sha256=catalog_sha
        )
    assert existing.read_bytes() == b'keep'

    out = tmp_path / 'bad_pin.sqlite3'
    with pytest.raises(ValueError, match='Catalog SHA-256 mismatch'):
        cutover_mod.build_hot_cutover_snapshot(
            source,
            out,
            catalog_bytes=catalog_bytes,
            expected_catalog_sha256='0' * 64,
            source_receipt=source_receipt,
        )
    assert not out.exists()

