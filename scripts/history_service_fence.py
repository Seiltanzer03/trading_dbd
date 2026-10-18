"""Stdlib-only read-only ExecCondition, installed outside the app checkout.

An active guard or unfinished journal blocks every application prestart step.
Marked history additionally requires a runtime-reader capability in the code
being started, so rolling back to an older checkout cannot open a partial DB.
"""
import argparse
import ast
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


def check_start(database, application_root, *, guard=None, journal=None):
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
            fields = namespace['history_manifest_fields'](conn)
            if not fields:
                raise ValueError('Missing hot state')
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
