"""Internal frozen-source phase, before any hot-plan/destructive handoff.

Caller must own its validated host/disposable lock and real control adapter.
No production CLI, background launcher, cloud operation or transfer is present.
The final native owner drives this phase under its independent hard time bound.
"""
import hashlib
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time

from scripts import sqlite_history_transaction as core
from scripts.history_storage_loader import history_state
from scripts.sqlite_history_partition import _identity

CONTRACT = 'sqlite-history-frozen-preparation-v1'
PREPARATION_SECONDS = 5400
FIELDS = {'contract','run_id','application_sha','nonce','boot_id','started_monotonic',
          'started_wall_ts','epoch','phase','source','service_started'}
PHASES = {'STOPPING','FROZEN','ABORTED_ORIGINAL','FENCE_LOST_GUARDED'}


def _boot_id():
    value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not re.fullmatch('[0-9a-f-]{36}',value):
        raise core.CutoverError('BOOT_ID_UNAVAILABLE')
    return value


def _epoch(state):
    pin = {key:state[key] for key in ('contract','run_id','application_sha','nonce','boot_id',
                                     'started_monotonic','started_wall_ts')}
    return dict(contract=core.CONTRACT,run_id=state['run_id'],
                plan_sha256=hashlib.sha256(core._canonical(pin)).hexdigest())


def _load(path, run_id, application_sha):
    state = core._read(path)
    if state is None:
        return None
    if (type(state) is not dict or set(state) != FIELDS or state['contract'] != CONTRACT
            or state['run_id'] != run_id or state['application_sha'] != application_sha
            or type(state['nonce']) is not str or not re.fullmatch('[0-9a-f]{32}',state['nonce'])
            or type(state['boot_id']) is not str or not re.fullmatch('[0-9a-f-]{36}',state['boot_id'])
            or any(type(state[key]) not in (int,float) or not math.isfinite(state[key]) or state[key] < 0
                   for key in ('started_monotonic','started_wall_ts'))
            or state['phase'] not in PHASES or type(state['service_started']) is not bool
            or (state['service_started'] and state['phase'] != 'ABORTED_ORIGINAL')
            or state['epoch'] != _epoch(state)):
        raise core.CutoverError('INVALID_FROZEN_PREPARATION_EPOCH')
    if state['source'] is not None:
        source = state['source']
        if type(source) is not dict or set(source) != {'identity','file_identity'}:
            raise core.CutoverError('INVALID_FROZEN_SOURCE_PROOF')
        core._valid_identity(source['identity'])
        if type(source['file_identity']) is not dict:
            raise core.CutoverError('INVALID_FROZEN_SOURCE_PROOF')
    elif state['phase'] in ('FROZEN','ABORTED_ORIGINAL'):
        raise core.CutoverError('MISSING_FROZEN_SOURCE_PROOF')
    return state


def _lost(root, state):
    state['phase'] = 'FENCE_LOST_GUARDED'
    state['service_started'] = False
    core._write(root/'cutover.preparation.json',state)
    core._write(root/'cutover.journal.json',dict(state['epoch'],
        phase='FENCE_LOST_GUARDED',service_started=False))
    raise core.FencingError('FROZEN_EPOCH_REQUIRES_DATA_RECONCILIATION')


def _fence(root, state, control):
    try:
        intact = (core._read(root/'cutover.active') == state['epoch']
                  and control.assert_fenced() is True and control.assert_writers_closed() is True)
    except Exception:
        intact = False
    if not intact:
        _lost(root,state)


def _measure(database, deadline):
    before = _identity(database)
    if not 4096 <= before['size'] <= core.MAX_IMAGE_BYTES:
        raise core.CutoverError('INVALID_FROZEN_SOURCE_SIZE')
    digest = hashlib.sha256()
    with database.open('rb') as stream:
        while True:
            core._check_deadline(deadline)
            chunk = stream.read(core.CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    conn = sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA cache_size=-2048')
        conn.set_progress_handler(lambda:time.monotonic() >= deadline,1000)
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise core.CutoverError('FROZEN_SOURCE_QUICK_CHECK_FAILED')
        if conn.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(history_state.TABLE,)).fetchone():
            raise core.CutoverError('INITIAL_UNMARKED_SOURCE_REQUIRED')
    finally:
        conn.close()
    core._check_deadline(deadline)
    if _identity(database) != before:
        raise core.FencingError('FROZEN_SOURCE_CHANGED')
    return dict(identity=dict(database_sha256=digest.hexdigest(),database_size_bytes=before['size']),
                file_identity=before)


def _verify_source(root, state, control, deadline):
    _fence(root,state,control)
    try:
        if _identity(root/'trades.db') != state['source']['file_identity']:
            _lost(root,state)
        core._verify_database(root/'trades.db',state['source']['identity'],deadline)
    except core.FencingError:
        raise
    except Exception:
        if time.monotonic() >= deadline:
            raise core.CutoverError('FROZEN_SOURCE_REVERIFY_TIMEOUT_GUARDED') from None
        _lost(root,state)
    _fence(root,state,control)


def _finish_abort(root, state, control):
    guard = root/'cutover.active'
    if guard.exists() or guard.is_symlink():
        _fence(root,state,control)
        if _identity(root/'trades.db') != state['source']['file_identity']:
            _lost(root,state)
        guard.unlink();core._fsync_directory(root)
    if not state['service_started']:
        # Once guard release allows real writes, never compare or restore old
        # source bytes on retry. Launch failures need forward recovery only.
        if control.start_verified_database() is not True:
            raise core.CutoverError('ORIGINAL_START_NOT_VERIFIED')
        state['service_started'] = True
        core._write(root/'cutover.preparation.json',state)
    return dict(state,source=dict(state['source']['identity']))


def prepare_epoch(root, *, run_id, application_sha, control):
    """Prepare/resume one epoch; an expired epoch aborts before any hot access."""
    root = Path(root)
    if (type(run_id) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',run_id)
            or type(application_sha) is not str or not re.fullmatch('[0-9a-f]{40}',application_sha)):
        raise core.CutoverError('INVALID_FROZEN_PREPARATION_INPUT')
    path = root/'cutover.preparation.json'
    state = _load(path,run_id,application_sha)
    if state is not None and state['phase'] == 'FENCE_LOST_GUARDED':
        raise core.FencingError('FROZEN_EPOCH_REQUIRES_DATA_RECONCILIATION')
    if state is not None and state['phase'] == 'ABORTED_ORIGINAL':
        if (root/'cutover.active').exists() or (root/'cutover.active').is_symlink():
            _verify_source(root,state,control,time.monotonic()+core.MAX_SECONDS)
        return _finish_abort(root,state,control)
    if core._read(root/'cutover.journal.json') is not None:
        raise core.CutoverError('EXISTING_TRANSFER_EPOCH_REQUIRES_OWN_PLAN')
    if state is None:
        if (root/'cutover.active').exists() or (root/'cutover.active').is_symlink():
            raise core.CutoverError('EXISTING_CUTOVER_GUARD')
        state = dict(contract=CONTRACT,run_id=run_id,application_sha=application_sha,
            nonce=secrets.token_hex(16),boot_id=_boot_id(),started_monotonic=time.monotonic(),
            started_wall_ts=time.time(),phase='STOPPING',source=None,service_started=False)
        state['epoch'] = _epoch(state)
        core._write(path,state)
        core._write(root/'cutover.active',state['epoch'])
    if state['boot_id'] != _boot_id():
        _lost(root,state)
    if state['phase'] == 'STOPPING':
        if control.stop_writers() is not True:
            _lost(root,state)
        _fence(root,state,control)
        if not 4096 <= core._regular(root/'trades.db').st_size <= core.MAX_IMAGE_BYTES:
            raise core.CutoverError('INVALID_FROZEN_SOURCE_SIZE')
        conn = sqlite3.connect(root/'trades.db',timeout=1)
        try:
            if conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                _lost(root,state)
        finally:
            conn.close()
        _fence(root,state,control)
        deadline = max(state['started_monotonic']+PREPARATION_SECONDS,
                       time.monotonic()+core.MAX_SECONDS)
        try:
            state['source'] = _measure(root/'trades.db',deadline)
        except core.FencingError:
            _lost(root,state)
        state['phase'] = 'FROZEN'
        core._write(path,state)
        _fence(root,state,control)
        if _identity(root/'trades.db') != state['source']['file_identity']:
            _lost(root,state)
    else:
        _verify_source(root,state,control,time.monotonic()+core.MAX_SECONDS)
    if time.monotonic()-state['started_monotonic'] >= PREPARATION_SECONDS:
        state['phase'] = 'ABORTED_ORIGINAL'
        core._write(path,state)
        return _finish_abort(root,state,control)
    return dict(state,source=dict(state['source']['identity']))
