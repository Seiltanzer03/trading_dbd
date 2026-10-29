"""Lossless OFF-HOST feature-text rebuild with full typed/rowid equality.

Compression produces a smaller marked physical image; expansion gives existing
research readers the exact original JSON. Neither grants replacement authority.
"""
from contextlib import closing
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import struct
import tempfile
import time

from scripts import sqlite_history_partition as archive
from scripts.history_storage_loader import history_state

codec = history_state._payload_codec()
CONTRACT = 'sqlite-lossless-json-rebuild-v1'
MAX_BYTES = 25 * 1024**3
RESERVE_BYTES = 1024**3
MAX_SECONDS = 3600


def _hash(path, check):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            check()
            chunk = stream.read(1024**2)
            if not chunk: break
            digest.update(chunk)
    return digest.hexdigest()


def _schema(conn):
    return conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema "
                        "ORDER BY type,name").fetchall()


def _tables(conn):
    tables = []
    for name, sql in conn.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' ORDER BY rowid"):
        if (not isinstance(sql, str) or len(sql.encode()) > 65536
                or re.search(r'\b(?:VIRTUAL|WITHOUT\s+ROWID)\b', sql, re.I)
                or (name.startswith('sqlite_') and name != 'sqlite_sequence')):
            raise ValueError('Unsupported JSON-rebuild table schema')
        specs = conn.execute('PRAGMA table_xinfo('+archive._quote(name)+')').fetchall()
        columns = [r[1] for r in specs]
        folded = {c.casefold() for c in columns}
        alias = next((a for a in ('rowid', '_rowid_', 'oid') if a not in folded), None)
        if not 0 < len(columns) <= 128 or any(r[6] for r in specs) or alias is None:
            raise ValueError('Unsupported JSON-rebuild columns or rowid')
        tables.append((name, sql, alias, columns))
    if not 0 < len(tables) <= 512:
        raise ValueError('Unsupported JSON-rebuild table count')
    return tables


def _connect(path):
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    conn.execute('PRAGMA trusted_schema=OFF')
    conn.execute('PRAGMA cache_size=-16384')
    return conn


def _publish_owned(work, destination, *, owner_fd=None):
    # Keep the inode open through link/unlink/fsync to exclude inode reuse.
    # A post-link fsync/readback failure must not strand an unreceipted output.
    with work.open('rb') as token:
        owned = os.fstat(token.fileno())
        if owner_fd is not None:
            expected = os.fstat(owner_fd)
            if (owned.st_dev,owned.st_ino) != (expected.st_dev,expected.st_ino):
                raise ValueError('JSON-rebuild worker ownership changed')
        try:
            archive._publish(work, destination)
            current = archive._identity(destination)
            actual = os.fstat(token.fileno())
            if (current['device'],current['inode'],current['size'],current['mtime_ns'],current['ctime_ns']) != (
                    actual.st_dev,actual.st_ino,actual.st_size,actual.st_mtime_ns,actual.st_ctime_ns):
                raise ValueError('JSON-rebuild publication ownership changed')
            return current
        except BaseException:
            try:
                current = destination.lstat()
            except FileNotFoundError:
                pass
            else:
                if (stat.S_ISREG(current.st_mode)
                        and (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                        == (owned.st_dev, owned.st_ino, owned.st_size, owned.st_mtime_ns)):
                    destination.unlink()
            raise


def _logical_hash(conn,table,columns,alias,decoded_column):
    if decoded_column is None:
        return archive._logical_hash(conn,table,columns,alias)
    expressions=[archive._quote(alias)]
    for col in columns:
        q=archive._quote(col)
        expressions += [f'typeof({q})',f"CASE WHEN typeof({q})='text' THEN CAST({q} AS BLOB) ELSE {q} END"]
    h=hashlib.sha256()
    for row in conn.execute('SELECT '+','.join(expressions)+' FROM '+table+' ORDER BY '+archive._quote(alias)):
        h.update(struct.pack('>q',row[0]))
        for col,kind,value in zip(columns,row[1::2],row[2::2]):
            raw=(b'' if kind=='null' else struct.pack('>q',value) if kind=='integer'
                 else struct.pack('>d',value) if kind=='real' else value)
            if not isinstance(raw,bytes): raise ValueError('Unexpected SQLite storage type')
            if col==decoded_column and kind=='text':
                raw=codec.unpack_text(raw.decode('utf8')).encode('utf8')
            h.update(kind.encode('ascii')+struct.pack('>Q',len(raw))+raw)
    return h.hexdigest()


def rebuild_snapshot(source, destination, *, source_receipt, mode, deadline=None):
    """Return a verified smaller image, or leave the source/output untouched.

    Proof binds the immutable source bytes. Every application row/storage type
    and actual hidden rowid is then compared independently against the output.
    No timestamps, field values, source authority or cold catalog pins change.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if (source != source.resolve() or destination != destination.resolve()
            or source.is_relative_to('/opt/seiltanzer')
            or destination.is_relative_to('/opt/seiltanzer')):
        raise ValueError('OFF-HOST JSON-rebuild paths required')
    if os.path.lexists(destination): raise FileExistsError(destination)
    if mode not in ('compress','expand'):
        raise ValueError('Unsupported JSON rebuild mode')
    before = archive._identity(source)
    if (not isinstance(source_receipt, dict)
            or source_receipt.get('immutable_sqlite_read') is not True
            or not any(source_receipt.get(k) is True for k in ('immutable_snapshot_verified','full_restore_verified','snapshot_restore_verified'))
            or source_receipt.get('verified_local_file') != before
            or source_receipt.get('database_size_bytes') != before['size']
            or not 4096 <= before['size'] <= MAX_BYTES
            or not re.fullmatch('[0-9a-f]{64}', str(source_receipt.get('database_sha256') or ''))):
        raise ValueError('Source lacks matching immutable byte proof')
    started = time.monotonic()
    if deadline is not None and (type(deadline) not in (float, int) or not math.isfinite(deadline)):
        raise ValueError('Invalid repack deadline')
    end = min(started+MAX_SECONDS, deadline) if deadline is not None else started+MAX_SECONDS

    def check(*, needed=RESERVE_BYTES):
        if time.monotonic() >= end: raise ValueError('JSON-rebuild deadline exhausted')
        if shutil.disk_usage(destination.parent).free < needed:
            raise ValueError('JSON-rebuild worker reserve unavailable')
        if archive._identity(source) != before:
            raise ValueError('JSON-rebuild source changed')

    check(needed=RESERVE_BYTES+(before['size'] if mode=='compress' else MAX_BYTES))
    if _hash(source, check) != source_receipt['database_sha256']:
        raise ValueError('JSON-rebuild source hash mismatch')
    work = None
    work_fd = None
    try:
        with closing(_connect(source)) as original:
            original.set_progress_handler(lambda: time.monotonic() >= end, 1000)
            if original.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('JSON-rebuild source integrity failed')
            if original.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('JSON-rebuild source foreign keys failed')
            tables, schema = _tables(original), _schema(original)
            payload_pins = codec.manifest_fields(original)
            if bool(payload_pins) != (mode=='expand'):
                raise ValueError('JSON rebuild source marker differs from requested mode')
            if mode=='expand':
                tables = [t for t in tables if t[0]!=codec.TABLE]
                schema = [r for r in schema if r[2]!=codec.TABLE]
            headers = {k: original.execute('PRAGMA '+k).fetchone()[0]
                       for k in ('encoding', 'application_id', 'user_version', 'page_size', 'auto_vacuum')}
            history_pins = history_state.history_manifest_fields(original)
            fd, name = tempfile.mkstemp(prefix='.JSON-rebuild-', suffix='.db', dir=destination.parent)
            work = Path(name); work_fd = fd; os.fchmod(fd, 0o600)
            table_proofs = {}
            with closing(sqlite3.connect(work.as_uri(), uri=True)) as compact:
                compact.execute('PRAGMA page_size='+str(headers['page_size']))
                compact.execute('PRAGMA auto_vacuum='+str(headers['auto_vacuum']))
                compact.execute('PRAGMA max_page_count='+str(MAX_BYTES//headers['page_size']))
                compact.create_function('rebuild_feature_text',1,codec.pack_text if mode=='compress' else codec.unpack_text)
                compact.execute("PRAGMA encoding='"+headers['encoding']+"'")
                compact.execute('PRAGMA application_id='+str(headers['application_id']))
                compact.execute('PRAGMA user_version='+str(headers['user_version']))
                compact.execute('PRAGMA journal_mode=OFF')
                compact.execute('PRAGMA cache_size=-16384')
                compact.execute('PRAGMA trusted_schema=OFF')
                # Only a disposable, unpublished worker file uses journal OFF.
                compact.set_progress_handler(lambda: time.monotonic() >= end
                    or shutil.disk_usage(destination.parent).free < RESERVE_BYTES, 10000)
                compact.execute('ATTACH DATABASE ? AS src', (source.as_uri()+'?mode=ro&immutable=1',))
                compact.execute('BEGIN')
                for name, sql, alias, columns in tables:
                    check()
                    if name == 'sqlite_sequence': continue
                    compact.execute(sql)
                    names = ','.join(archive._quote(c) for c in [alias, *columns])
                    q = archive._quote(name)
                    target = codec.TARGET_COLUMNS.get(name)
                    expressions = ','.join('rebuild_feature_text('+archive._quote(c)+')' if c==target else archive._quote(c) for c in [alias,*columns])
                    compact.execute(f'INSERT INTO main.{q}({names}) SELECT {expressions} FROM src.{q} ORDER BY '+archive._quote(alias))
                if any(t[0] == 'sqlite_sequence' for t in tables):
                    compact.execute('DELETE FROM main.sqlite_sequence')
                    compact.execute('INSERT INTO main.sqlite_sequence(rowid,name,seq) SELECT rowid,name,seq FROM src.sqlite_sequence ORDER BY rowid')
                for kind, name, table, sql in schema:
                    if kind in ('index', 'trigger', 'view') and sql is not None:
                        check(); compact.execute(sql)
                compact.commit()
                compact.execute('DETACH DATABASE src')
                if _schema(compact) != schema:
                    raise ValueError('JSON-rebuild schema objects differ')
                for name, _, alias, columns in tables:
                    check()
                    q = archive._quote(name)
                    source_root = _logical_hash(original,q,columns,alias,codec.TARGET_COLUMNS.get(name) if mode=='expand' else None)
                    compact_root = _logical_hash(compact,q,columns,alias,codec.TARGET_COLUMNS.get(name) if mode=='compress' else None)
                    if source_root != compact_root:
                        raise ValueError('JSON-rebuild typed table root differs')
                    table_proofs[name] = dict(logical_sha256=source_root,
                        row_count=original.execute('SELECT count(*) FROM '+q).fetchone()[0])
                if (compact.execute('PRAGMA quick_check').fetchone() != ('ok',)
                        or compact.execute('PRAGMA foreign_key_check').fetchone() is not None):
                    raise ValueError('JSON-rebuild output integrity or foreign keys failed')
                if mode=='compress': codec.install_marker(compact); compact.commit()
                expected_pins = {k:v for k,v in history_pins.items() if k!=codec.MANIFEST_FIELD}
                actual_pins = history_state.history_manifest_fields(compact)
                if {k:v for k,v in actual_pins.items() if k!=codec.MANIFEST_FIELD} != expected_pins:
                    raise ValueError('JSON-rebuild history pins differ')
                if any(compact.execute('PRAGMA '+k).fetchone()[0] != v for k,v in headers.items()):
                    raise ValueError('JSON-rebuild header values differ')
            check()
            if mode=='compress' and work.stat().st_size >= before['size']:
                raise ValueError('JSON-rebuild output is not smaller')
            output_hash = _hash(work, check)
            if _hash(source, check) != source_receipt['database_sha256']:
                raise ValueError('JSON-rebuild source hash changed')
            check()
            published = _publish_owned(work, destination, owner_fd=work_fd)
            result = dict(contract=CONTRACT, database_sha256=output_hash,
                database_size_bytes=destination.stat().st_size,
                source_database_sha256=source_receipt['database_sha256'],
                source_database_size_bytes=before['size'], page_size=headers['page_size'], mode=mode,
                analytical_snapshot_only=mode=='expand',
                reclaimed_bytes=before['size']-destination.stat().st_size,
                verified_local_file=published, table_proofs=table_proofs,
                all_typed_table_roots_verified=True, all_rows_preserved=True,
                all_hidden_rowids_preserved=True, schema_objects_preserved=True,
                cold_history_pins_preserved=True, production_authority=False,
                data_retirement_authorized=False, **actual_pins)
            return result
    except sqlite3.OperationalError:
        check()  # Convert deadline/source/capacity failures to bounded public codes.
        raise
    finally:
        if work_fd is not None:
            try:
                # Holding the original inode excludes reuse after publication
                # unlinks the work pathname. No pathname alone owns a sidecar.
                try:current = work.lstat()
                except FileNotFoundError:pass
                else:
                    owned = os.fstat(work_fd)
                    if stat.S_ISREG(current.st_mode) and (current.st_dev,current.st_ino)==(owned.st_dev,owned.st_ino):
                        work.unlink()
            finally:os.close(work_fd)
