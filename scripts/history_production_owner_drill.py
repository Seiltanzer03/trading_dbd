"""Disposable real-systemd owner/disconnected-driver proof; no cloud calls."""
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import history_production_plan as pins
from scripts import sqlite_history_transaction as core
from scripts.history_storage_loader import history_state
from scripts.history_production_owner import _run_owned_epoch
from scripts.history_production_control import database_handles


def fixture(root):
    """Synthetic contract fixture, never a production/cloud authority receipt."""
    root=Path(root)
    (root/'.disposable-history-root').write_text(core.DISPOSABLE_MARKER)
    original=root/'original.sqlite3';hot=root/'hot.sqlite3'
    ddl=('CREATE TABLE passive_market_observations(observation_id TEXT PRIMARY KEY,payload TEXT);'
         'CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY);')
    with sqlite3.connect(original) as conn:
        conn.executescript(ddl)
        conn.execute('INSERT INTO passive_market_observations(rowid,observation_id,payload) VALUES(1,?,?)',
                     ('contract-fixture-retired','fixture'*200000))
        conn.execute("INSERT INTO passive_market_observations(rowid,observation_id,payload) VALUES(2,'kept','fixture')")
        conn.execute("INSERT INTO g1s_observations(rowid,observation_id) VALUES(1,'kept')")
    identities={'original':dict(database_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
                                database_size_bytes=original.stat().st_size)}
    with sqlite3.connect(hot) as conn:
        conn.executescript(ddl)
        conn.execute("INSERT INTO passive_market_observations(rowid,observation_id,payload) VALUES(2,'kept','fixture')")
        conn.execute("INSERT INTO g1s_observations(rowid,observation_id) VALUES(1,'kept')")
        state=dict(contract=history_state.CONTRACT,bucket=history_state.BUCKET,
            catalog_source_database_sha256=pins.CATALOG_SOURCE,catalog_sha256=pins.CATALOG_SHA256,
            catalog_object_key=pins.CATALOG_PINS['cold_history_catalog_object_key'],
            cutover_source_database_sha256=identities['original']['database_sha256'],
            retired_rowids={'passive_market_observations':[1]},
            retained_identity={table:history_state.identity_proof(conn,table,boundary)
                for table,boundary in [('passive_market_observations',2),('g1s_observations',1)]},
            source_materialization_watermark=2,production_authority=False)
        history_state.write_history_state(conn,state)
        fields=history_state.history_manifest_fields(conn)
    identities['hot']=dict(database_sha256=hashlib.sha256(hot.read_bytes()).hexdigest(),
                           database_size_bytes=hot.stat().st_size)
    receipts={}
    for kind,path in [('original',original),('hot',hot)]:
        packed=gzip.compress(path.read_bytes(),mtime=0)
        compressed=hashlib.sha256(packed).hexdigest()
        key=f'research/sqlite-cutover/v1/run-001/{kind}/{identities[kind]["database_sha256"]}/{compressed}.sqlite3.gz'
        receipts[kind]=dict(contract='sqlite-history-image-v1',run_id='run-001',kind=kind,
            bucket=history_state.BUCKET,object_key=key,codec='gzip',**identities[kind],
            compressed_sha256=compressed,compressed_size_bytes=len(packed),full_get_verified=True)
    plan=dict(contract=pins.CONTRACT,mode='FENCED_PRODUCTION_ONLY',run_id='run-001',
              application_sha='a'*40,**identities,hot_manifest=dict(identities['hot'],**fields),
              artifact_receipts=receipts)
    private={kind:dict(url='fixture-only-no-provider',receipt={key:value for key,value in receipt.items()
              if key!='full_get_verified'}) for kind,receipt in receipts.items()}
    shutil.copyfile(original,root/'trades.db')
    return plan,private


class FileTransport:
    def __init__(self,root):self.root=Path(root)
    @contextmanager
    def open_image(self,kind):
        with (self.root/(kind+'.sqlite3')).open('rb') as stream:yield stream


class ProofControl:
    def __init__(self,root):self.root=Path(root)
    def stop_writers(self):return True
    def assert_fenced(self):return (self.root/'cutover.active').is_file()
    def assert_writers_closed(self):
        return not database_handles(self.root/'trades.db',exclude_pid=os.getpid())
    def prepare_transfer(self):return self.assert_fenced() and self.assert_writers_closed()
    def verify_hot_runtime(self,manifest):
        # This drill proves process ownership on a disposable v1 image only.
        # Actual production control and existing CI enforce the v2 capability.
        return True
    def start_verified_database(self):
        assert not (self.root/'cutover.active').exists()
        with sqlite3.connect(self.root/'trades.db') as conn:
            assert conn.execute('PRAGMA quick_check').fetchone()==('ok',)
        (self.root/'verified-launch').write_text('verified fixture only')
        return True


def child(root,*,expire=False):
    root=Path(root)
    if expire:
        from scripts import history_frozen_preparation as prep
        prep.PREPARATION_SECONDS=.3
    with core._owned_lock(root):
        result=_run_owned_epoch(root,run_id='run-001',application_sha='a'*40,
            control=ProofControl(root),transport_factory=lambda *args,**kwargs:FileTransport(root),
            wait=lambda seconds:time.sleep(min(seconds,.05)))
        core._write(root/'cutover.result.json',result)


def _system(*args,check=True):
    return subprocess.run(args,check=check,capture_output=True,text=True,timeout=15)


def launch_driver(unit,root,*,expire=False):
    command=['systemd-run','--no-block','--unit='+unit,'--property=Type=exec',
        '--property=RuntimeMaxSec=60','--property=KillMode=control-group',
        '--property=Restart=no','--working-directory='+str(Path(__file__).resolve().parents[1]),
        sys.executable,str(Path(__file__).resolve()),'--child',str(root)]
    if expire:command+=['--expire']
    _system(*command)
    # Parent harness kills this driver. The local systemd owner must survive.
    time.sleep(60)


def drill():
    if os.geteuid()!=0 or not Path('/run/systemd/system').is_dir():
        raise RuntimeError('ACTUAL_SYSTEMD_ROOT_REQUIRED')
    report=dict(contract='sqlite-history-native-owner-proof-v1',verified=False,
        contract_fixture=True,production_touched=False,provider_calls=0,orders_sent=0,cases=[])
    with tempfile.TemporaryDirectory(prefix='native-history-proof-') as temporary:
        for case in ('driver_death','preparation_expiry'):
            root=Path(temporary)/case;root.mkdir()
            plan,private=fixture(root)
            unit='history-owner-proof-'+uuid.uuid4().hex+'.service'
            command=[sys.executable,str(Path(__file__).resolve()),'--launch-driver',unit,str(root)]
            if case=='preparation_expiry':command+=['--expire']
            driver=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                end=time.monotonic()+20
                while time.monotonic()<end:
                    state=core._read(root/'cutover.preparation.json')
                    if state is not None and state['phase']=='FROZEN':break
                    if (root/'cutover.result.json').exists():break
                    time.sleep(.03)
                else:raise RuntimeError('NATIVE_FROZEN_PHASE_NOT_OBSERVED')
                driver.kill();driver.wait(timeout=3)
                if case=='driver_death':
                    active=_system('systemctl','is-active',unit).stdout.strip()
                    if active!='active':raise RuntimeError('OWNER_DIED_WITH_DRIVER')
                    core._write(root/'cutover.transport.json',private)
                    core._write(root/'cutover.request.json',plan)
                while time.monotonic()<end and not (root/'cutover.result.json').exists():time.sleep(.03)
                result=core._read(root/'cutover.result.json')
                expected='COMMITTED' if case=='driver_death' else 'ABORTED_ORIGINAL'
                if result is None or result['phase']!=expected or not (root/'verified-launch').exists():
                    raise RuntimeError('NATIVE_OWNER_FINITE_RESULT_FAILED')
                identity=plan['hot' if case=='driver_death' else 'original']
                if hashlib.sha256((root/'trades.db').read_bytes()).hexdigest()!=identity['database_sha256']:
                    raise RuntimeError('NATIVE_OWNER_VERIFIED_BYTES_DIFFER')
                report['cases'].append(dict(case=case,phase=result['phase'],verified=True))
            finally:
                if driver.poll() is None:driver.kill();driver.wait(timeout=3)
                _system('systemctl','stop',unit,check=False)
                _system('systemctl','reset-failed',unit,check=False)
    report['verified']=True
    return report


def main():
    args=sys.argv[1:]
    if args[:1]==['--child']:
        child(args[1],expire='--expire' in args);return 0
    if args[:1]==['--launch-driver']:
        launch_driver(args[1],args[2],expire='--expire' in args);return 0
    result=drill();print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
