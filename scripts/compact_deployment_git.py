#!/usr/bin/env python3
"""Reconstruct deployment Git metadata from canonical shallow roots, never data.

Linux rename exchange keeps .git continuously present. All refs must first be
fetchable from the canonical repository; unpublished local work fails closed.
Report-only is the default. Production callers serialize with deployment.
"""
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def git(repo, *args, git_dir=None):
    command = ['git', '-c', f'safe.directory={repo}', '-c', 'core.fsmonitor=false']
    command += ['--git-dir', str(git_dir), '--work-tree', str(repo)] if git_dir else ['-C', str(repo)]
    result = subprocess.run(command + list(args), capture_output=True, text=True,
                            timeout=180, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    if result.returncode:
        # Fetch stderr can include a credential-bearing URL. Never publish it.
        raise RuntimeError('CANONICAL_FETCH_FAILED' if args[0] == 'fetch' else 'GIT_VALIDATION_FAILED')
    return result.stdout.strip()


def exchange(left, right):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2):
        raise OSError(ctypes.get_errno(), 'ATOMIC_GIT_EXCHANGE_FAILED')
    fd = os.open(left.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def size(root):
    return sum(p.stat().st_blocks * 512 for p in root.rglob('*') if p.is_file())


def inspect(repo, expected_sha):
    metadata = repo / '.git'
    if not metadata.is_dir() or metadata.is_symlink() or any(
            (metadata / name).exists() for name in ('commondir', 'objects/info/alternates', 'worktrees')):
        raise RuntimeError('UNSAFE_GIT_METADATA')
    if not re.fullmatch('[0-9a-f]{40}', expected_sha) or git(repo, 'rev-parse', 'HEAD') != expected_sha:
        raise RuntimeError('SHA_MISMATCH')
    if git(repo, 'status', '--porcelain', '--untracked-files=no'):
        raise RuntimeError('DIRTY_TRACKED_FILES')
    refs = dict(line.split(' ', 1)[::-1] for line in git(repo, 'show-ref').splitlines())
    if len(set(refs.values()) | {expected_sha}) > 8:
        raise RuntimeError('TOO_MANY_GIT_ROOTS')
    if any(metadata.rglob('*.lock')):
        raise RuntimeError('GIT_OPERATION_IN_PROGRESS')
    return refs, (metadata / 'HEAD').read_bytes(), (metadata / 'config').read_bytes()


def compact(repo, origin, expected_sha, apply=False):
    repo = repo.resolve(strict=True)
    refs, head, config = inspect(repo, expected_sha)
    metadata = repo / '.git'
    before = size(metadata)
    receipt = {'status': 'REPORT_ONLY', 'head_sha': expected_sha, 'git_bytes_before': before,
               'ref_count': len(refs), 'application_files_modified': False,
               'database_modified': False, 'production_authority': False}
    if not apply:
        return receipt
    with (repo / '.deployment-git-maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        roots = sorted(set(refs.values()) | {expected_sha})
        blobs = {}
        for sha in roots:
            for entry in git(repo, 'ls-tree', '-rl', '-z', sha).split('\0'):
                fields = entry.split('\t', 1)[0].split()
                if len(fields) == 4 and fields[1] == 'blob':
                    blobs[fields[2]] = int(fields[3])
        if shutil.disk_usage(repo).free < 2 * sum(blobs.values()) + 64 * 1024 * 1024:
            raise RuntimeError('INSUFFICIENT_STAGING_SPACE')
        stage = Path(tempfile.mkdtemp(prefix='.git.compact-', dir=repo))
        swapped = False
        try:
            git(repo, 'init', '--bare', str(stage))
            for sha in roots:
                git(repo, 'fetch', '--depth=1', '--no-tags', origin, sha, git_dir=stage)
                if git(repo, 'rev-parse', 'FETCH_HEAD', git_dir=stage) != sha:
                    raise RuntimeError('CANONICAL_SHA_MISMATCH')
            for ref, sha in refs.items():
                git(repo, 'update-ref', ref, sha, git_dir=stage)
            (stage / 'HEAD').write_bytes(head)
            (stage / 'config').write_bytes(config)
            (stage / 'config').chmod(0o600)
            git(repo, 'read-tree', expected_sha, git_dir=stage)
            git(repo, 'fsck', '--full', '--no-reflogs', git_dir=stage)
            if git(repo, 'status', '--porcelain', '--untracked-files=no', git_dir=stage):
                raise RuntimeError('STAGED_TREE_MISMATCH')
            if inspect(repo, expected_sha) != (refs, head, config):
                raise RuntimeError('GIT_CHANGED_DURING_REPAIR')
            after = size(stage)
            if after >= before:
                receipt['status'] = 'NO_REDUCTION'
                return receipt
            exchange(metadata, stage)
            swapped = True
            if inspect(repo, expected_sha) != (refs, head, config):
                raise RuntimeError('POST_SWAP_VALIDATION_FAILED')
            # All old ref objects were proven remotely reconstructible. Only the
            # exchanged metadata directory is disposable, never any sibling.
            # Once deletion starts, never roll back to partially removed old
            # metadata. The fully verified new repository is now authoritative.
            swapped = False
            shutil.rmtree(stage)
            receipt.update(status='COMPACTED', git_bytes_after=after, bytes_removed=before-after)
            return receipt
        except BaseException:
            if swapped and stage.exists():
                exchange(metadata, stage)
                swapped = False
            raise
        finally:
            if not swapped and stage.exists():
                shutil.rmtree(stage)


def stage_bundle(repo, bundle, expected_sha):
    with (repo / '.deployment-git-maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _stage_bundle(repo, bundle, expected_sha)


def _stage_bundle(repo, bundle, expected_sha):
    repo = repo.resolve(strict=True)
    running_sha = git(repo, 'rev-parse', 'HEAD')
    inspect(repo, running_sha)
    if not re.fullmatch('[0-9a-f]{40}', expected_sha):
        raise RuntimeError('SHA_MISMATCH')
    if git(repo, 'bundle', 'list-heads', str(bundle)) != expected_sha + ' refs/deploy/exact':
        raise RuntimeError('BUNDLE_SHA_MISMATCH')
    git(repo, 'bundle', 'verify', str(bundle))
    shallow = repo / '.git' / 'shallow'
    old = shallow.read_bytes() if shallow.exists() else None
    roots = set((old or b'').decode('ascii').splitlines()) | {expected_sha}
    temporary = shallow.with_name('shallow.compact-tmp')
    try:
        with temporary.open('x') as handle:
            handle.write('\n'.join(sorted(roots))+'\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, shallow)
        git(repo, 'fetch', '--no-tags', str(bundle), '+refs/deploy/exact:refs/deploy/staged')
        if git(repo, 'rev-parse', 'HEAD') != running_sha:
            raise RuntimeError('RUNNING_HEAD_CHANGED')
        git(repo, 'cat-file', '-e', expected_sha+'^{commit}')
    finally:
        # A failed/interrupted fetch may already have imported this root. Its
        # parent boundary must survive even if refs/FETCH_HEAD were updated;
        # rolling back only shallow would corrupt the resulting object graph.
        temporary.unlink(missing_ok=True)
    return {'status': 'STAGED', 'staged_sha': expected_sha, 'running_sha': running_sha,
            'application_files_modified': False, 'database_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--origin', default='https://github.com/Seiltanzer03/trading_dbd.git')
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--stage-bundle', type=Path)
    args = parser.parse_args()
    try:
        receipt = (stage_bundle(args.repo, args.stage_bundle, args.expected_sha) if args.stage_bundle
                   else compact(args.repo, args.origin, args.expected_sha, args.apply))
        print(json.dumps(receipt, sort_keys=True))
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'STOP: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
