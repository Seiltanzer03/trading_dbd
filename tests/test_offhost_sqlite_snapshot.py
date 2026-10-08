import importlib.util
import os
import json
import hashlib
from pathlib import Path
import sqlite3

import pytest

_spec = importlib.util.spec_from_file_location(
    'offhost_sqlite_snapshot', Path(__file__).resolve().parents[1] / 'scripts/offhost_sqlite_snapshot.py')
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def test_replica_is_checkpointed_and_verified(tmp_path):
    database = tmp_path / 'replica.sqlite3'
    conn = sqlite3.connect(database)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE observations(value TEXT)')
    conn.execute("INSERT INTO observations VALUES('retained')")
    conn.commit()
    result = module.verify_replica(database)
    assert result['database_size_bytes'] == database.stat().st_size
    assert len(result['database_sha256']) == 64
    # Reading only the standalone DB must retain the committed row.
    with sqlite3.connect(f'file:{database}?immutable=1', uri=True) as readonly:
        assert readonly.execute('SELECT value FROM observations').fetchone()[0] == 'retained'
    conn.close()


def test_corrupt_replica_fails_closed(tmp_path):
    database = tmp_path / 'replica.sqlite3'
    database.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError):
        module.verify_replica(database)


def test_source_archive_checksum_rejected_before_compilation(monkeypatch):
    import io
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'wrong-source'))
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        module._verified_archive('src', module.SOURCE_SHA3)


def test_replica_progress_detects_rewrites_without_file_growth(tmp_path):
    replica = tmp_path / 'replica.sqlite3'
    replica.write_bytes(b'initial')
    first = module._replica_progress(os.getpid(), replica)
    replica.write_bytes(b'updated')
    # Hosted filesystems can report identical mtimes for immediate rewrites.
    os.utime(replica, ns=(first[2] + 1_000_000_000, first[2] + 1_000_000_000))
    second = module._replica_progress(os.getpid(), replica)
    assert first[1] == second[1]
    assert second[2] > first[2]
    assert module._replica_progress(os.getpid(), tmp_path / 'missing')[1:] == (0, 0)


def seed_receipt(tmp_path, *, many_pages=False):
    database = tmp_path/'seed.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE evidence(value TEXT)')
        conn.execute("INSERT INTO evidence VALUES('old')")
        if many_pages:
            conn.execute('CREATE TABLE padding(value BLOB)')
            conn.executemany('INSERT INTO padding VALUES(?)',[(bytes([i%256])*1024,) for i in range(600)])
    manifest = dict(source='LIVE_SQLITE_RSYNC', source_db='/opt/seiltanzer/data/trades.db',
                    git_commit='a'*40, started_ts=100., completed_ts=110., uploaded_ts=120.,
                    production_authority=False, database_size_bytes=database.stat().st_size,
                    database_sha256=hashlib.sha256(database.read_bytes()).hexdigest())
    digest = hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    receipt = dict(restore_contract='trading-dbd-live-seed-v1',seed_verified=True,
                   full_restore_verified=False, production_authority=False,
                   storage_manifest=manifest, storage_manifest_sha256=digest,
                   database_sha256=manifest['database_sha256'],database_size_bytes=manifest['database_size_bytes'])
    path=tmp_path/'seed.json'
    path.write_text(json.dumps(receipt))
    return database,path,receipt


def test_seed_entry_revalidates_real_bytes_and_keeps_historical_clock(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    database,path,receipt = seed_receipt(tmp_path)
    verified = module.verify_seed(database,path)
    assert verified['storage_manifest']['started_ts'] == 100.
    assert verified['storage_manifest']['git_commit'] == 'a'*40
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO evidence VALUES('tampered')")
    with pytest.raises(RuntimeError,match='Seed.*hash|Seed.*size'):
        module.verify_seed(database,path)


@pytest.mark.parametrize('suffix',['-wal','-shm'])
def test_seed_sidecars_are_refused_without_modifying_them(tmp_path,monkeypatch,suffix):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    database,path,_ = seed_receipt(tmp_path)
    sidecar=Path(str(database)+suffix)
    sidecar.write_bytes(b'pending')
    with pytest.raises(ValueError,match='sidecar'):
        module.verify_seed(database,path)
    assert sidecar.read_bytes() == b'pending'


def test_seed_receipt_tampered_epoch_is_refused(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    database,path,receipt = seed_receipt(tmp_path)
    receipt['storage_manifest']['started_ts']=999.
    path.write_text(json.dumps(receipt))
    with pytest.raises(RuntimeError,match='seed'):
        module.verify_seed(database,path)


def test_real_pinned_sqlite_refresh_retains_old_and_current_rows(tmp_path,monkeypatch):
    import subprocess
    import sys
    import shlex
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    binary=os.environ.get('SQLITE_RSYNC_TEST_BINARY')
    if not binary:
        pytest.skip('pinned copier integration runs in final CI and scoped local verification')
    executable=tmp_path/'sqlite3_rsync'
    import shutil
    shutil.copy2(binary,executable)
    state=tmp_path/'origin.json'
    wrapper=tmp_path/'origin'
    control=Path(__file__).resolve().parents[1]/'scripts/sqlite_replication_origin.py'
    wrapper.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(control))+
                      ' run --binary '+shlex.quote(str(executable))+' --state '+shlex.quote(str(state))+' -- "$@"\n')
    wrapper.chmod(0o700)
    ssh=tmp_path/'local-ssh'
    ssh.write_text('#!/bin/sh\n[ "$1" = "-e" ] && [ "$2" = "none" ] && [ "$3" = "fixture" ] || exit 2\nshift 3\nexec "$@"\n')
    ssh.chmod(0o700)
    database,path,_ = seed_receipt(tmp_path,many_pages=True)
    origin=tmp_path/'current.db'
    origin.write_bytes(database.read_bytes())
    with sqlite3.connect(origin) as conn:
        conn.execute("INSERT INTO evidence VALUES('current')")
        conn.execute("UPDATE evidence SET value='corrected-old' WHERE value='old'")
    module.verify_seed(database,path)
    transferred=subprocess.run([str(executable),'fixture:'+str(origin),str(database),
                                '--exe',str(wrapper),'--ssh',str(ssh),'-v','-v','-v'],
                               check=True,timeout=30,capture_output=True,text=True)
    module.verify_replica(database)
    with sqlite3.connect(database.resolve().as_uri()+'?immutable=1',uri=True) as conn:
        assert conn.execute('SELECT value FROM evidence ORDER BY rowid').fetchall() == [('corrected-old',),('current',)]
        assert conn.execute('SELECT count(*) FROM padding').fetchone()[0] == 600
    import re
    pages=int(re.search(r'page updates: (\d+)',transferred.stdout).group(1))
    assert 0 < pages < database.stat().st_size//4096
    assert json.loads(state.read_text())['pid'] > 0


def replication_boundary(tmp_path,monkeypatch,*,low_space=False,cleanup_fails=False):
    from types import SimpleNamespace
    import subprocess
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'scripts'))
    import production_ede_offload as transport
    database,receipt,_=seed_receipt(tmp_path)
    commands=[]
    uploaded={}
    class SFTP:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def put(self,local,remote): uploaded[remote]=Path(local).read_bytes()
        def chmod(self,*args): pass
    key=SimpleNamespace(get_name=lambda:'ssh-ed25519',get_base64=lambda:'test')
    client=SimpleNamespace(open_sftp=lambda:SFTP(),get_transport=lambda:SimpleNamespace(get_remote_server_key=lambda:key))
    probes=[]
    def execute(_client,command,**kwargs):
        commands.append(command)
        if 'sha256sum' in command:
            return hashlib.sha256(uploaded['/tmp/seiltanzer-sqlite-tools-123/sqlite3_rsync']).hexdigest()+' binary'
        if 'ORIGIN_READER_EXIT_CONFIRMED=1' in command:
            if cleanup_fails: raise RuntimeError('reader exit unknown')
            return 'ORIGIN_READER_EXIT_CONFIRMED=1\n'
        if 'statvfs' in command:
            probes.append(1)
            return json.dumps(dict(size=database.stat().st_size,
                free=0 if low_space and len(probes)>1 else 2*module.MIN_FREE_BYTES,
                wal_bytes=500+100*len(probes)))
        return ''
    monkeypatch.setattr(transport,'_exec',execute)
    monkeypatch.setattr(transport,'_verify_sha',lambda *a,**k:None)
    monkeypatch.setattr(transport,'_probe_api',lambda *a,**k:None)
    def install(path): path.write_bytes(b'pinned-tool');return path
    monkeypatch.setattr(module,'install_rsync',install)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:SimpleNamespace(free=10*module.MIN_FREE_BYTES))
    class Process:
        pid=999999
        waits=0
        def wait(self,timeout=None):
            self.waits+=1
            if low_space and self.waits==1: raise subprocess.TimeoutExpired('replica',10)
            return 0
        def poll(self): return None if low_space and self.waits==1 else 0
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(module.os,'killpg',lambda *a:None)
    return database,receipt,client,commands


def test_seeded_replication_confirms_origin_exit_before_fresh_manifest(tmp_path,monkeypatch):
    database,receipt,client,commands=replication_boundary(tmp_path,monkeypatch)
    result=module.replicate_live(client,password='test',expected_sha='b'*40,
        run_id='123',output=database,seed_receipt=receipt)
    assert result['origin_reader_exit_confirmed'] is True
    assert result['seed']['started_ts'] == 100.
    assert result['started_ts'] > 100.
    assert result['git_commit'] == 'b'*40


def test_low_disk_abort_logs_wal_and_stops_owned_reader(tmp_path,monkeypatch,capsys):
    database,receipt,client,commands=replication_boundary(tmp_path,monkeypatch,low_space=True)
    with pytest.raises(RuntimeError,match='headroom exhausted'):
        module.replicate_live(client,password='test',expected_sha='b'*40,
            run_id='123',output=database,seed_receipt=receipt)
    assert any('ORIGIN_READER_EXIT_CONFIRMED=1' in c for c in commands)
    output=capsys.readouterr().out
    assert 'free_bytes=0' in output and 'wal_bytes=700' in output and 'wal_delta_bytes=100' in output
    assert module.MIN_FREE_BYTES == 1024**3


def test_cleanup_failure_preserves_primary_disk_refusal(tmp_path,monkeypatch):
    database,receipt,client,commands=replication_boundary(tmp_path,monkeypatch,low_space=True,cleanup_fails=True)
    with pytest.raises(RuntimeError,match='headroom exhausted.*origin cleanup failed'):
        module.replicate_live(client,password='test',expected_sha='b'*40,
            run_id='123',output=database,seed_receipt=receipt)
    assert not any(c.startswith('rm -f') for c in commands)


def test_successful_copy_cannot_publish_when_reader_exit_unknown(tmp_path,monkeypatch):
    database,receipt,client,_=replication_boundary(tmp_path,monkeypatch,cleanup_fails=True)
    with pytest.raises(RuntimeError,match='origin cleanup failed'):
        module.replicate_live(client,password='test',expected_sha='b'*40,
            run_id='123',output=database,seed_receipt=receipt)


def test_sigterm_cancellation_runs_origin_cleanup_and_restores_handler(tmp_path,monkeypatch):
    import signal
    database,receipt,client,commands=replication_boundary(tmp_path,monkeypatch)
    old=signal.getsignal(signal.SIGTERM)
    class Process:
        pid=999999
        def wait(self,timeout=None):
            handler=signal.getsignal(signal.SIGTERM)
            if handler != old and callable(handler): handler(signal.SIGTERM,None)
            return 0
        def poll(self):return 0
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:Process())
    with pytest.raises(RuntimeError,match='cancelled'):
        module.replicate_live(client,password='test',expected_sha='b'*40,
            run_id='123',output=database,seed_receipt=receipt)
    assert signal.getsignal(signal.SIGTERM) == old
    assert any('ORIGIN_READER_EXIT_CONFIRMED=1' in c for c in commands)


def test_fast_copy_still_refuses_exhausted_final_headroom(tmp_path,monkeypatch):
    database,receipt,client,commands=replication_boundary(tmp_path,monkeypatch)
    import production_ede_offload as transport
    original=transport._exec
    probes=[]
    def capacity(*args,**kwargs):
        result=original(*args,**kwargs)
        if 'statvfs' in args[1]:
            probes.append(1)
            if len(probes)>1:
                stats=json.loads(result);stats['free']=0;result=json.dumps(stats)
        return result
    monkeypatch.setattr(transport,'_exec',capacity)
    with pytest.raises(RuntimeError,match='headroom exhausted'):
        module.replicate_live(client,password='test',expected_sha='b'*40,
            run_id='123',output=database,seed_receipt=receipt)


def test_actual_live_export_upload_restore_contract_round_trip(tmp_path,monkeypatch):
    import io
    import sys
    from types import SimpleNamespace
    database,receipt,client,_=replication_boundary(tmp_path,monkeypatch)
    exported=module.replicate_live(client,password='test',expected_sha='b'*40,
        run_id='123',output=database,seed_receipt=receipt)
    source_manifest=tmp_path/'source-manifest.json'
    source_manifest.write_text(json.dumps(exported))
    class Storage:
        def __init__(self):self.parts=[];self.objects={};self.metadata={}
        def get_bucket_versioning(self,**kwargs):return {}
        def create_multipart_upload(self,**kwargs):
            self.metadata=kwargs['Metadata'];return {'UploadId':'test'}
        def upload_part(self,**kwargs):
            self.parts.append(kwargs['Body']);return {'ETag':'verified'}
        def complete_multipart_upload(self,**kwargs):self.objects[kwargs['Key']]=b''.join(self.parts)
        def head_object(self,**kwargs):
            return dict(ContentLength=len(self.objects[kwargs['Key']]),Metadata=self.metadata)
        def get_object(self,**kwargs):
            body=self.objects[kwargs['Key']]
            if 'Range' in kwargs:
                first,last=map(int,kwargs['Range'][6:].split('-'));body=body[first:last+1]
            return {'Body':io.BytesIO(body)}
        def put_object(self,**kwargs):self.objects[kwargs['Key']]=kwargs['Body']
    storage=Storage()
    monkeypatch.setitem(sys.modules,'boto3',SimpleNamespace(client=lambda *a,**k:storage))
    monkeypatch.setitem(sys.modules,'botocore.config',SimpleNamespace(Config=lambda **k:None))
    import upload_verified_snapshot as uploader
    import restore_verified_snapshot as restorer
    key='backups/v1/daily-slot-4/snapshot.sqlite3.gz'
    stored=uploader.upload(database,source_manifest,bucket='trading-dbd-backups-2026',key=key)
    assert 'backup_id' not in stored and 'critical_table_counts' not in stored
    destination=tmp_path/'restored.db'
    restored=restorer.restore(bucket='trading-dbd-backups-2026',key=key,
        destination=destination,result_path=tmp_path/'restored.json',client=storage,seed_only=True)
    assert restored['full_restore_verified'] is False
    assert restored['storage_manifest']['git_commit'] == 'b'*40
    assert restored['storage_manifest']['started_ts'] == exported['started_ts']
    assert restored['database_sha256'] == exported['database_sha256']
    with sqlite3.connect(destination.resolve().as_uri()+'?immutable=1',uri=True) as conn:
        assert conn.execute('SELECT value FROM evidence').fetchall() == [('old',)]
