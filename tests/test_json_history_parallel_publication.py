"""The real worker rebuilds, packs and fully reads both disposable images.

Only SSH/native service and object storage are substituted. These generated
fixtures are coordination proofs, never production migration receipts.
"""
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from scripts import production_json_history_rebuild as driver
from scripts import publish_history_image as publisher
from scripts import history_frozen_preparation as prep
from scripts import history_production_plan as pins
from scripts import sqlite_history_transaction as core
from scripts.history_storage_loader import history_state
from test_json_history_separate_epoch import current_images, Control
from test_publish_history_image import MemoryClient
from test_sqlite_json_rebuild import proof


def worker(tmp_path, monkeypatch, *, frozen=True):
    data, metadata, original, _, _, _ = current_images(tmp_path)
    prep.prepare_epoch(metadata, run_id='run-001', application_sha='a'*40,
        control=Control(data, metadata), database=data/'trades.db', allow_marked_source=True)
    state=core._read(metadata/'cutover.preparation.json')
    fields=history_state.snapshot_history_manifest(original)
    monkeypatch.setattr(pins,'CATALOG_PINS',{k:v for k,v in fields.items()
        if k!='sqlite_history_state_sha256'})
    records={'cutover.preparation.json':state, 'cutover.active':state['epoch']} if frozen else {}
    parent=threading.get_ident();clients=[];receipts={};finished=set();aborts=[];launches=[]
    sftp=SimpleNamespace(get_channel=lambda:SimpleNamespace(settimeout=lambda _:None),close=lambda:None)
    client=SimpleNamespace(open_sftp=lambda:sftp,close=lambda:None)

    def read(_,name,**kwargs):
        assert threading.get_ident()==parent,'SFTP must stay on the driver thread'
        return records.get(name)

    def write(_,name,value,**kwargs):
        assert threading.get_ident()==parent,'SFTP must stay on the driver thread'
        records[name]=value
        if name=='cutover.abort.json':aborts.append(finished.copy())
        if name=='cutover.request.json':
            assert finished=={'original','hot'}
            assert all(value['artifact_receipts'][k]['full_get_verified'] for k in ('original','hot'))
            # Stop the disposable native boundary after validating the handoff.
            records['cutover.preparation.json']=dict(state,phase='ABORTED_ORIGINAL',service_started=True)
            records['cutover.result.json']=dict(run_id='run-001',phase='ABORTED_ORIGINAL',
                service_started=True,epoch=state['epoch'])

    def capture(_,identity,output,**kwargs):
        shutil.copyfile(original,output)
        result=proof(output)
        assert {k:result[k] for k in core._IDENTITY_FIELDS}==identity
        return result

    def factory():
        assert threading.get_ident()==parent,'Construct isolated clients on the parent thread'
        value=MemoryClient();value.close=lambda:None;clients.append(value);return value

    def launch(*args,**kwargs):
        launches.append(args[1]);raise RuntimeError('disposable launch boundary')

    monkeypatch.setenv('SSH_PASSWORD','disposable-test-only')
    monkeypatch.setattr(driver,'_connect',lambda *a,**kw:client)
    monkeypatch.setattr(driver,'_verify_sha',lambda *a:None)
    monkeypatch.setattr(driver,'_probe_api',lambda *a:None)
    monkeypatch.setattr(driver,'_capacity',lambda _:dict(free_bytes=1024**3,
        database_size_bytes=original.stat().st_size,database_allocated_bytes=original.stat().st_size))
    monkeypatch.setattr(driver.previous,'_remote_read',read)
    monkeypatch.setattr(driver.previous,'_remote_write',write)
    monkeypatch.setattr(driver.previous,'capture_frozen',capture)
    monkeypatch.setattr(driver.previous,'_private_transport',lambda *a:{})
    monkeypatch.setattr(driver,'yandex_image_client',factory)
    monkeypatch.setattr(driver,'_properties',lambda *a:dict(ActiveState='active' if frozen else 'inactive',LoadState='not-found'))
    monkeypatch.setattr(driver,'_exec',launch)
    return SimpleNamespace(records=records,clients=clients,receipts=receipts,finished=finished,
        aborts=aborts,launches=launches,original=original,directory=tmp_path/'worker',parent=parent)


def run(subject, *, resume=True):
    return driver.run(expected_sha='a'*40,run_id='run-001',directory=subject.directory,resume=resume)


def test_worker_overlaps_real_complete_cloud_readbacks_before_handoff(tmp_path,monkeypatch):
    subject=worker(tmp_path,monkeypatch)
    both_gets=threading.Barrier(2,timeout=3);deadlines=[];used_clients={};get_threads=set()
    def publish(path,identity,**kwargs):
        kind=kwargs['kind'];client=kwargs['client'];used_clients[kind]=client
        deadlines.append(kwargs['deadline']);client.source=Path(path)
        def opener(request,timeout):
            response=client.opener(request,timeout)
            get_threads.add(threading.get_ident());both_gets.wait();return response
        try:
            result=publisher.publish_image(path,identity,**kwargs,opener=opener)
            subject.receipts[kind]=result;return result
        finally:subject.finished.add(kind)
    monkeypatch.setattr(driver,'publish_image',publish)
    before=time.monotonic();report=run(subject)
    assert report['owner']['phase']=='ABORTED_ORIGINAL'
    assert subject.records['cutover.request.json']['artifact_receipts']==subject.receipts
    assert used_clients['original'] is not used_clients['hot']
    assert len(get_threads)==2 and subject.parent not in get_threads
    assert len(deadlines)==2 and deadlines[0]==deadlines[1]
    assert before<deadlines[0]<=before+3601
    assert not subject.aborts


def test_combined_worker_reserve_refuses_before_native_launch(tmp_path,monkeypatch):
    subject=worker(tmp_path,monkeypatch,frozen=False)
    monkeypatch.setattr(driver,'_capacity',lambda _:dict(free_bytes=1024**3,
        database_size_bytes=1024**3,database_allocated_bytes=1024**3))
    # Enough for the former three-image preflight, not two raw plus two gzip.
    monkeypatch.setattr(driver.shutil,'disk_usage',lambda _:SimpleNamespace(free=5*1024**3))
    with pytest.raises(core.CutoverError):run(subject,resume=False)
    assert subject.launches==[]
    assert 'cutover.request.json' not in subject.records


def test_combined_postbuild_reserve_refuses_before_either_upload(tmp_path,monkeypatch):
    subject=worker(tmp_path,monkeypatch)
    # The generated tiny builder and either individual publisher fit here.
    # Concurrent publication must still preserve the combined 2-GiB reserve.
    monkeypatch.setattr(driver.shutil,'disk_usage',lambda _:SimpleNamespace(free=3*1024**3//2))
    def publish(path,identity,**kwargs):
        kwargs['client'].source=Path(path)
        result=publisher.publish_image(path,identity,**kwargs,opener=kwargs['client'].opener)
        subject.finished.add(kwargs['kind']);return result
    monkeypatch.setattr(driver,'publish_image',publish)
    with pytest.raises(core.CutoverError):run(subject)
    assert not any(client.calls for client in subject.clients)
    assert 'cutover.request.json' not in subject.records
    assert subject.aborts==[set()]


def test_failed_image_joins_its_sibling_before_requesting_frozen_abort(tmp_path,monkeypatch):
    subject=worker(tmp_path,monkeypatch)
    both_gets=threading.Barrier(2,timeout=3)
    def publish(path,identity,**kwargs):
        kind=kwargs['kind'];client=kwargs['client'];client.source=Path(path)
        if kind=='original':client.hazard='get'
        def opener(request,timeout):
            response=client.opener(request,timeout);both_gets.wait();return response
        try:return publisher.publish_image(path,identity,**kwargs,opener=opener)
        finally:subject.finished.add(kind)
    monkeypatch.setattr(driver,'publish_image',publish)
    with pytest.raises(core.CutoverError):run(subject)
    assert subject.aborts==[{'original','hot'}]
    assert 'cutover.request.json' not in subject.records
    assert subject.original.exists()
