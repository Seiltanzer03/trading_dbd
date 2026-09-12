"""Consistent live SQLite replication onto a worker, without a second VPS DB.

The origin is fixed to production's authoritative database. Only run-scoped
executables are installed on the VPS; data files are never removed here.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import zipfile

TOOLS_URL = 'https://www.sqlite.org/2026/sqlite-tools-linux-x64-3530400.zip'
TOOLS_SHA3 = '6eeb57e8f2aef7687f9f016a980992cf2799c8c07a87c5e21495530f91915047'
MIN_FREE_BYTES = 1024 ** 3


def install_rsync(destination: Path) -> Path:
    with urllib.request.urlopen(TOOLS_URL, timeout=30) as response:
        archive = response.read(8 * 1024 ** 2)
    if hashlib.sha3_256(archive).hexdigest() != TOOLS_SHA3:
        raise RuntimeError('SQLite tools archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        executable = package.read('sqlite3_rsync')
    destination.write_bytes(executable)
    destination.chmod(0o700)
    return destination


def verify_replica(path: Path) -> dict:
    # Finish the worker's replica WAL before publishing a standalone DB file.
    with sqlite3.connect(path) as conn:
        checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if checkpoint[0] != 0:
            raise RuntimeError('Replica checkpoint is busy')
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Replica integrity check failed')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 ** 2), b''):
            digest.update(chunk)
    return {'database_sha256': digest.hexdigest(), 'database_size_bytes': path.stat().st_size}


def replicate_live(client, *, password: str, expected_sha: str,
                   run_id: str, output: Path) -> dict:
    from production_ede_offload import HOST, REMOTE_DATABASE, _exec, _verify_sha, _probe_api

    if not run_id.isdigit():
        raise ValueError('run_id must be numeric')
    if output.exists() or any(Path(str(output) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('Replica destination must be new')
    output.parent.mkdir(parents=True, exist_ok=True)
    _verify_sha(client, expected_sha)
    _probe_api(client)
    stat_command = (
        "python3 -c " + shlex.quote(
            "import json,os; p='/opt/seiltanzer/data/trades.db'; v=os.statvfs(p); "
            "print(json.dumps({'size':os.stat(p).st_size,'free':v.f_bavail*v.f_frsize}))"
        )
    )
    stats = json.loads(_exec(client, stat_command, timeout=10).strip())
    # Reserve room for the replica plus bounded worker headroom.
    if shutil.disk_usage(output.parent).free < stats['size'] + 2 * MIN_FREE_BYTES:
        raise RuntimeError('Worker lacks space for replica and bounded headroom')
    if stats['free'] < MIN_FREE_BYTES:
        raise RuntimeError('Production lacks WAL growth headroom')
    remote_dir = f'/tmp/seiltanzer-sqlite-tools-{run_id}'
    remote_binary = remote_dir + '/sqlite3_rsync'
    remote_wrapper = remote_dir + '/origin'
    process = None
    created_remote = False
    with tempfile.TemporaryDirectory(prefix='offhost-sqlite-') as temporary:
        local = Path(temporary)
        binary = install_rsync(local / 'sqlite3_rsync')
        wrapper = local / 'origin'
        wrapper.write_text('#!/bin/sh\nexec nice -n 19 ionice -c 3 ' +
                           shlex.quote(remote_binary) + ' "$@"\n')
        host_key = client.get_transport().get_remote_server_key()
        known_hosts = local / 'known_hosts'
        host_key_type = host_key.get_name()
        known_hosts.write_text(f'{HOST} {host_key_type} {host_key.get_base64()}\n')
        askpass = local / 'askpass'
        askpass.write_text('#!/bin/sh\nprintf %s "$SQLITE_RSYNC_SSH_PASSWORD"\n')
        askpass.chmod(0o700)
        ssh = local / 'ssh'
        ssh.write_text(
            '#!/bin/sh\nexport SSH_ASKPASS_REQUIRE=force DISPLAY=:0\n'
            'exec setsid -w ssh -o StrictHostKeyChecking=yes '
            '-o ServerAliveInterval=15 -o ServerAliveCountMax=2 '
            '-o HostKeyAlgorithms=' + shlex.quote(host_key_type) + ' '
            '-o UserKnownHostsFile=' + shlex.quote(str(known_hosts)) + ' "$@"\n')
        ssh.chmod(0o700)
        try:
            _exec(client, 'mkdir -m 700 ' + shlex.quote(remote_dir), timeout=10)
            created_remote = True
            with client.open_sftp() as sftp:
                sftp.put(str(binary), remote_binary)
                sftp.put(str(wrapper), remote_wrapper)
                sftp.chmod(remote_binary, 0o700)
                sftp.chmod(remote_wrapper, 0o700)
            remote_hash = _exec(client, 'sha256sum ' + shlex.quote(remote_binary), timeout=10).split()[0]
            if remote_hash != hashlib.sha256(binary.read_bytes()).hexdigest():
                raise RuntimeError('Uploaded SQLite executable checksum mismatch')
            started = time.time()
            process = subprocess.Popen(
                [str(binary), f'root@{HOST}:{REMOTE_DATABASE}', str(output.resolve()),
                 '--exe', remote_wrapper, '--ssh', str(ssh), '-v'],
                env={**os.environ, 'SQLITE_RSYNC_SSH_PASSWORD': password,
                     'SSH_ASKPASS': str(askpass)}, start_new_session=True,
            )
            while True:
                try:
                    status = process.wait(timeout=10)
                    if status != 0:
                        raise RuntimeError(f'Live SQLite replication failed: exit {status}')
                    break
                except subprocess.TimeoutExpired:
                    if time.time() - started > 1200:
                        raise RuntimeError('Live SQLite replication exceeded 20 minutes')
                    stats = json.loads(_exec(client, stat_command, timeout=10).strip())
                    if stats['free'] < MIN_FREE_BYTES:
                        raise RuntimeError('Aborting live replica: production WAL headroom exhausted')
            _verify_sha(client, expected_sha)
            _probe_api(client)
            return {**verify_replica(output), 'source_db': str(REMOTE_DATABASE),
                    'git_commit': expected_sha, 'started_ts': started,
                    'completed_ts': time.time(), 'source': 'LIVE_SQLITE_RSYNC',
                    'production_authority': False}
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            if created_remote:
                _exec(client, 'rm -f -- ' + shlex.quote(remote_binary) + ' ' +
                      shlex.quote(remote_wrapper) + ' && rmdir -- ' + shlex.quote(remote_dir), timeout=10)


def main():
    import argparse
    from production_ede_offload import _connect
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output-db', type=Path, required=True)
    args = parser.parse_args()
    password = os.environ.get('SSH_PASSWORD')
    if not password:
        parser.error('SSH_PASSWORD environment variable is required')
    client = _connect(password)
    try:
        manifest = replicate_live(client, password=password, expected_sha=args.expected_sha,
                                  run_id=args.run_id, output=args.output_db)
        args.output_db.with_suffix(args.output_db.suffix + '.manifest.json').write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    finally:
        client.close()


if __name__ == '__main__':
    main()
