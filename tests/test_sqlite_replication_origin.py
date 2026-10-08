from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import time
import pytest


SCRIPT=Path(__file__).resolve().parents[1]/'scripts/sqlite_replication_origin.py'


def ticks(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]


def require_native_proc():
    matches=int(Path('/proc/self/stat').read_text().split(' ',1)[0]) == os.getpid()
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        assert matches,'Final CI must verify cleanup with a matching native /proc'
    if not matches:
        pytest.skip('local sandbox mounts a foreign /proc; native PID cleanup required in final CI')


def test_cleanup_terminates_only_owned_reader_and_confirms_exit(tmp_path):
    require_native_proc()
    binary=tmp_path/'sqlite3_rsync'
    shutil.copy2('/bin/sleep',binary)
    process=subprocess.Popen([str(binary),'30'])
    state=tmp_path/'origin.json'
    state.write_text(json.dumps(dict(pid=process.pid,start_ticks=ticks(process.pid))))
    try:
        result=subprocess.run([sys.executable,str(SCRIPT),'cleanup','--binary',str(binary),
                               '--state',str(state)],capture_output=True,text=True,timeout=15)
        assert result.returncode == 0,result.stderr
        assert 'ORIGIN_READER_EXIT_CONFIRMED=1' in result.stdout
        process.wait(timeout=1)
        assert (tmp_path/'origin.stop').exists()
    finally:
        if process.poll() is None: process.kill()
        process.wait()


def test_cleanup_does_not_kill_reused_or_unrelated_pid(tmp_path):
    require_native_proc()
    binary=tmp_path/'sqlite3_rsync'
    shutil.copy2('/bin/sleep',binary)
    process=subprocess.Popen(['/bin/sleep','30'])
    state=tmp_path/'origin.json'
    state.write_text(json.dumps(dict(pid=process.pid,start_ticks='0')))
    try:
        result=subprocess.run([sys.executable,str(SCRIPT),'cleanup','--binary',str(binary),
                               '--state',str(state)],capture_output=True,text=True,timeout=15)
        assert result.returncode == 0,result.stderr
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()


def test_origin_refuses_start_after_cleanup_marker(tmp_path):
    binary=tmp_path/'sqlite3_rsync'
    shutil.copy2('/bin/sleep',binary)
    state=tmp_path/'origin.json'
    (tmp_path/'origin.stop').write_text('stopped')
    result=subprocess.run([sys.executable,str(SCRIPT),'run','--binary',str(binary),
                           '--state',str(state),'--','0'],capture_output=True,text=True,timeout=5)
    assert result.returncode != 0
    assert 'origin stopped' in result.stderr
    assert not state.exists()


def test_origin_records_identity_before_exec(tmp_path):
    binary=tmp_path/'sqlite3_rsync'
    shutil.copy2('/bin/sleep',binary)
    state=tmp_path/'origin.json'
    result=subprocess.run([sys.executable,str(SCRIPT),'run','--binary',str(binary),
                           '--state',str(state),'--','0'],capture_output=True,text=True,timeout=5)
    assert result.returncode == 0,result.stderr
    identity=json.loads(state.read_text())
    assert identity['pid'] > 0
    assert int(identity['start_ticks']) > 0


def test_target_scan_ignores_unrelated_protected_executable(monkeypatch, tmp_path):
    import importlib.util
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('origin_control',SCRIPT)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    binary=tmp_path/'sqlite3_rsync'
    state=tmp_path/'origin.json'
    fake=SimpleNamespace(name='904',stat=lambda: SimpleNamespace(st_uid=os.geteuid()))
    monkeypatch.setattr(module,'Path',lambda path: SimpleNamespace(iterdir=lambda: iter([fake]),read_bytes=lambda: b'/usr/bin/unrelated\0'))
    def inaccessible(pid):
        raise PermissionError('protected unrelated executable')
    monkeypatch.setattr(module,'identity',inaccessible)
    assert module.targets(binary,state) == []


def test_target_scan_refuses_unverifiable_owned_candidate(monkeypatch, tmp_path):
    import importlib.util
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('origin_control',SCRIPT)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    binary=tmp_path/'sqlite3_rsync'
    state=tmp_path/'origin.json'
    fake=SimpleNamespace(name='904',stat=lambda: SimpleNamespace(st_uid=os.geteuid()))
    monkeypatch.setattr(module,'Path',lambda path: SimpleNamespace(iterdir=lambda: iter([fake]),read_bytes=lambda: str(binary).encode()+b'\0'))
    def inaccessible(pid):
        raise PermissionError('cannot verify owned executable')
    monkeypatch.setattr(module,'identity',inaccessible)
    with pytest.raises(PermissionError):
        module.targets(binary,state)
