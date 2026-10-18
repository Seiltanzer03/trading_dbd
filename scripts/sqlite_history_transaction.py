"""Disposable proof of a durable, guarded single-file SQLite transfer.

This stage deliberately has no production CLI and refuses the production root.
The real systemd/workflow fence and reader closure remain release prerequisites.
Transport credentials never enter the bounded durable plan or journal.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time

from scripts.history_storage_loader import history_state
from scripts import sqlite_history_partition as archive

CONTRACT = 'sqlite-history-transaction-v1'
DISPOSABLE_MARKER = 'sqlite-history-disposable-proof-v1\n'
MAX_STATE_BYTES = 65536
MAX_IMAGE_BYTES = 25 * 1024**3
MAX_SECONDS = 1800
CHUNK_BYTES = 1024**2
PHASES = frozenset(('PREPARED', 'APPLYING_HOT', 'HOT_VERIFIED', 'RECOVERING',
                    'FAILED_GUARDED', 'FENCE_LOST_GUARDED', 'ORIGINAL_RESTORED', 'COMMITTED'))
_IDENTITY_FIELDS = {'database_sha256', 'database_size_bytes'}


class CutoverError(ValueError):
    """Stable operational code without provider payloads or credentials."""


class FencingError(CutoverError):
    """Writer exclusion was lost: automatic overwrite is no longer safe."""


def _canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('ascii')
    except (TypeError, ValueError, RecursionError) as exc:
        raise CutoverError('INVALID_STATE_JSON') from exc
    if not 0 < len(raw) <= MAX_STATE_BYTES:
        raise CutoverError('STATE_SIZE_BOUND')
    return raw


def _unique(items):
    value = {}
    for key, item in items:
        if key in value:
            raise CutoverError('DUPLICATE_STATE_MEMBER')
        value[key] = item
    return value


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise CutoverError('NON_REGULAR_OWNED_FILE')
    return info


def _read(path):
    if not os.path.lexists(path):
        return None
    info = _regular(path)
    if info.st_size > MAX_STATE_BYTES or info.st_mode & 0o077:
        raise CutoverError('UNSAFE_STATE_FILE')
    with path.open('rb') as stream:
        raw = stream.read(MAX_STATE_BYTES+1)
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CutoverError('INVALID_DURABLE_STATE') from exc
    if _canonical(value) != raw:
        raise CutoverError('NON_CANONICAL_DURABLE_STATE')
    return value


def _fsync_directory(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, value):
    raw = _canonical(value)
    if os.path.lexists(path):
        _regular(path)
    fd, name = tempfile.mkstemp(prefix='.owned-cutover-state-', dir=path.parent)
    work = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(work, path)
        _fsync_directory(path.parent)
    finally:
        work.unlink(missing_ok=True)


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise CutoverError('TRANSACTION_DEADLINE')


def _valid_identity(value):
    if (not isinstance(value, dict) or set(value) != _IDENTITY_FIELDS
            or not isinstance(value['database_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', value['database_sha256'])
            or type(value['database_size_bytes']) is not int
            or not 4096 <= value['database_size_bytes'] <= MAX_IMAGE_BYTES):
        raise CutoverError('INVALID_IMAGE_IDENTITY')


def _consume(stream, identity, deadline, *, write=None):
    digest = hashlib.sha256(); size = 0
    while True:
        _check_deadline(deadline)
        data = stream.read(CHUNK_BYTES)
        if not isinstance(data, bytes) or len(data) > CHUNK_BYTES:
            raise CutoverError('INVALID_TRANSFER_CHUNK')
        if not data:
            break
        size += len(data)
        if size > identity['database_size_bytes']:
            raise CutoverError('IMAGE_SIZE_MISMATCH')
        digest.update(data)
        if write is not None:
            write(data)
    if size != identity['database_size_bytes'] or digest.hexdigest() != identity['database_sha256']:
        raise CutoverError('IMAGE_IDENTITY_MISMATCH')


def _verify_database(path, identity, deadline, *, manifest=None):
    before = archive._identity(path)
    if before['size'] != identity['database_size_bytes']:
        raise CutoverError('IMAGE_SIZE_MISMATCH')
    with path.open('rb') as stream:
        _consume(stream, identity, deadline)
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    try:
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA cache_size=-2048')
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise CutoverError('SQLITE_QUICK_CHECK_FAILED')
        if manifest is not None:
            if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise CutoverError('HOT_FOREIGN_KEY_CHECK_FAILED')
            fields = history_state.validate_manifest_history(conn, manifest)
            if not fields:
                raise CutoverError('HOT_HISTORY_STATE_REQUIRED')
    finally:
        conn.close()
    _check_deadline(deadline)
    if archive._identity(path) != before:
        raise CutoverError('IMAGE_CHANGED_DURING_VERIFICATION')


def _plan_bytes(plan):
    if (not isinstance(plan, dict) or set(plan) != {
            'contract', 'mode', 'run_id', 'original', 'hot', 'hot_manifest', 'artifact_receipts'}
            or plan['contract'] != CONTRACT or plan['mode'] != 'DISPOSABLE_ONLY'
            or not isinstance(plan['run_id'], str)
            or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', plan['run_id'])):
        raise CutoverError('INVALID_DISPOSABLE_PLAN')
    for kind in ('original', 'hot'):
        _valid_identity(plan[kind])
    manifest = plan['hot_manifest']
    if (not isinstance(manifest, dict)
            or set(manifest) != _IDENTITY_FIELDS.union(history_state.PIN_FIELDS)
            or any(manifest.get(key) != value for key, value in plan['hot'].items())):
        raise CutoverError('INVALID_HOT_MANIFEST')
    receipts = plan['artifact_receipts']
    if not isinstance(receipts, dict) or set(receipts) != {'original', 'hot'}:
        raise CutoverError('MISSING_VERIFIED_ARTIFACT_RECEIPTS')
    for kind, receipt in receipts.items():
        expected = dict(plan[kind], contract='sqlite-history-disposable-image-v1',
                        kind=kind, stream_verified=True, production_authority=False)
        if kind == 'hot':
            expected['hot_manifest_sha256'] = hashlib.sha256(_canonical(manifest)).hexdigest()
        if (not isinstance(receipt, dict) or receipt != expected
                or receipt.get('stream_verified') is not True
                or receipt.get('production_authority') is not False):
            raise CutoverError('INVALID_VERIFIED_ARTIFACT_RECEIPT')
    return _canonical(plan)


def prepare_disposable_plan(original, hot, *, run_id, hot_manifest, transport):
    """Actually verify both local files and both complete disposable streams."""
    deadline = time.monotonic()+MAX_SECONDS
    identities = {}
    receipts = {}
    manifest = {key: hot_manifest[key] for key in _IDENTITY_FIELDS.union(history_state.PIN_FIELDS)}
    for kind, path in (('original', Path(original)), ('hot', Path(hot))):
        _check_deadline(deadline)
        before = archive._identity(path)
        if not 4096 <= before['size'] <= MAX_IMAGE_BYTES:
            raise CutoverError('INVALID_IMAGE_IDENTITY')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while True:
                _check_deadline(deadline)
                chunk = stream.read(CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        identity = dict(database_sha256=digest.hexdigest(), database_size_bytes=before['size'])
        _verify_database(path, identity, deadline, manifest=hot_manifest if kind == 'hot' else None)
        with transport.open_image(kind) as stream:
            _consume(stream, identity, deadline)
        if archive._identity(path) != before:
            raise CutoverError('SOURCE_CHANGED_DURING_PREPARATION')
        identities[kind] = identity
        receipts[kind] = dict(identity, contract='sqlite-history-disposable-image-v1',
                              kind=kind, stream_verified=True, production_authority=False)
        if kind == 'hot':
            receipts[kind]['hot_manifest_sha256'] = hashlib.sha256(_canonical(manifest)).hexdigest()
    plan = dict(contract=CONTRACT, mode='DISPOSABLE_ONLY', run_id=run_id,
                original=identities['original'], hot=identities['hot'],
                hot_manifest=manifest,
                artifact_receipts=receipts)
    _plan_bytes(plan)
    return plan


@contextmanager
def _owned_lock(root):
    import fcntl
    if root.absolute() != root.resolve() or root.resolve().is_relative_to(Path('/opt/seiltanzer')):
        raise CutoverError('PRODUCTION_OR_SYMLINK_ROOT_FORBIDDEN')
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise CutoverError('UNSAFE_DISPOSABLE_ROOT')
    marker = root/'.disposable-history-root'
    if _regular(marker).st_size != len(DISPOSABLE_MARKER) or marker.read_text() != DISPOSABLE_MARKER:
        raise CutoverError('DISPOSABLE_ROOT_REQUIRED')
    path = root/'cutover.owner.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
            raise CutoverError('UNSAFE_OWNER_LOCK')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CutoverError('TRANSACTION_ALREADY_OWNED') from None
        yield
    finally:
        os.close(fd)


def execute_cutover(root, plan, *, transport, control):
    """Run or resume a finite disposable transaction; never authorized production."""
    root = Path(root)
    raw_plan = _plan_bytes(plan)
    with _owned_lock(root):
        return _execute_validated_transfer(root, raw_plan, transport=transport, control=control)


def _execute_validated_transfer(root, raw_plan, *, transport, control, precommit=None):
    """Private core: caller owns its lock and already validated canonical plan.

    The public disposable entry point above keeps its production-root refusal.
    Production ownership needs its own strict root/plan/fence validator.
    """
    root = Path(root)
    plan = json.loads(raw_plan)  # Freeze the validated epoch against caller/adapter mutation.
    pin = hashlib.sha256(raw_plan).hexdigest()
    epoch = dict(contract=CONTRACT, run_id=plan['run_id'], plan_sha256=pin)
    guard = root/'cutover.active'
    journal = root/'cutover.journal.json'
    database = root/'trades.db'
    started = time.monotonic()
    deadline = started+MAX_SECONDS
    overall_deadline = started+2*MAX_SECONDS

    def check_guard():
        _check_deadline(deadline)
        try:
            owned = _read(guard) == epoch
        except (OSError, ValueError):
            owned = False
        if not owned:
            raise FencingError('ACTIVE_GUARD_FENCE_OWNERSHIP_LOST')

    def fence():
        check_guard()
        try:
            if control.assert_fenced() is not True or control.assert_writers_closed() is not True:
                raise FencingError('WRITERS_OR_SERVICE_FENCE_NOT_VERIFIED')
            archive._identity(database)  # Reject all SQLite sidecars and links.
        except Exception:
            raise FencingError('WRITERS_OR_SERVICE_FENCE_NOT_VERIFIED') from None

    def state(phase, *, service_started=False):
        value = dict(epoch, phase=phase, service_started=service_started)
        _write(journal, value)
        return value

    def transfer(kind):
        fence()
        with transport.open_image(kind) as source:
            # Opening and reading transport can block while writer exclusion
            # changes. Guard ownership alone does not prove closed writers.
            fence()
            fd = os.open(database, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            with os.fdopen(fd, 'wb', buffering=0) as target:
                def write(data):
                    fence()
                    target.write(data)
                _consume(source, plan[kind], deadline, write=write)
                target.flush()
                os.fsync(target.fileno())
        _fsync_directory(root)
        _verify_database(database, plan[kind], deadline,
                         manifest=plan['hot_manifest'] if kind == 'hot' else None)

    def recover():
        nonlocal deadline
        # Commit may have been renamed durably even if a later fsync failed.
        # Never start an original restore once the journal records commit.
        if (_read(journal) or {}).get('phase') == 'COMMITTED':
            raise CutoverError('COMMIT_RECORDED_NO_ROLLBACK')
        # A timed-out hot stream must not consume the original's recovery
        # budget. Both phases together still have a finite overall bound.
        deadline = min(overall_deadline, time.monotonic()+MAX_SECONDS)
        state('RECOVERING')
        try:
            transfer('original')
        except FencingError:
            state('FENCE_LOST_GUARDED')
            raise
        except Exception:
            state('FAILED_GUARDED')
            raise CutoverError('RECOVERY_FAILED_GUARDED') from None
        return state('ORIGINAL_RESTORED')

    def finish(current):
        if os.path.lexists(guard):
            fence()
            kind = 'hot' if current['phase'] == 'COMMITTED' else 'original'
            _verify_database(database, plan[kind], deadline,
                             manifest=plan['hot_manifest'] if kind == 'hot' else None)
            fence()
            guard.unlink()
            _fsync_directory(root)
        if not current['service_started']:
            # Outside every recovery handler: launch failure never rolls back
            # data that the service may already have opened and changed.
            try:
                if control.start_verified_database() is not True:
                    raise CutoverError('SERVICE_START_NOT_VERIFIED')
            except Exception:
                raise CutoverError('POST_COMMIT_OR_RESTORE_START_FAILED') from None
            current = state(current['phase'], service_started=True)
        return dict(contract=CONTRACT, run_id=plan['run_id'], phase=current['phase'],
                    cutover_committed=current['phase'] == 'COMMITTED',
                    service_started=current['service_started'], production_authority=False,
                    data_retirement_authorized=False, disposable_proof=True)

    current = _read(journal)
    if current is not None:
        if (set(current) != set(epoch).union(('phase', 'service_started'))
                or any(current.get(key) != value for key, value in epoch.items())
                or current.get('phase') not in PHASES
                or type(current.get('service_started')) is not bool
                or (current['service_started'] and current['phase'] not in ('COMMITTED', 'ORIGINAL_RESTORED'))):
            raise CutoverError('JOURNAL_PLAN_OR_PHASE_MISMATCH')
        if current['phase'] in ('COMMITTED', 'ORIGINAL_RESTORED'):
            return finish(current)
        if current['phase'] == 'FENCE_LOST_GUARDED':
            raise FencingError('FENCE_LOST_REQUIRES_DATA_RECONCILIATION')
        try:
            check_guard()
        except FencingError:
            state('FENCE_LOST_GUARDED')
            raise
        return finish(recover())
    existing = _read(guard)
    if existing is not None and existing != epoch:
        raise CutoverError('ACTIVE_GUARD_OWNERSHIP_LOST')
    if existing is None:
        _write(guard, epoch)
    fence()
    _verify_database(database, plan['original'], deadline)
    state('PREPARED')
    try:
        fence()
        state('APPLYING_HOT')
        transfer('hot')
        state('HOT_VERIFIED')
        fence()
        if precommit is not None and precommit() is not True:
            raise CutoverError('HOT_RESOURCE_ADMISSION_REFUSED')
        fence()
        current = state('COMMITTED')
    except FencingError:
        state('FENCE_LOST_GUARDED')
        raise
    except Exception:
        current = recover()
    return finish(current)
