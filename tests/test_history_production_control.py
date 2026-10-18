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
