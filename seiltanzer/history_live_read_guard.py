"""Stdlib direct-reader exclusion under the existing maintenance flock."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat

# Canonical identity is independent of a caller's requested source path.
PRODUCTION_ROOT = Path('/opt')/'seiltanzer'
PRODUCTION_DATABASE = PRODUCTION_ROOT/'data'/'trades.db'


def _live_read_unique(items):
    result = {}
    for key,value in items:
        if key in result:raise ValueError('AMBIGUOUS_HISTORY_JOURNAL')
        result[key] = value
    return result


def _live_read_terminal_journal(path):
    if not os.path.lexists(path):return
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077
            or not 0 < info.st_size <= 65536):
        raise ValueError('UNSAFE_HISTORY_JOURNAL')
    raw = path.read_bytes()
    state = json.loads(raw,object_pairs_hook=_live_read_unique)
    if (type(state) is not dict or set(state) != {'contract','run_id','plan_sha256','phase','service_started'}
            or state['contract'] != 'sqlite-history-transaction-v1'
            or type(state['run_id']) is not str
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',state['run_id'])
            or type(state['plan_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',state['plan_sha256'])
            or state['phase'] not in ('COMMITTED','ORIGINAL_RESTORED')
            or type(state['service_started']) is not bool
            or json.dumps(state,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode() != raw):
        raise ValueError('HISTORY_SOURCE_READ_FENCED')


@contextmanager
def guarded_live_read(database):
    """Hold SHARED exclusion for a direct live DB reader's entire lifetime."""
    database = Path(database).absolute()
    if database.resolve() != PRODUCTION_DATABASE:
        yield
        return
    import fcntl
    if database != database.resolve():
        raise ValueError('LIVE_SOURCE_ALIAS_REFUSED')
    lock_path = PRODUCTION_ROOT/'.deployment-git-maintenance.lock'
    fd = os.open(lock_path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022
                or info.st_uid != PRODUCTION_ROOT.stat().st_uid):
            raise ValueError('UNSAFE_HISTORY_MAINTENANCE_LOCK')
        try:
            fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('HISTORY_SOURCE_READ_FENCED') from None
        current = lock_path.lstat()
        if current.st_ino != info.st_ino or current.st_dev != info.st_dev:
            raise ValueError('HISTORY_MAINTENANCE_LOCK_CHANGED')
        if os.path.lexists(PRODUCTION_ROOT/'data'/'cutover.active'):
            raise ValueError('HISTORY_SOURCE_READ_FENCED')
        _live_read_terminal_journal(PRODUCTION_ROOT/'data'/'cutover.journal.json')
        source = database.lstat()
        if not stat.S_ISREG(source.st_mode) or source.st_nlink != 1:
            raise ValueError('INVALID_DIRECT_HISTORY_SOURCE')
        yield fd
    finally:
        os.close(fd)
