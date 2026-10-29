"""Bounded immutable catalog identity carried inside each compacted hot DB.

Stdlib only: off-host tools load this file without importing the application.
The marker records storage coverage, never trading or model authority.
"""
import hashlib
import importlib.util
import json
import re
import sqlite3
import struct
from pathlib import Path

TABLE = 'hot_history_storage_state'
CONTRACT = 'sqlite-history-hot-state-v1'
RUNTIME_CONTRACT = 'sqlite-history-hot-state-v2'
BUCKET = 'trading-dbd-backups-2026'
MAX_BYTES = 8 * 1024**2
MAX_RETIRED_ROWS = 1_000_000
PIN_FIELDS = ('cold_history_catalog_sha256', 'cold_history_catalog_bucket',
              'cold_history_catalog_object_key', 'sqlite_history_state_sha256')
PAYLOAD_PIN_FIELD = 'sqlite_json_payload_storage_sha256'
JSON_PAYLOAD_BACKUP_CONTRACT = 'trading-dbd-offhost-backup-v2-json-payloads'
_FIELDS = {'contract', 'bucket', 'catalog_source_database_sha256', 'catalog_sha256',
           'catalog_object_key', 'cutover_source_database_sha256', 'retired_rowids',
           'retained_identity', 'source_materialization_watermark', 'production_authority'}


def _runtime_projection():
    path = Path(__file__).resolve().with_name('history_runtime_projection.py')
    spec = importlib.util.spec_from_file_location('_history_runtime_projection', path)
    if spec is None or spec.loader is None:
        raise ValueError('Runtime history projection codec unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def identity_proof(conn, table, max_rowid):
    """Hash only the immutable rowid/capture-id mapping, never large payloads."""
    if table not in ('passive_market_observations', 'g1s_observations'):
        raise ValueError('Unsupported history identity table')
    ddl = conn.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
    if not ddl or not isinstance(ddl[0], str) or len(ddl[0]) > 65536:
        raise ValueError('Unsupported history identity schema')
    columns = {r[1].casefold() for r in conn.execute(f'PRAGMA table_xinfo("{table}")')}
    alias = next((name for name in ('rowid', '_rowid_', 'oid') if name not in columns), None)
    if alias is None or 'observation_id' not in columns:
        raise ValueError('Missing immutable history rowid/capture identity')
    # These tables carry large source payloads. A rowid range scan reads their
    # wide table pages merely to hash IDs; the existing single-column index
    # already contains every typed ID and its implicit rowid. Retain the exact
    # rowid ordering and fallback when no complete covering index exists.
    covering = ''
    for index in conn.execute(f'PRAGMA index_list("{table}")'):
        if index[4]:  # A partial index can omit null or otherwise retained IDs.
            continue
        quoted = '"' + index[1].replace('"', '""') + '"'
        indexed_columns = [row[2] for row in conn.execute(f'PRAGMA index_info({quoted})')]
        if indexed_columns == ['observation_id']:
            covering = ' INDEXED BY ' + quoted
            break
    where, params = (' WHERE 1=0', ()) if max_rowid is None else (f' WHERE "{alias}"<=?', (max_rowid,))
    query = (f'SELECT "{alias}",typeof(observation_id),length(CAST(observation_id AS BLOB)),'
             "CASE WHEN length(CAST(observation_id AS BLOB))>65536 THEN NULL "
             "WHEN typeof(observation_id)='text' THEN CAST(observation_id AS BLOB) ELSE observation_id END "
             f'FROM "{table}"{covering}{where} ORDER BY "{alias}"')
    digest = hashlib.sha256(); count = 0
    for rowid, kind, size, value in conn.execute(query, params):
        if size is not None and size > 65536:
            raise ValueError('History capture identity exceeds byte bound')
        raw = (b'' if kind == 'null' else struct.pack('>q', value) if kind == 'integer'
               else struct.pack('>d', value) if kind == 'real' else value)
        if not isinstance(raw, bytes):
            raise ValueError('Unsupported history identity storage class')
        digest.update(struct.pack('>q', rowid)+kind.encode('ascii')+struct.pack('>Q', len(raw))+raw)
        count += 1
    return dict(max_rowid=max_rowid, row_count=count, source_rowid_alias=alias,
                schema_sha256=hashlib.sha256(ddl[0].encode('utf8')).hexdigest(),
                logical_sha256=digest.hexdigest())


def catalog_key(source_sha, catalog_sha):
    for value in (source_sha, catalog_sha):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('Invalid history SHA-256 pin')
    return f'research/sqlite-history/v1/{source_sha}/catalogs/{catalog_sha}.json'


def canonical_state(state):
    runtime = isinstance(state, dict) and state.get('contract') == RUNTIME_CONTRACT
    fields = _FIELDS.union(('runtime_projection',)) if runtime else _FIELDS
    if (not isinstance(state, dict) or set(state) != fields
            or state['contract'] not in (CONTRACT, RUNTIME_CONTRACT) or state['bucket'] != BUCKET
            or state['production_authority'] is not False):
        raise ValueError('Unsupported hot history state')
    key = catalog_key(state['catalog_source_database_sha256'], state['catalog_sha256'])
    if (state['catalog_object_key'] != key
            or not isinstance(state['cutover_source_database_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', state['cutover_source_database_sha256'])):
        raise ValueError('Invalid hot history source pin')
    rows = state['retired_rowids']
    if not isinstance(rows, dict) or set(rows) != {'passive_market_observations'}:
        raise ValueError('Unsupported history retirement table')
    ids = rows['passive_market_observations']
    if not isinstance(ids, list) or not 0 < len(ids) <= MAX_RETIRED_ROWS:
        raise ValueError('Invalid history retired rowid bound')
    previous = 0
    for value in ids:
        if type(value) is not int or not previous < value < 2**63:
            raise ValueError('Invalid or unordered history retired rowids')
        previous = value
    if runtime:
        _runtime_projection().validate_projection_fields(state['runtime_projection'], len(ids))
    identities = state['retained_identity']
    if not isinstance(identities, dict) or set(identities) != {'passive_market_observations', 'g1s_observations'}:
        raise ValueError('Invalid retained history identity tables')
    for proof in identities.values():
        if (not isinstance(proof, dict) or set(proof) != {
                'max_rowid', 'row_count', 'source_rowid_alias', 'schema_sha256', 'logical_sha256'}
                or type(proof['row_count']) is not int or not 0 <= proof['row_count'] <= MAX_RETIRED_ROWS
                or proof['source_rowid_alias'] not in ('rowid', '_rowid_', 'oid')
                or (proof['max_rowid'] is None and proof['row_count'] != 0)
                or (proof['max_rowid'] is not None and (type(proof['max_rowid']) is not int
                    or not 0 < proof['max_rowid'] < 2**63))
                or any(not isinstance(proof[key], str) or not re.fullmatch('[0-9a-f]{64}', proof[key])
                       for key in ('schema_sha256', 'logical_sha256'))):
            raise ValueError('Invalid retained history identity proof')
    watermark = state['source_materialization_watermark']
    boundary = identities['passive_market_observations']['max_rowid']
    if (type(watermark) is not int or not 0 <= watermark < 2**63
            or boundary is None or ids[-1] > watermark or ids[-1] >= boundary):
        raise ValueError('Retired history exceeds the processed source watermark')
    raw = json.dumps(state, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf8')
    if len(raw) > MAX_BYTES:
        raise ValueError('Hot history state exceeds byte bound')
    return raw


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate hot history state member')
        result[key] = value
    return result


def read_history_state(conn):
    entry = conn.execute('SELECT type FROM sqlite_schema WHERE name=?', (TABLE,)).fetchone()
    if entry is None:
        return None
    if entry[0] != 'table':
        raise ValueError('Hot history state is not a table')
    columns = [r[1] for r in conn.execute(f'PRAGMA table_xinfo("{TABLE}")')]
    if columns != ['id', 'payload_json', 'payload_sha256']:
        raise ValueError('Unsupported hot history state schema')
    metadata = conn.execute(f'SELECT id,typeof(payload_json),length(CAST(payload_json AS BLOB)) '
                            f'FROM "{TABLE}" LIMIT 2').fetchall()
    if (len(metadata) != 1 or metadata[0][0] != 1 or metadata[0][1] != 'text'
            or not 0 < metadata[0][2] <= MAX_BYTES):
        raise ValueError('Invalid hot history state size or cardinality')
    raw, digest = conn.execute(f'SELECT CAST(payload_json AS BLOB),payload_sha256 '
                               f'FROM "{TABLE}" WHERE id=1').fetchone()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Hot history state hash differs')
    try:
        state = json.loads(raw, object_pairs_hook=_unique)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid hot history JSON') from exc
    if canonical_state(state) != raw:
        raise ValueError('Hot history state is not canonical')
    return state


def offhost_backup_contract(manifest):
    if PAYLOAD_PIN_FIELD not in manifest:
        return 'trading-dbd-offhost-backup-v1'
    if not isinstance(manifest[PAYLOAD_PIN_FIELD],str) or not re.fullmatch('[0-9a-f]{64}',manifest[PAYLOAD_PIN_FIELD]):
        raise ValueError('Invalid JSON payload backup pin')
    return JSON_PAYLOAD_BACKUP_CONTRACT


def _payload_codec():
    path = Path(__file__).resolve().with_name('sqlite_json_payloads.py')
    spec = importlib.util.spec_from_file_location('_history_json_payload_storage',path)
    if spec is None or spec.loader is None:
        raise ValueError('JSON payload storage verifier unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload_manifest_fields(conn):
    if conn.execute('SELECT 1 FROM sqlite_schema WHERE name=?',('json_feature_payload_storage',)).fetchone() is None:
        return {}
    return _payload_codec().manifest_fields(conn)


def history_manifest_fields(conn):
    payload_fields = _payload_manifest_fields(conn)
    state = read_history_state(conn)
    if state is None:
        return payload_fields
    for table, proof in state['retained_identity'].items():
        if identity_proof(conn, table, proof['max_rowid']) != proof:
            raise ValueError('Conflicting hot/cold history rowid: retained identity or retired rowid coverage changed')
    if state['contract'] == RUNTIME_CONTRACT:
        _runtime_projection().validate_runtime_projection(conn, state['runtime_projection'],
            retired_rowids=state['retired_rowids']['passive_market_observations'])
    return dict(cold_history_catalog_sha256=state['catalog_sha256'],
                cold_history_catalog_bucket=state['bucket'],
                cold_history_catalog_object_key=state['catalog_object_key'],
                sqlite_history_state_sha256=hashlib.sha256(canonical_state(state)).hexdigest(),
                **payload_fields)


def validate_manifest_history(conn, manifest):
    fields = history_manifest_fields(conn)
    if PAYLOAD_PIN_FIELD in manifest and PAYLOAD_PIN_FIELD not in fields:
        raise ValueError('Manifest JSON payload pin has no storage marker')
    if fields:
        if any(manifest.get(key) != value for key, value in fields.items()):
            raise ValueError('Manifest history pin differs from verified database state')
    elif any(key in manifest for key in PIN_FIELDS[1:]):
        raise ValueError('Manifest history pin has no embedded database state')
    return fields


def snapshot_history_manifest(path, manifest=None):
    """Inspect only the bounded marker in an already standalone snapshot."""
    conn = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    try:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA query_only=ON')
        if manifest is not None:
            return validate_manifest_history(conn, manifest)
        return history_manifest_fields(conn)
    finally:
        conn.close()


def write_history_state(conn, state):
    raw = canonical_state(state)
    if conn.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (TABLE,)).fetchone():
        raise ValueError('Cannot overwrite an existing hot history epoch')
    if state['contract'] == RUNTIME_CONTRACT:
        _runtime_projection().validate_runtime_projection(conn, state['runtime_projection'],
            retired_rowids=state['retired_rowids']['passive_market_observations'])
    conn.execute(f'CREATE TABLE "{TABLE}" (id INTEGER PRIMARY KEY CHECK(id=1),'
                 'payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL)')
    conn.execute(f'INSERT INTO "{TABLE}" VALUES(1,?,?)',
                 (raw.decode('utf8'), hashlib.sha256(raw).hexdigest()))
    for action in ('INSERT', 'UPDATE', 'DELETE'):
        conn.execute(f'CREATE TRIGGER "trg_history_state_{action.lower()}" '
                     f'BEFORE {action} ON "{TABLE}" '
                     "BEGIN SELECT RAISE(ABORT,'Hot history state is immutable'); END")


def begin_full_history_hydration(conn):
    """On a verified disposable worker copy, prepare our storage-only guards."""
    state = read_history_state(conn)
    if state is not None and state['contract'] == RUNTIME_CONTRACT:
        _runtime_projection().begin_full_hydration(conn)


def consume_history_state(conn):
    """Remove storage-only state from a disposable, fully hydrated output."""
    state = read_history_state(conn)
    if state is not None:
        if state['contract'] == RUNTIME_CONTRACT:
            _runtime_projection().consume_runtime_projection(conn)
        conn.execute(f'DROP TABLE "{TABLE}"')
