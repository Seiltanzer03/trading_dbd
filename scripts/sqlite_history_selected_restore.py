"""Reconstitute ONLY two complete history tables from a pinned private catalog.

This off-host data artifact is not a full database restore, dependency closure,
production database, or authorization to retire any source history.
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
from scripts.sqlite_history_catalog import _schema, validate_full_catalog, verify_full_tables
from scripts.sqlite_history_union import (
    CATALOG_CONTRACT, MAX_CATALOG_BYTES, MAX_OUTPUT_BYTES, MAX_PARTITIONS,
    MERGE_SECONDS, _merge,
)

MAX_SECONDS = 1800


def _catalog(raw, expected):
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CATALOG_BYTES
            or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)
            or hashlib.sha256(raw).hexdigest() != expected):
        raise ValueError('Selected history requires matching bounded catalog pin')

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate selected catalog JSON member')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid selected catalog JSON') from exc
    if not isinstance(value, dict) or value.get('contract') != CATALOG_CONTRACT:
        raise ValueError('Unsupported selected history catalog')
    parts, counts = value.get('partitions'), value.get('expected_table_counts')
    maximum = value.get('maximum_hydrated_bytes')
    if (not isinstance(parts, list) or len(parts) > MAX_PARTITIONS
            or not isinstance(counts, dict) or set(counts) != archive.TABLES
            or any(type(n) is not int or not 0 <= n < 2**63 for n in counts.values())
            or type(maximum) is not int or not 4096 <= maximum <= MAX_OUTPUT_BYTES
            or value.get('production_authority') is not False
            or value.get('data_retirement_authorized') is not False):
        raise ValueError('Invalid selected history catalog bounds or scope')
    validate_full_catalog(value)
    keys = [part['object_key'] for part in parts]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate selected history partition')
    for table, proof in value['full_table_proofs'].items():
        ddl, columns = proof['source_schema_sql'], proof['columns']
        if (len(ddl.encode('utf-8')) > 65536
                or not re.match(r'CREATE TABLE\s+(?:"'+table+r'"|'+table+r')\s*\(', ddl, re.I)
                or not 0 < len(columns) <= 128
                or any(not isinstance(c, str) for c in columns)
                or len({c.casefold() for c in columns}) != len(columns)
                or proof['source_rowid_alias'] in {c.casefold() for c in columns}):
            raise ValueError('Invalid original selected history schema')
    return value


def restore_catalog_tables(client, destination, *, catalog_bytes, expected_catalog_sha256):
    """Publish a selected-table artifact after complete typed-root verification."""
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    catalog = _catalog(catalog_bytes, expected_catalog_sha256)
    maximum = catalog['maximum_hydrated_bytes']
    deadline = time.monotonic()+MAX_SECONDS

    def check(*, needed=archive.RESERVE_BYTES):
        if time.monotonic() >= deadline:
            raise ValueError('Selected history restore deadline exhausted')
        if shutil.disk_usage(destination.parent).free < needed:
            raise ValueError('Selected restore lacks unchanged 1GiB worker reserve')

    check(needed=archive.RESERVE_BYTES+2*maximum+3*archive.MAX_BYTES)
    fd, name = tempfile.mkstemp(prefix='.selected-history-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    work = Path(name)
    conn = None
    try:
        conn = sqlite3.connect(work, uri=True)
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA cache_size=-2048')
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('PRAGMA temp_store=FILE')
        conn.execute('PRAGMA page_size=4096')
        conn.execute('PRAGMA max_page_count='+str(maximum//4096))
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, archive.MAX_ROW_BYTES+65536)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        for table, proof in catalog['full_table_proofs'].items():
            check()
            conn.execute(proof['source_schema_sql'])
            if any(_schema(conn, table)[key] != proof[key]
                   for key in ('columns', 'source_schema_sql', 'source_rowid_alias')):
                raise ValueError('Created selected history schema differs')
        conn.commit()
        for receipt in catalog['partitions']:
            check(needed=archive.RESERVE_BYTES+3*archive.MAX_BYTES)
            with tempfile.TemporaryDirectory(prefix='.selected-cold-', dir=destination.parent) as directory:
                restored = Path(directory)/'rows.sqlite3'
                archive.restore_from_cloud(client, receipt, restored)
                check()
                merge_deadline = min(deadline, time.monotonic()+MERGE_SECONDS)
                conn.set_progress_handler(lambda: time.monotonic() >= merge_deadline, 1000)
                _merge(conn, restored, receipt['partition'])
                if time.monotonic() >= merge_deadline:
                    raise ValueError('Selected history merge deadline exhausted')
                conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            check()
        verify_full_tables(conn, catalog)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if tables != archive.TABLES or conn.execute("SELECT 1 FROM sqlite_schema WHERE type IN ('view','trigger')").fetchone():
            raise ValueError('Selected history artifact has unexpected schema objects')
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Selected history SQLite quick_check failed')
        # Deliberately no full-database FK claim: referenced tables may be omitted.
        # The separate full hot/cold union retains its strict global FK gate.
        conn.close(); conn = None
        before = archive._identity(work)
        if not 0 < before['size'] <= maximum:
            raise ValueError('Selected history exceeds catalog output bound')
        digest = hashlib.sha256()
        with work.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024**2), b''):
                check()
                digest.update(chunk)
        check()
        if archive._identity(work) != before:
            raise ValueError('Selected history changed before publication')
        archive._publish(work, destination)
        return dict(contract='sqlite-selected-history-restore-v1',
                    scope='SELECTED_HISTORY_TABLES_ONLY',
                    full_selected_tables_verified=True, full_database_integrity_verified=False,
                    replaces_production_database=False, typed_values_verified=True,
                    database_sha256=digest.hexdigest(), database_size_bytes=before['size'],
                    catalog_sha256=expected_catalog_sha256,
                    source_database_sha256=catalog['source_database_sha256'],
                    expected_table_counts=catalog['expected_table_counts'],
                    partition_count=len(catalog['partitions']),
                    verified_local_file=archive._identity(destination),
                    production_authority=False, data_retirement_authorized=False)
    finally:
        if conn is not None:
            conn.close()
        archive._cleanup(work)
