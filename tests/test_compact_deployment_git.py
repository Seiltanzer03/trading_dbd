"""Real Git repositories: metadata repair must never rewrite application state."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path('scripts/compact_deployment_git.py').resolve()


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


@pytest.fixture
def deployment(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / 'obsolete.bin').write_bytes(os.urandom(1024 * 1024))
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'old artifact')
    (source / 'obsolete.bin').unlink()
    (source / 'app.py').write_text('print("running")\n')
    (source / '.gitignore').write_text('data/\n')
    git(source, 'add', '-A')
    git(source, 'commit', '-qm', 'current application')
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'clone', '--bare', str(source), str(origin)], check=True, capture_output=True)
    repo = tmp_path / 'production'
    subprocess.run(['git', 'clone', origin.as_uri(), str(repo)], check=True, capture_output=True)
    (repo / 'data').mkdir()
    (repo / 'data' / 'trades.db').write_bytes(b'authoritative history')
    return repo, origin.as_uri(), git(repo, 'rev-parse', 'HEAD')


def run(deployment, *extra):
    repo, origin, sha = deployment
    return subprocess.run([sys.executable, str(SCRIPT), '--repo', str(repo),
                           '--origin', origin, '--expected-sha', sha, *extra],
                          text=True, capture_output=True)


def test_replaces_only_reconstructible_git_history_and_preserves_deployment(deployment):
    repo, origin, sha = deployment
    before = sum(p.stat().st_size for p in (repo / '.git').rglob('*') if p.is_file())
    result = run(deployment, '--apply')
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['status'] == 'COMPACTED'
    assert receipt['bytes_removed'] > 900_000
    assert git(repo, 'rev-parse', 'HEAD') == sha
    assert git(repo, 'remote', 'get-url', 'origin') == origin
    assert git(repo, 'symbolic-ref', 'HEAD') == 'refs/heads/main'
    assert git(repo, 'status', '--porcelain', '--untracked-files=no') == ''
    assert (repo / 'app.py').read_text() == 'print("running")\n'
    assert (repo / 'data' / 'trades.db').read_bytes() == b'authoritative history'
    assert (repo / '.git' / 'shallow').is_file()
    assert not list(repo.glob('.git.compact-*'))
    assert sum(p.stat().st_size for p in (repo / '.git').rglob('*') if p.is_file()) < before / 5


def test_deploy_fetch_does_not_reintroduce_discarded_code_history(deployment):
    repo, origin, _ = deployment
    assert run(deployment, '--apply').returncode == 0
    publisher = repo.parent / 'publisher'
    subprocess.run(['git', 'clone', origin, str(publisher)], check=True, capture_output=True)
    git(publisher, 'config', 'user.name', 'Publisher')
    git(publisher, 'config', 'user.email', 'publisher@example.invalid')
    (publisher / 'app.py').write_text('print("next release")\n')
    git(publisher, 'add', '.')
    git(publisher, 'commit', '-qm', 'next release')
    git(publisher, 'push', 'origin', 'main')
    workflow = Path('.github/workflows/deploy.yml').read_text()
    line = next(line.strip() for line in workflow.splitlines()
                if line.strip().startswith('git fetch ') and 'https://github.com/' in line)
    command = line.replace('https://github.com/Seiltanzer03/trading_dbd.git', origin)
    result = subprocess.run(['bash', '-c', command], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert git(repo, 'rev-list', '--count', 'FETCH_HEAD') == '1'


def test_default_report_does_not_modify_git_or_application(deployment):
    repo, _, _ = deployment
    index = (repo / '.git' / 'index').read_bytes()
    result = run(deployment)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'REPORT_ONLY'
    assert (repo / '.git' / 'index').read_bytes() == index
    assert not (repo / '.git' / 'shallow').exists()


def test_stage_shallow_bundle_keeps_running_head_and_does_not_restore_old_history(deployment):
    repo, origin, running_sha = deployment
    assert run(deployment, '--apply').returncode == 0
    publisher = repo.parent / 'stage-publisher'
    subprocess.run(['git', 'clone', '--depth=1', origin, str(publisher)], check=True, capture_output=True)
    git(publisher, 'config', 'user.name', 'Publisher')
    git(publisher, 'config', 'user.email', 'publisher@example.invalid')
    (publisher / 'app.py').write_text('print("staged")\n')
    git(publisher, 'add', '.')
    git(publisher, 'commit', '-qm', 'staged application')
    (publisher / 'app.py').write_text('print("second staged revision")\n')
    git(publisher, 'add', '.')
    git(publisher, 'commit', '-qm', 'second staged revision')
    staged_sha = git(publisher, 'rev-parse', 'HEAD')
    # Actual workflow checkout is one commit deep, including the staged SHA.
    (publisher / '.git' / 'shallow').write_text(staged_sha + '\n')
    git(publisher, 'update-ref', 'refs/deploy/exact', staged_sha)
    bundle = repo.parent / 'staged.bundle'
    git(publisher, 'bundle', 'create', str(bundle), 'refs/deploy/exact')
    result = subprocess.run([sys.executable, str(SCRIPT), '--repo', str(repo),
                             '--expected-sha', staged_sha, '--stage-bundle', str(bundle)],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert git(repo, 'rev-parse', 'HEAD') == running_sha
    assert git(repo, 'rev-parse', 'refs/deploy/staged') == staged_sha
    assert git(repo, 'rev-list', '--count', 'refs/deploy/staged') == '1'
    assert (repo / 'app.py').read_text() == 'print("running")\n'
    assert (repo / 'data' / 'trades.db').read_bytes() == b'authoritative history'
    git(repo, 'fsck', '--full')

    # A process can be interrupted after git imported objects and updated refs.
    # Restoring the old shallow boundary then leaves a corrupt Git graph.
    spec = importlib.util.spec_from_file_location('capacity_script', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_git = module.git
    def interrupt_after_import(path, *args, **kwargs):
        value = real_git(path, *args, **kwargs)
        if args[0] == 'fetch':
            raise KeyboardInterrupt()
        return value
    module.git = interrupt_after_import
    # Restore the original boundary to reproduce a first import interruption.
    (repo / '.git' / 'shallow').write_text(running_sha+'\n')
    git(repo, 'update-ref', '-d', 'refs/deploy/staged')
    with pytest.raises(KeyboardInterrupt):
        module.stage_bundle(repo, bundle, staged_sha)
    git(repo, 'fsck', '--full')
    assert git(repo, 'rev-parse', 'HEAD') == running_sha


@pytest.mark.parametrize('unsafe', ['dirty', 'wrong_sha', 'unpublished_ref', 'linked_metadata'])
def test_refuses_unsafe_replacement_and_retains_original(deployment, unsafe):
    repo, origin, sha = deployment
    if unsafe == 'dirty':
        (repo / 'app.py').write_text('local uncommitted work\n')
    elif unsafe == 'wrong_sha':
        deployment = (repo, origin, '0' * 40)
    elif unsafe == 'unpublished_ref':
        git(repo, 'config', 'user.name', 'Local')
        git(repo, 'config', 'user.email', 'local@example.invalid')
        git(repo, 'checkout', '-b', 'unpublished')
        (repo / 'private.py').write_text('local only\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-qm', 'private local work')
        git(repo, 'checkout', 'main')
    else:
        metadata = repo / '.git'
        moved = repo.parent / 'shared.git'
        metadata.rename(moved)
        metadata.symlink_to(moved, target_is_directory=True)
    before_head = git(repo, 'rev-parse', 'HEAD')
    result = run(deployment, '--apply')
    assert result.returncode != 0
    assert {'dirty': 'DIRTY_TRACKED_FILES', 'wrong_sha': 'SHA_MISMATCH',
            'unpublished_ref': 'CANONICAL_FETCH_FAILED',
            'linked_metadata': 'UNSAFE_GIT_METADATA'}[unsafe] in result.stderr
    assert git(repo, 'rev-parse', 'HEAD') == before_head == sha
    assert (repo / 'data' / 'trades.db').read_bytes() == b'authoritative history'
    assert not list(repo.glob('.git.compact-*'))
