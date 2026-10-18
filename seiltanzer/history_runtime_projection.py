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


def rejected_source_record(source, rowid, *, dataset_contract_version, source_hasher=None):
    """Project a real IFD row only after current immutable rejection/hash proof.

    The source connection is a fully verified immutable full database. This
    function grants no cold transport proof or authority by itself.
    """
    if type(rowid) is not int or not 0 < rowid < 2**63:
        raise ValueError('Invalid rejected source rowid')
    cursor = source.execute('SELECT * FROM passive_market_observations WHERE rowid=?', (rowid,))
    found = cursor.fetchone()
    if found is None:
        raise ValueError('Missing rejected source row')
    row = dict(zip((c[0] for c in cursor.description), found))
    if row.get('resolution_status') != 'insufficient_future_data':
        raise ValueError('Only terminal rejected source rows may be projected')
    memberships = source.execute('SELECT dataset_contract_version,source_record_sha256,'
        'forecast_eval_eligible,q_to_p_eligible FROM g1_dataset_membership WHERE observation_id=?',
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


def create_rejected_source_index(output, source, retired_rowids, *, dataset_contract_version, deadline):
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
                                         source_hasher=hasher)
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
