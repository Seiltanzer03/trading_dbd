"""A disposable single-file transfer never launches partial or rolled-back data."""
import copy
import io
import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import sqlite_history_transaction as transaction
from scripts.sqlite_history_cutover import build_hot_cutover_snapshot
from test_sqlite_history_cutover import _build_cutover_fixture


class ProcessDeath(BaseException):
    pass


class MemoryTransport:
    def __init__(self, original, hot):
        self.images = dict(original=original.read_bytes(), hot=hot.read_bytes())
        self.calls = []
        self.failures = {}

    @contextmanager
    def open_image(self, kind):
        self.calls.append(kind)
        failure = self.failures.get(kind)
        if isinstance(failure, BaseException):
            raise failure
        yield io.BytesIO(self.images[kind] if failure is None else failure)


class FencedControl:
    def __init__(self, root):
        self.root = root
        self.starts = 0
        self.closed = True
        self.fenced = True
        self.fail_start = False

    def assert_fenced(self):
        assert self.fenced
        assert (self.root/'cutover.active').is_file()
        return True

    def assert_writers_closed(self):
        assert self.closed
        return True

    def start_verified_database(self):
        assert not (self.root/'cutover.active').exists()
        if self.fail_start:
            raise RuntimeError('injected start failure')
        with sqlite3.connect(self.root/'trades.db') as conn:
            assert conn.execute('PRAGMA quick_check').fetchone() == ('ok',)
        self.starts += 1
        return True


@pytest.fixture
def setup_transaction(tmp_path):
    original, source_proof, raw, pin, _ = _build_cutover_fixture(tmp_path)
    hot = tmp_path/'hot.sqlite3'
    result = build_hot_cutover_snapshot(original, hot, source_receipt=source_proof,
                                        catalog_bytes=raw, expected_catalog_sha256=pin)
    root = tmp_path/'disposable'
    root.mkdir()
    (root/'.disposable-history-root').write_text(transaction.DISPOSABLE_MARKER)
    shutil.copyfile(original, root/'trades.db')
    transport = MemoryTransport(original, hot)
    plan = transaction.prepare_disposable_plan(
        original, hot, run_id='drill-001', hot_manifest=result['hot_restore_proof']['storage_manifest'],
        transport=transport)
    transport.calls.clear()
    control = FencedControl(root)
    return root, plan, transport, control, original.read_bytes(), hot.read_bytes()


def test_success_launches_only_verified_hot_file(setup_transaction):
    root, plan, transport, control, _, hot = setup_transaction
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'COMMITTED'
    assert result['production_authority'] is False
    assert control.starts == 1
    assert (root/'trades.db').read_bytes() == hot
    assert not (root/'cutover.active').exists()
    assert transport.calls == ['hot']


def test_short_hot_stream_restores_exact_original_before_launch(setup_transaction):
    root, plan, transport, control, original, _ = setup_transaction
    transport.failures['hot'] = b'partial-sqlite'
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert result['cutover_committed'] is False
    assert (root/'trades.db').read_bytes() == original
    assert not (root/'cutover.active').exists()
    assert control.starts == 1
    assert transport.calls == ['hot', 'original']


def test_failed_original_recovery_keeps_guard_and_can_resume_same_plan(setup_transaction):
    root, plan, transport, control, original, _ = setup_transaction
    transport.failures.update(hot=b'partial', original=b'unrecoverable')
    with pytest.raises(transaction.CutoverError, match='RECOVERY_FAILED_GUARDED'):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert (root/'cutover.active').exists()
    assert json.loads((root/'cutover.journal.json').read_text())['phase'] == 'FAILED_GUARDED'
    assert control.starts == 0
    del transport.failures['original']
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original
    assert control.starts == 1


def test_process_death_before_commit_resumes_original_not_hot(setup_transaction):
    root, plan, transport, control, original, _ = setup_transaction
    transport.failures['hot'] = ProcessDeath('worker died')
    with pytest.raises(ProcessDeath):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert (root/'cutover.active').exists()
    assert control.starts == 0
    transport.calls.clear()
    transport.failures.clear()
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert transport.calls == ['original']
    assert (root/'trades.db').read_bytes() == original


def test_committed_restart_does_not_restore_original_over_new_writes(setup_transaction):
    root, plan, transport, control, _, _ = setup_transaction
    transaction.execute_cutover(root, plan, transport=transport, control=control)
    with sqlite3.connect(root/'trades.db') as conn:
        conn.execute("INSERT INTO trades(symbol) VALUES('NEW_REAL_WRITE')")
    after = (root/'trades.db').read_bytes()
    transport.calls.clear()
    result = transaction.execute_cutover(root, copy.deepcopy(plan), transport=transport, control=control)
    assert result['phase'] == 'COMMITTED'
    assert transport.calls == []
    assert (root/'trades.db').read_bytes() == after
    assert control.starts == 1


@pytest.mark.parametrize('phase', ['PREPARED', 'APPLYING_HOT', 'HOT_VERIFIED', 'COMMITTED'])
def test_restart_at_durable_phase_obeys_commit_point(setup_transaction, monkeypatch, phase):
    root, plan, transport, control, original, hot = setup_transaction
    actual_write = transaction._write
    def die_after_phase(path, value):
        actual_write(path, value)
        if path.name == 'cutover.journal.json' and value.get('phase') == phase:
            raise ProcessDeath(phase)
    monkeypatch.setattr(transaction, '_write', die_after_phase)
    with pytest.raises(ProcessDeath):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert control.starts == 0
    assert (root/'cutover.active').exists()
    monkeypatch.setattr(transaction, '_write', actual_write)
    transport.calls.clear()
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    committed = phase == 'COMMITTED'
    assert result['phase'] == ('COMMITTED' if committed else 'ORIGINAL_RESTORED')
    assert (root/'trades.db').read_bytes() == (hot if committed else original)
    assert transport.calls == ([] if committed else ['original'])


@pytest.mark.parametrize('payload', ['wrong_hash', 'excess_bytes'])
def test_invalid_hot_image_restores_original(setup_transaction, payload):
    root, plan, transport, control, original, hot = setup_transaction
    transport.failures['hot'] = (b'X'+hot[1:]) if payload == 'wrong_hash' else hot+b'X'
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original


def test_actual_process_death_after_truncation_resumes_from_durable_journal(setup_transaction, tmp_path):
    root, plan, transport, control, original, _ = setup_transaction
    plan_path = tmp_path/'plan.json'
    plan_path.write_text(json.dumps(plan))
    program = '''
import contextlib,json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts.sqlite_history_transaction import execute_cutover
class Partial:
    calls=0
    def read(self,n):
        self.calls+=1
        if self.calls==1: return b'partial'
        os._exit(91)
class Transport:
    @contextlib.contextmanager
    def open_image(self,kind):
        assert kind=='hot'
        yield Partial()
class Control:
    def assert_fenced(self): return True
    def assert_writers_closed(self): return True
    def start_verified_database(self): os._exit(99)
execute_cutover(Path(sys.argv[2]),json.loads(Path(sys.argv[3]).read_text()),transport=Transport(),control=Control())
'''
    result = subprocess.run([sys.executable, '-c', program,
                             str(Path(__file__).resolve().parents[1]), str(root), str(plan_path)],
                            capture_output=True, timeout=5)
    assert result.returncode == 91, result.stderr.decode()
    assert (root/'trades.db').read_bytes() != original
    assert json.loads((root/'cutover.journal.json').read_text())['phase'] == 'APPLYING_HOT'
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original


def test_launch_failure_never_rolls_back_committed_writes(setup_transaction):
    root, plan, transport, control, _, _ = setup_transaction
    control.fail_start = True
    with pytest.raises(transaction.CutoverError, match='START_FAILED'):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    with sqlite3.connect(root/'trades.db') as conn:
        conn.execute("INSERT INTO trades(symbol) VALUES('POST_COMMIT')")
    after = (root/'trades.db').read_bytes()
    transport.calls.clear()
    control.fail_start = False
    transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert transport.calls == []
    assert (root/'trades.db').read_bytes() == after


def test_lost_writer_fence_blocks_all_automatic_rollback_even_after_restart(setup_transaction, monkeypatch):
    root, plan, transport, control, _, hot = setup_transaction
    actual_write = transaction._write
    def open_unexpected_writer(path, value):
        actual_write(path, value)
        if value.get('phase') == 'HOT_VERIFIED':
            control.closed = False
    monkeypatch.setattr(transaction, '_write', open_unexpected_writer)
    with pytest.raises(transaction.CutoverError, match='FENCE'):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert (root/'cutover.active').exists()
    assert (root/'trades.db').read_bytes() == hot
    assert control.starts == 0
    monkeypatch.setattr(transaction, '_write', actual_write)
    control.closed = True
    transport.calls.clear()
    with pytest.raises(transaction.CutoverError, match='FENCE'):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert transport.calls == []
    assert (root/'trades.db').read_bytes() == hot


@pytest.mark.parametrize('writer_returns_at', ['stream_open', 'stream_read'])
def test_writer_return_during_transport_preserves_new_trade(setup_transaction, monkeypatch, writer_returns_at):
    root, plan, transport, control, original, _ = setup_transaction
    actual_open = transport.open_image
    changed = []

    def return_writer():
        control.closed = False
        # The read case simulates a writer replacing the truncated disposable
        # image. No transfer may subsequently overwrite its committed record.
        if writer_returns_at == 'stream_read':
            (root/'trades.db').write_bytes(original)
        with sqlite3.connect(root/'trades.db') as conn:
            conn.execute("INSERT INTO trades(symbol) VALUES('RETURNED_WRITER')")
        changed.append((root/'trades.db').read_bytes())

    @contextmanager
    def late_writer(kind):
        assert kind == 'hot', 'fence loss must never trigger original restoration'
        with actual_open(kind) as source:
            if writer_returns_at == 'stream_open':
                return_writer()
                yield source
            else:
                class LateRead:
                    def read(self, size):
                        if not changed:
                            return_writer()
                        return source.read(size)
                yield LateRead()

    monkeypatch.setattr(transport, 'open_image', late_writer)
    with pytest.raises(transaction.FencingError):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert (root/'trades.db').read_bytes() == changed[0]
    with sqlite3.connect(root/'trades.db') as conn:
        assert conn.execute("SELECT count(*) FROM trades WHERE symbol='RETURNED_WRITER'").fetchone() == (1,)
    assert json.loads((root/'cutover.journal.json').read_text())['phase'] == 'FENCE_LOST_GUARDED'
    assert control.starts == 0


@pytest.mark.parametrize('lost_guard', ['missing', 'malformed'])
def test_restart_guard_loss_cannot_be_repaired_into_automatic_rollback(setup_transaction, monkeypatch, lost_guard):
    root, plan, transport, control, _, _ = setup_transaction
    transport.failures['hot'] = ProcessDeath()
    with pytest.raises(ProcessDeath):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    guard = root/'cutover.active'
    owned_guard = guard.read_bytes()
    if lost_guard == 'missing':
        guard.unlink()
    else:
        guard.write_bytes(b'not-the-owned-guard')
    with sqlite3.connect(root/'trades.db') as conn:
        conn.execute("INSERT INTO trades(symbol) VALUES('AFTER_GUARD_LOSS')")
    changed = (root/'trades.db').read_bytes()
    transport.failures.clear()
    transport.calls.clear()
    with pytest.raises(transaction.FencingError):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert json.loads((root/'cutover.journal.json').read_text())['phase'] == 'FENCE_LOST_GUARDED'
    guard.write_bytes(owned_guard)
    guard.chmod(0o600)
    with pytest.raises(transaction.FencingError, match='REQUIRES_DATA_RECONCILIATION'):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert (root/'trades.db').read_bytes() == changed
    assert transport.calls == []
    assert control.starts == 0


@pytest.mark.parametrize('corruption', ['manifest_pin', 'numeric_verified_flag', 'numeric_authority_flag'])
def test_corrupted_preparation_receipt_fails_before_guard_or_transfer(setup_transaction, corruption):
    root, plan, transport, control, original, _ = setup_transaction
    changed = copy.deepcopy(plan)
    if corruption == 'manifest_pin':
        changed['hot_manifest']['cold_history_catalog_sha256'] = '0'*64
    elif corruption == 'numeric_verified_flag':
        changed['artifact_receipts']['hot']['stream_verified'] = 1
    else:
        changed['artifact_receipts']['hot']['production_authority'] = 0
    with pytest.raises(transaction.CutoverError, match='RECEIPT'):
        transaction.execute_cutover(root, changed, transport=transport, control=control)
    assert not (root/'cutover.active').exists()
    assert not (root/'cutover.journal.json').exists()
    assert (root/'trades.db').read_bytes() == original
    assert transport.calls == []


def test_wrong_owner_cannot_resume_a_guarded_epoch(setup_transaction):
    root, plan, transport, control, _, _ = setup_transaction
    transport.failures['hot'] = ProcessDeath()
    with pytest.raises(ProcessDeath):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    before = (root/'trades.db').read_bytes()
    changed = copy.deepcopy(plan)
    changed['run_id'] = 'different-owner'
    transport.calls.clear()
    with pytest.raises(transaction.CutoverError, match='MISMATCH'):
        transaction.execute_cutover(root, changed, transport=transport, control=control)
    assert (root/'trades.db').read_bytes() == before
    assert transport.calls == []
    assert control.starts == 0


@pytest.mark.parametrize('hazard', ['sidecar', 'database_symlink', 'guard_symlink', 'unsupported_phase'])
def test_unsafe_files_or_journal_never_reach_transfer(setup_transaction, tmp_path, hazard):
    root, plan, transport, control, original, _ = setup_transaction
    if hazard == 'sidecar':
        (root/'trades.db-wal').write_bytes(b'not-our-WAL')
    elif hazard == 'database_symlink':
        real = tmp_path/'authoritative-other.sqlite3'
        (root/'trades.db').rename(real)
        (root/'trades.db').symlink_to(real)
    elif hazard == 'guard_symlink':
        real = tmp_path/'unrelated.json'
        real.write_bytes(b'unchanged')
        (root/'cutover.active').symlink_to(real)
    else:
        transaction._write(root/'cutover.journal.json', dict(phase='UNKNOWN'))
    with pytest.raises((transaction.CutoverError, ValueError)):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert transport.calls == []
    assert (root/'trades.db').read_bytes() == original
    assert control.starts == 0
    if hazard in ('database_symlink', 'guard_symlink'):
        assert real.read_bytes() == (original if hazard == 'database_symlink' else b'unchanged')


def test_private_state_and_lock_refuse_concurrent_owner(setup_transaction):
    import fcntl
    root, plan, transport, control, _, _ = setup_transaction
    with (root/'cutover.owner.lock').open('w') as owner:
        (root/'cutover.owner.lock').chmod(0o600)
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(transaction.CutoverError, match='ALREADY_OWNED'):
            transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert transport.calls == []
    transaction.execute_cutover(root, plan, transport=transport, control=control)
    for name in ('cutover.owner.lock', 'cutover.journal.json'):
        assert (root/name).stat().st_mode & 0o777 == 0o600


def test_disposable_controller_refuses_production_root_before_filesystem_mutation(setup_transaction):
    _, plan, transport, control, _, _ = setup_transaction
    with pytest.raises(transaction.CutoverError, match='PRODUCTION'):
        transaction.execute_cutover('/opt/seiltanzer/data', plan, transport=transport, control=control)
    assert transport.calls == []


def test_transport_cannot_mutate_the_verified_plan_during_transfer(setup_transaction, monkeypatch):
    root, plan, transport, control, original, _ = setup_transaction
    actual_open = transport.open_image
    @contextmanager
    def mutate_caller_plan(kind):
        if kind == 'hot':
            plan['original'] = copy.deepcopy(plan['hot'])
            transport.failures['hot'] = b'partial'
        with actual_open(kind) as stream:
            yield stream
    monkeypatch.setattr(transport, 'open_image', mutate_caller_plan)
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original


@pytest.mark.parametrize('phase', ['RECOVERING', 'ORIGINAL_RESTORED'])
def test_interrupted_recovery_is_resumable_without_a_hot_retry(setup_transaction, monkeypatch, phase):
    root, plan, transport, control, original, _ = setup_transaction
    transport.failures['hot'] = b'partial'
    actual_write = transaction._write
    def die_after_phase(path, value):
        actual_write(path, value)
        if path.name == 'cutover.journal.json' and value.get('phase') == phase:
            raise ProcessDeath(phase)
    monkeypatch.setattr(transaction, '_write', die_after_phase)
    with pytest.raises(ProcessDeath):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert (root/'cutover.active').exists()
    assert control.starts == 0
    monkeypatch.setattr(transaction, '_write', actual_write)
    transport.calls.clear()
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert transport.calls == ([] if phase == 'ORIGINAL_RESTORED' else ['original'])
    assert (root/'trades.db').read_bytes() == original


def test_expired_hot_budget_still_has_a_finite_original_recovery_budget(setup_transaction, monkeypatch):
    root, plan, transport, control, original, _ = setup_transaction
    clock = [0.0]
    monkeypatch.setattr(transaction, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    actual_open = transport.open_image
    @contextmanager
    def expire_hot(kind):
        if kind == 'hot':
            clock[0] = transaction.MAX_SECONDS+1
        with actual_open(kind) as stream:
            yield stream
    monkeypatch.setattr(transport, 'open_image', expire_hot)
    result = transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original
    assert transport.calls == ['hot', 'original']


def test_commit_publication_error_never_starts_original_restore(setup_transaction, monkeypatch):
    root, plan, transport, control, _, hot = setup_transaction
    actual_write = transaction._write
    def fail_after_commit(path, value):
        actual_write(path, value)
        if value.get('phase') == 'COMMITTED':
            raise OSError('directory fsync failed after commit publication')
    monkeypatch.setattr(transaction, '_write', fail_after_commit)
    with pytest.raises(transaction.CutoverError, match='NO_ROLLBACK'):
        transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert transport.calls == ['hot']
    assert (root/'trades.db').read_bytes() == hot
    assert (root/'cutover.active').exists()
    assert control.starts == 0
    monkeypatch.setattr(transaction, '_write', actual_write)
    transaction.execute_cutover(root, plan, transport=transport, control=control)
    assert control.starts == 1


def test_preparation_deadline_precedes_full_image_hash_or_stream_access(setup_transaction, monkeypatch):
    root, plan, transport, _, _, _ = setup_transaction
    monkeypatch.setattr(transaction, 'MAX_SECONDS', 0)
    def unbounded_hash(_):
        pytest.fail('expired preparation entered unbounded whole-file hash')
    monkeypatch.setattr(transaction.archive, '_digest', unbounded_hash)
    with pytest.raises(transaction.CutoverError, match='DEADLINE'):
        transaction.prepare_disposable_plan(
            root/'trades.db', root.parent/'hot.sqlite3', run_id='expired',
            hot_manifest=plan['hot_manifest'], transport=transport)
    assert transport.calls == []
