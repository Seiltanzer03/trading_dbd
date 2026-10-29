"""Stdlib-only read-only ExecCondition, installed outside the app checkout.

An active guard or unfinished journal blocks every application prestart step.
Marked history additionally requires a runtime-reader capability in the code
being started, so rolling back to an older checkout cannot open a partial DB.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

CONTRACT = 'sqlite-history-service-fence-v1'
HISTORY_TABLE = 'hot_history_storage_state'
RUNTIME_CONTRACT = 'sqlite-history-runtime-v1'
MAX_JOURNAL_BYTES = 65536
MAX_MODULE_BYTES = 262144
SQL_SECONDS = 5
_PHASES = frozenset(('PREPARED', 'APPLYING_HOT', 'HOT_VERIFIED', 'RECOVERING',
                    'FAILED_GUARDED', 'FENCE_LOST_GUARDED', 'ORIGINAL_RESTORED', 'COMMITTED'))


def _regular(path, maximum):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= maximum):
        raise ValueError('Unsupported owned file')
    return info


def _unique(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('Duplicate journal member')
        value[key] = item
    return value


def _journal(path):
    if not os.path.lexists(path):
        return None
    info = _regular(path, MAX_JOURNAL_BYTES)
    if info.st_mode & 0o077:
        raise ValueError('Journal is not private')
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=_unique)
    if (not isinstance(value, dict) or set(value) != {
            'contract', 'run_id', 'plan_sha256', 'phase', 'service_started'}
            or value['contract'] != 'sqlite-history-transaction-v1'
            or not isinstance(value['run_id'], str)
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value['run_id'])
            or not isinstance(value['plan_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', value['plan_sha256'])
            or value['phase'] not in _PHASES or type(value['service_started']) is not bool
            or (value['service_started'] and value['phase'] not in ('COMMITTED', 'ORIGINAL_RESTORED'))
            or json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False).encode('ascii') != raw):
        raise ValueError('Unsupported journal')
    return value


def _has_runtime_capability(app_root):
    path = app_root/'seiltanzer/history_runtime_views.py'
    try:
        _regular(path, MAX_MODULE_BYTES)
        tree = ast.parse(path.read_bytes())
        values = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'HISTORY_RUNTIME_CONTRACT'
                    for target in node.targets):
                values.append(ast.literal_eval(node.value))
        return values == [RUNTIME_CONTRACT]
    except (OSError, SyntaxError, ValueError, RecursionError):
        return False


def _literal_reader_contract(app_root, module, name):
    path = app_root/'seiltanzer'/module
    _regular(path, MAX_MODULE_BYTES)
    values = [ast.literal_eval(node.value) for node in ast.parse(path.read_bytes()).body
              if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name
                                                      for target in node.targets)]
    if len(values) != 1 or not isinstance(values[0], str) or not 0 < len(values[0]) <= 128:
        raise ValueError('Missing or ambiguous reader contract')
    return values[0]


def _private_record(path):
    info=_regular(path,MAX_JOURNAL_BYTES)
    if info.st_mode&0o077:raise ValueError('Epoch metadata is not private')
    raw=path.read_bytes()
    value=json.loads(raw,object_pairs_hook=_unique)
    if (not isinstance(value,dict) or json.dumps(value,sort_keys=True,separators=(',',':'),
            ensure_ascii=True,allow_nan=False).encode('ascii')!=raw):
        raise ValueError('Invalid canonical epoch metadata')
    return value


def _json_epoch_reason(pointer):
    if not os.path.lexists(pointer):return None
    value=_private_record(pointer)
    if (set(value)!={'contract','run_id','application_sha'}
            or value['contract']!='sqlite-json-history-epoch-link-v1'
            or not isinstance(value['run_id'],str)
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value['run_id'])
            or not isinstance(value['application_sha'],str)
            or not re.fullmatch('[0-9a-f]{40}',value['application_sha'])):
        raise ValueError('Invalid JSON epoch link')
    metadata=pointer.parent/('json-rebuild-'+value['run_id'])
    info=metadata.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o700
            or info.st_uid!=pointer.lstat().st_uid):
        raise ValueError('Invalid JSON epoch directory')
    if os.path.lexists(metadata/'cutover.active'):return 'ACTIVE_JSON_HISTORY_EPOCH'
    journal=_journal(metadata/'cutover.journal.json')
    if journal is not None:
        if journal['run_id']!=value['run_id']:raise ValueError('JSON epoch journal differs')
        if journal['phase'] not in ('COMMITTED','ORIGINAL_RESTORED'):
            return 'UNFINISHED_JSON_HISTORY_EPOCH'
        plan=_private_record(metadata/'cutover.plan.json')
        if (plan.get('run_id')!=value['run_id'] or plan.get('application_sha')!=value['application_sha']
                or journal['plan_sha256']!=hashlib.sha256(core_bytes(plan)).hexdigest()):
            raise ValueError('JSON epoch terminal plan differs')
        return None
    state=_private_record(metadata/'cutover.preparation.json')
    if state.get('run_id')!=value['run_id'] or state.get('application_sha')!=value['application_sha']:
        raise ValueError('JSON epoch preparation differs')
    return None if state.get('phase')=='ABORTED_ORIGINAL' else 'UNFINISHED_JSON_HISTORY_EPOCH'


def core_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def check_start(database, application_root, *, guard=None, journal=None,json_epoch=None):
    """Return a bounded safe startup decision, without importing the application."""
    database, app_root = Path(database), Path(application_root)
    guard = Path(guard) if guard is not None else database.parent/'cutover.active'
    journal = Path(journal) if journal is not None else database.parent/'cutover.journal.json'

    def result(allowed, reason, *, marked=None):
        return dict(contract=CONTRACT, start_allowed=allowed, reason=reason,
                    hot_history_present=marked, production_authority=False)

    if os.path.lexists(guard):
        return result(False, 'ACTIVE_CUTOVER_GUARD')
    try:
        reason=_json_epoch_reason(Path(json_epoch) if json_epoch is not None
                                  else database.parent/'json-history.epoch.json')
        if reason is not None:return result(False,reason)
    except Exception:
        return result(False,'JSON_HISTORY_EPOCH_INVALID')
    try:
        current = _journal(journal)
    except Exception:
        return result(False, 'CUTOVER_JOURNAL_INVALID')
    if current is not None and current['phase'] not in ('COMMITTED', 'ORIGINAL_RESTORED'):
        return result(False, 'UNFINISHED_CUTOVER_JOURNAL')
    conn = None
    try:
        _regular(database, 25*1024**3)
        conn = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA cache_size=-2048')
        deadline = time.monotonic()+SQL_SECONDS
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        payload_entry = conn.execute('SELECT type FROM sqlite_schema WHERE name=?',('json_feature_payload_storage',)).fetchone()
        if payload_entry is not None:
            try:
                if (_literal_reader_contract(app_root,'history_runtime_views.py','JSON_PAYLOAD_READER_CONTRACT')
                        != 'sqlite-json-zlib-base64-v1'
                        or _literal_reader_contract(app_root,'sqlite_json_payloads.py','SQLITE_JSON_PAYLOAD_CONTRACT')
                        != 'sqlite-json-zlib-base64-v1'):
                    raise ValueError('Missing JSON payload reader')
            except Exception:
                return result(False,'JSON_PAYLOAD_READER_CAPABILITY_REQUIRED')
            try:
                codec_path = app_root/'seiltanzer/sqlite_json_payloads.py'
                _regular(codec_path,MAX_MODULE_BYTES)
                codec = dict(__name__='_payload_storage_start_guard',__file__=str(codec_path))
                exec(compile(codec_path.read_bytes(),str(codec_path),'exec'),codec)
                if not codec['manifest_fields'](conn): raise ValueError('Missing payload marker')
            except Exception:
                return result(False,'JSON_PAYLOAD_STORAGE_INVALID')
            if time.monotonic() >= deadline:
                return result(False,'JSON_PAYLOAD_STORAGE_CHECK_TIMEOUT')
        entry = conn.execute('SELECT type FROM sqlite_schema WHERE name=?', (HISTORY_TABLE,)).fetchone()
        if entry is None:
            if current is not None and current['phase'] == 'COMMITTED':
                return result(False, 'COMMITTED_HISTORY_MARKER_MISSING', marked=False)
            return result(True, 'UNMARKED_DATABASE', marked=False)
        if entry != ('table',):
            return result(False, 'HOT_HISTORY_STATE_INVALID', marked=True)
        if not _has_runtime_capability(app_root):
            return result(False, 'HOT_HISTORY_RUNTIME_CAPABILITY_REQUIRED', marked=True)
        path = app_root/'seiltanzer/history_storage_state.py'
        try:
            _regular(path, MAX_MODULE_BYTES)
            namespace = dict(__name__='_history_storage_for_start', __file__=str(path))
            # This trusted stdlib-only owner is loaded directly; never execute
            # application __init__, provider refresh, model or background work.
            exec(compile(path.read_bytes(), str(path), 'exec'), namespace)
            storage_state = namespace['read_history_state'](conn)
            if storage_state['contract'] != namespace['RUNTIME_CONTRACT']:
                return result(False, 'HOT_HISTORY_RUNTIME_PROJECTION_REQUIRED', marked=True)
            fields = namespace['history_manifest_fields'](conn)
            if not fields:
                raise ValueError('Missing hot state')
            codec = namespace['_runtime_projection']()
            payload = codec.read_runtime_rollups(conn,
                expected_sha256=storage_state['runtime_projection']['rollups']['payload_sha256'])
            contracts = (
                ('source_contract_version','passive_learning.py','PASSIVE_SCHEMA_VERSION'),
                ('dataset_contract_version','g1_dataset_runtime.py','G1_DATASET_CONTRACT_VERSION'),
                ('measurement_contract_version','measurement_q_runtime.py','MEASUREMENT_RUNTIME_VERSION'),
            )
            if any(payload[key] != _literal_reader_contract(app_root, module, name)
                   for key, module, name in contracts):
                return result(False, 'HOT_HISTORY_READER_CONTRACT_MISMATCH', marked=True)
        except Exception:
            return result(False, 'HOT_HISTORY_STATE_INVALID', marked=True)
        if time.monotonic() >= deadline:
            return result(False, 'HOT_HISTORY_STATE_CHECK_TIMEOUT', marked=True)
        return result(True, 'VERIFIED_HOT_RUNTIME_CAPABILITY', marked=True)
    except Exception:
        return result(False, 'AUTHORITATIVE_DATABASE_UNAVAILABLE')
    finally:
        if conn is not None:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='/opt/seiltanzer/data/trades.db')
    parser.add_argument('--application-root', default='/opt/seiltanzer')
    parser.add_argument('--guard')
    parser.add_argument('--journal')
    args = parser.parse_args()
    result = check_start(args.database, args.application_root, guard=args.guard, journal=args.journal)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['start_allowed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
