from pathlib import Path
import shutil
import sys
import shlex
import subprocess
import gzip
import ast
import inspect
import stat
import tempfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ops_yandex_closed_logs as ops


@pytest.fixture
def log_source(tmp_path, monkeypatch):
    root = tmp_path / 'log'
    journal_root = root / 'journal'
    diag_root = tmp_path / 'runner' / '_diag'
    monkeypatch.setattr(ops, 'LOG_ROOT', root)
    monkeypatch.setattr(ops, 'JOURNAL_ROOT', journal_root)
    monkeypatch.setattr(ops, 'DIAG_ROOT', diag_root)
    parent = journal_root / ('a' * 32)
    parent.mkdir(parents=True)
    source = parent / 'system@1234-5678-abcd.journal'
    source.write_bytes(b'closed forensic log\x00' * 10)
    return source


@pytest.fixture
def btmp_owner(log_source, monkeypatch):
    # Model production root ownership without requiring a root test runner.
    original_stat = Path.stat
    owner = {'uid': 0}
    paths = {ops.LOG_ROOT / 'btmp', ops.LOG_ROOT / 'btmp.1'}
    def production_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path not in paths:
            return result
        fields = {name: getattr(result, name) for name in dir(result) if name.startswith('st_')}
        fields['st_uid'] = owner['uid']
        return SimpleNamespace(**fields)
    monkeypatch.setattr(Path, 'stat', production_stat)
    return owner


def receipt(source, workspace):
    original = workspace / ops.transport_name(source)
    original.write_bytes(source.read_bytes())
    value = ops.archive.prepare(original, original.with_suffix('.gz'), expected_sha='f' * 40)
    return {**value, 'original_path': str(source), 'verified_full_get': True}


def test_retirement_requires_preserved_exact_bytes_and_rejects_active_journal(log_source, tmp_path):
    value = receipt(log_source, tmp_path)
    calls = []
    proof = ops.retire(log_source, value, is_closed=lambda p: calls.append(p) or True)
    assert len(calls) == 2
    assert proof['all_bytes_preserved_in_cloud'] is True
    assert proof['retired_bytes'] == value['source_size_bytes']
    assert not log_source.exists()
    active = log_source.with_name('system.journal')
    active.write_bytes(b'active')
    with pytest.raises(ValueError, match='Only closed'):
        ops.check_scope(active)
    assert active.exists()


@pytest.mark.parametrize('change', ['open', 'opens_later', 'wrong_hash', 'changed', 'unverified', 'wrong_path'])
def test_incomplete_or_changed_cloud_proof_preserves_source(log_source, tmp_path, change):
    value = receipt(log_source, tmp_path)
    closed = lambda p: True
    if change == 'open':
        closed = lambda p: False
    elif change == 'opens_later':
        answers = iter([True, False])
        closed = lambda p: next(answers)
    elif change == 'wrong_hash':
        value['source_sha256'] = '0' * 64
    elif change == 'changed':
        log_source.write_bytes(log_source.read_bytes() + b'new')
    elif change == 'unverified':
        value['verified_full_get'] = False
    elif change == 'wrong_path':
        value['original_path'] = '/var/log/btmp.1'
    with pytest.raises(ValueError):
        ops.retire(log_source, value, is_closed=closed)
    assert log_source.exists()


def test_symlink_and_database_paths_are_outside_scope(log_source, tmp_path):
    linked = log_source.with_name('system@dead-beef.journal')
    linked.symlink_to(log_source)
    with pytest.raises(ValueError, match='Symlinked'):
        ops.check_scope(linked)
    db = tmp_path / 'trades.db'
    db.write_bytes(b'preserve')
    with pytest.raises(ValueError, match='Only closed'):
        ops.check_scope(db)
    assert db.read_bytes() == b'preserve'


def test_failed_cloud_full_get_never_requests_remote_retirement(log_source, tmp_path, monkeypatch):
    calls = []
    def remote(client, expression):
        calls.append(expression)
        assert len(calls) == 1
        return {'signature': list(ops.archive.signature(log_source)),
                'sha256': ops.archive.digest(log_source)}
    class Sftp:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, source, destination): shutil.copyfile(source, destination)
    class Client:
        def open_sftp(self): return Sftp()
    monkeypatch.setattr(ops, 'remote', remote)
    monkeypatch.setattr(ops.archive, 's3_client', lambda: object())
    def failed(*args, **kwargs): raise RuntimeError('cloud full GET failed')
    monkeypatch.setattr(ops.archive, 'upload', failed)
    with pytest.raises(RuntimeError, match='cloud full GET failed'):
        ops.archive_one(Client(), str(log_source), 'f' * 40, tmp_path)
    assert len(calls) == 1
    assert log_source.exists()


@pytest.mark.parametrize('mode', [0o600, 0o660])
def test_btmp_rotation_preserves_inode_bytes_and_creates_new_active_file(log_source, monkeypatch, btmp_owner, mode):
    source = ops.LOG_ROOT / 'btmp'
    source.write_bytes(b'failed logins')
    source.chmod(mode)
    before = source.stat()
    monkeypatch.setattr(ops, 'utmp_gid', lambda: before.st_gid)
    def rotate(path):
        path.rename(path.with_name('btmp.1'))
        path.write_bytes(b'new logins')
        path.chmod(mode)
    rotated = ops.rotate_btmp(source, rotate=rotate, is_closed=lambda p: True)
    assert rotated.read_bytes() == b'failed logins'
    assert rotated.stat().st_ino == before.st_ino
    assert source.read_bytes() == b'new logins'


def test_btmp_rotation_refuses_existing_rotated_file_before_mutation(log_source, monkeypatch, btmp_owner):
    source = ops.LOG_ROOT / 'btmp'
    source.write_bytes(b'active')
    source.chmod(0o600)
    (ops.LOG_ROOT / 'btmp.1').write_bytes(b'prior archive')
    monkeypatch.setattr(ops, 'utmp_gid', lambda: source.stat().st_gid)
    with pytest.raises(ValueError, match='Existing rotated'):
        ops.rotate_btmp(source, rotate=lambda p: pytest.fail('must not rotate'), is_closed=lambda p: True)
    assert source.read_bytes() == b'active'


def test_btmp_rotation_refuses_nonroot_owner_before_mutation(log_source, monkeypatch, btmp_owner):
    source = ops.LOG_ROOT / 'btmp'
    source.write_bytes(b'active')
    source.chmod(0o600)
    btmp_owner['uid'] = 1001
    monkeypatch.setattr(ops, 'utmp_gid', lambda: source.stat().st_gid)
    with pytest.raises(ValueError, match='Unexpected btmp ownership'):
        ops.rotate_btmp(source, rotate=lambda p: pytest.fail('must not rotate'), is_closed=lambda p: True)
    assert source.read_bytes() == b'active'


def test_btmp_rotation_refuses_symlink(log_source):
    source = ops.LOG_ROOT / 'btmp'
    source.symlink_to(log_source)
    with pytest.raises(ValueError, match='Unsafe active'):
        ops.rotate_btmp(source, rotate=lambda p: pytest.fail('must not rotate'), is_closed=lambda p: True)
    assert log_source.exists()


def test_actual_constructed_remote_retirement_payload(log_source, tmp_path, monkeypatch):
    value = receipt(log_source, tmp_path)
    def execute(client, command, timeout):
        argv = shlex.split(command)
        code = argv[2].replace('LOG_ROOT=Path("/var/log")', f'LOG_ROOT=Path({str(ops.LOG_ROOT)!r})')
        result = subprocess.run([sys.executable, '-c', code],capture_output=True,text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout
    monkeypatch.setattr(ops, '_exec', execute)
    proof = ops.remote(None, 'def is_closed(p): return True\nmanifest=' + repr(value) +
                       '\nwrite_receipt(' + repr(str(log_source)) + ',manifest)\n' +
                       'print(json.dumps(retire(' + repr(str(log_source)) + ',manifest,is_closed=is_closed)))')
    assert proof['all_bytes_preserved_in_cloud'] is True
    assert not log_source.exists()
    assert Path(str(log_source)+'.cloud-'+value['source_sha256']+'.archive.json').is_file()


@pytest.mark.parametrize('symlink', [False, True])
def test_receipt_cannot_overwrite_other_file(log_source, tmp_path, symlink):
    value = receipt(log_source, tmp_path)
    destination = Path(str(log_source)+'.cloud-'+value['source_sha256']+'.archive.json')
    protected = tmp_path / 'trades.db'
    protected.write_bytes(b'protected data')
    if symlink:
        destination.symlink_to(protected)
    else:
        destination.write_bytes(b'prior receipt')
    with pytest.raises(FileExistsError):
        ops.write_receipt(log_source, value)
    assert protected.read_bytes() == b'protected data'
    assert log_source.exists()
    if not symlink:
        assert destination.read_bytes() == b'prior receipt'


def test_existing_compressed_btmp_is_preserved_as_exact_opaque_bytes(log_source, tmp_path):
    source = ops.LOG_ROOT / 'btmp.1.gz'
    original = gzip.compress(b'original rotated forensic log\x00' * 20, mtime=0)
    source.write_bytes(original)
    value = receipt(source, tmp_path)
    proof = ops.retire(source, value, is_closed=lambda p: True)
    assert proof['retired_bytes'] == len(original)
    packed = tmp_path / ops.transport_name(source)
    assert packed.read_bytes() == original
    assert gzip.decompress(packed.read_bytes()) == b'original rotated forensic log\x00' * 20
    assert not source.exists()


def test_actual_candidate_payload_includes_existing_compressed_btmp(log_source, monkeypatch):
    source = ops.LOG_ROOT / 'btmp.1.gz'
    source.write_bytes(gzip.compress(b'forensic bytes', mtime=0))
    def execute(client, command, timeout):
        code = shlex.split(command)[2]
        code = code.replace('LOG_ROOT=Path("/var/log")', f'LOG_ROOT=Path({str(ops.LOG_ROOT)!r})')
        code = code.replace('\nimport time\npaths=', '\ndef is_closed(p): return True\nimport time\npaths=')
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout
    monkeypatch.setattr(ops, '_exec', execute)
    assert {'path': str(source), 'size': source.stat().st_size} in ops.candidates(None)


@pytest.mark.parametrize('name', ['btmp.2.gz', 'btmp.gz', 'btmp.1.gz.link'])
def test_other_compressed_btmp_names_are_outside_scope(log_source, name):
    source = ops.LOG_ROOT / name
    source.write_bytes(b'preserved')
    with pytest.raises(ValueError, match='Only closed'):
        ops.check_scope(source)
    assert source.read_bytes() == b'preserved'


@pytest.mark.parametrize('debug_failure', [False, True])
def test_actual_rotation_callback_preflights_before_mutation_and_reports_native_failure(log_source, monkeypatch, btmp_owner, debug_failure):
    source = ops.LOG_ROOT / 'btmp'
    source.write_bytes(b'original forensic bytes')
    source.chmod(0o660)
    monkeypatch.setattr(ops, 'utmp_gid', lambda: source.stat().st_gid)
    expression = next(node.value for node in ast.walk(ast.parse(inspect.getsource(ops.main)))
                      if isinstance(node, ast.Constant) and isinstance(node.value, str)
                      and '\ndef rotate(path):' in node.value)
    callback = next(node for node in ast.parse(expression).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'rotate')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        config = Path(argv[-1]).read_text()
        assert '\nsu root utmp\n' in config
        assert '\ncreate 0660 root utmp\n' in config
        if len(calls) == 1:
            assert '--debug' in argv
            assert source.read_bytes() == b'original forensic bytes'
            return SimpleNamespace(returncode=int(debug_failure), stderr='native directory permission failure')
        assert '--debug' not in argv
        source.rename(source.with_name('btmp.1'))
        source.write_bytes(b'new active bytes')
        source.chmod(0o660)
        return SimpleNamespace(returncode=0, stderr='')
    namespace = {'Path': Path, 'stat': stat, 'tempfile': tempfile,
                 'subprocess': SimpleNamespace(run=run)}
    exec(compile(ast.Module(body=[callback], type_ignores=[]), '<actual-rotation-callback>', 'exec'), namespace)
    rotate = lambda p: ops.rotate_btmp(p, rotate=namespace['rotate'], is_closed=lambda p: True)
    if debug_failure:
        with pytest.raises(RuntimeError, match='native directory permission failure'):
            rotate(source)
        assert len(calls) == 1
        assert source.read_bytes() == b'original forensic bytes'
        assert not source.with_name('btmp.1').exists()
    else:
        rotated = rotate(source)
        assert len(calls) == 2
        assert rotated.read_bytes() == b'original forensic bytes'
        assert source.read_bytes() == b'new active bytes'
