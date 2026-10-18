"""Actual SQLite precommit reserve failure must restore the exact original."""
import pytest
import hashlib
import importlib
import sqlite3

from scripts import sqlite_history_transaction as core
from test_sqlite_history_transaction import setup_transaction
from test_history_production_plan import production_plan


def owner_inputs(monkeypatch,setup):
    from scripts import history_production_plan as pins
    root,old,transport,control,_,_ = setup
    plan = production_plan(pins,setup)
    plan['hot_manifest'] = old['hot_manifest']
    monkeypatch.setattr(pins,'CATALOG_PINS',{key:old['hot_manifest'][key] for key in pins.CATALOG_PINS})
    control.stop_writers = lambda:True
    control.prepare_transfer = lambda:True
    control.verify_hot_runtime = lambda manifest:True
    core._write(root/'cutover.request.json',plan)
    private = {kind:dict(url='private-test-'+kind,
                        receipt={key:value for key,value in plan['artifact_receipts'][kind].items()
                                 if key != 'full_get_verified'})
               for kind in ('original','hot')}
    core._write(root/'cutover.transport.json',private)
    return plan,transport,control


def test_native_owner_verifies_full_images_then_commits_actual_sqlite(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_production_owner')
    root,_,_,_,_,hot = setup_transaction
    plan,transport,control = owner_inputs(monkeypatch,setup_transaction)
    with core._owned_lock(root):
        result = owner._run_owned_epoch(root,run_id='run-001',application_sha='a'*40,
            control=control,transport_factory=lambda *args,**kwargs:transport)
    assert result['phase'] == 'COMMITTED'
    assert result['disposable_proof'] is False
    assert result['data_retirement_authorized'] is True
    assert (root/'trades.db').read_bytes() == hot
    assert transport.calls == ['original','hot','hot']
    assert core._read(root/'cutover.plan.json') == plan
    assert control.starts == 1


def test_bad_claimed_full_read_never_truncates_original(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_production_owner')
    root,_,_,_,original,_ = setup_transaction
    _,transport,control = owner_inputs(monkeypatch,setup_transaction)
    transport.images['hot'] = b'corrupted hot despite receipt'
    with core._owned_lock(root):
        result = owner._run_owned_epoch(root,run_id='run-001',application_sha='a'*40,
            control=control,transport_factory=lambda *args,**kwargs:transport)
    assert result['phase'] == 'ABORTED_ORIGINAL'
    assert (root/'trades.db').read_bytes() == original
    assert not (root/'cutover.journal.json').exists()
    assert control.starts == 1


def test_owner_idle_polls_do_not_rescan_full_source(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_production_owner')
    from scripts import history_frozen_preparation as prep
    root,_,_,control,original,_ = setup_transaction
    control.stop_writers = lambda:True
    real = prep._measure;scans=[]
    def measure(*args):scans.append(1);return real(*args)
    monkeypatch.setattr(prep,'_measure',measure)
    clock=[10.0];monkeypatch.setattr(owner.time,'monotonic',lambda:clock[0])
    waits=[]
    def wait(seconds):waits.append(seconds);clock[0]+=2000
    with core._owned_lock(root):
        result=owner._run_owned_epoch(root,run_id='run-001',application_sha='a'*40,
            control=control,wait=wait)
    assert result['phase'] == 'ABORTED_ORIGINAL'
    assert len(scans) == 1
    assert len(waits) == 3
    assert (root/'trades.db').read_bytes() == original
    assert control.starts == 1


def test_committed_owner_resume_never_reads_old_image_after_new_writes(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_production_owner')
    root=setup_transaction[0]
    _,transport,control = owner_inputs(monkeypatch,setup_transaction)
    control.fail_start=True
    with core._owned_lock(root),pytest.raises(ValueError,match='START_FAILED'):
        owner._run_owned_epoch(root,run_id='run-001',application_sha='a'*40,
            control=control,transport_factory=lambda *args,**kwargs:transport)
    with sqlite3.connect(root/'trades.db') as conn:
        conn.execute('CREATE TABLE new_after_commit(id INTEGER PRIMARY KEY)')
        conn.execute('INSERT INTO new_after_commit VALUES(127)')
    after=(root/'trades.db').read_bytes();transport.calls.clear();control.fail_start=False
    with core._owned_lock(root):
        result=owner._run_owned_epoch(root,run_id='run-001',application_sha='a'*40,
            control=control,transport_factory=lambda *args,**kwargs:transport)
    assert result['phase'] == 'COMMITTED'
    assert transport.calls == []
    assert (root/'trades.db').read_bytes() == after


def test_lost_preparation_guard_is_terminal_without_cloud_access(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_production_owner')
    from scripts import history_frozen_preparation as prep
    root,_,transport,control,original,_=setup_transaction
    control.stop_writers=lambda:True
    with core._owned_lock(root):
        prep.prepare_epoch(root,run_id='run-001',application_sha='a'*40,control=control)
        (root/'cutover.active').unlink()
        with pytest.raises(core.FencingError):
            owner._run_owned_epoch(root,run_id='run-001',application_sha='a'*40,control=control)
    assert transport.calls == []
    assert (root/'trades.db').read_bytes() == original
    assert control.starts == 0


def test_owner_uses_real_signed_gzip_adapter_with_public_receipt_flags(setup_transaction,monkeypatch):
    owner = importlib.import_module('scripts.history_production_owner')
    from scripts.history_image_transport import SignedImageTransport
    from test_history_image_transport import image,Response
    root,_,_,_,original,hot=setup_transaction
    _,_,control=owner_inputs(monkeypatch,setup_transaction)
    entries={};compressed={}
    for kind,raw in [('original',original),('hot',hot)]:
        entries[kind],packed=image(raw,kind)
        compressed[entries[kind]['url']]=packed
    core._write(root/'cutover.transport.json',entries)
    def factory(private,**kwargs):
        return SignedImageTransport(private,opener=lambda request,timeout:
            Response(compressed[request.full_url]),**kwargs)
    with core._owned_lock(root):
        result=owner._run_owned_epoch(root,run_id='run-001',application_sha='a'*40,
                                     control=control,transport_factory=factory)
    assert result['phase'] == 'COMMITTED'
    assert (root/'trades.db').read_bytes() == hot


def test_same_invocation_does_not_rescan_verified_hot_before_guard_release(setup_transaction,monkeypatch):
    root,plan,transport,control,_,_=setup_transaction
    real=core._verify_database;scans=[]
    def verify(path,identity,*args,**kwargs):
        scans.append(identity['database_sha256'])
        return real(path,identity,*args,**kwargs)
    monkeypatch.setattr(core,'_verify_database',verify)
    result=core.execute_cutover(root,plan,transport=transport,control=control)
    assert result['phase']=='COMMITTED'
    assert scans == [plan['original']['database_sha256'],plan['hot']['database_sha256']]


@pytest.mark.parametrize('failure',['false','exception'])
def test_actual_precommit_resource_refusal_restores_original(setup_transaction,failure):
    root,plan,transport,control,original,hot = setup_transaction
    phases = []
    def admission():
        phases.append(core._read(root/'cutover.journal.json')['phase'])
        assert (root/'trades.db').read_bytes() == hot
        if failure == 'exception':
            raise ValueError('measured reserve unavailable')
        return False
    with core._owned_lock(root):
        result = core._execute_validated_transfer(root,core._plan_bytes(plan),
            transport=transport,control=control,precommit=admission)
    assert phases == ['HOT_VERIFIED']
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original
    assert control.starts == 1
    assert transport.calls == ['hot','original']


def test_precommit_fence_loss_never_restores_old_over_new_writer(setup_transaction):
    root,plan,transport,control,_,hot = setup_transaction
    def admission():
        control.closed = False
        raise core.FencingError('writer exclusion lost')
    with core._owned_lock(root),pytest.raises(core.FencingError):
        core._execute_validated_transfer(root,core._plan_bytes(plan),
            transport=transport,control=control,precommit=admission)
    assert core._read(root/'cutover.journal.json')['phase'] == 'FENCE_LOST_GUARDED'
    assert (root/'trades.db').read_bytes() == hot
    assert transport.calls == ['hot']
    assert control.starts == 0
