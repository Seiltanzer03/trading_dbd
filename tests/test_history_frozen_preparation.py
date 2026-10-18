"""The native owner's frozen-source phase uses actual unchanged SQLite bytes."""
import hashlib
import importlib
import sqlite3

import pytest

from scripts import sqlite_history_transaction as core
from test_sqlite_history_transaction import setup_transaction


def control_for_setup(setup):
    control = setup[3]
    control.stop_writers = lambda:True
    return control


def test_actual_frozen_source_is_pinned_before_any_hot_handoff(setup_transaction):
    owner = importlib.import_module('scripts.history_frozen_preparation')
    root,_,transport,_,original,_ = setup_transaction
    with core._owned_lock(root):
        state = owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,
                                    control=control_for_setup(setup_transaction))
    assert state['phase'] == 'FROZEN'
    assert state['source']['database_sha256'] == hashlib.sha256(original).hexdigest()
    assert state['source']['database_size_bytes'] == len(original)
    assert (root/'trades.db').read_bytes() == original
    assert (root/'cutover.active').is_file()
    assert not (root/'cutover.journal.json').exists()
    assert transport.calls == []
    assert setup_transaction[3].starts == 0


def test_expired_native_epoch_launches_only_unchanged_original(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_frozen_preparation')
    root,_,_,control,original,_ = setup_transaction
    clock = [10.0]
    monkeypatch.setattr(owner.time,'monotonic',lambda:clock[0])
    with core._owned_lock(root):
        first = owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,
                                    control=control_for_setup(setup_transaction))
        clock[0] += owner.PREPARATION_SECONDS+1
        result = owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,control=control)
    assert result['phase'] == 'ABORTED_ORIGINAL'
    assert result['started_monotonic'] == first['started_monotonic']
    assert (root/'trades.db').read_bytes() == original
    assert not (root/'cutover.active').exists()
    assert control.starts == 1


@pytest.mark.parametrize('hazard',['missing_guard','changed_source','changed_run'])
def test_frozen_epoch_cannot_be_stolen_or_overwrite_new_source(setup_transaction,hazard):
    owner = importlib.import_module('scripts.history_frozen_preparation')
    root,_,_,control,original,_ = setup_transaction
    with core._owned_lock(root):
        owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,
                            control=control_for_setup(setup_transaction))
        if hazard == 'missing_guard':(root/'cutover.active').unlink()
        elif hazard == 'changed_source':
            with sqlite3.connect(root/'trades.db') as conn:
                conn.execute('CREATE TABLE new_real_records(id INTEGER PRIMARY KEY)')
                conn.execute('INSERT INTO new_real_records VALUES(99)')
        before = (root/'trades.db').read_bytes()
        with pytest.raises(ValueError):
            owner.prepare_epoch(root,run_id='other' if hazard=='changed_run' else 'freeze-001',
                                application_sha='a'*40,control=control)
    assert (root/'trades.db').read_bytes() == before
    assert control.starts == 0
    if hazard != 'changed_run':
        assert core._read(root/'cutover.journal.json')['phase'] == 'FENCE_LOST_GUARDED'


def test_resume_keeps_original_deadline_and_rejects_changed_application(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_frozen_preparation')
    root = setup_transaction[0];control = control_for_setup(setup_transaction)
    clock = [10.0];monkeypatch.setattr(owner.time,'monotonic',lambda:clock[0])
    with core._owned_lock(root):
        first = owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,control=control)
        clock[0] += 1000
        second = owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,control=control)
        with pytest.raises(ValueError):
            owner.prepare_epoch(root,run_id='freeze-001',application_sha='b'*40,control=control)
    assert first == second


def test_each_frozen_prepare_or_safe_abort_performs_one_integrity_scan(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_frozen_preparation')
    root = setup_transaction[0];control = control_for_setup(setup_transaction)
    real_connect = sqlite3.connect
    queries = []
    class Connection:
        def __init__(self,*args,**kwargs):
            self.inner = real_connect(*args,**kwargs)
            self.owner_integrity = kwargs.get('uri') is True
        def __getattr__(self,name):return getattr(self.inner,name)
        def __enter__(self):self.inner.__enter__();return self
        def __exit__(self,*args):return self.inner.__exit__(*args)
        def execute(self,sql,*args):
            if sql == 'PRAGMA quick_check' and self.owner_integrity:queries.append(sql)
            return self.inner.execute(sql,*args)
    monkeypatch.setattr(owner.sqlite3,'connect',Connection)
    clock = [10.0];monkeypatch.setattr(owner.time,'monotonic',lambda:clock[0])
    with core._owned_lock(root):
        owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,control=control)
        assert len(queries) == 1
        queries.clear();clock[0] += owner.PREPARATION_SECONDS+1
        owner.prepare_epoch(root,run_id='freeze-001',application_sha='a'*40,control=control)
        assert len(queries) == 1
