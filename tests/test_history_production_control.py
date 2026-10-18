"""Host ownership and descriptor inventory use actual locks and files."""
import fcntl
import importlib
import os
from pathlib import Path

import pytest


def test_host_owner_rejects_noncanonical_root_before_service_control(tmp_path):
    module=importlib.import_module('scripts.history_production_control')
    with pytest.raises(ValueError,match='CANONICAL_PRODUCTION_ROOT_REQUIRED'):
        with module.production_control(tmp_path,application_sha='a'*40):
            pytest.fail('Noncanonical root acquired production control')


def test_live_descriptor_inventory_detects_readers_and_writers(tmp_path):
    module=importlib.import_module('scripts.history_production_control')
    # Native CI must prove scanning the actual PID namespace. The local CUA
    # executor may expose a foreign /proc and cannot claim that proof.
    from test_sqlite_replication_origin import require_native_proc
    require_native_proc()
    source=tmp_path/'trades.db';source.write_bytes(b'actual-source')
    with source.open('rb') as reader:
        result=module.database_handles(source,exclude_pid=None)
        assert any(item['pid']==os.getpid() and item['fd']==reader.fileno()
                   and not item['writable'] for item in result)
    with source.open('r+b') as writer:
        assert any(item['pid']==os.getpid() and item['fd']==writer.fileno()
                   and item['writable'] for item in module.database_handles(source,exclude_pid=None))
    assert module.database_handles(source,exclude_pid=None) == []


def test_native_launch_command_has_finite_independent_systemd_lifetime():
    module=importlib.import_module('scripts.history_production_control')
    command=module.launch_command(run_id='run-001',application_sha='a'*40)
    assert command[0] == '/usr/bin/systemd-run'
    assert '--no-block' in command
    assert '--property=RuntimeMaxSec=10800' in command
    assert '--property=KillMode=control-group' in command
    assert '--property=Restart=no' in command
    assert '--unit=seiltanzer-history-run-001.service' in command
    assert not any('PRIVATE' in item or 'Password' in item for item in command)
    with pytest.raises(ValueError):module.launch_command(run_id='../escape',application_sha='a'*40)


@pytest.mark.parametrize('name',['trades.db','cutover.transport.json','cutover.preparation.json'])
def test_wrong_uid_owned_input_is_refused_before_fence(tmp_path,name,monkeypatch):
    module=importlib.import_module('scripts.history_production_control')
    data=tmp_path/'data';data.mkdir(mode=0o700)
    (data/'trades.db').write_bytes(b'original')
    target=data/name;target.write_bytes(b'private');target.chmod(0o600)
    monkeypatch.setattr(module,'OWNER_UID',os.geteuid(),raising=False)
    real=Path.lstat
    def lstat(path,*args,**kwargs):
        info=real(path,*args,**kwargs)
        if path!=target:return info
        values=list(info);values[4]=65534
        return os.stat_result(values)
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(ValueError,match='OWNER'):module.validate_owned_data(data)


def test_changed_input_owner_is_revalidated_at_every_fence(tmp_path,monkeypatch):
    module=importlib.import_module('scripts.history_production_control')
    root=tmp_path/'host';root.mkdir();data=root/'data';data.mkdir(mode=0o700)
    source=data/'trades.db';source.write_bytes(b'original')
    (data/'cutover.active').write_text('guard');(data/'cutover.active').chmod(0o600)
    lock=root/'.deployment-git-maintenance.lock';lock.touch()
    monkeypatch.setattr(module,'ROOT',root);monkeypatch.setattr(module,'DATABASE',source)
    monkeypatch.setattr(module,'OWNER_UID',os.geteuid(),raising=False)
    with lock.open('r+b') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        control=object.__new__(module.ProductionControl)
        control.lock_fd=stream.fileno();control.lock_identity=os.fstat(stream.fileno())
        control.cgroup=tmp_path/'missing-cgroup'
        assert control.assert_fenced() is True
        real=Path.lstat
        def lstat(path,*args,**kwargs):
            info=real(path,*args,**kwargs)
            if path!=source:return info
            values=list(info);values[4]=65534
            return os.stat_result(values)
        monkeypatch.setattr(Path,'lstat',lstat)
        with pytest.raises(ValueError,match='OWNER'):control.assert_fenced()


def test_data_root_must_be_private(tmp_path):
    module=importlib.import_module('scripts.history_production_control')
    data=tmp_path/'data';data.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='DIRECTORY'):module.validate_owned_data(data)
