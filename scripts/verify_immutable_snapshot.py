#!/usr/bin/env python3
"""Verify an offhost SQLite snapshot before opening it without another copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def verify(database: Path, manifest_path: Path) -> dict:
    if not database.is_file() or not manifest_path.is_file():
        raise RuntimeError('snapshot database or manifest is missing')
    if any(Path(str(database) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise RuntimeError('snapshot has mutable SQLite sidecars')
    manifest = json.loads(manifest_path.read_text())
    expected_size = int(manifest.get('database_size_bytes', -1))
    if database.stat().st_size != expected_size:
        raise RuntimeError('snapshot size does not match manifest')
    digest = hashlib.sha256()
    with database.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 ** 2), b''):
            digest.update(chunk)
    if digest.hexdigest() != str(manifest.get('database_sha256') or ''):
        raise RuntimeError('snapshot SHA256 does not match manifest')
    with sqlite3.connect(f'file:{database.resolve()}?mode=ro', uri=True, timeout=30) as conn:
        conn.execute('PRAGMA query_only=ON')
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('snapshot quick_check failed')
    database.chmod(0o444)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    manifest = verify(args.database, args.manifest)
    print('VERIFIED_IMMUTABLE_SNAPSHOT=1')
    print('VERIFIED_IMMUTABLE_SNAPSHOT_SOURCE=' + str(manifest.get('source') or manifest.get('reason') or 'UNKNOWN'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
