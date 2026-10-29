"""Lossless OFF-HOST SQLite page rebuild; no live writer or retirement entry.

Explicit rowids and ordered typed roots matter: VACUUM may renumber rowids.
This tool grants no authority to replace a production database.
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
import tempfile
import time

from scripts import sqlite_history_partition as archive
from scripts.history_storage_loader import history_state

CONTRACT = 'sqlite-lossless-page-repack-v1'
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
            raise ValueError('Unsupported page-repack table schema')
        specs = conn.execute('PRAGMA table_xinfo('+archive._quote(name)+')').fetchall()
        columns = [r[1] for r in specs]
        folded = {c.casefold() for c in columns}
        alias = next((a for a in ('rowid', '_rowid_', 'oid') if a not in folded), None)
        if not 0 < len(columns) <= 128 or any(r[6] for r in specs) or alias is None:
            raise ValueError('Unsupported page-repack columns or rowid')
        tables.append((name, sql, alias, columns))
    if not 0 < len(tables) <= 512:
        raise ValueError('Unsupported page-repack table count')
    return tables


def _connect(path):
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    conn.execute('PRAGMA trusted_schema=OFF')
    conn.execute('PRAGMA cache_size=-16384')
    return conn


def _publish_owned(work, destination):
    # Keep the inode open through link/unlink/fsync to exclude inode reuse.
    # A post-link fsync/readback failure must not strand an unreceipted output.
    with work.open('rb') as token:
        owned = os.fstat(token.fileno())
        try:
            archive._publish(work, destination)
            return archive._identity(destination)
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


def repack_snapshot(source, destination, *, source_receipt, page_size=1024, deadline=None):
    """Return a verified smaller image, or leave the source/output untouched.

    Proof binds the immutable source bytes. Every application row/storage type
    and actual hidden rowid is then compared independently against the output.
    No timestamps, field values, source authority or cold catalog pins change.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if (source != source.resolve() or destination != destination.resolve()
            or source.is_relative_to('/opt/seiltanzer')
            or destination.is_relative_to('/opt/seiltanzer')):
        raise ValueError('OFF-HOST page-repack paths required')
    if os.path.lexists(destination): raise FileExistsError(destination)
    if type(page_size) is not int or page_size not in (1024, 4096):
        raise ValueError('Unsupported repack page size')
    before = archive._identity(source)
    if (not isinstance(source_receipt, dict)
            or source_receipt.get('immutable_sqlite_read') is not True
            or source_receipt.get('immutable_snapshot_verified') is not True
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
        if time.monotonic() >= end: raise ValueError('Page-repack deadline exhausted')
        if shutil.disk_usage(destination.parent).free < needed:
            raise ValueError('Page-repack worker reserve unavailable')
        if archive._identity(source) != before:
            raise ValueError('Page-repack source changed')

    check(needed=RESERVE_BYTES+before['size'])
    if _hash(source, check) != source_receipt['database_sha256']:
        raise ValueError('Page-repack source hash mismatch')
    work = None
    try:
        with closing(_connect(source)) as original:
            original.set_progress_handler(lambda: time.monotonic() >= end, 1000)
            if original.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('Page-repack source integrity failed')
            if original.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('Page-repack source foreign keys failed')
            tables, schema = _tables(original), _schema(original)
            headers = {k: original.execute('PRAGMA '+k).fetchone()[0]
                       for k in ('encoding', 'application_id', 'user_version')}
            history_pins = history_state.history_manifest_fields(original)
            fd, name = tempfile.mkstemp(prefix='.page-repack-', suffix='.db', dir=destination.parent)
            work = Path(name); os.fchmod(fd, 0o600); os.close(fd)
            table_proofs = {}
            with closing(sqlite3.connect(work.as_uri(), uri=True)) as compact:
                compact.execute('PRAGMA page_size='+str(page_size))
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
                    compact.execute(f'INSERT INTO main.{q}({names}) SELECT {names} FROM src.{q} ORDER BY '+archive._quote(alias))
                if any(t[0] == 'sqlite_sequence' for t in tables):
                    compact.execute('DELETE FROM main.sqlite_sequence')
                    compact.execute('INSERT INTO main.sqlite_sequence(rowid,name,seq) SELECT rowid,name,seq FROM src.sqlite_sequence ORDER BY rowid')
                for kind, name, table, sql in schema:
                    if kind in ('index', 'trigger', 'view') and sql is not None:
                        check(); compact.execute(sql)
                compact.commit()
                compact.execute('DETACH DATABASE src')
                if _schema(compact) != schema:
                    raise ValueError('Page-repack schema objects differ')
                for name, _, alias, columns in tables:
                    check()
                    q = archive._quote(name)
                    source_root = archive._logical_hash(original, q, columns, alias)
                    compact_root = archive._logical_hash(compact, q, columns, alias)
                    if source_root != compact_root:
                        raise ValueError('Page-repack typed table root differs')
                    table_proofs[name] = dict(logical_sha256=source_root,
                        row_count=original.execute('SELECT count(*) FROM '+q).fetchone()[0])
                if (compact.execute('PRAGMA quick_check').fetchone() != ('ok',)
                        or compact.execute('PRAGMA foreign_key_check').fetchone() is not None):
                    raise ValueError('Page-repack output integrity or foreign keys failed')
                if history_state.history_manifest_fields(compact) != history_pins:
                    raise ValueError('Page-repack history pins differ')
                if any(compact.execute('PRAGMA '+k).fetchone()[0] != v for k,v in headers.items()):
                    raise ValueError('Page-repack header values differ')
            check()
            if work.stat().st_size >= before['size']:
                raise ValueError('Page-repack output is not smaller')
            output_hash = _hash(work, check)
            if _hash(source, check) != source_receipt['database_sha256']:
                raise ValueError('Page-repack source hash changed')
            check()
            published = _publish_owned(work, destination)
            result = dict(contract=CONTRACT, database_sha256=output_hash,
                database_size_bytes=destination.stat().st_size,
                source_database_sha256=source_receipt['database_sha256'],
                source_database_size_bytes=before['size'], page_size=page_size,
                reclaimed_bytes=before['size']-destination.stat().st_size,
                verified_local_file=published, table_proofs=table_proofs,
                all_typed_table_roots_verified=True, all_rows_preserved=True,
                all_hidden_rowids_preserved=True, schema_objects_preserved=True,
                cold_history_pins_preserved=True, production_authority=False,
                data_retirement_authorized=False, **history_pins)
            return result
    except sqlite3.OperationalError:
        check()  # Convert deadline/source/capacity failures to bounded public codes.
        raise
    finally:
        if work is not None: archive._cleanup(work)
