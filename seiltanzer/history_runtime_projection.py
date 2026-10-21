"""Verified metadata for rejected cold sources; never a full source substitute.

Stdlib only. This module deliberately declares no runtime-start capability.
An index by itself does not close historical counters or runtime readers.
"""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import time

TABLE = 'archived_passive_rejections'
CONTRACT = 'sqlite-history-rejected-source-index-v1'
MAX_ROWS = 1_000_000
COLUMNS = ('source_rowid', 'observation_id', 'instrument', 'captured_ts', 'target_ts',
           'horizon_minutes', 'feature_contract_version', 'resolution_status', 'source_record_sha256')
DDL = f'CREATE TABLE "{TABLE}" (' + (
    'source_rowid INTEGER PRIMARY KEY,observation_id TEXT NOT NULL UNIQUE,'
    'instrument TEXT NOT NULL,captured_ts REAL NOT NULL,target_ts REAL NOT NULL,'
    'horizon_minutes INTEGER NOT NULL,feature_contract_version TEXT NOT NULL,'
    "resolution_status TEXT NOT NULL CHECK(resolution_status='insufficient_future_data'),"
    'source_record_sha256 TEXT NOT NULL)')


def _guards():
    return {f'trg_{TABLE}_{action.lower()}':
            f'CREATE TRIGGER "trg_{TABLE}_{action.lower()}" BEFORE {action} ON "{TABLE}" '
            "BEGIN SELECT RAISE(ABORT,'Archived rejection index is immutable'); END"
            for action in ('INSERT', 'UPDATE', 'DELETE')}


def _check(deadline):
    if time.monotonic() >= deadline:
        raise ValueError('Archived rejected source deadline exhausted')


def _record(values):
    rowid, identity, instrument, captured, target, horizon, version, status, source_hash = values
    if (type(rowid) is not int or not 0 < rowid < 2**63
            or not isinstance(identity, str) or not 0 < len(identity.encode('utf8')) <= 4096
            or not isinstance(instrument, str) or not 0 < len(instrument) <= 128
            or type(captured) not in (int, float) or not math.isfinite(captured)
            or type(target) not in (int, float) or not math.isfinite(target) or target <= captured
            or type(horizon) is not int or not 0 < horizon < 2**31
            or not isinstance(version, str) or not 0 < len(version) <= 128
            or status != 'insufficient_future_data'
            or not isinstance(source_hash, str) or not re.fullmatch('[0-9a-f]{64}', source_hash)):
        raise ValueError('Invalid archived rejected source metadata')
    return tuple(values)


def _source_hasher():
    # Load the same canonical owner as live mutation checks without app startup.
    path = Path(__file__).resolve().with_name('g1_source_record.py')
    spec = importlib.util.spec_from_file_location('_archive_g1_source_identity', path)
    if spec is None or spec.loader is None:
        raise ValueError('Canonical rejected source identity unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.source_record_sha256


def rejected_source_record(source, rowid, *, dataset_contract_version, source_hasher=None, source_schema='main'):
    """Project a real IFD row only after current immutable rejection/hash proof.

    The source connection is a fully verified immutable full database. This
    function grants no cold transport proof or authority by itself.
    """
    if type(rowid) is not int or not 0 < rowid < 2**63:
        raise ValueError('Invalid rejected source rowid')
    if source_schema not in ('main', 'src'):
        raise ValueError('Unsupported rejected source schema')
    schema = '"'+source_schema+'".'
    if any(r[1].lower() == 'rowid' for r in source.execute(f'PRAGMA "{source_schema}".table_xinfo(passive_market_observations)')):
        raise ValueError('Unsupported rejected source rowid alias')
    cursor = source.execute('SELECT * FROM '+schema+'passive_market_observations WHERE rowid=?', (rowid,))
    found = cursor.fetchone()
    if found is None:
        raise ValueError('Missing rejected source row')
    row = dict(zip((c[0] for c in cursor.description), found))
    if row.get('resolution_status') != 'insufficient_future_data':
        raise ValueError('Only terminal rejected source rows may be projected')
    memberships = source.execute('SELECT dataset_contract_version,source_record_sha256,'
        'forecast_eval_eligible,q_to_p_eligible FROM '+schema+'g1_dataset_membership WHERE observation_id=?',
        (row['observation_id'],)).fetchall()
    current = [r for r in memberships if r[0] == dataset_contract_version]
    if (len(current) != 1 or any(type(r[2]) is not int or type(r[3]) is not int
                               or r[2] != 0 or r[3] != 0 for r in memberships)):
        raise ValueError('Unprocessed or eligible rejected source membership')
    digest = (source_hasher or _source_hasher())(row)
    if digest != current[0][1]:
        raise ValueError('Changed rejected source identity')
    metadata = dict(row, source_rowid=rowid, source_record_sha256=digest)
    return _record([metadata.get(key) for key in COLUMNS])


def create_rejected_source_index(output, source, retired_rowids, *, dataset_contract_version, deadline, source_schema='main'):
    """Create an all-or-nothing immutable index from actual full source rows."""
    _check(deadline)
    if (not isinstance(dataset_contract_version, str) or not 0 < len(dataset_contract_version) <= 128
            or not isinstance(retired_rowids, list) or not 0 < len(retired_rowids) <= MAX_ROWS):
        raise ValueError('Invalid rejected source index contract or size')
    previous = 0
    for rowid in retired_rowids:
        if type(rowid) is not int or not previous < rowid < 2**63:
            raise ValueError('Invalid rejected source rowid order')
        previous = rowid
    if output.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (TABLE,)).fetchone():
        raise ValueError('Cannot overwrite an existing rejected source index')
    hasher = _source_hasher()
    output.execute('SAVEPOINT owned_rejected_source_index')
    try:
        output.execute(DDL)
        for rowid in retired_rowids:
            _check(deadline)
            row = rejected_source_record(source, rowid, dataset_contract_version=dataset_contract_version,
                                         source_hasher=hasher, source_schema=source_schema)
            output.execute(f'INSERT INTO "{TABLE}" VALUES(?,?,?,?,?,?,?,?,?)', row)
        for sql in _guards().values():
            output.execute(sql)
        proof = _index_proof(output, dataset_contract_version, deadline=deadline)
        output.execute('RELEASE owned_rejected_source_index')
        return proof
    except BaseException:
        output.execute('ROLLBACK TO owned_rejected_source_index')
        output.execute('RELEASE owned_rejected_source_index')
        raise


def _index_proof(conn, dataset_contract_version, *, retired_rowids=None, deadline=None):
    deadline = time.monotonic()+5 if deadline is None else deadline
    _check(deadline)
    schema = conn.execute('SELECT sql FROM sqlite_schema WHERE type=\'table\' AND name=?', (TABLE,)).fetchone()
    if schema != (DDL,) and (schema is None or tuple(schema) != (DDL,)):
        raise ValueError('Unsupported rejected source index schema')
    guards = dict(conn.execute('SELECT name,sql FROM sqlite_schema WHERE type=\'trigger\' AND tbl_name=?', (TABLE,)))
    if guards != _guards():
        raise ValueError('Missing or changed rejected source index guards')
    digest = hashlib.sha256(CONTRACT.encode('ascii')+b'\0')
    count = 0
    for raw in conn.execute(f'SELECT {",".join(COLUMNS)} FROM "{TABLE}" ORDER BY source_rowid'):
        _check(deadline)
        row = _record(raw)
        if retired_rowids is not None and (count >= len(retired_rowids) or row[0] != retired_rowids[count]):
            raise ValueError('Wrong rejected source index retired coverage')
        digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')+b'\n')
        count += 1
        if count > MAX_ROWS:
            raise ValueError('Excessive rejected source index')
    if count == 0 or (retired_rowids is not None and count != len(retired_rowids)):
        raise ValueError('Incomplete rejected source index coverage')
    return dict(contract=CONTRACT, dataset_contract_version=dataset_contract_version,
                row_count=count, logical_sha256=digest.hexdigest(), production_authority=False,
                schema_sha256=hashlib.sha256(DDL.encode('utf8')).hexdigest())


def validate_rejected_source_index(conn, proof, *, retired_rowids, deadline=None):
    if (not isinstance(proof, dict) or proof.get('production_authority') is not False
            or not isinstance(proof.get('dataset_contract_version'), str)):
        raise ValueError('Invalid rejected source index proof')
    measured = _index_proof(conn, proof['dataset_contract_version'],
                            retired_rowids=retired_rowids, deadline=deadline)
    if proof != measured:
        raise ValueError('Changed rejected source index proof')
    return measured


ROLLUP_TABLE = 'archived_passive_rollups'
ROLLUP_CONTRACT = 'sqlite-history-runtime-rollups-v1'
MAX_ROLLUP_BYTES = 65536
STATISTIC_FIELDS = frozenset(('legacy_f2_n', 'legacy_f3_n', 'legacy_f31_n', 'current_f32_n',
    'fixed_horizon_raw_n', 'option_native_raw_n', 'terminal_q_eligible_n', 'terminal_q_resolved_n',
    'first_touch_clean_n', 'first_touch_ambiguous_n', 'first_touch_point_only_n',
    'sigma_valid_n', 'sigma_missing_n'))
MEASUREMENT_FIELDS = frozenset(('horizon_contract_error_n', 'q_semantic_contract_error_n',
    'proxy_mapping_error_n', 'path_contract_error_n', 'time_contract_error_n',
    'terminal_pit_valid_n', 'first_touch_clean_n', 'inverse_proxy_observation_n',
    'runtime_current_n', 'runtime_background_origin_n', 'pristine_candidate_n',
    'background_collector_origin_n', 'manual_origin_n', 'test_origin_n', 'replay_origin_n'))


def _rollup_bytes(payload):
    fields = {'contract', 'source_contract_version', 'measurement_contract_version',
              'dataset_contract_version', 'statistics', 'measurement', 'evidence_eligible_n', 'production_authority'}
    if (not isinstance(payload, dict) or set(payload) != fields
            or payload.get('contract') != ROLLUP_CONTRACT or payload.get('production_authority') is not False
            or any(not isinstance(payload.get(key), str) or not 0 < len(payload[key]) <= 128
                   for key in ('source_contract_version', 'measurement_contract_version', 'dataset_contract_version'))):
        raise ValueError('Invalid runtime rollup contract')
    stats, measurement = payload['statistics'], payload['measurement']
    if (not isinstance(stats, dict) or set(stats) != STATISTIC_FIELDS.union(
            ('raw_n', 'resolution_counts', 'feature_version_counts'))
            or not isinstance(measurement, dict) or set(measurement) != MEASUREMENT_FIELDS):
        raise ValueError('Invalid runtime rollup counter fields')
    count = stats['raw_n']
    if (type(count) is not int or not 0 < count <= MAX_ROWS
            or stats['resolution_counts'] != {'insufficient_future_data': count}
            or type(stats['resolution_counts'].get('insufficient_future_data')) is not int
            or not isinstance(stats['feature_version_counts'], dict)
            or not 0 < len(stats['feature_version_counts']) <= 128
            or any(not isinstance(k, str) or not 0 < len(k) <= 128 or type(v) is not int or not 0 < v <= count
                   for k, v in stats['feature_version_counts'].items())
            or sum(stats['feature_version_counts'].values()) != count
            or any(type(stats[key]) is not int or not 0 <= stats[key] <= count for key in STATISTIC_FIELDS)
            or stats['current_f32_n'] != stats['feature_version_counts'].get(payload['source_contract_version'], 0)
            or stats['sigma_valid_n']+stats['sigma_missing_n'] != count
            or stats['terminal_q_resolved_n'] != 0
            or any(type(v) is not int or not 0 <= v <= 4*count for v in measurement.values())
            or type(payload['evidence_eligible_n']) is not int or not 0 <= payload['evidence_eligible_n'] <= count):
        raise ValueError('Invalid runtime rollup count or scope')
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')
    if len(raw) > MAX_ROLLUP_BYTES:
        raise ValueError('Excessive runtime rollup bytes')
    return raw


def _rollup_guards():
    return {f'trg_{ROLLUP_TABLE}_{action.lower()}':
            f'CREATE TRIGGER "trg_{ROLLUP_TABLE}_{action.lower()}" BEFORE {action} ON "{ROLLUP_TABLE}" '
            "BEGIN SELECT RAISE(ABORT,'Archived runtime rollups are immutable'); END"
            for action in ('INSERT', 'UPDATE', 'DELETE')}


def write_runtime_rollups(conn, payload):
    raw = _rollup_bytes(payload)
    if conn.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (ROLLUP_TABLE,)).fetchone():
        raise ValueError('Cannot overwrite existing runtime rollups')
    digest = hashlib.sha256(raw).hexdigest()
    conn.execute('SAVEPOINT owned_runtime_rollups')
    try:
        conn.execute(f'CREATE TABLE "{ROLLUP_TABLE}" (id INTEGER PRIMARY KEY CHECK(id=1),'
                     'payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL)')
        conn.execute(f'INSERT INTO "{ROLLUP_TABLE}" VALUES(1,?,?)', (raw.decode('utf8'), digest))
        for sql in _rollup_guards().values():
            conn.execute(sql)
        conn.execute('RELEASE owned_runtime_rollups')
    except BaseException:
        conn.execute('ROLLBACK TO owned_runtime_rollups')
        conn.execute('RELEASE owned_runtime_rollups')
        raise
    return dict(contract=ROLLUP_CONTRACT, payload_sha256=digest,
                row_count=payload['statistics']['raw_n'], production_authority=False)


def read_runtime_rollups(conn, *, expected_sha256):
    columns = [r[1] for r in conn.execute(f'PRAGMA table_xinfo("{ROLLUP_TABLE}")')]
    if columns != ['id', 'payload_json', 'payload_sha256']:
        raise ValueError('Invalid runtime rollup schema')
    metadata = conn.execute(f'SELECT id,typeof(payload_json),length(CAST(payload_json AS BLOB)) '
                            f'FROM "{ROLLUP_TABLE}" LIMIT 2').fetchall()
    if (len(metadata) != 1 or tuple(metadata[0])[:2] != (1, 'text')
            or not 0 < metadata[0][2] <= MAX_ROLLUP_BYTES):
        raise ValueError('Invalid runtime rollup size')
    guards = dict(conn.execute('SELECT name,sql FROM sqlite_schema WHERE type=\'trigger\' AND tbl_name=?', (ROLLUP_TABLE,)))
    if guards != _rollup_guards():
        raise ValueError('Invalid runtime rollup guards')
    raw, digest = conn.execute(f'SELECT CAST(payload_json AS BLOB),payload_sha256 FROM "{ROLLUP_TABLE}" WHERE id=1').fetchone()
    if digest != expected_sha256 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Changed runtime rollup hash')
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid runtime rollup JSON') from exc
    if _rollup_bytes(payload) != raw:
        raise ValueError('Noncanonical runtime rollup JSON')
    return payload


PROJECTION_CONTRACT = 'sqlite-history-runtime-projection-v1'


def validate_projection_fields(proof, retired_count):
    if (not isinstance(proof, dict) or set(proof) != {'contract', 'rejected_index', 'rollups'}
            or proof['contract'] != PROJECTION_CONTRACT):
        raise ValueError('Invalid runtime projection fields')
    index, rollup = proof['rejected_index'], proof['rollups']
    if (not isinstance(index, dict) or set(index) != {'contract', 'dataset_contract_version', 'row_count',
            'logical_sha256', 'production_authority', 'schema_sha256'}
            or index['contract'] != CONTRACT or index['production_authority'] is not False
            or not isinstance(index['dataset_contract_version'], str) or not 0 < len(index['dataset_contract_version']) <= 128
            or type(index['row_count']) is not int or index['row_count'] != retired_count
            or index['schema_sha256'] != hashlib.sha256(DDL.encode('utf8')).hexdigest()
            or not isinstance(index['logical_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', index['logical_sha256'])
            or not isinstance(rollup, dict) or set(rollup) != {'contract', 'payload_sha256', 'row_count', 'production_authority'}
            or rollup['contract'] != ROLLUP_CONTRACT or rollup['production_authority'] is not False
            or type(rollup['row_count']) is not int or rollup['row_count'] != retired_count
            or not isinstance(rollup['payload_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', rollup['payload_sha256'])):
        raise ValueError('Invalid runtime projection pins or coverage')


def _source_guards():
    return {f'trg_archive_source_{action.lower()}':
            f'CREATE TRIGGER "trg_archive_source_{action.lower()}" BEFORE {action} ON "passive_market_observations" '
            f'WHEN EXISTS(SELECT 1 FROM "{TABLE}" WHERE observation_id=NEW.observation_id OR source_rowid=NEW.rowid) '
            "BEGIN SELECT RAISE(ABORT,'Archived source identity cannot be resurrected'); END"
            for action in ('INSERT', 'UPDATE')}


def install_source_guards(conn):
    for sql in _source_guards().values():
        conn.execute(sql)


def validate_runtime_projection(conn, proof, *, retired_rowids, deadline=None):
    validate_projection_fields(proof, len(retired_rowids))
    validate_rejected_source_index(conn, proof['rejected_index'], retired_rowids=retired_rowids, deadline=deadline)
    payload = read_runtime_rollups(conn, expected_sha256=proof['rollups']['payload_sha256'])
    contract = proof['rejected_index']['dataset_contract_version']
    if payload['dataset_contract_version'] != contract or payload['statistics']['raw_n'] != len(retired_rowids):
        raise ValueError('Runtime projection and rollup contracts differ')
    expected_guards = _source_guards()
    actual_guards = dict(conn.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND name IN (?,?)",
                                      tuple(expected_guards)))
    if expected_guards != actual_guards:
        raise ValueError('Runtime projection resurrection guards differ')
    if conn.execute(f'SELECT 1 FROM "{TABLE}" a JOIN passive_market_observations p '
                    'ON p.rowid=a.source_rowid OR p.observation_id=a.observation_id LIMIT 1').fetchone():
        raise ValueError('Runtime projection overlaps retained full source')
    if conn.execute(f'SELECT 1 FROM "{TABLE}" a LEFT JOIN g1_dataset_membership g '
        'ON g.observation_id=a.observation_id AND g.dataset_contract_version=? '
        'WHERE g.observation_id IS NULL OR g.source_record_sha256 IS NOT a.source_record_sha256 '
        'OR g.forecast_eval_eligible IS NOT 0 OR g.q_to_p_eligible IS NOT 0 LIMIT 1', (contract,)).fetchone():
        raise ValueError('Runtime projection rejected membership no longer matches')
    # Keep the immutable archived set as the outer loop. The ordinary join can
    # scan every unrelated live membership on each bounded startup validation.
    # All versions of each archived identity still participate in this check.
    if conn.execute(f'SELECT 1 FROM "{TABLE}" a CROSS JOIN g1_dataset_membership g USING(observation_id) '
                    'WHERE g.forecast_eval_eligible IS NOT 0 OR g.q_to_p_eligible IS NOT 0 LIMIT 1').fetchone():
        raise ValueError('Runtime projection overlaps admitted membership')
    return payload


def begin_full_hydration(conn):
    """Only on the verified worker copy: remove our two storage-only guards.

    The union still denies every other actual trigger origin during INSERT.
    Full source validation precedes this step; output stays unpublished.
    """
    for name in _source_guards():
        conn.execute(f'DROP TRIGGER "{name}"')


def consume_runtime_projection(conn):
    for name in _source_guards():
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    for table in (TABLE, ROLLUP_TABLE):
        conn.execute(f'DROP TABLE "{table}"')
