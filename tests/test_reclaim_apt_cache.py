from __future__ import annotations

import fcntl
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


def cache_tree(root: Path):
    for directory in ('var/lib/apt/lists', 'var/cache/apt/archives',
                      'var/lib/dpkg', 'opt/seiltanzer'):
        (root / directory).mkdir(parents=True)
    for path in ('var/lib/apt/lists/lock', 'var/cache/apt/archives/lock',
                 'var/lib/dpkg/lock', 'var/lib/dpkg/lock-frontend',
                 'opt/seiltanzer/.deployment-git-maintenance.lock'):
        (root / path).touch()
    metadata = root / 'var/lib/apt/lists/example_InRelease'
    metadata.write_bytes(b'signed reproducible index')
    binary = root / 'var/cache/apt/pkgcache.bin'
    binary.write_bytes(b'generated package index')
    deb = root / 'var/cache/apt/archives/example_1_amd64.deb'
    deb.write_bytes(b'downloadable package archive')
    retained = root / 'var/cache/apt/archives/unrelated.json'
    retained.write_bytes(b'unknown content')
    partial = root / 'var/lib/apt/lists/partial'
    partial.mkdir()
    (partial / 'pending').write_bytes(b'incomplete apt transaction')
    history = root / 'opt/seiltanzer/data/trades.db'
    history.parent.mkdir()
    history.write_bytes(b'retained trading history')
    return metadata, binary, deb, retained, partial, history


def reclaim(root: Path, apply=False):
    module = importlib.import_module('scripts.reclaim_apt_cache')
    return module.reclaim(root, apply=apply)


def test_cleanup_removes_only_reconstructible_package_files(tmp_path):
    metadata, binary, deb, retained, partial, history = cache_tree(tmp_path)
    planned = reclaim(tmp_path)
    assert planned['removed_files'] == 0
    assert all(p.exists() for p in (metadata, binary, deb, retained, partial, history))
    result = reclaim(tmp_path, apply=True)
    assert result['removed_files'] == 3
    assert not any(p.exists() for p in (metadata, binary, deb))
    assert retained.read_bytes() == b'unknown content'
    assert (partial / 'pending').read_bytes() == b'incomplete apt transaction'
    assert history.read_bytes() == b'retained trading history'
    assert (tmp_path / 'var/lib/apt/lists/lock').exists()


def test_unknown_files_in_package_lists_are_preserved(tmp_path):
    cache_tree(tmp_path)
    lists = tmp_path / 'var/lib/apt/lists'
    unknown = [lists / name for name in ('unrelated.json', 'manual-backup', 'operator-note.txt')]
    for path in unknown:
        path.write_bytes(b'unknown operator content')
    assert reclaim(tmp_path, apply=True)['removed_files'] == 3
    assert all(path.read_bytes() == b'unknown operator content' for path in unknown)


def test_known_compressed_apt_indexes_are_reconstructible(tmp_path):
    cache_tree(tmp_path)
    lists = tmp_path / 'var/lib/apt/lists'
    for name in ('mirror_dists_jammy_Release.gpg',
                 'mirror_dists_jammy_main_binary-amd64_Packages.lz4',
                 'mirror_dists_jammy_main_i18n_Translation-en.lz4',
                 'mirror_dists_jammy_main_dep11_Components-amd64.yml.gz',
                 'mirror_dists_jammy_main_cnf_Commands-amd64.lz4'):
        (lists / name).write_bytes(b'redownloadable index')
    assert reclaim(tmp_path, apply=True)['removed_files'] == 8


@pytest.mark.parametrize('kind', ['entry_symlink', 'parent_symlink', 'hardlink'])
def test_unsafe_path_is_refused_before_any_cache_removal(tmp_path, kind):
    metadata, binary, _, _, _, history = cache_tree(tmp_path)
    if kind == 'entry_symlink':
        metadata.unlink()
        metadata.symlink_to(history)
    elif kind == 'parent_symlink':
        directory = tmp_path / 'var/cache/apt'
        saved = tmp_path / 'saved-apt'
        directory.rename(saved)
        directory.symlink_to(saved, target_is_directory=True)
    else:
        metadata.unlink()
        os.link(history, metadata)
    with pytest.raises(RuntimeError, match='unsafe|Unsafe'):
        reclaim(tmp_path, apply=True)
    assert binary.exists()
    assert history.read_bytes() == b'retained trading history'


@pytest.mark.parametrize('path,mode', [
    ('var/lib/apt/lists/lock', 'record'),
    ('opt/seiltanzer/.deployment-git-maintenance.lock', 'flock'),
])
def test_concurrent_package_or_production_operation_prevents_removal(tmp_path, path, mode):
    metadata, binary, _, _, _, _ = cache_tree(tmp_path)
    code = ('import fcntl,sys; f=open(sys.argv[1],"r+"); '
            + ('fcntl.lockf(f,fcntl.LOCK_EX); ' if mode == 'record' else 'fcntl.flock(f,fcntl.LOCK_EX); ')
            + 'print("held",flush=True); sys.stdin.read()')
    process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path / path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'held'
        with pytest.raises(RuntimeError, match='busy|Busy'):
            reclaim(tmp_path, apply=True)
        assert metadata.exists() and binary.exists()
    finally:
        process.communicate('', timeout=5)
