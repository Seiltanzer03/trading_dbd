"""Verified rowid-preserving hot/cold SQLite compaction under a pinned catalog.

Retires only dependency-unreferenced cataloged `insufficient_future_data` rows
from `passive_market_observations`, preserves all surviving `rowid` values and
watermarks, closes pre-tombstone `ai_decision_acks` FK references via
`ensure_connection_schema`, and publishes a physically compacted zero-freelist
SQLite database file.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from scripts import sqlite_history_partition as archive
from scripts.restore_verified_snapshot import manifest_digest
from scripts.sqlite_history_catalog import (
    _schema,
    catalog_max_rowids,
    validate_full_catalog,
    verify_full_tables,
)
from scripts.sqlite_history_union import (
    CATALOG_CONTRACT,
    MAX_CATALOG_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_PARTITIONS,
)


def _load_ensure_connection_schema():
    try:
        from seiltanzer.ai_decision_state import ensure_connection_schema
        return ensure_connection_schema
    except ModuleNotFoundError:
        import importlib.util
        module_path = Path(__file__).resolve().parent.parent / 'seiltanzer' / 'ai_decision_state.py'
        spec = importlib.util.spec_from_file_location('_ai_decision_state_standalone', module_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ensure_connection_schema


ensure_connection_schema = _load_ensure_connection_schema()

CONTRACT = 'sqlite-history-cutover-v1'
TARGET_TABLE = 'passive_market_observations'
RETIRED_STATUS = 'insufficient_future_data'
MAX_SECONDS = 1800

DEPENDENCY_EXCLUSION_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ('g1_q_capture_attempts', ('created_observation_id',), ' IS NOT NULL'),
    ('g1_dataset_cut_members', ('observation_id',), ' IS NOT NULL'),
    ('g1c_shadow_predictions', ('observation_id',), ' IS NOT NULL'),
    ('virtual_position_observations', ('passive_observation_id', 'observation_id'), " IS NOT NULL AND resolution_status = 'pending'"),
    ('g1_dataset_membership', ('observation_id',), ' IS NOT NULL AND (forecast_eval_eligible = 1 OR q_to_p_eligible = 1)'),
)


def _parse_catalog(raw_bytes: bytes, expected_catalog_sha256: str | None = None) -> tuple[dict, str]:
    if not isinstance(raw_bytes, bytes) or not 0 < len(raw_bytes) <= MAX_CATALOG_BYTES:
        raise ValueError('Invalid or excessive catalog bytes')
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if expected_catalog_sha256 is not None and digest != expected_catalog_sha256:
        raise ValueError('Catalog SHA-256 mismatch against expected pin')

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate catalog JSON member')
            result[key] = value
        return result

    try:
        catalog = json.loads(raw_bytes, object_pairs_hook=unique)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid catalog JSON') from exc
    if not isinstance(catalog, dict) or catalog.get('contract') != CATALOG_CONTRACT:
        raise ValueError('Unsupported history catalog')
    parts = catalog.get('partitions')
    maximum = catalog.get('maximum_hydrated_bytes')
    if (not isinstance(parts, list) or not 0 < len(parts) <= MAX_PARTITIONS
            or type(maximum) is not int or not 4096 <= maximum <= MAX_OUTPUT_BYTES):
        raise ValueError('Invalid catalog bounds')
    validate_full_catalog(catalog)
    return catalog, digest


def _existing_tables(conn: sqlite3.Connection, schema_name: str = 'main') -> set[str]:
    qschema = archive._quote(schema_name)
    return {
        row[0]
        for row in conn.execute(
            f"SELECT name FROM {qschema}.sqlite_schema WHERE type='table'"
        ).fetchall()
    }


def _populate_protected_observation_ids(conn: sqlite3.Connection, schema_name: str = 'src') -> int:
    """Materialize an indexed temp table of dependency-referenced observation_ids."""
    existing = _existing_tables(conn, schema_name)
    qschema = archive._quote(schema_name)
    conn.execute('DROP TABLE IF EXISTS temp._cutover_protected_obs')
    conn.execute(
        'CREATE TEMP TABLE _cutover_protected_obs (observation_id TEXT PRIMARY KEY)'
    )
    for table, candidate_columns, predicate_suffix in DEPENDENCY_EXCLUSION_SPECS:
        if table not in existing:
            continue
        qtable = archive._quote(table)
        actual_cols = {
            r[1]
            for r in conn.execute(f'PRAGMA {qschema}.table_xinfo({qtable})').fetchall()
        }
        chosen_col = next((c for c in candidate_columns if c in actual_cols), None)
        if chosen_col is None:
            continue
        qcol = archive._quote(chosen_col)
        conn.execute(
            f'INSERT OR IGNORE INTO temp._cutover_protected_obs(observation_id) '
            f'SELECT {qcol} FROM {qschema}.{qtable} WHERE {qcol}{predicate_suffix}'
        )
    return int(
        conn.execute('SELECT COUNT(*) FROM temp._cutover_protected_obs').fetchone()[0]
    )


def _rowid_alias(conn: sqlite3.Connection, schema_name: str, table: str) -> tuple[str | None, list[str]]:
    qschema = archive._quote(schema_name)
    qtable = archive._quote(table)
    ddl_row = conn.execute(
        f"SELECT sql FROM {qschema}.sqlite_schema WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not ddl_row or not isinstance(ddl_row[0], str):
        raise ValueError(f'Missing table DDL for {table}')
    ddl = ddl_row[0]
    specs = conn.execute(f'PRAGMA {qschema}.table_xinfo({qtable})').fetchall()
    columns = [r[1] for r in specs if not r[6]]
    if re.search(r'WITHOUT\s+ROWID', ddl, re.I):
        return None, columns
    folded = {c.casefold() for c in columns}
    alias = next((name for name in ('rowid', '_rowid_', 'oid') if name not in folded), None)
    return alias, columns


def build_hot_cutover_snapshot(
    source: str | Path,
    destination: str | Path,
    *,
    catalog_bytes: bytes,
    expected_catalog_sha256: str | None = None,
    source_receipt: dict | None = None,
    verify_source_catalog_roots: bool = True,
) -> dict:
    """Build a physically compacted, rowid-preserving hot SQLite snapshot.

    1. Verifies `source` is an immutable regular SQLite file with no sidecars.
    2. Verifies `catalog_bytes` is a complete selected-table catalog and (when
       `verify_source_catalog_roots=True`) that `source`'s catalog prefix
       (`rowid <= catalog_max_rowid`) matches the pinned catalog's exact row
       counts and typed `logical_sha256` roots.
    3. Rebuilds directly from `source` (attached immutable read-only) into a
       fresh zero-freelist SQLite file while retiring only dependency-unreferenced
       cataloged `insufficient_future_data` rows from `passive_market_observations`
       (`rowid < catalog_max_rowid` and `rowid < MAX(rowid)`), then applies
       `ensure_connection_schema` to close the pre-tombstone `ai_decision_acks`
       FK references (`trade_id IN (77, 96)`).
    4. Preserves all `rowid` values across all tables, verifies `PRAGMA quick_check`
       and `PRAGMA foreign_key_check == []`, and returns a signed cutover receipt
       plus `hot_restore_proof` ready for `sqlite_history_union.hydrate_snapshot`.
    """
    source, destination = Path(source), Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    before = archive._identity(source)
    if source_receipt is not None:
        if (not isinstance(source_receipt, dict)
                or source_receipt.get('immutable_sqlite_read') is not True
                or not (source_receipt.get('snapshot_restore_verified') is True
                        or source_receipt.get('full_restore_verified') is True)
                or source_receipt.get('verified_local_file') != before
                or source_receipt.get('database_size_bytes') != before['size']
                or not re.fullmatch('[0-9a-f]{64}', str(source_receipt.get('database_sha256') or ''))):
            raise ValueError('Source lacks matching verified local restore proof')
    catalog, catalog_sha = _parse_catalog(catalog_bytes, expected_catalog_sha256)
    max_rowids = catalog_max_rowids(catalog)
    target_max_catalog_rowid = max_rowids.get(TARGET_TABLE)
    if target_max_catalog_rowid is None or target_max_catalog_rowid <= 0:
        raise ValueError('Catalog has no rows for passive_market_observations')

    deadline = time.monotonic() + MAX_SECONDS

    def check(*, needed: int = archive.RESERVE_BYTES) -> None:
        if time.monotonic() >= deadline:
            raise ValueError('Hot/cold cutover deadline exhausted')
        if shutil.disk_usage(destination.parent).free < needed:
            raise ValueError('Hot/cold cutover lacks required worker disk reserve')

    check(needed=archive.RESERVE_BYTES + before['size'])

    # Step 1: Verify source database's catalog prefix matches the pinned catalog roots
    # and measure post-catalog suffix before any modifications.
    post_catalog_proofs: dict[str, dict] = {}
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as ro_conn:
        ro_conn.execute('PRAGMA trusted_schema=OFF')
        ro_conn.execute('PRAGMA cache_size=-16384')
        ro_conn.set_progress_handler(lambda: time.monotonic() >= deadline, 5000)
        for table, proof in catalog['full_table_proofs'].items():
            qtable = archive._quote(table)
            alias = proof['source_rowid_alias']
            qalias = archive._quote(alias)
            max_rowid = max_rowids[table]
            suffix_where, suffix_params = ('', ()) if max_rowid is None else (' WHERE ' + qalias + '>?', (max_rowid,))
            post_count = ro_conn.execute('SELECT COUNT(*) FROM ' + qtable + suffix_where, suffix_params).fetchone()[0]
            if post_count > 0:
                post_catalog_proofs[table] = dict(
                    row_count=post_count,
                    logical_sha256=archive._logical_hash(
                        ro_conn, qtable, proof['columns'], alias, suffix_where, suffix_params
                    ),
                )
        if verify_source_catalog_roots:
            verify_full_tables(ro_conn, catalog, post_catalog_proofs=post_catalog_proofs)

    check(needed=archive.RESERVE_BYTES + before['size'])

    # Step 2: Build fresh compacted SQLite database (`work_path`) directly from
    # immutable `source` preserving exact rowids and retiring only eligible cold rows.
    fd_work, work_name = tempfile.mkstemp(prefix='.cutover-work-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd_work)
    work_path = Path(work_name)

    work_conn = None
    try:
        work_conn = sqlite3.connect(work_path, uri=True)
        work_conn.execute('PRAGMA cache_size=-32768')
        work_conn.execute('PRAGMA journal_mode=DELETE')
        work_conn.execute('PRAGMA synchronous=OFF')
        work_conn.execute('PRAGMA foreign_keys=OFF')
        work_conn.execute('PRAGMA temp_store=MEMORY')
        work_conn.set_progress_handler(lambda: time.monotonic() >= deadline, 5000)

        work_conn.execute(
            'ATTACH DATABASE ? AS src',
            (source.resolve().as_uri() + '?mode=ro&immutable=1',),
        )
        page_size = work_conn.execute('PRAGMA src.page_size').fetchone()[0]
        user_version = work_conn.execute('PRAGMA src.user_version').fetchone()[0]
        application_id = work_conn.execute('PRAGMA src.application_id').fetchone()[0]
        work_conn.execute(f'PRAGMA page_size={int(page_size)}')
        work_conn.execute(f'PRAGMA user_version={int(user_version)}')
        work_conn.execute(f'PRAGMA application_id={int(application_id)}')

        # Gather schema objects from `src.sqlite_schema`, putting `trades` and
        # `ai_decision_acks` first so `ensure_connection_schema` runs before
        # bulk table loads and leaves `freelist_count == 0`.
        raw_table_rows = work_conn.execute(
            "SELECT name, sql FROM src.sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rootpage, rowid"
        ).fetchall()
        priority_order = {'trades': 0, 'trade_lifecycle_identities': 1, 'ai_decision_acks': 2}
        table_rows = sorted(
            raw_table_rows,
            key=lambda item: (priority_order.get(item[0], 10),),
        )
        other_rows = work_conn.execute(
            "SELECT type, name, tbl_name, sql FROM src.sqlite_schema "
            "WHERE type IN ('index', 'trigger', 'view') "
            "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        ).fetchall()

        # Materialize indexed temp table of protected observation_ids for O(1) lookup.
        protected_obs_count = _populate_protected_observation_ids(work_conn, 'src')

        # Compute retirement boundary for `passive_market_observations`.
        source_max_rowid = work_conn.execute(
            f'SELECT MAX(rowid) FROM src.{archive._quote(TARGET_TABLE)}'
        ).fetchone()[0]
        source_total_target_rows = work_conn.execute(
            f'SELECT COUNT(*) FROM src.{archive._quote(TARGET_TABLE)}'
        ).fetchone()[0]
        retire_cutoff_rowid = min(int(target_max_catalog_rowid), int(source_max_rowid or 0))
        retire_predicate = (
            'p.rowid < ? '
            'AND p."resolution_status" = ? '
            'AND NOT EXISTS ('
            'SELECT 1 FROM temp._cutover_protected_obs x '
            'WHERE x.observation_id = p."observation_id"'
            ')'
        )
        retire_params = (retire_cutoff_rowid, RETIRED_STATUS)

        src_table_names = {name for name, _ in table_rows}
        fk_migrated = False
        table_row_counts: dict[str, int] = {}
        for name, ddl in table_rows:
            check()
            if name == 'trade_lifecycle_identities' and fk_migrated:
                table_row_counts[name] = work_conn.execute(
                    'SELECT COUNT(*) FROM main."trade_lifecycle_identities"'
                ).fetchone()[0]
                continue
            work_conn.execute(ddl)
            qname = archive._quote(name)
            alias, columns = _rowid_alias(work_conn, 'src', name)
            qcols = ','.join(archive._quote(c) for c in columns)
            if name == TARGET_TABLE:
                if alias is None:
                    raise ValueError('passive_market_observations must be a rowid table')
                qalias = archive._quote(alias)
                work_conn.execute(
                    f'INSERT INTO main.{qname}({qalias},{qcols}) '
                    f'SELECT p.{qalias},' + ','.join(f'p.{archive._quote(c)}' for c in columns) + ' '
                    f'FROM src.{qname} p '
                    f'WHERE NOT ({retire_predicate}) '
                    f'ORDER BY p.{qalias}',
                    retire_params,
                )
            elif alias is not None:
                qalias = archive._quote(alias)
                work_conn.execute(
                    f'INSERT INTO main.{qname}({qalias},{qcols}) '
                    f'SELECT {qalias},{qcols} FROM src.{qname} ORDER BY {qalias}'
                )
            else:
                work_conn.execute(
                    f'INSERT INTO main.{qname}({qcols}) SELECT {qcols} FROM src.{qname}'
                )
            table_row_counts[name] = work_conn.execute(f'SELECT COUNT(*) FROM main.{qname}').fetchone()[0]
            if name == 'ai_decision_acks' and 'trades' in src_table_names:
                ensure_connection_schema(work_conn)
                fk_migrated = True

        hot_target_count = table_row_counts[TARGET_TABLE]
        retired_count = source_total_target_rows - hot_target_count

        # Copy sqlite_sequence if present so AUTOINCREMENT sequences never regress.
        has_src_seq = work_conn.execute(
            "SELECT 1 FROM src.sqlite_schema WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
        has_main_seq = work_conn.execute(
            "SELECT 1 FROM main.sqlite_schema WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
        if has_src_seq and has_main_seq:
            work_conn.execute('DELETE FROM main.sqlite_sequence')
            work_conn.execute('INSERT INTO main.sqlite_sequence(name, seq) SELECT name, seq FROM src.sqlite_sequence')

        # Recreate indexes, triggers, and views after bulk table loads.
        existing_main_objects = {
            r[0]
            for r in work_conn.execute(
                "SELECT name FROM main.sqlite_schema WHERE type IN ('index', 'trigger', 'view')"
            ).fetchall()
        }
        for obj_type, name, tbl_name, ddl in other_rows:
            check()
            if name in existing_main_objects:
                continue
            work_conn.execute(ddl)

        work_conn.execute('DROP TABLE IF EXISTS temp._cutover_protected_obs')
        work_conn.commit()
        work_conn.execute('DETACH DATABASE src')

        # Verify compacted hot invariants.
        if hot_target_count + retired_count != source_total_target_rows:
            raise ValueError('Hot + retired row count does not equal source row count')
        hot_max_rowid = work_conn.execute(
            f'SELECT MAX(rowid) FROM main.{archive._quote(TARGET_TABLE)}'
        ).fetchone()[0]
        if hot_max_rowid != source_max_rowid:
            raise ValueError('Compacted hot database altered passive_market_observations MAX(rowid)')

        # Verify zero resolved or pending rows were retired.
        hot_status_counts = dict(
            work_conn.execute(
                f'SELECT resolution_status, COUNT(*) FROM main.{archive._quote(TARGET_TABLE)} GROUP BY resolution_status'
            ).fetchall()
        )
        freelist_count = work_conn.execute('PRAGMA freelist_count').fetchone()[0]
        if freelist_count != 0:
            raise ValueError(f'Compacted SQLite database has non-zero freelist_count={freelist_count}')
        if work_conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Compacted SQLite quick_check failed')
        fk_violations = work_conn.execute('PRAGMA foreign_key_check').fetchall()
        if fk_violations:
            raise ValueError(f'Compacted SQLite foreign_key_check failed: {fk_violations}')
        work_conn.close()
        work_conn = None

        if archive._identity(source) != before:
            raise ValueError('Source database changed during hot/cold cutover build')

        archive._identity(work_path)
        size = work_path.stat().st_size
        if size >= before['size'] and retired_count > 0:
            raise ValueError('Compacted SQLite file did not shrink despite retiring rows')
        digest = archive._digest(work_path)
        archive._publish(work_path, destination)
        dest_identity = archive._identity(destination)

        storage_manifest = dict(
            database_sha256=digest,
            database_size_bytes=size,
            cold_history_catalog_sha256=catalog_sha,
        )
        hot_restore_proof = dict(
            immutable_sqlite_read=True,
            snapshot_restore_verified=True,
            verified_local_file=dest_identity,
            database_sha256=digest,
            database_size_bytes=size,
            storage_manifest=storage_manifest,
            storage_manifest_sha256=manifest_digest(storage_manifest),
        )
        return dict(
            contract=CONTRACT,
            cutover_verified=True,
            source_database_size_bytes=before['size'],
            hot_database_size_bytes=size,
            reclaimed_bytes=before['size'] - size,
            hot_database_sha256=digest,
            catalog_sha256=catalog_sha,
            catalog_source_database_sha256=catalog['source_database_sha256'],
            target_table=TARGET_TABLE,
            target_max_catalog_rowid=target_max_catalog_rowid,
            source_max_rowid=source_max_rowid,
            hot_max_rowid=hot_max_rowid,
            source_target_row_count=source_total_target_rows,
            retired_row_count=retired_count,
            hot_target_row_count=hot_target_count,
            protected_observation_count=protected_obs_count,
            hot_status_counts=hot_status_counts,
            post_catalog_row_counts={t: p['row_count'] for t, p in post_catalog_proofs.items()},
            freelist_count=freelist_count,
            quick_check='ok',
            foreign_key_violations=0,
            verified_local_file=dest_identity,
            hot_restore_proof=hot_restore_proof,
        )
    finally:
        if work_conn is not None:
            work_conn.close()
        archive._cleanup(work_path)
