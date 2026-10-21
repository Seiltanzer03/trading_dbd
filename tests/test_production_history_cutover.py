"""Frozen capture proves actual bytes without applying the live-WAL bypass."""
import hashlib
import importlib
import io
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest


class LocalSSHChannel:
    """Injected SSH endpoint; gzip bytes still undergo real decompression."""
    def __init__(self,source,reads,exit_status=0):
        import gzip
        self.stream=io.BytesIO(gzip.compress(source.read_bytes(),compresslevel=1,mtime=0))
        self.reads=reads;self.exit_status=exit_status
    def settimeout(self,value):pass
    def exec_command(self,command):
        assert command=='/bin/gzip -1 -c -- /opt/seiltanzer/data/trades.db'
        self.reads.append(('/opt/seiltanzer/data/trades.db','gzip_ssh_stream'))
    def makefile(self,mode):assert mode=='rb';return self.stream
    def exit_status_ready(self):return self.exit_status is not None
    def recv_exit_status(self):return self.exit_status
    def close(self):self.stream.close()


class LocalSFTP:
    def __init__(self,source,exit_status=0):self.source=source;self.reads=[];self.exit_status=exit_status
    def get_channel(self):
        return SimpleNamespace(get_transport=lambda:SimpleNamespace(
            open_session=lambda **kwargs:LocalSSHChannel(self.source,self.reads,self.exit_status)))



@pytest.fixture
def frozen_source(tmp_path):
    source=tmp_path/'frozen.sqlite3'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE actual(id INTEGER PRIMARY KEY,value BLOB)')
        conn.execute('INSERT INTO actual VALUES(19,?)',(b'actual-frozen-history'*1000,))
    raw=source.read_bytes()
    identity=dict(database_sha256=hashlib.sha256(raw).hexdigest(),database_size_bytes=len(raw))
    return source,raw,identity


def test_frozen_capture_matches_owner_hash_and_real_sqlite(frozen_source,tmp_path):
    module=importlib.import_module('scripts.production_history_cutover')
    source,raw,identity=frozen_source
    output=tmp_path/'captured.sqlite3';sftp=LocalSFTP(source)
    proof=module.capture_frozen(sftp,identity,output,deadline=time.monotonic()+10)
    assert output.read_bytes()==raw
    assert proof['immutable_snapshot_verified'] is True
    assert proof['database_sha256']==identity['database_sha256']
    assert sftp.reads==[('/opt/seiltanzer/data/trades.db','gzip_ssh_stream')]
    assert output.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('change',['digest','size','expired'])
def test_failed_frozen_capture_removes_only_own_partial(frozen_source,tmp_path,change):
    module=importlib.import_module('scripts.production_history_cutover')
    source,raw,identity=frozen_source
    if change=='digest':identity['database_sha256']='0'*64
    elif change=='size':identity['database_size_bytes']-=1
    output=tmp_path/'captured.sqlite3'
    with pytest.raises(ValueError):
        module.capture_frozen(LocalSFTP(source),identity,output,
                              deadline=time.monotonic()+(-1 if change=='expired' else 10))
    assert not output.exists()
    assert source.read_bytes()==raw


@pytest.mark.parametrize('exit_status',[17,None])
def test_frozen_capture_requires_successful_bounded_remote_exit(frozen_source,tmp_path,exit_status):
    from scripts.production_history_cutover import capture_frozen
    source,raw,identity=frozen_source
    output=tmp_path/'rejected.sqlite3'
    with pytest.raises(ValueError,match='FROZEN_CAPTURE_COMMAND_FAILED|TRANSACTION_DEADLINE'):
        capture_frozen(LocalSFTP(source,exit_status),identity,output,
                       deadline=time.monotonic()+(.1 if exit_status is None else 10))
    assert not output.exists()
    assert source.read_bytes()==raw


def test_existing_capture_destination_is_preserved(frozen_source,tmp_path):
    module=importlib.import_module('scripts.production_history_cutover')
    source,_,identity=frozen_source
    output=tmp_path/'existing.sqlite3';output.write_bytes(b'keep-existing')
    with pytest.raises(FileExistsError):
        module.capture_frozen(LocalSFTP(source),identity,output,deadline=time.monotonic()+10)
    assert output.read_bytes()==b'keep-existing'


def test_manual_cutover_workflow_shares_production_queue_and_hides_credentials():
    import yaml
    path=Path(__file__).resolve().parents[1]/'.github/workflows/production-history-cutover.yml'
    assert path.exists(),'Missing integrated manual cutover workflow'
    workflow=yaml.safe_load(path.read_text())
    triggers=workflow.get('on',workflow.get(True))
    assert set(triggers)=={'workflow_dispatch'}
    assert workflow['concurrency']['group']=='production-seiltanzer'
    assert workflow['concurrency']['cancel-in-progress'] is False
    assert workflow['permissions']=={'contents':'read','statuses':'read'}
    steps=workflow['jobs']['cutover']['steps']
    execute=next(item for item in steps if 'scripts.production_history_cutover' in item.get('run',''))
    assert 'SSH_PASSWORD' in execute['env']
    assert 'AWS_ACCESS_KEY_ID' in execute['env']
    assert '--password' not in execute['run']
    assert 'PRIVATE' not in execute['run']


def test_union_capacity_is_checked_before_handoff_at_production_scale(tmp_path,monkeypatch):
    from scripts import production_history_cutover as driver
    from scripts import sqlite_history_partition as archive
    gib=1024**3;maximum=25*gib
    required=archive.RESERVE_BYTES+2*maximum+3*archive.MAX_BYTES
    monkeypatch.setattr(driver.shutil,'disk_usage',lambda _:SimpleNamespace(free=25*gib))
    with pytest.raises(ValueError,match='UNION_WORKER_CAPACITY'):
        driver.require_union_capacity(tmp_path,{'maximum_hydrated_bytes':maximum})
    monkeypatch.setattr(driver.shutil,'disk_usage',lambda _:SimpleNamespace(free=required))
    assert driver.require_union_capacity(tmp_path,{'maximum_hydrated_bytes':maximum})==required


def test_only_exact_owned_capture_is_unlinked_after_verified_publication(frozen_source,tmp_path):
    from scripts import production_history_cutover as driver
    source,raw,identity=frozen_source
    output=tmp_path/'capture.sqlite3'
    proof=driver.capture_frozen(LocalSFTP(source),identity,output,deadline=time.monotonic()+10)
    output.write_bytes(raw+b'changed')
    with pytest.raises(ValueError):driver.release_owned_capture(output,proof)
    assert output.exists()


def test_unloaded_same_epoch_unit_is_recreated_with_original_pins(monkeypatch):
    from scripts import production_history_cutover as driver
    calls=[]
    def execute(client,command,**kwargs):
        calls.append(command)
        return 'LoadState=not-found\nActiveState=inactive\nResult=success\n'
    monkeypatch.setattr(driver,'_exec',execute)
    driver.ensure_owner_running(object(),run_id='run-001',expected_sha='a'*40)
    assert len(calls)==2
    assert '/usr/bin/systemd-run' in calls[1]
    assert 'run-001' in calls[1] and 'a'*40 in calls[1]


def test_rejected_owner_is_reported_without_waiting_for_preparation(monkeypatch):
    from scripts import production_history_cutover as driver
    monkeypatch.setattr(driver,'_exec',lambda *args,**kwargs:
        'LoadState=loaded\nActiveState=failed\nResult=exit-code\n')
    with pytest.raises(ValueError,match='NATIVE_OWNER_NOT_RUNNING'):
        driver.assert_owner_running(object(),'run-001')


@pytest.mark.parametrize('capture_failure,diagnostic',[
    (None,False),('deadline',False),('provider',False),('receipt_write',False),
    ('sqlite_build',False),('sqlite_context',False),
    (None,True),('sqlite_build',True),('publish',True),('health',True),('preexisting',True),
])
def test_complete_driver_builds_publishes_hands_off_and_reconstitutes(tmp_path,monkeypatch,capture_failure,diagnostic,capsys):
    """Real SQLite, catalog partitions, gzip and owner; SSH/S3 endpoints injected."""
    import json
    import os
    import shutil
    import threading
    from scripts import production_history_cutover as driver
    from scripts import history_production_plan as pins
    from scripts import history_production_owner as owner
    from scripts import sqlite_history_transaction as core
    from scripts.history_image_transport import SignedImageTransport
    from scripts.history_service_fence import check_start
    from scripts.history_storage_loader import history_state
    from scripts.publish_history_image import publish_image
    from test_publish_history_image import MemoryClient
    from test_sqlite_history_cutover import _build_cutover_fixture
    from test_sqlite_history_transaction import FencedControl
    from test_ede_ledger_archive import ObjectStore
    source,_,catalog,pin,parts=_build_cutover_fixture(tmp_path,include_runtime_metadata=True,
                                                   include_post_catalog=True)
    with sqlite3.connect(source) as conn:
        source_count=conn.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone()[0]
    catalog_data=json.loads(catalog)
    catalog_key=history_state.catalog_key(catalog_data['source_database_sha256'],pin)
    monkeypatch.setattr(pins,'CATALOG_SHA256',pin)
    monkeypatch.setattr(pins,'CATALOG_PINS',dict(cold_history_catalog_sha256=pin,
        cold_history_catalog_bucket='trading-dbd-backups-2026',
        cold_history_catalog_object_key=catalog_key))
    cloud=MemoryClient();cloud.objects.update(parts.objects)
    cloud.objects[catalog_key]=(catalog,{})
    cloud.get_object=lambda **kw:ObjectStore.get_object(cloud,**kw)
    cloud.corrupt=False
    data=tmp_path/'host';data.mkdir()
    (data/'.disposable-history-root').write_text(core.DISPOSABLE_MARKER)
    shutil.copyfile(source,data/'trades.db')
    class SFTP:
        def path(self,path):
            assert path.startswith(driver.REMOTE_DATA)
            return data/path.removeprefix(driver.REMOTE_DATA)
        def stat(self,path):return self.path(path).stat()
        def lstat(self,path):
            info=self.path(path).lstat()
            # This injected remote endpoint represents the root-owned VPS,
            # regardless of the hosted runner user's local UID.
            return SimpleNamespace(st_mode=info.st_mode,st_size=info.st_size,st_uid=0)
        def open(self,path,mode):
            return self.path(path).open('xb' if mode=='wx' else mode)
        def chmod(self,path,mode):self.path(path).chmod(mode)
        def posix_rename(self,a,b):os.replace(self.path(a),self.path(b))
        def remove(self,path):self.path(path).unlink()
        def get_channel(self):return SimpleNamespace(settimeout=lambda _:None,get_transport=lambda:SimpleNamespace(
            open_session=lambda **kw:LocalSSHChannel(data/'trades.db',[])))
        def close(self):pass
    client=SimpleNamespace(open_sftp=lambda:SFTP(),close=lambda:None)
    errors=[];threads=[]
    control=FencedControl(data)
    control.stop_writers=lambda:True
    control.prepare_transfer=lambda:True
    app=Path(__file__).resolve().parents[1]
    control.verify_hot_runtime=lambda manifest:check_start(data/'trades.db',app,
        guard=data/'absent',journal=data/'absent')['reason']=='VERIFIED_HOT_RUNTIME_CAPABILITY'
    real_sleep=time.sleep
    def launch(*args,**kwargs):
        if 'systemctl show' in args[1]:
            return 'LoadState=loaded\nActiveState=active\nResult=success\n'
        def local_owner():
            try:
                with core._owned_lock(data):
                    result=owner._run_owned_epoch(data,run_id='run-001',application_sha='a'*40,
                        control=control,wait=lambda _:real_sleep(.01),
                        transport_factory=lambda private,**kw:SignedImageTransport(private,
                            opener=cloud.opener,**kw))
                    core._write(data/'cutover.result.json',result)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=local_owner,daemon=True);threads.append(thread);thread.start()
        return 'started'
    monkeypatch.setenv('SSH_PASSWORD','test-only')
    monkeypatch.setattr(driver,'_connect',lambda *args,**kwargs:client)
    monkeypatch.setattr(driver,'_verify_sha',lambda *args:None)
    def probe(*args):
        if diagnostic and control.starts:
            if capture_failure=='health':raise RuntimeError('PRIVATE-PROVIDER-SECRET')
            assert not (data/'cutover.active').exists()
    monkeypatch.setattr(driver,'_probe_api',probe)
    monkeypatch.setattr(driver,'_exec',launch)
    monkeypatch.setattr(driver,'yandex_image_client',lambda:cloud)
    publications=[]
    def publish(*args,**kwargs):
        if diagnostic:
            assert control.starts==1, 'Diagnostic upload delayed return of terminal'
            assert not (data/'cutover.active').exists()
            assert not (data/'cutover.request.json').exists()
            assert not (data/'cutover.plan.json').exists()
            if capture_failure=='publish':raise RuntimeError('PRIVATE-PROVIDER-SECRET')
        publications.append(kwargs['kind'])
        return publish_image(*args,opener=cloud.opener,**kwargs)
    monkeypatch.setattr(driver,'publish_image',publish)
    monkeypatch.setattr(driver.time,'sleep',lambda _:real_sleep(.01))
    if capture_failure in ('deadline','provider','receipt_write'):
        def expired(*args,**kwargs):
            if capture_failure=='provider':raise RuntimeError('https://PRIVATE-PROVIDER-SECRET/?credential=test-only')
            raise core.CutoverError('TRANSACTION_DEADLINE')
        monkeypatch.setattr(driver,'capture_frozen',expired)
        if capture_failure=='receipt_write':
            real_write=core._write
            def no_receipt(path,value):
                if Path(path).name=='failure.json':raise OSError('worker disk full')
                return real_write(path,value)
            monkeypatch.setattr(core,'_write',no_receipt)
    if capture_failure in ('sqlite_build','sqlite_context'):
        def sqlite_failure(*args,**kwargs):
            if diagnostic:
                assert control.starts==1
                assert publications==['original']
            try:
                with sqlite3.connect(':memory:') as conn:
                    conn.execute('SELECT * FROM "PRIVATE-PROVIDER-SECRET"')
            except sqlite3.OperationalError:
                if capture_failure=='sqlite_context':
                    raise RuntimeError('https://PRIVATE-PROVIDER-SECRET/?credential=test-only')
                raise
        monkeypatch.setattr(driver,'build_current_hot_cutover_snapshot',sqlite_failure)
    if capture_failure=='preexisting':
        core._write(data/'cutover.preparation.json',dict(run_id='run-001',
            application_sha='a'*40,phase='FROZEN'))
        with pytest.raises(ValueError,match='DIAGNOSTIC_REQUIRES_FRESH_EPOCH'):
            driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'worker',diagnostic=True)
        assert not (data/'cutover.abort.json').exists()
        assert not (data/'cutover.request.json').exists()
        assert not threads and control.starts==0
        return
    try:
        if capture_failure:
            error=(sqlite3.OperationalError if capture_failure=='sqlite_build' else
                   RuntimeError if capture_failure in ('provider','sqlite_context','publish','health') else core.CutoverError)
            with pytest.raises(error):
                driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'worker',diagnostic=diagnostic)
            if capture_failure!='receipt_write':
                failure=json.loads((tmp_path/'worker/failure.json').read_bytes())
                assert failure['stage']==('build_hot' if capture_failure in ('sqlite_build','sqlite_context') else
                    'publish_original' if capture_failure=='publish' else
                    'verify_original_service' if capture_failure=='health' else 'frozen_capture')
                assert failure['code']==('TRANSACTION_DEADLINE' if capture_failure=='deadline' else 'HISTORY_STEP_FAILED')
                assert 'PRIVATE-PROVIDER-SECRET' not in json.dumps(failure)
                if capture_failure in ('sqlite_build','sqlite_context'):
                    chain=failure['error_chain']
                    sqlite_record=chain[0 if capture_failure=='sqlite_build' else 1]
                    assert sqlite_record['exception_type']=='OperationalError'
                    assert sqlite_record['sqlite_errorcode']==1
                    assert sqlite_record['sqlite_errorname']=='SQLITE_ERROR'
                    frames=[frame for record in chain for frame in record['frames']]
                    assert any(frame['file']=='production_history_cutover.py' and frame['function']=='run' for frame in frames)
                    assert all(set(frame)=={'file','line','function'} and frame['line']>0 for frame in frames)
                    assert str(tmp_path) not in json.dumps(failure)
            assert 'PRIVATE-PROVIDER-SECRET' not in capsys.readouterr().out
            assert (data/'cutover.abort.json').exists()
            assert not (data/'cutover.request.json').exists()
            if diagnostic:
                assert control.starts==1
                assert not (data/'cutover.active').exists()
                assert not (data/'cutover.plan.json').exists()
                if capture_failure=='sqlite_build':
                    receipt=json.loads((tmp_path/'worker/capture-proof.json').read_bytes())
                    assert receipt['image']['full_get_verified'] is True
                    assert receipt['owner']['phase']=='ABORTED_ORIGINAL'
                if capture_failure=='health':assert publications==[]
            return
        report=driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'worker',diagnostic=diagnostic)
        assert not errors
        if diagnostic:
            assert report['owner']['phase']=='ABORTED_ORIGINAL'
            assert report['original_service_verified'] is True
            assert report['diagnostic_build_verified'] is True
            assert report['production_retirement_observed'] is False
            assert publications==['original']
            assert control.starts==1
            assert not (data/'cutover.active').exists()
            assert not (data/'cutover.request.json').exists()
            assert not (data/'cutover.plan.json').exists()
            assert not (data/'cutover.journal.json').exists()
            assert (tmp_path/'worker/capture-proof.json').exists()
            with sqlite3.connect(data/'trades.db') as original:
                assert original.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone()[0]==source_count
            return
        assert report['owner']['phase']=='COMMITTED'
        assert report['owner']['disposable_proof'] is False
        assert report['worker_full_union_verified'] is True
        assert report['hot_build']['retired_row_count']>0
        assert control.starts==1
        with sqlite3.connect(tmp_path/'worker/reconstituted.sqlite3') as full:
            assert full.execute('SELECT COUNT(*) FROM passive_market_observations').fetchone()[0]==source_count
        assert 'PRIVATE-PROVIDER-SECRET' not in json.dumps(report)
        monkeypatch.setattr(driver,'_exec',lambda *args,**kwargs:
            pytest.fail('Completed epoch tried restarting a vanished unit'))
        monkeypatch.setattr(driver,'SignedImageTransport',lambda private,**kwargs:
            SignedImageTransport(private,opener=cloud.opener,**kwargs))
        resumed=driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'resumed-worker',resume=True)
        assert resumed['owner']['phase']=='COMMITTED'
        assert resumed['worker_full_union_verified'] is True
    finally:
        for thread in threads:thread.join(timeout=2)
    assert not errors


def test_failure_evidence_bounds_exception_cycles_and_ignores_provider_sqlite_attributes():
    from scripts import production_history_cutover as driver
    errors=[RuntimeError('https://PRIVATE-PROVIDER-SECRET/') for _ in range(6)]
    for left,right in zip(errors,errors[1:]+errors[:1]):
        left.__context__=right
        left.sqlite_errorcode=1
        left.sqlite_errorname='SQLITE_ERROR'
    chain=driver._safe_error_chain(errors[0])
    assert len(chain)==4
    assert all(row['sqlite_errorcode'] is None and row['sqlite_errorname'] is None for row in chain)
    assert 'PRIVATE-PROVIDER-SECRET' not in repr(chain)


@pytest.mark.parametrize('label,code,expected_code',[
    ('SQLITE_ERROR https://PRIVATE-PROVIDER-SECRET/',True,None),
    ('SQLITE_secretCredential123',1,1),
    ('SQLITE_BUSY',1,1),
    ('SQLITE_CREATE_INDEX',1,1),
])
def test_failure_evidence_rejects_noncanonical_sqlite_diagnostic_attributes(label,code,expected_code):
    from scripts import production_history_cutover as driver
    try:
        with sqlite3.connect(':memory:') as conn:
            conn.execute('SELECT * FROM "PRIVATE-PROVIDER-SECRET"')
    except sqlite3.Error as exc:
        exc.sqlite_errorcode=code
        exc.sqlite_errorname=label
        chain=driver._safe_error_chain(exc)
    assert chain[0]['sqlite_errorcode']==expected_code
    assert chain[0]['sqlite_errorname'] is None
    assert 'PRIVATE-PROVIDER-SECRET' not in repr(chain)


def test_failure_evidence_preserves_genuine_extended_sqlite_result_code():
    from scripts import production_history_cutover as driver
    try:
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE x(v INTEGER UNIQUE)')
            conn.execute('INSERT INTO x VALUES(1)')
            conn.execute('INSERT INTO x VALUES(1)')
    except sqlite3.IntegrityError as exc:
        chain=driver._safe_error_chain(exc)
    assert chain[0]['sqlite_errorcode']==2067
    assert chain[0]['sqlite_errorname']=='SQLITE_CONSTRAINT_UNIQUE'


def test_diagnostic_resume_is_refused_before_remote_access(tmp_path,monkeypatch):
    from scripts import production_history_cutover as driver
    monkeypatch.setattr(driver,'_connect',lambda *a,**k:pytest.fail('Unexpected remote access'))
    with pytest.raises(ValueError,match='DIAGNOSTIC_REQUIRES_FRESH_EPOCH'):
        driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'worker',
                   resume=True,diagnostic=True)
    assert not (tmp_path/'worker').exists()


@pytest.mark.parametrize('changed',['phase','service_started','application_sha','epoch','source','guard','plan','request'])
def test_diagnostic_return_refuses_unverified_or_already_handed_off_state(monkeypatch,changed):
    from scripts import production_history_cutover as driver
    identity=dict(database_sha256='b'*64,database_size_bytes=4096)
    epoch=dict(contract='sqlite-history-frozen-v1',run_id='run-001')
    frozen=dict(run_id='run-001',application_sha='a'*40,epoch=epoch,
                source={'identity':identity})
    result=dict(run_id='run-001',application_sha='a'*40,epoch=epoch,
                source=identity,phase='ABORTED_ORIGINAL',service_started=True)
    if changed in ('phase','service_started','application_sha','epoch','source'):
        result[changed]=False
    writes=[]
    monkeypatch.setattr(driver,'_assert_remote_frozen',lambda *a:None)
    monkeypatch.setattr(driver,'_remote_read',lambda s,name:
        epoch if changed=='guard' and name=='cutover.active' else
        {} if changed in ('plan','request') and name=='cutover.'+changed+'.json' else None)
    monkeypatch.setattr(driver,'_remote_write',lambda s,name,value:writes.append(name))
    monkeypatch.setattr(driver,'_completed_result',lambda *a:result)
    monkeypatch.setattr(driver,'_verify_sha',lambda *a:pytest.fail('Unverified return accepted'))
    with pytest.raises(ValueError,match='DIAGNOSTIC_'):
        driver._restore_diagnostic_original(object(),object(),frozen,time.monotonic()+1)
    assert writes==([] if changed in ('plan','request') else ['cutover.abort.json'])


def test_poll_tolerates_terminal_result_archived_between_stat_and_open(tmp_path):
    import os
    from scripts import production_history_cutover as driver
    from scripts import sqlite_history_transaction as core
    path=tmp_path/'cutover.result.json'
    path.write_bytes(core._canonical(dict(run_id='previous',phase='ABORTED_ORIGINAL',service_started=True)))
    path.chmod(0o600)
    class RacingSFTP:
        def lstat(self,remote):
            assert remote==driver.REMOTE_DATA+path.name
            info=path.stat()
            os.replace(path,tmp_path/'archived-result.json')
            return SimpleNamespace(st_mode=info.st_mode,st_size=info.st_size,st_uid=0)
        def open(self,remote,mode):return path.open(mode)
    assert driver._completed_result(RacingSFTP(),'current',None) is None
    assert (tmp_path/'archived-result.json').exists()


def test_remote_state_io_failure_remains_fail_closed_without_private_details(tmp_path):
    from scripts import production_history_cutover as driver
    class UnreadableSFTP:
        def lstat(self,remote):
            import stat
            return SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_size=12,st_uid=0)
        def open(self,remote,mode):raise PermissionError(13,'PRIVATE_TRANSPORT_DETAIL')
    with pytest.raises(ValueError,match='REMOTE_OWNER_STATE_UNAVAILABLE') as failure:
        driver._remote_read(UnreadableSFTP(),'cutover.result.json')
    assert 'PRIVATE_TRANSPORT_DETAIL' not in str(failure.value)
