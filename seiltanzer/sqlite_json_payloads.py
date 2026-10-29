"""Bounded lossless feature-text storage; never changes JSON/source authority.

Only a marked database enables readers/writers. Raw backups retain physical
bytes; analytical expansion reconstructs the original text and typed records.
Stdlib-only so offline guards and restore tools need no application startup.
"""
import base64
from functools import lru_cache
import hashlib
import json
import re
import zlib

SQLITE_JSON_PAYLOAD_CONTRACT = 'sqlite-json-zlib-base64-v1'
PREFIX = '~stj1:'
TABLE = 'json_feature_payload_storage'
MANIFEST_FIELD = 'sqlite_json_payload_storage_sha256'
MIN_RAW_BYTES = 4096
MAX_RAW_BYTES = 2*1024**2
MAX_ENCODED_BYTES = MAX_RAW_BYTES + 256
TARGET_COLUMNS = {'passive_market_observations':'features_json',
                  'g1s_observations':'frozen_features_json'}
_READER_COLUMNS = frozenset(TARGET_COLUMNS.values())
TABLE_SQL = (f'CREATE TABLE "{TABLE}" (id INTEGER PRIMARY KEY CHECK(id=1),'
             'payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL)')


class PayloadStorageError(ValueError):
    """Stable codes never contain the encoded or decoded source payload."""


def _unpack(value):
    try:
        if not value.startswith(PREFIX): raise ValueError()
        length, sha, wire = value[len(PREFIX):].split(':',2)
        if (not length.isascii() or not length.isdecimal() or len(length)>8
                or str(int(length)) != length
                or not MIN_RAW_BYTES <= int(length) <= MAX_RAW_BYTES
                or not re.fullmatch('[0-9a-f]{64}',sha)
                or len(value)>MAX_ENCODED_BYTES or len(value)>=int(length)):
            raise ValueError()
        packed = base64.b64decode(wire,validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(packed,int(length)+1)
        if (len(raw)!=int(length) or not decoder.eof
                or decoder.unused_data or decoder.unconsumed_tail
                or hashlib.sha256(raw).hexdigest()!=sha):
            raise ValueError()
        return raw
    except (ValueError,TypeError,UnicodeError,zlib.error):
        raise PayloadStorageError('INVALID_SQLITE_JSON_PAYLOAD') from None


@lru_cache(maxsize=32)
def _cached_unpack(value):
    return _unpack(value)


def unpack_text(value):
    """Exact original string; malformed reserved envelopes fail closed."""
    if not isinstance(value,str) or not value.startswith('~stj'):
        return value
    if len(value)>MAX_ENCODED_BYTES:
        raise PayloadStorageError('INVALID_SQLITE_JSON_PAYLOAD')
    try:
        declared = int(value[len(PREFIX):].split(':',1)[0]) if value.startswith(PREFIX) else 0
    except ValueError:
        declared = 0
    # Cache bytes rather than Unicode objects: at most8MiB of expanded bytes
    # plus1MiB ASCII keys, even when one non-BMP character widens a whole str.
    try:
        if len(value)<=32768 and 0<declared<=256*1024:
            return _cached_unpack(value).decode('utf8')
        return _unpack(value).decode('utf8')
    except UnicodeError:
        raise PayloadStorageError('INVALID_SQLITE_JSON_PAYLOAD') from None


def pack_text(value):
    if not isinstance(value,str): return value
    if value.startswith('~stj'):
        unpack_text(value)
        return value
    if len(value)>MAX_RAW_BYTES: return value
    raw = value.encode('utf8')
    if not MIN_RAW_BYTES <= len(raw) <= MAX_RAW_BYTES: return value
    wire = base64.b64encode(zlib.compress(raw,1)).decode('ascii')
    packed = PREFIX+str(len(raw))+':'+hashlib.sha256(raw).hexdigest()+':'+wire
    return packed if len(packed)<len(raw) else value


def _definition():
    return dict(contract=SQLITE_JSON_PAYLOAD_CONTRACT,
        columns=[[t,c] for t,c in sorted(TARGET_COLUMNS.items())],
        text_encoding='utf-8',compression='zlib-level1-base64',
        minimum_raw_bytes=MIN_RAW_BYTES,maximum_raw_bytes=MAX_RAW_BYTES,
        production_authority=False)


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)


def marker_guards():
    return {f'trg_{TABLE}_{action.lower()}':
        f'CREATE TRIGGER "trg_{TABLE}_{action.lower()}" BEFORE {action} ON "{TABLE}" '
        "BEGIN SELECT RAISE(ABORT,'JSON payload storage marker is immutable'); END"
        for action in ('INSERT','UPDATE','DELETE')}


def _schema_name(schema):
    if schema not in ('main','src','verified_hot'):
        raise PayloadStorageError('UNSUPPORTED_JSON_PAYLOAD_SCHEMA')
    return schema


def manifest_fields(conn, *, schema='main'):
    cursor = conn.cursor()
    cursor.row_factory = None
    try:
        return _manifest_fields(cursor,schema)
    finally:
        cursor.close()


def _manifest_fields(cursor,schema):
    schema = _schema_name(schema)
    row = cursor.execute(f'SELECT type,sql FROM {schema}.sqlite_schema WHERE name=?',(TABLE,)).fetchone()
    if row is None: return {}
    if tuple(row)!=('table',TABLE_SQL):
        raise PayloadStorageError('INVALID_JSON_PAYLOAD_STORAGE_MARKER')
    guards = dict(cursor.execute(f"SELECT name,sql FROM {schema}.sqlite_schema WHERE type='trigger' AND tbl_name=?",(TABLE,)))
    if guards!=marker_guards(): raise PayloadStorageError('INVALID_JSON_PAYLOAD_STORAGE_GUARDS')
    rows = cursor.execute(f'SELECT id,payload_json,payload_sha256 FROM {schema}."{TABLE}" LIMIT 2').fetchall()
    if len(rows)!=1 or rows[0][0]!=1:
        raise PayloadStorageError('INVALID_JSON_PAYLOAD_STORAGE_MARKER')
    raw, sha = rows[0][1], rows[0][2]
    expected = _canonical(_definition())
    if (not isinstance(raw,str) or len(raw)>4096 or raw!=expected
            or sha!=hashlib.sha256(expected.encode()).hexdigest()):
        raise PayloadStorageError('INVALID_JSON_PAYLOAD_STORAGE_PIN')
    for table,col in TARGET_COLUMNS.items():
        columns = {r[1]:r[2] for r in cursor.execute(f'PRAGMA {schema}.table_info("{table}")')}
        if str(columns.get(col,'')).upper()!='TEXT':
            raise PayloadStorageError('JSON_PAYLOAD_TARGET_COLUMN_MISSING')
    return {MANIFEST_FIELD:sha}


def install_marker(conn):
    if conn.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(TABLE,)).fetchone():
        raise PayloadStorageError('JSON_PAYLOAD_STORAGE_ALREADY_MARKED')
    raw = _canonical(_definition())
    conn.execute('SAVEPOINT install_json_payload_marker')
    try:
        conn.execute(TABLE_SQL)
        conn.execute(f'INSERT INTO "{TABLE}" VALUES(1,?,?)',(raw,hashlib.sha256(raw.encode()).hexdigest()))
        for sql in marker_guards().values(): conn.execute(sql)
        fields = manifest_fields(conn)
    except BaseException:
        conn.execute('ROLLBACK TO install_json_payload_marker')
        conn.execute('RELEASE install_json_payload_marker')
        raise
    conn.execute('RELEASE install_json_payload_marker')
    return fields


def drop_marker(conn):
    """Only for an owned analytical expansion after all target cells decoded."""
    if not manifest_fields(conn): return
    for name in marker_guards(): conn.execute('DROP TRIGGER "'+name+'"')
    conn.execute(f'DROP TABLE "{TABLE}"')


def install_reader(conn, *, schema='main'):
    """Preserve tuple/Row/custom factories; never decode unrelated columns."""
    enabled = bool(manifest_fields(conn,schema=schema))
    conn.create_function('storage_json_text',1,unpack_text if enabled else lambda v:v,deterministic=True)
    if not enabled: return False
    previous = conn.row_factory
    if getattr(previous,'_sqlite_json_payload_reader',False): return True

    def reader(cursor, row):
        values = tuple(unpack_text(v) if d[0] in _READER_COLUMNS else v
                       for d,v in zip(cursor.description,row))
        return previous(cursor,values) if previous is not None else values
    reader._sqlite_json_payload_reader = True
    conn.row_factory = reader
    return True
