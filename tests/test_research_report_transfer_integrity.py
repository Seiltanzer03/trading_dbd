"""A damaged upload must never replace the last usable research report."""
from __future__ import annotations

import errno
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('integrity_report_publisher', SCRIPTS / 'publish_active_edge_report.py')
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
SHA = '1' * 40


class LocalSFTP:
    """Exercise real file writes; replace only the external SSH transport."""
    def __init__(self, directory, failure):
        self.directory, self.failure = directory, failure

    def path(self, remote):
        return self.directory / Path(remote).name

    def put(self, source, remote):
        raw = Path(source).read_bytes()
        if self.failure == 'corrupt':
            raw = raw[:32768] + b'\0' + raw[32769:]
        elif self.failure == 'short':
            raw = raw[:-1]
        elif self.failure == 'extra':
            raw += b'\0'
        self.path(remote).write_bytes(raw)
        if self.failure == 'upload':
            raise OSError(errno.ENOSPC, 'upload cannot complete')

    def open(self, remote, mode='rb'):
        return self.path(remote).open(mode)

    def posix_rename(self, source, destination):
        if self.failure == 'rename':
            raise OSError(errno.ENOSPC, 'atomic rename cannot complete')
        self.path(source).replace(self.path(destination))

    def remove(self, remote):
        self.path(remote).unlink()

    def rename(self, source, destination):
        self.path(source).rename(self.path(destination))

    def close(self):
        pass


def run_publisher(tmp_path, monkeypatch, *, failure=None, run_id='9001'):
    source = tmp_path / 'new.json'
    source.write_text(json.dumps({'production_authority': False, 'padding': 'x' * 65536}))
    directory = tmp_path / 'remote'
    directory.mkdir()
    destination = directory / 'edge_family_sources_latest.json'
    destination.write_bytes(b'{"previous":"usable"}')
    transfer = LocalSFTP(directory, failure)

    class Client:
        def open_sftp(self):
            return transfer

        def close(self):
            pass

    monkeypatch.setattr(publisher, '_connect', lambda password: Client())
    monkeypatch.setattr(publisher, '_verify_sha', lambda client, sha: None)
    monkeypatch.setattr(publisher, '_probe_api', lambda client: None)
    monkeypatch.setattr(publisher, '_exec', lambda client, command: None)
    monkeypatch.setattr(sys, 'argv', ['publish', '--password', 'fixture-only',
        '--expected-sha', SHA, '--input', str(source), '--remote-name', destination.name,
        '--run-id', run_id])
    return source, destination


@pytest.mark.parametrize('failure', ['corrupt', 'short', 'extra', 'upload', 'rename'])
def test_failed_transfer_preserves_previous_bytes_and_removes_only_its_temp(tmp_path, monkeypatch, failure):
    source, destination = run_publisher(tmp_path, monkeypatch, failure=failure)
    unrelated = destination.parent / 'edge_family_sources_latest.json.tmp-other'
    unrelated.write_bytes(b'other publication')
    with pytest.raises((RuntimeError, OSError)):
        publisher.main()
    assert destination.read_bytes() == b'{"previous":"usable"}'
    assert not list(destination.parent.glob('*.tmp-9001'))
    assert unrelated.read_bytes() == b'other publication'


def test_verified_transfer_installs_complete_stamped_bytes(tmp_path, monkeypatch):
    source, destination = run_publisher(tmp_path, monkeypatch)
    assert publisher.main() == 0
    assert destination.read_bytes() == source.read_bytes()
    report = json.loads(destination.read_bytes())
    assert report['published_for_sha'] == '1' * 40
    assert report['production_authority'] is False
    assert not list(destination.parent.glob('*.tmp-*'))


@pytest.mark.parametrize('run_id', ['../other', 'x' * 81])
def test_run_id_cannot_escape_owned_temporary_path(tmp_path, monkeypatch, run_id):
    _, destination = run_publisher(tmp_path, monkeypatch, run_id=run_id)
    with pytest.raises(RuntimeError, match='run_id'):
        publisher.main()
    assert destination.read_bytes() == b'{"previous":"usable"}'
