"""Complete selected-table catalogs from verified, quiescent OFF-HOST copies.

Full typed roots are measured independently of chunks. This is transport and
restore evidence, never permission to delete live history or activate a model.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from scripts import sqlite_history_partition as archive
from scripts.sqlite_history_union import CATALOG_CONTRACT, MAX_CATALOG_BYTES, MAX_OUTPUT_BYTES, MAX_PARTITIONS

MAX_SECONDS = 3600
CHUNK_PAYLOAD_BYTES = archive.MAX_BYTES // 2


def _schema(conn, table):
    quote = archive._quote
    row = conn.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
    if not row or not isinstance(row[0], str) or len(row[0]) > 65536 or re.search(r'WITHOUT\s+ROWID', row[0], re.I):
        raise ValueError('Unsupported complete-history table schema')
    specs = conn.execute('PRAGMA table_xinfo('+quote(table)+')').fetchall()
    columns = [r[1] for r in specs]
    folded = {c.casefold() for c in columns}
    alias = next((name for name in ('rowid', '_rowid_', 'oid') if name not in folded), None)
    if not 0 < len(columns) <= 128 or any(r[6] for r in specs) or alias is None or '__archive_rowid' in folded:
        raise ValueError('Unsupported complete-history columns')
    return dict(source_schema_sql=row[0], columns=columns, source_rowid_alias=alias)


def validate_full_catalog(catalog):
    """Require one source epoch and disjoint ordered coverage of each table."""
    proofs = catalog.get('full_table_proofs')
    counts = catalog.get('expected_table_counts')
    source_hash = catalog.get('source_database_sha256')
    if (not isinstance(proofs, dict) or set(proofs) != archive.TABLES
            or not isinstance(counts, dict) or set(counts) != set(proofs)
            or not re.fullmatch('[0-9a-f]{64}', str(source_hash or ''))):
        raise ValueError('Invalid complete selected-table proofs')
    seen, previous = {t:0 for t in proofs}, {}
    for table, proof in proofs.items():
        if (not isinstance(proof, dict) or type(proof.get('row_count')) is not int
                or not 0 <= proof['row_count'] < 2**63 or proof['row_count'] != counts[table]
                or not re.fullmatch('[0-9a-f]{64}', str(proof.get('logical_sha256') or ''))
                or not isinstance(proof.get('source_schema_sql'), str)
                or not isinstance(proof.get('columns'), list)
                or proof.get('source_rowid_alias') not in ('rowid', '_rowid_', 'oid')):
            raise ValueError('Invalid complete typed table root')
    for receipt in catalog['partitions']:
        archive._validate_cloud(receipt, True)
        part = receipt['partition']; table = part['table']; proof = proofs[table]
        ids = archive._rowids(part.get('rowids'))
        if (part.get('source_database_sha256') != source_hash or part.get('row_count') != len(ids)
                or ids != part['rowids'] or (table in previous and ids[0] <= previous[table])
                or any(part.get(k) != proof[k] for k in ('columns','source_schema_sql','source_rowid_alias'))):
            raise ValueError('Incomplete, overlapping or mixed-epoch catalog chunks')
        previous[table] = ids[-1]; seen[table] += len(ids)
    if seen != counts:
        raise ValueError('Incomplete catalog table coverage')


def catalog_max_rowids(catalog):
    """Return the highest cataloged rowid per table (or None when empty)."""
    validate_full_catalog(catalog)
    max_rowids = {table: None for table in catalog['full_table_proofs']}
    for receipt in catalog['partitions']:
        part = receipt['partition']
        max_rowids[part['table']] = part['rowids'][-1]
    return max_rowids


def verify_full_tables(conn, catalog, *, post_catalog_proofs=None):
    """Compare restored tables to independently measured original typed roots."""
    max_rowids = catalog_max_rowids(catalog)
    for table, proof in catalog['full_table_proofs'].items():
        actual = _schema(conn, table)
        if any(actual[k] != proof[k] for k in actual):
            raise ValueError('Restored complete table schema or count differs')
        qtable = archive._quote(table)
        alias = proof['source_rowid_alias']
        qalias = archive._quote(alias)
        max_rowid = max_rowids[table]
        post_proof = (post_catalog_proofs or {}).get(table)
        if post_proof and post_proof.get('row_count', 0) > 0:
            if max_rowid is None:
                prefix_where, prefix_params = ' WHERE 1=0', ()
                suffix_where, suffix_params = '', ()
            else:
                prefix_where, prefix_params = ' WHERE '+qalias+'<=?', (max_rowid,)
                suffix_where, suffix_params = ' WHERE '+qalias+'>?', (max_rowid,)
            prefix_count = conn.execute('SELECT COUNT(*) FROM '+qtable+prefix_where, prefix_params).fetchone()[0]
            suffix_count = conn.execute('SELECT COUNT(*) FROM '+qtable+suffix_where, suffix_params).fetchone()[0]
            total_count = conn.execute('SELECT COUNT(*) FROM '+qtable).fetchone()[0]
            if (prefix_count != proof['row_count']
                    or suffix_count != post_proof['row_count']
                    or total_count != proof['row_count'] + post_proof['row_count']):
                raise ValueError('Restored complete table schema or count differs')
            if archive._logical_hash(conn, qtable, proof['columns'], alias, prefix_where, prefix_params) != proof['logical_sha256']:
                raise ValueError('Restored complete typed table root differs')
            if archive._logical_hash(conn, qtable, proof['columns'], alias, suffix_where, suffix_params) != post_proof['logical_sha256']:
                raise ValueError('Restored post-catalog typed table suffix differs')
        else:
            count = conn.execute('SELECT COUNT(*) FROM '+qtable).fetchone()[0]
            if count != proof['row_count']:
                raise ValueError('Restored complete table schema or count differs')
            if archive._logical_hash(conn, qtable, proof['columns'], alias) != proof['logical_sha256']:
                raise ValueError('Restored complete typed table root differs')


def publish_full_catalog(source, destination, *, source_receipt, client, maximum_hydrated_bytes):
    """Export ALL rows in both selected tables; publish only after full readback."""
    source, destination = Path(source), Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    before = archive._identity(source)
    if (not isinstance(source_receipt, dict) or source_receipt.get('immutable_sqlite_read') is not True
            or not (source_receipt.get('snapshot_restore_verified') is True or source_receipt.get('full_restore_verified') is True)
            or source_receipt.get('verified_local_file') != before
            or source_receipt.get('database_size_bytes') != before['size']
            or not re.fullmatch('[0-9a-f]{64}', str(source_receipt.get('database_sha256') or ''))):
        raise ValueError('Source lacks matching full local restore proof')
    if type(maximum_hydrated_bytes) is not int or not before['size'] <= maximum_hydrated_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError('Invalid complete catalog output bound')
    deadline = time.monotonic()+MAX_SECONDS
    def check():
        if time.monotonic() >= deadline:
            raise ValueError('Complete catalog deadline exhausted')
        if shutil.disk_usage(destination.parent).free < archive.RESERVE_BYTES+4*archive.MAX_BYTES:
            raise ValueError('Complete catalog lacks unchanged 1GiB worker reserve')
        if archive._identity(source) != before:
            raise ValueError('Verified full catalog source changed')
    check()
    catalog = dict(contract=CATALOG_CONTRACT, source_database_sha256=source_receipt['database_sha256'],
                   maximum_hydrated_bytes=maximum_hydrated_bytes, partitions=[],
                   expected_table_counts={}, full_table_proofs={},
                   production_authority=False, data_retirement_authorized=False)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)) as conn:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA cache_size=-2048')
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, archive.MAX_ROW_BYTES+65536)
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        # Count preflight precedes expensive typed scans and every cloud write.
        for table in sorted(archive.TABLES):
            proof = _schema(conn, table)
            proof['row_count'] = conn.execute('SELECT COUNT(*) FROM '+archive._quote(table)).fetchone()[0]
            catalog['expected_table_counts'][table] = proof['row_count']
            catalog['full_table_proofs'][table] = proof
        if sum((n+archive.MAX_ROWS-1)//archive.MAX_ROWS for n in catalog['expected_table_counts'].values()) > MAX_PARTITIONS:
            raise ValueError('Complete catalog exceeds partition budget')
        for table, proof in catalog['full_table_proofs'].items():
            check()
            proof['logical_sha256'] = archive._logical_hash(conn, archive._quote(table), proof['columns'], proof['source_rowid_alias'])
        with tempfile.TemporaryDirectory(prefix='.full-history-', dir=destination.parent) as directory:
            for table, proof in catalog['full_table_proofs'].items():
                last = None
                while True:
                    check()
                    quote = archive._quote; alias = quote(proof['source_rowid_alias'])
                    where, parameters = ('', ()) if last is None else (' WHERE '+alias+'>?', (last,))
                    rows = conn.execute('SELECT '+alias+','+archive._size_expression(proof['columns'])
                        +' FROM '+quote(table)+where+' ORDER BY '+alias+' LIMIT ?', (*parameters,archive.MAX_ROWS)).fetchall()
                    if not rows:
                        break
                    ids, size = [], 0
                    for rowid, row_bytes in rows:
                        if row_bytes > archive.MAX_ROW_BYTES:
                            raise ValueError('Complete catalog contains excessive row')
                        if ids and size+row_bytes > CHUNK_PAYLOAD_BYTES:
                            break
                        ids.append(rowid); size += row_bytes
                    if len(catalog['partitions']) >= MAX_PARTITIONS:
                        raise ValueError('Complete catalog exceeds partition budget')
                    path = Path(directory)/'chunk.sqlite3'
                    part = archive.export_partition(source, path, table=table, rowids=ids, source_receipt=source_receipt)
                    cloud = archive.upload_partition(client, path, receipt=part)
                    catalog['partitions'].append(cloud)
                    path.unlink(); last = ids[-1]
                    if len(json.dumps(catalog,separators=(',',':')).encode()) > MAX_CATALOG_BYTES:
                        raise ValueError('Complete catalog exceeds JSON budget')
            # Do not trust timestamps alone: one final bounded actual-byte hash.
            digest = hashlib.sha256(); size = 0
            with source.open('rb') as stream:
                for chunk in iter(lambda:stream.read(4*1024**2), b''):
                    check(); size += len(chunk)
                    if size > before['size']:
                        raise ValueError('Complete catalog source byte size changed')
                    digest.update(chunk)
            check()
            if size != before['size'] or digest.hexdigest() != source_receipt['database_sha256']:
                raise ValueError('Complete catalog source byte hash changed')
            validate_full_catalog(catalog)
            raw = json.dumps(catalog, sort_keys=True, separators=(',',':')).encode()
            if not 0 < len(raw) <= MAX_CATALOG_BYTES:
                raise ValueError('Complete catalog exceeds JSON budget')
            catalog_sha = hashlib.sha256(raw).hexdigest()
            key = archive.PREFIX+catalog['source_database_sha256']+'/catalogs/'+catalog_sha+'.json'
            check()
            client.put_object(Bucket=archive.BUCKET, Key=key, Body=raw, ContentType='application/json')
            with client.get_object(Bucket=archive.BUCKET, Key=key)['Body'] as body:
                if body.read(len(raw)+1) != raw:
                    raise ValueError('Complete catalog full GET readback mismatch')
            check()
            staged = Path(directory)/'catalog.json'
            with staged.open('xb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
            archive._publish(staged, destination)
    return dict(contract=CATALOG_CONTRACT, catalog_sha256=catalog_sha, bucket=archive.BUCKET,
                object_key=key, partition_count=len(catalog['partitions']),
                expected_table_counts=catalog['expected_table_counts'], verified_full_get=True,
                full_selected_table_catalog=True, production_authority=False, data_retirement_authorized=False)
