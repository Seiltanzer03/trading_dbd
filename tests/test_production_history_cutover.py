"""Frozen capture proves actual bytes without applying the live-WAL bypass."""
import hashlib
import importlib
import io
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest


class LocalSFTP:
    def __init__(self,source):self.source=source;self.reads=[]
    def open(self,path,mode):
        self.reads.append((path,mode))
        return self.source.open('rb')


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
    assert sftp.reads==[('/opt/seiltanzer/data/trades.db','rb')]
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


def test_complete_driver_builds_publishes_hands_off_and_reconstitutes(tmp_path,monkeypatch):
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
        def lstat(self,path):return self.path(path).lstat()
        def open(self,path,mode):return self.path(path).open('xb' if mode=='wx' else mode)
        def chmod(self,path,mode):self.path(path).chmod(mode)
        def posix_rename(self,a,b):os.replace(self.path(a),self.path(b))
        def remove(self,path):self.path(path).unlink()
        def get_channel(self):return SimpleNamespace(settimeout=lambda _:None)
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
    monkeypatch.setattr(driver,'_probe_api',lambda *args:None)
    monkeypatch.setattr(driver,'_exec',launch)
    monkeypatch.setattr(driver,'yandex_image_client',lambda:cloud)
    monkeypatch.setattr(driver,'publish_image',lambda *args,**kwargs:
        publish_image(*args,opener=cloud.opener,**kwargs))
    monkeypatch.setattr(driver.time,'sleep',lambda _:real_sleep(.01))
    try:
        report=driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'worker')
        assert not errors
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
