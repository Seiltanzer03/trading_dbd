"""Verified OFF-HOST hot/cold union for existing direct SQLite readers.

No live pruning, source writes, cloud credentials or production authority.
The caller must supply the complete catalog pinned by a verified hot restore.
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

CONTRACT = 'sqlite-history-union-v1'
CATALOG_CONTRACT = 'sqlite-history-catalog-v1'
MAX_CATALOG_BYTES = 8 * 1024**2
MAX_PARTITIONS = 1024
MAX_OUTPUT_BYTES = 25 * 1024**3
COPY_SECONDS = 300
MERGE_SECONDS = 30
MAX_SECONDS = 1800


def _catalog(raw, proof, before):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CATALOG_BYTES:
        raise ValueError('Invalid or excessive catalog bytes')
    if not isinstance(proof, dict):
        raise ValueError('Missing local hot restore proof')
    manifest = proof.get('storage_manifest')
    digest = hashlib.sha256(raw).hexdigest()
    if (proof.get('immutable_sqlite_read') is not True
            or not (proof.get('snapshot_restore_verified') is True or proof.get('full_restore_verified') is True)
            or proof.get('verified_local_file') != before
            or proof.get('database_size_bytes') != before['size']
            or not re.fullmatch('[0-9a-f]{64}', str(proof.get('database_sha256') or ''))
            or not isinstance(manifest, dict)
            or manifest_digest(manifest) != proof.get('storage_manifest_sha256')
            or manifest.get('database_sha256') != proof['database_sha256']
            or manifest.get('database_size_bytes') != before['size']
            or manifest.get('cold_history_catalog_sha256') != digest):
        raise ValueError('Catalog lacks matching verified hot manifest pin')
    # Ambiguous duplicate JSON members are not a catalog contract.
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate catalog JSON member')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid catalog JSON') from exc
    if not isinstance(value, dict) or value.get('contract') != CATALOG_CONTRACT:
        raise ValueError('Unsupported history catalog')
    parts, counts = value.get('partitions'), value.get('expected_table_counts')
    maximum = value.get('maximum_hydrated_bytes')
    if (not isinstance(parts, list) or not 0 < len(parts) <= MAX_PARTITIONS
            or not isinstance(counts, dict) or not counts
            or type(maximum) is not int or not before['size'] <= maximum <= MAX_OUTPUT_BYTES):
        raise ValueError('Invalid catalog bounds')
    keys, tables = set(), set()
    for receipt in parts:
        archive._validate_cloud(receipt, True)
        key = receipt['object_key']
        if key in keys:
            raise ValueError('Duplicate catalog partition')
        keys.add(key)
        tables.add(receipt['partition']['table'])
    if (set(counts) != tables
            or any(type(n) is not int or not 0 < n < 2**63 for n in counts.values())):
        raise ValueError('Catalog must declare exact affected table counts')
    return value, digest


def _check(deadline, directory, *, needed=archive.RESERVE_BYTES):
    if time.monotonic() >= deadline:
        raise ValueError('History union deadline exhausted')
    if shutil.disk_usage(directory).free < needed:
        raise ValueError('History union lacks unchanged 1GiB worker reserve')


def _merge(conn, restored, receipt):
    table, columns, alias = receipt['table'], receipt['columns'], receipt['source_rowid_alias']
    quote = archive._quote
    ddl = conn.execute("SELECT sql FROM main.sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
    if not ddl or ddl[0] != receipt['source_schema_sql']:
        raise ValueError('Hot and cold history schemas differ')
    specs = conn.execute('PRAGMA main.table_xinfo('+quote(table)+')').fetchall()
    if [r[1] for r in specs] != columns or any(r[6] for r in specs):
        raise ValueError('Hot and cold column contracts differ')
    ids = archive._rowids(receipt['rowids'])
    source_table, target_table = 'cold.'+quote(table), 'main.'+quote(table)
    where = ' WHERE '+quote(alias)+' IN ('+','.join('?' for _ in ids)+')'
    conn.execute('ATTACH DATABASE ? AS cold', (Path(restored).resolve().as_uri()+'?mode=ro&immutable=1',))
    try:
        overlap = [r[0] for r in conn.execute('SELECT '+quote(alias)+' FROM '+target_table+where, ids)]
        if overlap:
            overlap.sort()
            shared = ' WHERE '+quote(alias)+' IN ('+','.join('?' for _ in overlap)+')'
            if (archive._logical_hash(conn, target_table, columns, alias, shared, overlap)
                    != archive._logical_hash(conn, source_table, columns, alias, shared, overlap)):
                raise ValueError('Conflicting hot/cold history rowid')
        names = ','.join(quote(c) for c in columns)
        # Plain INSERT deliberately refuses PK/UNIQUE conflicts: no replace/ignore.
        denied = False
        def authorize(action, arg1, arg2, database, origin):
            nonlocal denied
            # SQLite supplies the actual trigger/view origin; SQL spelling,
            # comments and trigger-body side effects cannot bypass this guard.
            if origin is not None:
                denied = True
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        try:
            conn.execute('INSERT INTO '+target_table+'('+quote(alias)+','+names+') SELECT '
                         +quote(alias)+','+names+' FROM '+source_table+' WHERE '+quote(alias)
                         +' NOT IN (SELECT '+quote(alias)+' FROM '+target_table+')')
        except sqlite3.DatabaseError as exc:
            if denied:
                raise ValueError('History hydration cannot replay target INSERT trigger') from exc
            raise
        finally:
            conn.set_authorizer(None)
        if archive._logical_hash(conn, target_table, columns, alias, where, ids) != receipt['logical_sha256']:
            raise ValueError('History merge changed typed row values')
        conn.commit()
    finally:
        conn.rollback()
        conn.execute('DETACH DATABASE cold')


def hydrate_snapshot(source, destination, *, source_receipt, catalog_bytes, client):
    """Publish a complete verified union, or leave source/destination untouched."""
    source, destination = Path(source), Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    before = archive._identity(source)
    catalog, catalog_sha = _catalog(catalog_bytes, source_receipt, before)
    maximum = catalog['maximum_hydrated_bytes']
    deadline = time.monotonic() + MAX_SECONDS
    _check(deadline, destination.parent,
           needed=archive.RESERVE_BYTES + 2*maximum + 3*archive.MAX_BYTES)
    fd, name = tempfile.mkstemp(prefix='.history-union-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    work = Path(name)
    conn = original = None
    try:
        original = sqlite3.connect(source.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
        original.execute('PRAGMA trusted_schema=OFF')
        original.execute('PRAGMA cache_size=-2048')
        conn = sqlite3.connect(work, uri=True)
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA cache_size=-2048')
        copy_deadline = min(deadline, time.monotonic()+COPY_SECONDS)
        original.backup(conn, pages=256,
                        progress=lambda *_: _check(copy_deadline, destination.parent))
        original.close(); original = None
        _check(deadline, destination.parent)
        conn.execute('PRAGMA journal_mode=DELETE')
        page_size = conn.execute('PRAGMA page_size').fetchone()[0]
        if work.stat().st_size > maximum:
            raise ValueError('Hot snapshot exceeds catalog output bound')
        conn.execute('PRAGMA max_page_count='+str(maximum//page_size))
        conn.execute('PRAGMA temp_store=FILE')
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        for receipt in catalog['partitions']:
            _check(deadline, destination.parent)
            with tempfile.TemporaryDirectory(prefix='.history-union-cold-', dir=destination.parent) as directory:
                restored = Path(directory)/'rows.sqlite3'
                archive.restore_from_cloud(client, receipt, restored)
                _check(deadline, destination.parent)
                merge_deadline = min(deadline, time.monotonic()+MERGE_SECONDS)
                _check(merge_deadline, destination.parent)
                conn.set_progress_handler(lambda: time.monotonic() >= merge_deadline, 1000)
                _merge(conn, restored, receipt['partition'])
                _check(merge_deadline, destination.parent)
                conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            _check(deadline, destination.parent)
        counts = {}
        for table, expected in catalog['expected_table_counts'].items():
            counts[table] = conn.execute('SELECT COUNT(*) FROM '+archive._quote(table)).fetchone()[0]
            if counts[table] != expected:
                raise ValueError('Hydrated table count differs from complete catalog')
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Hydrated SQLite quick_check failed')
        if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ValueError('Hydrated SQLite foreign key check failed')
        conn.close(); conn = None
        _check(deadline, destination.parent)
        if archive._identity(source) != before:
            raise ValueError('Verified hot source changed during hydration')
        size = work.stat().st_size
        if size > maximum:
            raise ValueError('Hydrated output exceeds catalog byte bound')
        digest = hashlib.sha256()
        with work.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024**2), b''):
                _check(deadline, destination.parent)
                digest.update(chunk)
        if archive._identity(source) != before:
            raise ValueError('Verified hot source changed before publication')
        archive._identity(work)  # No mutable output sidecars may be published.
        archive._publish(work, destination)
        return dict(contract=CONTRACT, history_union_verified=True,
                    database_sha256=digest.hexdigest(), database_size_bytes=size,
                    hot_database_sha256=source_receipt['database_sha256'], catalog_sha256=catalog_sha,
                    expected_table_counts=counts, typed_values_verified=True,
                    verified_local_file=archive._identity(destination),
                    production_authority=False, data_retirement_authorized=False)
    finally:
        if original is not None:
            original.close()
        if conn is not None:
            conn.close()
        archive._cleanup(work)
