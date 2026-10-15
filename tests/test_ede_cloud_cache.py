import json
import os
import hashlib
import shlex
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ede_ledger_archive import ARCHIVE, OFFLOAD, ObjectStore


@pytest.fixture
def cached(tmp_path):
    source = tmp_path / 'ede_v13_candidate_registry.jsonl'
    original = b'{"event":"EVALUATION_RECORDED","original_clock":123}\n' * 100
    source.write_bytes(original)
    packed = source.with_name(source.name + '.gz')
    store = ObjectStore()
    manifest = ARCHIVE.upload(store, packed, ARCHIVE.prepare(source, packed, expected_sha='a'*40))
    receipt = source.with_name(source.name + '.archive.json')
    receipt.write_text(json.dumps(manifest, sort_keys=True)+'\n')
    source.unlink()
    return source, packed, receipt, manifest, store, original


def test_verified_cache_release_preserves_receipt_and_full_cloud_history(cached, tmp_path):
    source, packed, receipt, manifest, store, original = cached
    receipt_bytes = receipt.read_bytes()
    expected = packed.stat().st_size
    result = ARCHIVE.retire_cached_ledger(packed, manifest, is_closed=lambda _: True)
    assert result['retired_cache_bytes'] == expected
    assert result['all_bytes_preserved_in_cloud'] is True
    assert not packed.exists() and not source.exists()
    assert receipt.read_bytes() == receipt_bytes
    restored = tmp_path / 'restored'
    ARCHIVE.restore(store, json.loads(receipt.read_text()), restored)
    assert restored.read_bytes() == original


@pytest.mark.parametrize('problem', [
    'unverified', 'changed_receipt', 'corrupt_cache', 'new_plaintext',
    'open_cache', 'cache_symlink', 'cache_hardlink', 'receipt_symlink', 'missing_receipt'])
def test_cache_release_fails_closed_without_removing_remaining_evidence(cached, problem):
    source, packed, receipt, manifest, store, original = cached
    if problem == 'unverified':
        manifest = {**manifest, 'verified_full_get': False}
    if problem == 'changed_receipt':
        receipt.write_text(json.dumps({**manifest, 'archived_by_sha': 'b'*40}))
    if problem == 'corrupt_cache':
        packed.write_bytes(b'changed')
    if problem == 'new_plaintext':
        source.write_bytes(b'new unarchived history\n')
    if problem in {'cache_symlink', 'cache_hardlink'}:
        target = packed.with_name('other.gz')
        if problem == 'cache_symlink':
            packed.rename(target)
            packed.symlink_to(target)
        else:
            os.link(packed, target)
    if problem == 'receipt_symlink':
        target = receipt.with_name('other.json')
        receipt.rename(target)
        receipt.symlink_to(target)
    if problem == 'missing_receipt':
        receipt.unlink()
    before = packed.read_bytes()
    with pytest.raises((ValueError, OSError)):
        ARCHIVE.retire_cached_ledger(packed, manifest, is_closed=lambda _: problem != 'open_cache')
    assert packed.read_bytes() == before
    if problem == 'new_plaintext':
        assert source.read_bytes() == b'new unarchived history\n'


def test_cache_release_rechecks_receipt_and_raw_absence_after_closed_check(cached):
    source, packed, receipt, manifest, store, original = cached
    calls = 0

    def closed(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            source.write_bytes(b'new local generation\n')
        return True

    with pytest.raises(ValueError):
        ARCHIVE.retire_cached_ledger(packed, manifest, is_closed=closed)
    assert packed.exists() and source.read_bytes() == b'new local generation\n'


def test_missing_cache_is_idempotent_but_receipt_remains(cached):
    source, packed, receipt, manifest, store, original = cached
    packed.unlink()
    assert ARCHIVE.retire_cached_ledger(packed, manifest, is_closed=lambda _: True)['retired_cache_bytes'] == 0
    assert json.loads(receipt.read_text()) == manifest


def test_cloud_only_mode_rechecks_full_object_before_remote_cache_retirement(cached, monkeypatch):
    source, packed, receipt, manifest, store, original = cached
    monkeypatch.setattr(OFFLOAD, '_remote_ledger_archive_state', lambda _: {
        'plaintext': False, 'cache': True, 'manifest': manifest}, raising=False)
    events = []
    restore = ARCHIVE.restore

    def checked_restore(client, value, output):
        restore(client, value, output)
        assert Path(output).read_bytes() == original
        events.append('full_cloud_restore')

    def execute(client, command, timeout=180):
        assert events == ['full_cloud_restore']
        events.append('remote_cache_retirement')
        return '{"retired_cache_bytes":100}'

    monkeypatch.setattr(ARCHIVE, 'restore', checked_restore)
    monkeypatch.setattr(OFFLOAD, '_exec', execute)
    assert OFFLOAD._cloud_only_ledger(None, store,
        before={'size': len(original), 'sha256': manifest['source_sha256']}) is True
    assert events == ['full_cloud_restore', 'remote_cache_retirement']


def test_cloud_corruption_refuses_cache_retirement(cached, monkeypatch):
    source, packed, receipt, manifest, store, original = cached
    monkeypatch.setattr(OFFLOAD, '_remote_ledger_archive_state', lambda _: {
        'plaintext': False, 'cache': True, 'manifest': manifest}, raising=False)
    store.corrupt = True
    monkeypatch.setattr(OFFLOAD, '_exec', lambda *a, **kw: pytest.fail('must not retire cache'))
    with pytest.raises((ValueError, OSError, EOFError)):
        OFFLOAD._cloud_only_ledger(None, store,
            before={'size': len(original), 'sha256': manifest['source_sha256']})
    assert packed.exists() and json.loads(receipt.read_text()) == manifest


@pytest.mark.parametrize('state,before,expected', [
    ({'plaintext': True}, {}, False),
    ({'plaintext': False, 'cache': False, 'manifest': None},
     {'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}, True),
])
def test_legacy_and_empty_ledger_layouts_keep_explicit_paths(state, before, expected, monkeypatch):
    monkeypatch.setattr(OFFLOAD, '_remote_ledger_archive_state', lambda _: state, raising=False)
    assert OFFLOAD._cloud_only_ledger(None, None, before=before) is expected


def test_orphan_cache_without_receipt_is_not_silently_replaced(monkeypatch):
    monkeypatch.setattr(OFFLOAD, '_remote_ledger_archive_state', lambda _: {
        'plaintext': False, 'cache': True, 'manifest': None}, raising=False)
    with pytest.raises(ValueError):
        OFFLOAD._cloud_only_ledger(None, None, before={'size': 0})


def test_receipt_identity_must_match_installed_history(cached, monkeypatch):
    source, packed, receipt, manifest, store, original = cached
    monkeypatch.setattr(OFFLOAD, '_remote_ledger_archive_state', lambda _: {
        'plaintext': False, 'cache': False, 'manifest': manifest}, raising=False)
    with pytest.raises(ValueError):
        OFFLOAD._cloud_only_ledger(None, store, before={'size':len(original)+1, 'sha256':'0'*64})


@pytest.mark.parametrize('cloud_only', [True, False])
def test_publisher_stages_only_required_layout_and_keeps_exact_cloud_bytes(cached, tmp_path, monkeypatch, cloud_only):
    source, packed, receipt, old_manifest, store, original = cached
    output = tmp_path / 'output'
    output.mkdir()
    new_bytes = original + b'{"event":"NEW_EVALUATION","original_clock":456}\n'
    for name in OFFLOAD.OUTPUT_NAMES:
        (output/name).write_bytes(new_bytes if name==source.name else b'{}\n')
    files = {}

    class SFTP:
        def put(self, local, remote): files[remote] = Path(local).read_bytes()
        def posix_rename(self, old, new): files[new] = files.pop(old)
        def remove(self, name):
            if name not in files:
                raise FileNotFoundError(name)
            del files[name]
        def close(self): pass

    client = SimpleNamespace(open_sftp=lambda: SFTP(), close=lambda: None)
    monkeypatch.setattr(OFFLOAD, '_connect', lambda _: client)
    monkeypatch.setattr(OFFLOAD, '_verify_sha', lambda *a: None)
    monkeypatch.setattr(OFFLOAD, '_probe_api', lambda *a: None)
    monkeypatch.setattr(OFFLOAD, '_exec', lambda *a, **kw: '{}')
    monkeypatch.setattr(ARCHIVE, 's3_client', lambda: store)
    monkeypatch.setattr(OFFLOAD, '_remote_ledger_identity', lambda _: {
        'size':len(original), 'sha256':hashlib.sha256(original).hexdigest()})
    monkeypatch.setattr(OFFLOAD, '_cloud_only_ledger', lambda *a, **kw: cloud_only, raising=False)
    capacity = []
    monkeypatch.setattr(OFFLOAD, '_require_remote_archive_capacity', lambda _, size: capacity.append(size))
    assert OFFLOAD.publish(SimpleNamespace(output_dir=output,password='unused',expected_sha='a'*40,run_id='1')) == 0
    remote_receipt = str(OFFLOAD.REMOTE_RESEARCH/(source.name+'.archive.json'))
    remote_gzip = str(OFFLOAD.REMOTE_RESEARCH/(source.name+'.gz'))
    assert (remote_gzip in files) is (not cloud_only)
    assert list(files)[-1] == remote_receipt
    assert capacity == [sum(map(len, files.values()))]
    manifest = json.loads(files[remote_receipt])
    restored = tmp_path / 'restored-published'
    ARCHIVE.restore(store, manifest, restored)
    assert restored.read_bytes() == new_bytes


@pytest.mark.skipif(shutil.which('lsof') is None, reason='real remote closed-cache check needs lsof')
def test_real_remote_helpers_publish_cloud_only_and_next_fetch_restores_exact_history(cached, tmp_path, monkeypatch):
    source, packed, receipt, old_manifest, store, original = cached
    output = tmp_path/'output'
    output.mkdir()
    new_bytes = original + b'{"event":"NEW_EVALUATION","original_clock":456}\n'
    for name in OFFLOAD.OUTPUT_NAMES:
        (output/name).write_bytes(new_bytes if name==source.name else b'{}\n')

    class SFTP:
        def put(self, local, remote): shutil.copyfile(local, remote)
        def posix_rename(self, old, new): os.replace(old, new)
        def remove(self, name): Path(name).unlink()
        def stat(self, name): return Path(name).stat()
        def get(self, remote, local): shutil.copyfile(remote, local)
        def close(self): pass

    def execute(client, command, timeout=180):
        result = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=timeout)
        assert result.returncode == 0, result.stderr
        return result.stdout

    client = SimpleNamespace(open_sftp=lambda: SFTP(), close=lambda: None)
    monkeypatch.setattr(OFFLOAD, 'REMOTE_RESEARCH', source.parent)
    monkeypatch.setattr(OFFLOAD, '_connect', lambda _:client)
    monkeypatch.setattr(OFFLOAD, '_verify_sha', lambda *a:None)
    monkeypatch.setattr(OFFLOAD, '_probe_api', lambda *a:None)
    monkeypatch.setattr(OFFLOAD, '_exec', execute)
    monkeypatch.setattr(ARCHIVE, 's3_client', lambda:store)

    def capacity(client, size):
        assert not packed.exists()
        assert json.loads(receipt.read_text()) == old_manifest
        ARCHIVE.require_capacity(source.parent, size)

    monkeypatch.setattr(OFFLOAD, '_require_remote_archive_capacity', capacity)
    assert OFFLOAD.publish(SimpleNamespace(output_dir=output,password='unused',expected_sha='a'*40,run_id='1')) == 0
    assert not source.exists() and not packed.exists()
    published_receipt = receipt.read_bytes()
    assert json.loads(published_receipt)['source_sha256'] == hashlib.sha256(new_bytes).hexdigest()
    downloaded = tmp_path/'next-fetch'
    assert OFFLOAD.fetch_ledgers(SimpleNamespace(output_dir=downloaded,password='unused',expected_sha='a'*40)) == 0
    assert (downloaded/source.name).read_bytes() == new_bytes
    assert receipt.read_bytes() == published_receipt
