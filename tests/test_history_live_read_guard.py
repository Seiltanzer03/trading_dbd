"""Real flock prevents a direct SQLite reader racing a destructive epoch."""
import fcntl
import importlib
import json
import os
import sqlite3

import pytest


@pytest.fixture
def guarded_source(tmp_path,monkeypatch):
    module = importlib.import_module('seiltanzer.history_live_read_guard')
    root = tmp_path/'production';root.mkdir()
    data = root/'data';data.mkdir()
    database = data/'trades.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE source(id INTEGER PRIMARY KEY)')
        conn.execute('INSERT INTO source VALUES(73)')
    lock = root/'.deployment-git-maintenance.lock';lock.touch()
    monkeypatch.setattr(module,'PRODUCTION_ROOT',root)
    monkeypatch.setattr(module,'PRODUCTION_DATABASE',database)
    return module,root,database,lock


def test_real_reader_holds_shared_owner_lock_until_sqlite_finishes(guarded_source):
    module,_,database,lock = guarded_source
    with module.guarded_live_read(database):
        with sqlite3.connect(database) as conn:
            assert conn.execute('SELECT id FROM source').fetchone() == (73,)
            with lock.open('rb') as exclusive:
                with pytest.raises(BlockingIOError):fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with lock.open('rb') as exclusive:fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_exclusive_cutover_refuses_reader_before_sqlite_open(guarded_source):
    module,_,database,lock = guarded_source
    with lock.open('rb') as exclusive:
        fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError):
            with module.guarded_live_read(database):
                pytest.fail('Reader reached SQLite during cutover')


@pytest.mark.parametrize('hazard',['guard','dangling_guard','pending','invalid','duplicate','public_journal'])
def test_guarded_or_ambiguous_epoch_refuses_direct_source(guarded_source,hazard):
    module,root,database,_ = guarded_source
    if hazard == 'guard':(root/'data/cutover.active').write_text('active')
    elif hazard == 'dangling_guard':(root/'data/cutover.active').symlink_to(root/'missing')
    else:
        journal = root/'data/cutover.journal.json'
        if hazard == 'invalid':raw='broken'
        elif hazard == 'duplicate':raw='{"phase":"COMMITTED","phase":"APPLYING_HOT"}'
        else:raw=json.dumps(dict(contract='sqlite-history-transaction-v1',run_id='run-001',
            plan_sha256='a'*64,phase='APPLYING_HOT',service_started=False),sort_keys=True,separators=(',',':'))
        journal.write_text(raw);journal.chmod(0o644 if hazard=='public_journal' else 0o600)
    with pytest.raises(ValueError):
        with module.guarded_live_read(database):pytest.fail('fenced source opened')


def test_failed_reader_releases_actual_shared_lock(guarded_source):
    module,_,database,lock = guarded_source
    with pytest.raises(RuntimeError):
        with module.guarded_live_read(database):raise RuntimeError('reader interrupted')
    with lock.open('rb') as exclusive:fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_private_worker_copy_does_not_claim_production_lock(guarded_source,tmp_path):
    module,_,_,lock = guarded_source
    worker = tmp_path/'worker.sqlite3'
    with lock.open('rb') as exclusive:
        fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with module.guarded_live_read(worker):
            with sqlite3.connect(worker) as conn:
                assert conn.execute('SELECT 1').fetchone() == (1,)


def test_inventory_reader_retains_shared_lock_until_close(guarded_source):
    from scripts.production_ede_inventory import _ReadOnlyRuntime
    _,_,database,lock = guarded_source
    runtime = _ReadOnlyRuntime(database)
    try:
        assert runtime._conn.execute('SELECT id FROM source').fetchone()[0] == 73
        with lock.open('rb') as exclusive:
            with pytest.raises(BlockingIOError):fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:runtime.close()
    with lock.open('rb') as exclusive:fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_inventory_constructor_failure_closes_connection_and_reader_lock(guarded_source,monkeypatch):
    from scripts.production_ede_inventory import _ReadOnlyRuntime
    _,_,database,lock = guarded_source
    connection = sqlite3.connect(database)
    connection.execute('BEGIN')
    monkeypatch.setattr(sqlite3,'connect',lambda *args,**kwargs:connection)
    runtime = object.__new__(_ReadOnlyRuntime)
    try:
        with pytest.raises(sqlite3.OperationalError):runtime.__init__(database)
        with lock.open('rb') as exclusive:fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
    finally:runtime.close()


def test_actual_embedded_native_export_refuses_active_epoch(guarded_source):
    from scripts.export_native_family_history import REMOTE_EXPORT
    _,root,database,_ = guarded_source
    (root/'data/cutover.active').write_text('owned epoch')
    before = database.read_bytes()
    program = REMOTE_EXPORT.replace("PRODUCTION_ROOT = Path('/opt')/'seiltanzer'",
                                    'PRODUCTION_ROOT = Path('+repr(str(root))+')')
    program = program.replace('/opt/seiltanzer/data/trades.db',str(database))
    with pytest.raises(ValueError,match='HISTORY_SOURCE_READ_FENCED'):exec(program,{})
    assert database.read_bytes() == before


@pytest.mark.parametrize('module_name',[
    'scripts.production_ede_v12_audit','scripts.production_ede_v13_audit',
    'scripts.run_universal_outcome_audit'])
def test_other_runtime_readers_hold_lock_until_close(guarded_source,module_name):
    _,_,database,lock = guarded_source
    module = importlib.import_module(module_name)
    runtime = module.ReadOnlyRuntime(database)
    try:
        assert runtime._conn.execute('SELECT id FROM source').fetchone()[0] == 73
        with lock.open('rb') as exclusive:
            with pytest.raises(BlockingIOError):fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:runtime.close()
    with lock.open('rb') as exclusive:fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)


@pytest.mark.parametrize('module_name',[
    'scripts.production_ede_v12_audit','scripts.production_ede_v13_audit'])
def test_snapshot_source_cannot_open_during_cutover(guarded_source,tmp_path,module_name):
    _,_,database,lock = guarded_source
    module = importlib.import_module(module_name)
    destination = tmp_path/'copy.sqlite3'
    with lock.open('rb') as exclusive:
        fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='HISTORY_SOURCE_READ_FENCED'):
            module.immutable_snapshot(database,destination)
    assert not destination.exists()


def test_conditional_audit_is_fenced_before_querying_live_source(guarded_source):
    from seiltanzer.historical_conditional_audit import audit_database
    _,_,database,lock = guarded_source
    with lock.open('rb') as exclusive:
        fcntl.flock(exclusive,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='HISTORY_SOURCE_READ_FENCED'):
            audit_database(str(database))


@pytest.mark.parametrize('kind',['math','reviews'])
def test_embedded_readers_refuse_before_sqlite_under_active_epoch(guarded_source,kind):
    _,root,database,_ = guarded_source
    (root/'data/cutover.active').write_text('owned epoch')
    if kind == 'math':
        from scripts.export_mathematical_edge_sources import REMOTE_EXPORT
        program = REMOTE_EXPORT
    else:
        from scripts.export_unified_edge_reviews import remote_program
        program = remote_program(1)
    program = program.replace("PRODUCTION_ROOT = Path('/opt')/'seiltanzer'",
                              'PRODUCTION_ROOT = Path('+repr(str(root))+')')
    program = program.replace('/opt/seiltanzer/data/trades.db',str(database))
    with pytest.raises(ValueError,match='HISTORY_SOURCE_READ_FENCED'):exec(program,{})


def test_standalone_replication_origin_refuses_active_epoch_before_process(guarded_source,tmp_path,monkeypatch):
    from scripts import sqlite_replication_origin as origin
    _,root,database,_ = guarded_source
    (root/'data/cutover.active').write_text('owned epoch')
    monkeypatch.setattr(origin,'PRODUCTION_DATABASE',database)
    monkeypatch.setattr(os,'execvp',lambda *args:pytest.fail('Unfenced origin reached exec'))
    with pytest.raises(ValueError,match='HISTORY_SOURCE_READ_FENCED'):
        origin.run(tmp_path/'sqlite3_rsync',tmp_path/'origin.json',
                   ['--origin',str(database)])
    assert not (tmp_path/'origin.json').exists()
