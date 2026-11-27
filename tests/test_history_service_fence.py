"""Startup refuses guarded/partial history before opening any application writer."""
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import history_service_fence as fence
from scripts.sqlite_history_cutover import build_hot_cutover_snapshot
from test_sqlite_history_cutover import _build_cutover_fixture
from test_history_runtime_state import runtime_state
from test_history_rejected_source_index import rejected_source


def _paths(tmp_path):
    root = tmp_path/'data'
    root.mkdir()
    app = tmp_path/'app'
    (app/'seiltanzer').mkdir(parents=True)
    db = root/'trades.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY)')
    return db, app


def _journal(database, phase):
    path = database.parent/'cutover.journal.json'
    path.write_text(json.dumps(dict(contract='sqlite-history-transaction-v1', run_id='proof-1',
                                    plan_sha256='1'*64, phase=phase, service_started=False),
                               sort_keys=True, separators=(',', ':')))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize('guard_kind', ['regular', 'dangling_symlink'])
def test_active_guard_refuses_before_sqlite_is_opened(tmp_path, monkeypatch, guard_kind):
    db, app = _paths(tmp_path)
    guard = db.parent/'cutover.active'
    if guard_kind == 'regular':
        guard.write_text('active')
    else:
        guard.symlink_to(tmp_path/'absent')
    def no_sql(*_, **__):
        pytest.fail('guarded startup opened SQLite')
    monkeypatch.setattr(fence.sqlite3, 'connect', no_sql)
    result = fence.check_start(db, app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'ACTIVE_CUTOVER_GUARD'


@pytest.mark.parametrize('phase', ['PREPARED', 'APPLYING_HOT', 'HOT_VERIFIED',
                                  'RECOVERING', 'FAILED_GUARDED', 'FENCE_LOST_GUARDED'])
def test_pending_journal_blocks_even_if_active_guard_disappeared(tmp_path, phase):
    db, app = _paths(tmp_path)
    _journal(db, phase)
    result = fence.check_start(db, app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'UNFINISHED_CUTOVER_JOURNAL'


def test_unmarked_database_allows_existing_application_and_exact_original_restore(tmp_path):
    db, app = _paths(tmp_path)
    assert fence.check_start(db, app)['start_allowed'] is True
    _journal(db, 'ORIGINAL_RESTORED')
    assert fence.check_start(db, app)['start_allowed'] is True
    _journal(db, 'COMMITTED')
    result = fence.check_start(db, app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'COMMITTED_HISTORY_MARKER_MISSING'


@pytest.fixture
def marked_database(tmp_path):
    db, app = _paths(tmp_path)
    source, proof, raw, pin, _ = _build_cutover_fixture(tmp_path)
    db.unlink()
    build_hot_cutover_snapshot(source, db, source_receipt=proof, catalog_bytes=raw,
                               expected_catalog_sha256=pin)
    shutil.copyfile(Path(__file__).resolve().parents[1]/'seiltanzer/history_storage_state.py',
                    app/'seiltanzer/history_storage_state.py')
    return db, app


def test_marked_database_refuses_code_without_proven_runtime_reader_capability(marked_database):
    db, app = marked_database
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = fence.check_start(db, app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'HOT_HISTORY_RUNTIME_CAPABILITY_REQUIRED'
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_legacy_physical_marker_is_not_runtime_acceptance(marked_database):
    db, app = marked_database
    capability = app/'seiltanzer/history_runtime_views.py'
    capability.write_text("HISTORY_RUNTIME_CONTRACT='sqlite-history-runtime-v1'\nraise RuntimeError('must not import app')\n")
    _journal(db, 'COMMITTED')
    result = fence.check_start(db, app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'HOT_HISTORY_RUNTIME_PROJECTION_REQUIRED'


def test_actual_v2_projection_requires_matching_literal_reader_contracts(tmp_path, runtime_state):
    from seiltanzer import history_storage_state as storage
    db, app = _paths(tmp_path)
    conn, data = runtime_state
    storage.write_history_state(conn, data)
    conn.commit()
    with sqlite3.connect(db) as target:
        conn.backup(target)
    for name in ('history_storage_state.py','history_runtime_projection.py', 'passive_learning.py',
                 'g1_dataset_runtime.py','measurement_q_runtime.py'):
        shutil.copyfile(Path(__file__).resolve().parents[1]/'seiltanzer'/name, app/'seiltanzer'/name)
    (app/'seiltanzer/history_runtime_views.py').write_text(
        "HISTORY_RUNTIME_CONTRACT='sqlite-history-runtime-v1'\nraise RuntimeError('must not import app')\n")
    _journal(db, 'COMMITTED')
    assert fence.check_start(db, app)['start_allowed'] is True
    (app/'seiltanzer/passive_learning.py').write_text("PASSIVE_SCHEMA_VERSION='changed-source-contract'\n")
    result = fence.check_start(db, app)
    assert result['start_allowed'] is False
    assert result['reason'] == 'HOT_HISTORY_READER_CONTRACT_MISMATCH'
    shutil.copyfile(Path(__file__).resolve().parents[1]/'seiltanzer/passive_learning.py', app/'seiltanzer/passive_learning.py')
    with sqlite3.connect(db) as target:
        target.execute('UPDATE passive_market_observations SET rowid=3 WHERE rowid=2')
    assert fence.check_start(db, app)['reason'] == 'HOT_HISTORY_STATE_INVALID'


def test_current_checkout_accepts_verified_v2_without_site_packages_or_source_writes(tmp_path, runtime_state):
    import subprocess
    from seiltanzer import history_storage_state as storage
    conn, data = runtime_state
    storage.write_history_state(conn, data)
    conn.commit()
    database = tmp_path/'current-v2.sqlite3'
    with sqlite3.connect(database) as target:
        conn.backup(target)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable,'-S',str(root/'scripts/history_service_fence.py'),
        '--database',str(database),'--application-root',str(root)],capture_output=True,timeout=7)
    assert result.returncode == 0, result.stdout.decode()+result.stderr.decode()
    assert json.loads(result.stdout)['reason'] == 'VERIFIED_HOT_RUNTIME_CAPABILITY'
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('malformed', ['duplicate', 'unknown_phase', 'public_mode', 'symlink'])
def test_ambiguous_journal_never_allows_service_start(tmp_path, malformed):
    db, app = _paths(tmp_path)
    path = _journal(db, 'ORIGINAL_RESTORED')
    if malformed == 'duplicate':
        path.write_text('{"phase":"COMMITTED","phase":"ORIGINAL_RESTORED"}')
    elif malformed == 'unknown_phase':
        _journal(db, 'UNKNOWN')
    elif malformed == 'public_mode':
        path.chmod(0o644)
    else:
        real = tmp_path/'other.json'
        path.rename(real)
        path.symlink_to(real)
    assert fence.check_start(db, app)['start_allowed'] is False


def test_missing_authoritative_database_does_not_create_an_empty_one(tmp_path):
    db, app = _paths(tmp_path)
    db.unlink()
    assert fence.check_start(db, app)['start_allowed'] is False
    assert not db.exists()


@pytest.mark.parametrize('guard_appears', [False, True])
def test_service_retry_reopens_busy_database_and_rechecks_cutover(
        tmp_path, monkeypatch, capsys, guard_appears):
    db, app = _paths(tmp_path)
    writer = sqlite3.connect(db)
    writer.execute('BEGIN EXCLUSIVE')

    def release_lock_after_first_check(_seconds):
        writer.rollback()
        if guard_appears:
            (db.parent/'cutover.active').touch()

    monkeypatch.setattr(fence.time, 'sleep', release_lock_after_first_check)
    monkeypatch.setattr(sys, 'argv', ['history_service_fence.py', '--retry-transient',
        '--database', str(db), '--application-root', str(app)])
    try:
        assert fence.main() == int(guard_appears)
    finally:
        writer.close()
    first, final = map(json.loads, capsys.readouterr().out.splitlines())
    assert first['reason'] == 'AUTHORITATIVE_DATABASE_BUSY'
    assert first['retry_attempt'] == 1
    assert final['reason'] == ('ACTIVE_CUTOVER_GUARD' if guard_appears else 'UNMARKED_DATABASE')
    assert final['start_allowed'] is not guard_appears


@pytest.mark.parametrize('reason,attempts', [
    ('HOT_HISTORY_STATE_CHECK_TIMEOUT', 3),
    ('HOT_HISTORY_STATE_INVALID', 1),
    ('HOT_HISTORY_READER_CONTRACT_MISMATCH', 1),
])
def test_service_check_stays_blocked_after_bounded_or_permanent_failure(
        monkeypatch, capsys, reason, attempts):
    monkeypatch.setattr(fence, 'check_start',
                        lambda *_args, **_kwargs: {'start_allowed': False, 'reason': reason})
    monkeypatch.setattr(fence.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(sys, 'argv', ['history_service_fence.py', '--retry-transient'])
    assert fence.main() == 1
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(results) == attempts
    assert results[-1] == {'start_allowed': False, 'reason': reason}
