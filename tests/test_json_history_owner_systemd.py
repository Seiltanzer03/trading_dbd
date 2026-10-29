"""Real systemd disconnection/expiry proof, confined to generated fixture DBs."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scripts import json_history_owner as native
from scripts import sqlite_history_transaction as core
from scripts import history_production_plan as pins
from scripts.history_production_control import database_handles
from test_json_history_separate_epoch import current_images


class FileTransport:
    def __init__(self,data):self.data=data
    @contextmanager
    def open_image(self,kind):
        with (self.data/(kind+'.sqlite3')).open('rb') as stream:yield stream


class Control:
    def __init__(self,data):self.data=data
    def stop_writers(self):return True
    def assert_fenced(self):return (self.data/'cutover.active').is_file()
    def assert_writers_closed(self):return not database_handles(self.data/'trades.db',exclude_pid=os.getpid())
    def prepare_transfer(self):return self.assert_fenced() and self.assert_writers_closed()
    def verify_hot_runtime(self,manifest):return True
    def start_verified_database(self):
        assert not (self.data/'cutover.active').exists()
        (self.data/'verified-launch').write_text('generated contract fixture only')
        return True


def child(data,expire):
    data=Path(data)
    if expire:native.prep.PREPARATION_SECONDS=.5
    plan=core._read(data/'fixture-plan.json')
    # These are generated fixture pins, confined by the disposable-root lock.
    # Actual production entry has no fixture mode or catalog-pin override.
    pins.CATALOG_PINS={key:value for key,value in plan['original_manifest'].items()
        if key not in core._IDENTITY_FIELDS|{'sqlite_history_state_sha256'}}
    metadata=data/'json-rebuild-run-001'
    with core._owned_lock(data):
        result=native._run_json_epoch(data,metadata,run_id='run-001',application_sha='a'*40,
            control=Control(data),transport_factory=lambda *a,**kw:FileTransport(data),
            wait=lambda seconds:time.sleep(min(seconds,.03)))
        core._write(metadata/'cutover.result.json',result)


def system(*args,check=True):
    return subprocess.run(args,check=check,capture_output=True,text=True,timeout=15)


@pytest.mark.skipif(os.environ.get('JSON_HISTORY_SYSTEMD_PROOF')!='1',reason='explicit real systemd proof only')
@pytest.mark.parametrize('expire',[False,True])
def test_real_json_owner_survives_driver_death_or_returns_current_original(tmp_path,expire):
    if os.geteuid()!=0 or not Path('/run/systemd/system').is_dir():
        pytest.fail('Actual root systemd environment required')
    import gzip
    from scripts.history_storage_loader import history_state
    from scripts.production_json_history_rebuild import make_plan
    from test_sqlite_json_rebuild import proof
    data,metadata,original,coded,built,old=current_images(tmp_path)
    (data/'.disposable-history-root').write_text(core.DISPOSABLE_MARKER)
    for kind,path in [('original',original),('hot',coded)]:shutil.copyfile(path,data/(kind+'.sqlite3'))
    fields=history_state.snapshot_history_manifest(original)
    previous=pins.CATALOG_PINS
    pins.CATALOG_PINS={k:v for k,v in fields.items() if k!='sqlite_history_state_sha256'}
    receipts={}
    for kind,path in [('original',original),('hot',coded)]:
        identity={k:proof(path)[k] for k in core._IDENTITY_FIELDS}
        wire=gzip.compress(path.read_bytes(),mtime=0);sha=hashlib.sha256(wire).hexdigest()
        receipts[kind]=dict(contract='sqlite-history-image-v1',run_id='run-001',kind=kind,
            bucket=history_state.BUCKET,codec='gzip',**identity,compressed_sha256=sha,
            compressed_size_bytes=len(wire),full_get_verified=True,
            object_key=f'research/sqlite-cutover/v1/run-001/{kind}/{identity["database_sha256"]}/{sha}.sqlite3.gz')
    try:plan=make_plan(original,coded,built,source_proof=proof(original),run_id='run-001',
                       application_sha='a'*40,receipts=receipts)
    finally:pins.CATALOG_PINS=previous
    private={kind:dict(url='no-cloud-fixture',receipt={k:v for k,v in value.items()
        if k!='full_get_verified'}) for kind,value in receipts.items()}
    core._write(data/'fixture-plan.json',plan)
    unit='json-history-fixture-'+uuid.uuid4().hex+'.service'
    command=['systemd-run','--no-block','--unit='+unit,'--property=Type=exec',
        '--property=RuntimeMaxSec=60','--property=KillMode=control-group','--property=Restart=no',
        '--working-directory='+str(Path(__file__).resolve().parents[1]),
        sys.executable,str(Path(__file__).resolve()),'--child',str(data),str(int(expire))]
    # Killing this separate driver must leave its systemd-owned child alive.
    driver=subprocess.Popen([sys.executable,'-c',
        'import subprocess,sys,time; subprocess.run(sys.argv[1:],check=True); time.sleep(60)',
        *command],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        end=time.monotonic()+30
        while time.monotonic()<end:
            state=core._read(metadata/'cutover.preparation.json')
            if state is not None and state['phase']=='FROZEN':break
            time.sleep(.03)
        else:pytest.fail('Native frozen phase not observed')
        driver.kill();driver.wait(timeout=3)
        if not expire:
            assert system('systemctl','is-active',unit).stdout.strip()=='active'
            core._write(metadata/'cutover.transport.json',private)
            core._write(metadata/'cutover.request.json',plan)
        while time.monotonic()<end and not (metadata/'cutover.result.json').exists():time.sleep(.03)
        result=core._read(metadata/'cutover.result.json')
        assert result is not None
        assert result['phase']==('ABORTED_ORIGINAL' if expire else 'COMMITTED')
        expected=original if expire else coded
        assert hashlib.sha256((data/'trades.db').read_bytes()).hexdigest()==hashlib.sha256(expected.read_bytes()).hexdigest()
        assert (data/'cutover.journal.json').read_bytes()==old
        assert (data/'verified-launch').exists()
        assert not (data/'cutover.active').exists()
    finally:
        if driver.poll() is None:driver.kill();driver.wait(timeout=3)
        system('systemctl','stop',unit,check=False)
        system('systemctl','reset-failed',unit,check=False)


if __name__=='__main__':child(sys.argv[2],bool(int(sys.argv[3])))
