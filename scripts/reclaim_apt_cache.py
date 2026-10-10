#!/usr/bin/env python3
"""Reclaim reproducible apt cache only, under package and production locks."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import re
import stat

LOCKS = (
    ('opt/seiltanzer/.deployment-git-maintenance.lock', True),
    ('var/lib/dpkg/lock-frontend', False),
    ('var/lib/dpkg/lock', False),
    ('var/lib/apt/lists/lock', False),
    ('var/cache/apt/archives/lock', False),
)
CACHE_DIRS = ('var/lib/apt/lists', 'var/cache/apt', 'var/cache/apt/archives')
APT_INDEX_NAME = re.compile(
    r'.+_(?:InRelease|Release(?:\.gpg)?|'
    r'(?:Packages|Sources|Translation-[A-Za-z0-9@._-]+|'
    r'Components-[A-Za-z0-9_.-]+\.yml|Commands-[A-Za-z0-9_.-]+)'
    r'(?:\.(?:lz4|gz|xz|bz2))?)'
)


def _safe_file(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
        raise RuntimeError('Unsafe package cache file or lock')


def reclaim(root: Path, *, apply: bool = False) -> dict:
    """The root argument permits isolated filesystem tests; CLI always uses /."""
    with ExitStack() as stack:
        def keep(fd):
            stack.callback(os.close, fd)
            return fd

        root_fd = keep(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))

        def directory(relative):
            current = root_fd
            try:
                for part in relative.split('/'):
                    current = keep(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                           dir_fd=current))
                    if os.fstat(current).st_uid != os.geteuid():
                        raise RuntimeError('Unsafe package cache directory owner')
                return current
            except OSError as exc:
                raise RuntimeError('Unsafe or unavailable package cache directory') from exc

        # Never create or unlink locks. apt uses POSIX record locks; deployment
        # uses flock. Both must be honored, including report-only invocations.
        for relative, production in LOCKS:
            parent, name = relative.rsplit('/', 1)
            try:
                fd = keep(os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory(parent)))
                _safe_file(os.fstat(fd))
                locking = fcntl.flock if production else fcntl.lockf
                locking(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Busy package or production operation') from exc
            except OSError as exc:
                raise RuntimeError('Unsafe or unavailable package lock') from exc

        candidates = []
        for relative in CACHE_DIRS:
            fd = directory(relative)
            for name in sorted(os.listdir(fd)):
                selected = (
                    relative == 'var/lib/apt/lists' and APT_INDEX_NAME.fullmatch(name) is not None
                    or relative == 'var/cache/apt' and name in ('pkgcache.bin', 'srcpkgcache.bin')
                    or relative == 'var/cache/apt/archives' and name.endswith('.deb')
                )
                if not selected:
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                _safe_file(info)
                candidates.append((fd, name, info))
        total = sum(info.st_size for _, _, info in candidates)
        if len(candidates) > 10000 or total > 2 * 1024 ** 3:
            raise RuntimeError('Package cache exceeds bounded cleanup scope')
        # Validate the complete set before the first mutation, and bind unlinks
        # to the opened directory descriptors rather than re-resolving paths.
        for fd, name, before in candidates:
            after = os.stat(name, dir_fd=fd, follow_symlinks=False)
            _safe_file(after)
            if (after.st_dev, after.st_ino, after.st_size) != (before.st_dev, before.st_ino, before.st_size):
                raise RuntimeError('Unsafe package cache change during preflight')
        before = os.fstatvfs(root_fd)
        removed = 0
        if apply:
            for fd, name, _ in candidates:
                os.unlink(name, dir_fd=fd)
                removed += 1
        after = os.fstatvfs(root_fd)
        return {
            'status': 'APPLIED' if apply else 'REPORT_ONLY',
            'candidate_files': len(candidates), 'candidate_bytes': total,
            'removed_files': removed,
            'free_before_bytes': before.f_bavail * before.f_frsize,
            'free_after_bytes': after.f_bavail * after.f_frsize,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and os.geteuid() != 0:
        raise RuntimeError('Package cache reclamation requires root')
    print(json.dumps(reclaim(Path('/'), apply=args.apply), sort_keys=True))


if __name__ == '__main__':
    main()
