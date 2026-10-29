"""A new physical encoding never resets the already committed cold epoch."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest
from scripts import history_frozen_preparation as prep
from scripts import sqlite_history_transaction as core
from scripts import sqlite_json_rebuild as builder
from test_sqlite_history_cutover import _build_cutover_fixture
from scripts.sqlite_history_cutover import build_current_hot_cutover_snapshot
from test_sqlite_json_rebuild import proof
from test_sqlite_history_transaction import MemoryTransport
from test_sqlite_history_current_cutover import _proof


class Control:
    def __init__(self,data,metadata):
        self.data,self.metadata=data,metadata;self.starts=0
    def stop_writers(self):return True
    def assert_fenced(self):return (self.metadata/'cutover.active').is_file()
    def assert_writers_closed(self):return True
    def start_verified_database(self):
        assert not (self.metadata/'cutover.active').exists()
        self.starts+=1
        return True


def current_images(tmp_path):
    src,receipt,raw,pin,client=_build_cutover_fixture(tmp_path,include_post_catalog=True,
        include_feature_json=True,include_runtime_metadata=True)
    receipt=_proof(src,receipt,pin)
    original=tmp_path/'current.db'
    build_current_hot_cutover_snapshot(src,original,source_receipt=receipt,catalog_bytes=raw,
                                      expected_catalog_sha256=pin,client=client)
    with closing(sqlite3.connect(original)) as conn:
        conn.execute('UPDATE passive_market_observations SET features_json=? WHERE rowid=140',
                     (' {"exact": "'+'полная история; '*4000+'"} ',));conn.commit()
    coded=tmp_path/'coded.db'
    built=builder.rebuild_snapshot(original,coded,source_receipt=proof(original),mode='compress')
    data=tmp_path/'data';data.mkdir()
    shutil.copyfile(original,data/'trades.db')
    old=core._canonical(dict(contract=core.CONTRACT,run_id='old-cold-epoch',
        plan_sha256='b'*64,phase='COMMITTED',service_started=True))
    (data/'cutover.journal.json').write_bytes(old)
    (data/'cutover.journal.json').chmod(0o600)
    metadata=data/'json-rebuild-run-001';metadata.mkdir(mode=0o700)
    return data,metadata,original,coded,built,old


def test_new_frozen_metadata_preserves_marked_current_source_and_old_epoch(tmp_path):
    data,metadata,original,_,_,old=current_images(tmp_path)
    control=Control(data,metadata)
    state=prep.prepare_epoch(metadata,run_id='run-001',application_sha='a'*40,
        control=control,database=data/'trades.db',allow_marked_source=True)
    assert state['phase']=='FROZEN'
    assert state['source']['database_sha256']==hashlib.sha256(original.read_bytes()).hexdigest()
    assert (data/'cutover.journal.json').read_bytes()==old
    assert not (metadata/'cutover.journal.json').exists()
    assert control.starts==0


def test_missing_canonical_guard_cannot_start_a_pending_separate_epoch(tmp_path):
    from scripts import history_service_fence as fence
    data,metadata,_,_,_,_=current_images(tmp_path)
    application=Path(__file__).resolve().parents[1]
    assert fence.check_start(data/'trades.db',application)['start_allowed'] is True
    core._write(data/'json-history.epoch.json',dict(contract='sqlite-json-history-epoch-link-v1',
        run_id='run-001',application_sha='a'*40))
    core._write(metadata/'cutover.active',dict(contract=core.CONTRACT,run_id='run-001',plan_sha256='d'*64))
    assert not (data/'cutover.active').exists()
    result=fence.check_start(data/'trades.db',application)
    assert result['start_allowed'] is False
    assert result['reason']=='ACTIVE_JSON_HISTORY_EPOCH'


@pytest.mark.parametrize('failed',[False,True])
def test_separate_journal_transfers_or_restores_only_the_exact_current_database(tmp_path,failed):
    data,metadata,original,coded,built,old=current_images(tmp_path)
    transport=MemoryTransport(original,coded)
    if failed:transport.failures['hot']=b'partial input'
    plan=dict(contract=core.CONTRACT,mode='DISPOSABLE_ONLY',run_id='run-001',
        original={k:proof(original)[k] for k in core._IDENTITY_FIELDS},
        hot={k:built[k] for k in core._IDENTITY_FIELDS},
        hot_manifest={k:built[k] for k in core._IDENTITY_FIELDS|{
            'cold_history_catalog_sha256','cold_history_catalog_bucket',
            'cold_history_catalog_object_key','sqlite_history_state_sha256',
            'sqlite_json_payload_storage_sha256'}},artifact_receipts={})
    control=Control(data,metadata)
    result=core._execute_validated_transfer(metadata,core._canonical(plan),
        transport=transport,control=control,database=data/'trades.db')
    assert result['phase']==('ORIGINAL_RESTORED' if failed else 'COMMITTED')
    assert (data/'trades.db').read_bytes()==(original if failed else coded).read_bytes()
    assert (data/'cutover.journal.json').read_bytes()==old
    assert core._read(metadata/'cutover.journal.json')['service_started'] is True
    assert control.starts==1


@pytest.mark.parametrize('failed',[False,True])
def test_json_owner_keeps_canonical_guard_and_independently_checks_both_images(tmp_path,monkeypatch,failed):
    import gzip,importlib
    from scripts import history_production_plan as pins
    from scripts.history_storage_loader import history_state
    from test_sqlite_history_transaction import FencedControl
    owner=importlib.import_module('scripts.json_history_owner')
    data,metadata,original,coded,built,old=current_images(tmp_path)
    fields=history_state.snapshot_history_manifest(original)
    monkeypatch.setattr(pins,'CATALOG_PINS',{k:v for k,v in fields.items() if k!='sqlite_history_state_sha256'})
    identities={kind:{k:proof(path)[k] for k in core._IDENTITY_FIELDS}
                for kind,path in [('original',original),('hot',coded)]}
    receipts={}
    for kind,path in [('original',original),('hot',coded)]:
        wire=gzip.compress(path.read_bytes(),mtime=0);sha=hashlib.sha256(wire).hexdigest()
        receipts[kind]=dict(contract='sqlite-history-image-v1',run_id='run-001',kind=kind,
            bucket=history_state.BUCKET,codec='gzip',**identities[kind],
            compressed_sha256=sha,compressed_size_bytes=len(wire),full_get_verified=True,
            object_key=f'research/sqlite-cutover/v1/run-001/{kind}/{identities[kind]["database_sha256"]}/{sha}.sqlite3.gz')
    from scripts.production_json_history_rebuild import make_plan
    plan=make_plan(original,coded,built,source_proof=proof(original),run_id='run-001',
                   application_sha='a'*40,receipts=receipts)
    assert plan['original_manifest']==dict(identities['original'],**fields)
    assert plan['hot_manifest']['sqlite_history_state_sha256']==fields['sqlite_history_state_sha256']
    private={kind:dict(url='disposable-fixture-only',receipt={k:v for k,v in receipt.items()
             if k!='full_get_verified'}) for kind,receipt in receipts.items()}
    transport=MemoryTransport(original,coded);control=FencedControl(data)
    control.stop_writers=lambda:True
    control.prepare_transfer=lambda:control.assert_fenced() and control.assert_writers_closed()
    control.verify_hot_runtime=lambda _:True
    # The native owner must read both complete cloud images before a partial
    # transfer can occur. Only the third image open injects the transfer fault.
    real_open=transport.open_image
    from contextlib import contextmanager
    @contextmanager
    def open_image(kind):
        assert (data/'cutover.active').exists()
        if failed and transport.calls==['original','hot']:transport.failures['hot']=b'partial'
        with real_open(kind) as stream:yield stream
    transport.open_image=open_image
    def handoff(_):
        assert (data/'cutover.active').exists()
        core._write(metadata/'cutover.request.json',plan)
        core._write(metadata/'cutover.transport.json',private)
    result=owner._run_json_epoch(data,metadata,run_id='run-001',application_sha='a'*40,
        control=control,transport_factory=lambda *a,**kw:transport,wait=handoff)
    assert result['phase']==('ORIGINAL_RESTORED' if failed else 'COMMITTED')
    assert (data/'trades.db').read_bytes()==(original if failed else coded).read_bytes()
    assert transport.calls==(['original','hot','hot','original'] if failed else ['original','hot','hot'])
    assert not (data/'cutover.active').exists()
    assert (data/'cutover.journal.json').read_bytes()==old
    prior={p.name:p.read_bytes() for p in metadata.iterdir() if p.is_file()}
    from test_sqlite_history_transaction import ProcessDeath
    second=data/'json-rebuild-run-002';second.mkdir(mode=0o700)
    def disconnect(_):raise ProcessDeath()
    expected_error=ProcessDeath if failed else core.FencingError
    with pytest.raises(expected_error):
        owner._run_json_epoch(data,second,run_id='run-002',application_sha='a'*40,
            control=control,wait=disconnect)
    assert (data/'trades.db').read_bytes()==(original if failed else coded).read_bytes()
    assert {p.name:p.read_bytes() for p in metadata.iterdir() if p.is_file()}==prior
    assert (data/'cutover.journal.json').read_bytes()==old


def test_safe_original_abort_allows_new_epoch_and_captures_legitimate_new_writes(tmp_path):
    from scripts import json_history_owner as owner
    from test_sqlite_history_transaction import FencedControl,ProcessDeath
    data,metadata,_,_,_,old=current_images(tmp_path)
    control=FencedControl(data);control.stop_writers=lambda:True
    def abort(_):
        state=core._read(metadata/'cutover.preparation.json')
        core._write(metadata/'cutover.abort.json',dict(contract='sqlite-history-preparation-abort-v1',
            run_id='run-001',application_sha='a'*40,epoch=state['epoch']))
    first=owner._run_json_epoch(data,metadata,run_id='run-001',application_sha='a'*40,
        control=control,wait=abort)
    assert first['phase']=='ABORTED_ORIGINAL' and first['service_started'] is True
    previous={p.name:p.read_bytes() for p in metadata.iterdir() if p.is_file()}
    with closing(sqlite3.connect(data/'trades.db')) as conn:
        conn.execute('CREATE TABLE legitimate_new_trade_audit(id INTEGER PRIMARY KEY,note TEXT)')
        conn.execute("INSERT INTO legitimate_new_trade_audit VALUES(17,'new actual fixture write')");conn.commit()
    actual=(data/'trades.db').read_bytes()
    second=data/'json-rebuild-run-002';second.mkdir(mode=0o700)
    def disconnect(_):raise ProcessDeath()
    with pytest.raises(ProcessDeath):
        owner._run_json_epoch(data,second,run_id='run-002',application_sha='a'*40,
            control=control,wait=disconnect)
    state=core._read(second/'cutover.preparation.json')
    assert state['source']['identity']['database_sha256']==hashlib.sha256(actual).hexdigest()
    assert (data/'trades.db').read_bytes()==actual
    assert {p.name:p.read_bytes() for p in metadata.iterdir() if p.is_file()}==previous
    assert (data/'cutover.journal.json').read_bytes()==old
    assert core._read(data/'json-history.epoch.json')['run_id']=='run-002'


def test_terminal_resume_does_not_require_observing_a_running_owner(tmp_path,monkeypatch):
    from scripts import production_json_history_rebuild as driver
    from types import SimpleNamespace
    from scripts import json_history_owner as native
    from scripts import history_production_plan as pins
    from scripts.history_storage_loader import history_state
    codec=history_state._payload_codec()
    payload_pin=hashlib.sha256(codec._canonical(codec._definition()).encode()).hexdigest()
    original=dict(database_sha256='c'*64,database_size_bytes=900000)
    hot=dict(database_sha256='d'*64,database_size_bytes=4096)
    fields=dict(pins.CATALOG_PINS,sqlite_history_state_sha256='e'*64)
    receipts={kind:dict(contract='sqlite-history-image-v1',run_id='run-001',kind=kind,
        bucket='trading-dbd-backups-2026',codec='gzip',**identity,
        compressed_sha256='f'*64,compressed_size_bytes=4096,full_get_verified=True,
        object_key=f'research/sqlite-cutover/v1/run-001/{kind}/{identity["database_sha256"]}/{"f"*64}.sqlite3.gz')
        for kind,identity in [('original',original),('hot',hot)]}
    plan=dict(contract=native.CONTRACT,mode='FENCED_PRODUCTION_ONLY',run_id='run-001',
        application_sha='a'*40,original=original,hot=hot,artifact_receipts=receipts,
        original_manifest=dict(original,**fields),
        hot_manifest=dict(hot,**fields,sqlite_json_payload_storage_sha256=payload_pin))
    receipt=dict(contract=core.CONTRACT,run_id='run-001',phase='COMMITTED',
                 service_started=True,cutover_committed=True)
    records={'cutover.preparation.json':dict(run_id='run-001',application_sha='a'*40,
        phase='FROZEN',source=dict(identity=original)),'cutover.plan.json':plan,
        'cutover.journal.json':dict(contract=core.CONTRACT,run_id='run-001',phase='COMMITTED',
            plan_sha256=hashlib.sha256(core._canonical(plan)).hexdigest(),service_started=True),
        'cutover.result.json':receipt}
    sftp=SimpleNamespace(get_channel=lambda:SimpleNamespace(settimeout=lambda _:None),close=lambda:None)
    client=SimpleNamespace(open_sftp=lambda:sftp,close=lambda:None)
    monkeypatch.setenv('SSH_PASSWORD','disposable-test-only')
    monkeypatch.setattr(driver,'_connect',lambda *a,**kw:client)
    monkeypatch.setattr(driver,'_verify_sha',lambda *a:None)
    monkeypatch.setattr(driver,'_probe_api',lambda *a:None)
    monkeypatch.setattr(driver,'_capacity',lambda _:dict(free_bytes=20*1024**3,
        database_size_bytes=4096,database_allocated_bytes=4096))
    monkeypatch.setattr(driver.previous,'_remote_read',lambda s,name,**kw:records.get(name))
    monkeypatch.setattr(driver.previous,'_remote_write',lambda *a,**kw:None)
    monkeypatch.setattr(driver.previous,'_private_transport',lambda *a:{})
    monkeypatch.setattr(driver,'yandex_image_client',lambda:object())
    monkeypatch.setattr(driver,'_properties',lambda *a:dict(ActiveState='inactive',LoadState='not-found'))
    monkeypatch.setattr(driver,'_exec',lambda *a,**kw:'')
    clock=iter(range(0,1000,31));monkeypatch.setattr(driver.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(driver.time,'sleep',lambda _:None)
    report=driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'worker',resume=True)
    assert report['owner']==receipt
    assert not (tmp_path/'worker'/'failure.json').exists()


def test_resume_can_finish_an_owned_link_before_preparation_was_written(tmp_path,monkeypatch):
    from scripts import production_json_history_rebuild as driver
    from types import SimpleNamespace
    records={'json-history.epoch.json':dict(contract='sqlite-json-history-epoch-link-v1',
        run_id='run-001',application_sha='a'*40),
        'cold-epoch.json':dict(contract='sqlite-json-cold-epoch-link-v1',run_id='run-001',
            application_sha='a'*40,cold_journal_sha256='b'*64)}
    sftp=SimpleNamespace(get_channel=lambda:SimpleNamespace(settimeout=lambda _:None),close=lambda:None)
    client=SimpleNamespace(open_sftp=lambda:sftp,close=lambda:None);launches=[]
    monkeypatch.setenv('SSH_PASSWORD','disposable-test-only')
    monkeypatch.setattr(driver,'_connect',lambda *a,**kw:client)
    monkeypatch.setattr(driver,'_verify_sha',lambda *a:None)
    monkeypatch.setattr(driver,'_capacity',lambda _:dict(free_bytes=20*1024**3,
        database_size_bytes=4096,database_allocated_bytes=4096))
    monkeypatch.setattr(driver,'_probe_api',lambda *a:None)
    monkeypatch.setattr(driver.previous,'_remote_read',lambda s,name,**kw:records.get(name))
    monkeypatch.setattr(driver,'yandex_image_client',lambda:object())
    monkeypatch.setattr(driver,'_properties',lambda *a:dict(ActiveState='inactive',LoadState='not-found'))
    def launch(*args,**kwargs):
        launches.append(args[1]);raise RuntimeError('stop at the fixture launch boundary')
    monkeypatch.setattr(driver,'_exec',launch)
    with pytest.raises(core.CutoverError):
        driver.run(expected_sha='a'*40,run_id='run-001',directory=tmp_path/'worker',resume=True)
    assert len(launches)==1
    assert 'scripts.json_history_owner' in launches[0]
