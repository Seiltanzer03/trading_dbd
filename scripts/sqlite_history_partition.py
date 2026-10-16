"""Bounded lossless partitions of verified OFF-HOST SQLite copies.

Native SQLite is the typed, length-framed container. No JSON payload is parsed,
no source table is changed, and no dependency/reader/retirement admission is
implied. A local restore token expires when its file identity changes.
"""
from __future__ import annotations

import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import tempfile
import time

CONTRACT = 'sqlite-history-partition-v1'
TABLES = {'passive_market_observations', 'g1s_observations'}
MAX_ROWS = 500
MAX_BYTES = 128 * 1024**2
MAX_ROW_BYTES = 8 * 1024**2
MAX_SECONDS = 30
RESERVE_BYTES = 1024**3
BUCKET = 'trading-dbd-backups-2026'
PREFIX = 'research/sqlite-history/v1/'
CLOUD_CONTRACT = 'sqlite-history-cloud-partition-v1'


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _identity(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Expected one regular immutable input file')
    if any(os.path.lexists(str(path)+suffix) for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Mutable SQLite sidecar is present')
    return dict(path=str(path.resolve()), device=info.st_dev, inode=info.st_ino,
                size=info.st_size, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


def _digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def _rowids(values):
    if (not isinstance(values, (list, tuple)) or not 0 < len(values) <= MAX_ROWS
            or any(type(v) is not int or not -2**63 <= v < 2**63 for v in values)
            or len(set(values)) != len(values)):
        raise ValueError('Invalid or excessive row selection')
    return sorted(values)


def _open_work(destination):
    import shutil
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    if shutil.disk_usage(destination.parent).free < RESERVE_BYTES + MAX_BYTES:
        raise ValueError('Partition staging lacks unchanged 1GiB reserve')
    fd, name = tempfile.mkstemp(prefix='.history-partition-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    return Path(name)


def _connection(path, source):
    if MAX_SECONDS <= 0:
        raise ValueError('Partition SQL deadline exhausted')
    conn = sqlite3.connect(path, uri=True)
    try:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_ROW_BYTES+65536)
        conn.execute('PRAGMA cache_size=-2048')
        conn.execute('PRAGMA page_size=4096')
        conn.execute('PRAGMA max_page_count='+str(MAX_BYTES//4096))
        conn.execute('ATTACH DATABASE ? AS src', (Path(source).resolve().as_uri()+'?mode=ro&immutable=1',))
        deadline = time.monotonic()+MAX_SECONDS
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        return conn
    except BaseException:
        conn.close()
        raise


def _publish(work, destination):
    with work.open('rb') as stream:
        os.fsync(stream.fileno())
    os.link(work, destination)  # Exclusive publication, including destination races.
    work.unlink()
    fd = os.open(Path(destination).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _cleanup(work):
    # Only the mkstemp-owned file and its SQLite sidecars, never source/history.
    for suffix in ('', '-journal', '-wal', '-shm'):
        Path(str(work)+suffix).unlink(missing_ok=True)


def _container_sql(columns):
    # Deliberately NO affinity on payload columns: retain actual storage types.
    return 'CREATE TABLE partition_rows ("__archive_rowid" INTEGER PRIMARY KEY,'+','.join(_quote(c) for c in columns)+')'


def _size_expression(columns):
    return '+'.join('COALESCE(length(CAST('+_quote(c)+' AS BLOB)),0)' for c in columns)


def _logical_hash(conn, table, columns, rowid, where='', parameters=()):
    expressions = [_quote(rowid)]
    for col in columns:
        q = _quote(col)
        expressions += [f'typeof({q})', f"CASE WHEN typeof({q})='text' THEN CAST({q} AS BLOB) ELSE {q} END"]
    query = 'SELECT '+','.join(expressions)+' FROM '+table+where+' ORDER BY '+_quote(rowid)
    h = hashlib.sha256()
    for row in conn.execute(query, parameters):
        h.update(struct.pack('>q', row[0]))
        for kind, value in zip(row[1::2], row[2::2]):
            raw = (b'' if kind == 'null' else struct.pack('>q', value) if kind == 'integer'
                   else struct.pack('>d', value) if kind == 'real' else value)
            if not isinstance(raw, bytes):
                raise ValueError('Unexpected SQLite storage type')
            h.update(kind.encode('ascii')+struct.pack('>Q', len(raw))+raw)
    return h.hexdigest()


def export_partition(source, destination, *, table, rowids, source_receipt):
    ids = _rowids(rowids)
    if table not in TABLES or not isinstance(source_receipt, dict):
        raise ValueError('Unsupported history partition')
    before = _identity(source)
    if (source_receipt.get('immutable_sqlite_read') is not True
            or not (source_receipt.get('snapshot_restore_verified') is True or source_receipt.get('full_restore_verified') is True)
            or source_receipt.get('verified_local_file') != before
            or source_receipt.get('database_size_bytes') != before['size']
            or not re.fullmatch('[0-9a-f]{64}', str(source_receipt.get('database_sha256') or ''))):
        raise ValueError('Source lacks matching full local restore proof')
    work = _open_work(destination)
    try:
        with _connection(work, source) as conn:
            ddl = conn.execute("SELECT sql FROM src.sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
            if not ddl or len(ddl[0]) > 65536 or re.search(r'WITHOUT\s+ROWID', ddl[0], re.I):
                raise ValueError('Unsupported source table schema')
            specs = conn.execute('PRAGMA src.table_xinfo('+_quote(table)+')').fetchall()
            columns = [r[1] for r in specs]
            folded = {c.casefold() for c in columns}
            alias = next((name for name in ('rowid', '_rowid_', 'oid') if name not in folded), None)
            if (not 0 < len(columns) <= 128 or any(r[6] for r in specs)
                    or '__archive_rowid' in folded or alias is None):
                raise ValueError('Unsupported source columns or hidden rowid')
            names = ','.join(_quote(c) for c in columns)
            where = ' WHERE '+_quote(alias)+' IN ('+','.join('?' for _ in ids)+')'
            source_table = 'src.'+_quote(table)
            sizes = _size_expression(columns)
            count, largest = conn.execute('SELECT COUNT(*),MAX('+sizes+') FROM '+source_table+where, ids).fetchone()
            if count != len(ids) or largest is None or largest > MAX_ROW_BYTES:
                raise ValueError('Missing row or excessive partition row size')
            conn.execute(_container_sql(columns))
            conn.execute('INSERT INTO partition_rows SELECT '+_quote(alias)+','+names+' FROM '+source_table+where, ids)
            logical_hash = _logical_hash(conn, source_table, columns, alias, where, ids)
            if logical_hash != _logical_hash(conn, 'partition_rows', columns, '__archive_rowid'):
                raise ValueError('Native partition changed source values')
        conn.close()
        if _identity(source) != before:
            raise ValueError('Verified source changed during partition export')
        result = dict(contract=CONTRACT, table=table, source_schema_sql=ddl[0],
            columns=columns, source_rowid_alias=alias, rowids=ids, row_count=len(ids),
            source_database_sha256=source_receipt['database_sha256'],
            logical_sha256=logical_hash, partition_sha256=_digest(work),
            partition_size_bytes=work.stat().st_size, production_authority=False,
            data_retirement_authorized=False, dependency_closure_verified=False)
        _publish(work, destination)
        return result
    finally:
        if 'conn' in locals():
            conn.close()
        _cleanup(work)


def restore_partition(source, destination, *, receipt):
    if (not isinstance(receipt, dict) or receipt.get('contract') != CONTRACT
            or receipt.get('table') not in TABLES or receipt.get('production_authority') is not False
            or receipt.get('data_retirement_authorized') is not False):
        raise ValueError('Invalid partition receipt or authority')
    ids = _rowids(receipt.get('rowids'))
    columns = receipt.get('columns')
    if (not isinstance(columns, list) or not 0 < len(columns) <= 128
            or any(not isinstance(c,str) for c in columns) or len(set(columns)) != len(columns)
            or receipt.get('row_count') != len(ids) or '__archive_rowid' in {c.casefold() for c in columns}):
        raise ValueError('Invalid partition columns or row count')
    before = _identity(source)
    if (not 0 < before['size'] <= MAX_BYTES or before['size'] != receipt.get('partition_size_bytes')
            or _digest(source) != receipt.get('partition_sha256')):
        raise ValueError('Partition file size or hash mismatch')
    table, ddl = receipt['table'], receipt.get('source_schema_sql')
    if not isinstance(ddl, str) or len(ddl)>65536 or not re.match(r'CREATE TABLE\s+(?:"'+table+r'"|'+table+r')\s*\(', ddl, re.I):
        raise ValueError('Unexpected original table schema')
    work = _open_work(destination)
    try:
        with _connection(work, source) as conn:
            schema = conn.execute('SELECT name,type,sql FROM src.sqlite_schema').fetchall()
            if schema != [('partition_rows','table',_container_sql(columns))]:
                raise ValueError('Partition container schema mismatch')
            actual = [r[0] for r in conn.execute('SELECT __archive_rowid FROM src.partition_rows ORDER BY __archive_rowid LIMIT 501')]
            largest = conn.execute('SELECT MAX('+_size_expression(columns)+') FROM src.partition_rows').fetchone()[0]
            if largest is None or largest > MAX_ROW_BYTES:
                raise ValueError('Excessive restored partition row size')
            if actual != ids or _logical_hash(conn,'src.partition_rows',columns,'__archive_rowid') != receipt.get('logical_sha256'):
                raise ValueError('Partition rows or typed hash mismatch')
            conn.execute(ddl)
            specs = conn.execute('PRAGMA table_xinfo('+_quote(table)+')').fetchall()
            alias = receipt.get('source_rowid_alias')
            if [r[1] for r in specs] != columns or any(r[6] for r in specs) or alias not in ('rowid','_rowid_','oid') or alias in {c.casefold() for c in columns}:
                raise ValueError('Restored source schema or rowid mismatch')
            names = ','.join(_quote(c) for c in columns)
            conn.execute('INSERT INTO '+_quote(table)+'('+_quote(alias)+','+names+') SELECT __archive_rowid,'+names+' FROM src.partition_rows')
            if _logical_hash(conn,_quote(table),columns,alias) != receipt['logical_sha256']:
                raise ValueError('Restored schema changed typed values')
        conn.close()
        if _identity(source) != before:
            raise ValueError('Partition changed during restore')
        _publish(work,destination)
        return dict(row_count=len(ids), typed_values_verified=True,
                    production_authority=False, data_retirement_authorized=False)
    finally:
        if 'conn' in locals():
            conn.close()
        _cleanup(work)


def _cloud_key(partition):
    source_hash, chunk_hash = partition.get('source_database_sha256'), partition.get('partition_sha256')
    if (partition.get('table') not in TABLES or not re.fullmatch('[0-9a-f]{64}',str(source_hash or ''))
            or not re.fullmatch('[0-9a-f]{64}',str(chunk_hash or ''))):
        raise ValueError('Invalid partition cloud identity')
    return PREFIX+source_hash+'/'+partition['table']+'/'+chunk_hash+'.sqlite3.gz'


def _cloud_metadata(value):
    return {'contract':CLOUD_CONTRACT,'compressed-sha256':value['compressed_sha256'],
            'partition-sha256':value['partition']['partition_sha256']}


def _validate_cloud(value, require_verified):
    if (not isinstance(value,dict) or value.get('contract')!=CLOUD_CONTRACT
            or value.get('bucket')!=BUCKET or not isinstance(value.get('partition'),dict)
            or value.get('production_authority') is not False
            or value.get('data_retirement_authorized') is not False
            or (require_verified and value.get('verified_full_get') is not True)):
        raise ValueError('Invalid cloud partition receipt')
    part=value['partition']
    if (value.get('object_key')!=_cloud_key(part)
            or type(part.get('partition_size_bytes')) is not int or not 0<part['partition_size_bytes']<=MAX_BYTES
            or type(value.get('compressed_size_bytes')) is not int or not 0<value['compressed_size_bytes']<=MAX_BYTES+1024**2
            or not re.fullmatch('[0-9a-f]{64}',str(value.get('compressed_sha256') or ''))):
        raise ValueError('Invalid cloud partition size/hash/key')


def restore_from_cloud(client, receipt, destination, *, require_verified=True):
    """Full compressed/raw GET verification, followed by a typed row restore."""
    try:
        from scripts.ede_ledger_archive import _HashReader,require_capacity
    except ModuleNotFoundError:
        from ede_ledger_archive import _HashReader,require_capacity
    destination=Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    _validate_cloud(receipt,require_verified)
    require_capacity(destination.parent,3*MAX_BYTES)
    head=client.head_object(Bucket=BUCKET,Key=receipt['object_key'])
    metadata={str(k).lower():str(v) for k,v in head.get('Metadata',{}).items()}
    if head.get('ContentLength')!=receipt['compressed_size_bytes'] or metadata!=_cloud_metadata(receipt):
        raise ValueError('Cloud partition HEAD mismatch')
    with tempfile.TemporaryDirectory(prefix='.history-cloud-',dir=destination.parent) as directory:
        partition=Path(directory)/'partition.sqlite3'
        body=client.get_object(Bucket=BUCKET,Key=receipt['object_key'])['Body']
        reader=_HashReader(body,receipt['compressed_size_bytes'])
        size,hash_value=0,hashlib.sha256()
        try:
            with partition.open('xb') as stream,gzip.GzipFile(fileobj=reader,mode='rb') as decoded:
                for chunk in iter(lambda:decoded.read(1024**2),b''):
                    size+=len(chunk)
                    if size>receipt['partition']['partition_size_bytes']:
                        raise ValueError('Decoded partition exceeds declared size')
                    hash_value.update(chunk);stream.write(chunk)
                stream.flush();os.fsync(stream.fileno())
            if (size!=receipt['partition']['partition_size_bytes'] or hash_value.hexdigest()!=receipt['partition']['partition_sha256']
                    or reader.size!=receipt['compressed_size_bytes'] or reader.hash.hexdigest()!=receipt['compressed_sha256']):
                raise ValueError('Cloud partition full GET hash/size mismatch')
        finally:
            body.close()
        return restore_partition(partition,destination,receipt=receipt['partition'])


def upload_partition(client, source, *, receipt):
    """Publish only after full cloud readback AND typed restoration succeed."""
    try:
        from scripts.ede_ledger_archive import require_capacity
    except ModuleNotFoundError:
        from ede_ledger_archive import require_capacity
    source=Path(source);before=_identity(source)
    if not 0<before['size']<=MAX_BYTES or before['size']!=receipt.get('partition_size_bytes'):
        raise ValueError('Invalid upload partition size')
    key=_cloud_key(receipt)
    require_capacity(source.parent,3*MAX_BYTES)
    with tempfile.TemporaryDirectory(prefix='.history-upload-',dir=source.parent) as directory:
        packed=Path(directory)/'partition.gz';raw_hash=hashlib.sha256()
        with source.open('rb') as original,packed.open('xb') as output:
            with gzip.GzipFile(fileobj=output,mode='wb',filename='',mtime=0) as encoded:
                for chunk in iter(lambda:original.read(1024**2),b''):
                    raw_hash.update(chunk);encoded.write(chunk)
        if raw_hash.hexdigest()!=receipt.get('partition_sha256') or _identity(source)!=before:
            raise ValueError('Partition changed before upload')
        cloud=dict(contract=CLOUD_CONTRACT,bucket=BUCKET,object_key=key,
            partition=receipt,compressed_sha256=_digest(packed),compressed_size_bytes=packed.stat().st_size,
            production_authority=False,data_retirement_authorized=False,verified_full_get=False)
        _validate_cloud(cloud,False)
        client.upload_file(str(packed),BUCKET,key,
            ExtraArgs={'ContentType':'application/gzip','Metadata':_cloud_metadata(cloud)})
        restore_from_cloud(client,cloud,Path(directory)/'verified.sqlite3',require_verified=False)
        cloud['verified_full_get']=True
        body=json.dumps(cloud,sort_keys=True,separators=(',',':')).encode()
        if len(body)>128*1024:
            raise ValueError('Cloud partition manifest exceeds budget')
        manifest_key=key+'.manifest.json'
        client.put_object(Bucket=BUCKET,Key=manifest_key,Body=body,ContentType='application/json')
        with client.get_object(Bucket=BUCKET,Key=manifest_key)['Body'] as check:
            if check.read(len(body)+1)!=body:
                raise ValueError('Cloud partition receipt readback mismatch')
        return cloud
