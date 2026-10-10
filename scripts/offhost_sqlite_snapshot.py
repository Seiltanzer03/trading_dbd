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

SQLITE_VERSION = '3530400'
SOURCE_SHA3 = 'b834d474b9b393d85a9e3ee4cc11f1329e007e9376a424ee740796f5c4bda3a8'
AMALGAMATION_SHA3 = '628a44cfe82c66aed1ccbbe85a562d2e33ebe64b3288981ed76285612227934e'
MIN_FREE_BYTES = 1024 ** 3
REPLICA_IDLE_SECONDS = 600
REPLICA_MAX_SECONDS = 2700
SOURCE_STAT_COMMAND = 'python3 -c ' + shlex.quote(
    "import json,os; p='/opt/seiltanzer/data/trades.db'; v=os.statvfs(p); "
    "print(json.dumps({'size':os.stat(p).st_size,'free':v.f_bavail*v.f_frsize,"
    "'wal_bytes':os.path.getsize(p+'-wal') if os.path.exists(p+'-wal') else 0}))"
)


def source_stats(client) -> dict:
    from production_ede_offload import _exec
    stats = json.loads(_exec(client, SOURCE_STAT_COMMAND, timeout=10).strip())
    if not isinstance(stats, dict) or any(
        type(stats.get(key)) is not int or stats[key] < 0
        for key in ('size', 'free', 'wal_bytes')
    ) or stats['size'] == 0:
        raise RuntimeError('Invalid production capacity measurement')
    return stats


def source_preflight(client, *, expected_sha: str, output: Path) -> dict:
    """Read-only admission; actual replication repeats it after seed restore."""
    from production_ede_offload import _verify_sha, _probe_api
    _verify_sha(client, expected_sha)
    _probe_api(client)
    stats = source_stats(client)
    if stats['free'] < MIN_FREE_BYTES:
        raise RuntimeError('Production lacks WAL growth headroom')
    worker_preflight(stats, output)
    return stats


def worker_preflight(stats: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # The seeded database is already resident at actual replication. Keep the
    # original worst-case rewritten WAL/journal reserve after restore as well.
    if shutil.disk_usage(output.parent).free < stats['size'] + 2 * MIN_FREE_BYTES:
        raise RuntimeError('Worker lacks space for replica and bounded headroom')


def _replica_progress(pid: int, output: Path) -> tuple[int, int, int]:
    """Observe actual worker writes, including rewrites of a pre-sized DB file."""
    written = 0
    try:
        for line in Path(f'/proc/{pid}/io').read_text().splitlines():
            if line.startswith('write_bytes:'):
                written = int(line.split(':', 1)[1])
                break
    except (OSError, ValueError):
        pass
    try:
        stat = output.stat()
        return written, stat.st_size, stat.st_mtime_ns
    except OSError:
        return written, 0, 0


def _verified_archive(product: str, digest: str) -> zipfile.ZipFile:
    url = f'https://www.sqlite.org/2026/sqlite-{product}-{SQLITE_VERSION}.zip'
    with urllib.request.urlopen(url, timeout=30) as response:
        archive = response.read(20 * 1024 ** 2)
    if hashlib.sha3_256(archive).hexdigest() != digest:
        raise RuntimeError('SQLite source archive checksum mismatch')
    return zipfile.ZipFile(io.BytesIO(archive))


def install_rsync(destination: Path) -> Path:
    # Official prebuilt tools require GLIBC_2.38, absent on Ubuntu 22.04.
    # Build the same pinned sources statically on the worker, never on the VPS.
    with tempfile.TemporaryDirectory(prefix='sqlite-rsync-build-') as temporary:
        source = Path(temporary)
        with _verified_archive('src', SOURCE_SHA3) as archive:
            (source / 'sqlite3_rsync.c').write_bytes(
                archive.read(f'sqlite-src-{SQLITE_VERSION}/tool/sqlite3_rsync.c'))
        with _verified_archive('amalgamation', AMALGAMATION_SHA3) as archive:
            for name in ('sqlite3.c', 'sqlite3.h'):
                (source / name).write_bytes(
                    archive.read(f'sqlite-amalgamation-{SQLITE_VERSION}/{name}'))
        subprocess.run([
            'cc', '-O2', '-static', '-DSQLITE_ENABLE_DBPAGE_VTAB',
            '-DSQLITE_THREADSAFE=0', '-DSQLITE_OMIT_LOAD_EXTENSION',
            '-DSQLITE_OMIT_DEPRECATED', '-I', str(source),
            str(source / 'sqlite3_rsync.c'), str(source / 'sqlite3.c'),
            '-lm', '-o', str(destination.resolve()),
        ], check=True, timeout=240)
    destination.chmod(0o700)
    subprocess.run([str(destination.resolve()), '--version'], check=True, timeout=10)
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


def verify_seed(path: Path, receipt_path: Path) -> dict:
    """Rebind an owned restore receipt to immutable worker bytes before refresh."""
    from restore_verified_snapshot import (
        LIVE_SEED_CONTRACT, manifest_digest, validate_live_seed_manifest,
    )
    if not path.is_file() or any(Path(str(path)+s).exists() for s in ('-wal','-shm')):
        raise ValueError('Seed must be a standalone database without sidecars')
    if receipt_path.stat().st_size > 1024**2:
        raise RuntimeError('Seed receipt exceeds bounded size')
    receipt = json.loads(receipt_path.read_text())
    if not isinstance(receipt,dict) or not all((
        receipt.get('restore_contract') == LIVE_SEED_CONTRACT,
        receipt.get('seed_verified') is True,
        receipt.get('full_restore_verified') is False,
        receipt.get('production_authority') is False,
        isinstance(receipt.get('storage_manifest'),dict),
    )):
        raise RuntimeError('Seed restore receipt contract mismatch')
    manifest = receipt['storage_manifest']
    validate_live_seed_manifest(manifest)
    if manifest_digest(manifest) != receipt.get('storage_manifest_sha256'):
        raise RuntimeError('Seed storage manifest digest mismatch')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):
            digest.update(chunk)
    if any(receipt.get(k) != manifest.get(k) for k in ('database_sha256','database_size_bytes')) or (
        path.stat().st_size != manifest.get('database_size_bytes') or
        digest.hexdigest() != manifest.get('database_sha256')
    ):
        raise RuntimeError('Seed database hash or size mismatch')
    connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Seed integrity check failed')
    finally:
        connection.close()
    return receipt


def replicate_live(client, *, password: str, expected_sha: str,
                   run_id: str, output: Path, seed_receipt: Path | None = None) -> dict:
    previous=signal.getsignal(signal.SIGTERM)
    def cancel(_signal,_frame):
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise RuntimeError('Live SQLite replication cancelled by SIGTERM')
    signal.signal(signal.SIGTERM,cancel)
    try:
        return _replicate_live(client,password=password,expected_sha=expected_sha,
                               run_id=run_id,output=output,seed_receipt=seed_receipt)
    finally:
        signal.signal(signal.SIGTERM,previous)


def _replicate_live(client, *, password: str, expected_sha: str,
                    run_id: str, output: Path, seed_receipt: Path | None = None) -> dict:
    from production_ede_offload import HOST, REMOTE_DATABASE, _exec, _verify_sha, _probe_api

    if not run_id.isdigit():
        raise ValueError('run_id must be numeric')
    stats = source_preflight(client, expected_sha=expected_sha, output=output)
    seed = verify_seed(output,seed_receipt) if seed_receipt is not None else None
    if seed is None and (output.exists() or any(Path(str(output) + suffix).exists() for suffix in ('-wal', '-shm'))):
        raise ValueError('Replica destination must be new')
    output.parent.mkdir(parents=True, exist_ok=True)
    stat_command = SOURCE_STAT_COMMAND
    initial_wal=stats['wal_bytes']
    initial_free=stats['free']
    def log_capacity(current):
        print(f"LIVE_SQLITE_SOURCE_CAPACITY free_bytes={current['free']} "
              f"free_delta_bytes={current['free']-initial_free} wal_bytes={current['wal_bytes']} "
              f"wal_delta_bytes={current['wal_bytes']-initial_wal}",flush=True)
    log_capacity(stats)
    remote_dir = f'/tmp/seiltanzer-sqlite-tools-{run_id}'
    remote_binary = remote_dir + '/sqlite3_rsync'
    remote_wrapper = remote_dir + '/origin'
    remote_control = remote_dir + '/control.py'
    remote_state = remote_dir + '/origin.json'
    control_source=Path(__file__).with_name('sqlite_replication_origin.py').read_text()
    process = None
    created_remote = False
    primary_error = None
    with tempfile.TemporaryDirectory(prefix='offhost-sqlite-') as temporary:
        local = Path(temporary)
        binary = install_rsync(local / 'sqlite3_rsync')
        wrapper = local / 'origin'
        wrapper.write_text('#!/bin/sh\nexec python3 ' + shlex.quote(remote_control) +
            ' run --binary ' + shlex.quote(remote_binary) + ' --state ' +
            shlex.quote(remote_state) + ' -- "$@"\n')
        control=local/'control.py'
        control.write_text(control_source)
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
                sftp.put(str(control),remote_control)
                sftp.put(str(binary), remote_binary)
                sftp.put(str(wrapper), remote_wrapper)
                sftp.chmod(remote_binary, 0o700)
                sftp.chmod(remote_wrapper, 0o700)
            remote_hash = _exec(client, 'sha256sum ' + shlex.quote(remote_binary), timeout=10).split()[0]
            if remote_hash != hashlib.sha256(binary.read_bytes()).hexdigest():
                raise RuntimeError('Uploaded SQLite executable checksum mismatch')
            _exec(client, shlex.quote(remote_wrapper) + ' --version', timeout=10)
            started = time.time()
            clock_started = time.monotonic()
            last_progress = clock_started
            last_log = clock_started
            observed = (0, 0, 0)
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
                    now = time.monotonic()
                    progress = _replica_progress(process.pid, output)
                    if progress != observed:
                        observed = progress
                        last_progress = now
                    if now - last_log >= 60:
                        print(f'LIVE_SQLITE_RSYNC_PROGRESS elapsed={int(now - clock_started)}s '
                              f'idle={int(now - last_progress)}s '
                              f'file_bytes={progress[1]} worker_write_bytes={progress[0]}',
                              flush=True)
                        last_log = now
                    if now - clock_started > REPLICA_MAX_SECONDS:
                        raise RuntimeError('Live SQLite replication exceeded 45 minutes')
                    if now - last_progress > REPLICA_IDLE_SECONDS:
                        raise RuntimeError('Live SQLite replication stalled for 10 minutes')
                    stats = json.loads(_exec(client, stat_command, timeout=10).strip())
                    if stats['free'] < MIN_FREE_BYTES:
                        log_capacity(stats)
                        raise RuntimeError('Aborting live replica: production WAL headroom exhausted')
                    if now-last_log < 1:
                        log_capacity(stats)
        except BaseException as exc:
            primary_error=exc
        finally:
            cleanup_error=None
            try:
                if process is not None and process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=10)
            except BaseException as exc:
                cleanup_error=exc
            if created_remote:
                try:
                    confirmed=_exec(client,'python3 -c ' + shlex.quote(control_source) +
                        ' cleanup --binary ' + shlex.quote(remote_binary) +
                        ' --state ' + shlex.quote(remote_state),timeout=20)
                    if 'ORIGIN_READER_EXIT_CONFIRMED=1' not in confirmed.splitlines():
                        raise RuntimeError('Origin reader exit receipt missing')
                    print('ORIGIN_READER_EXIT_CONFIRMED=1',flush=True)
                    _exec(client, 'rm -f -- ' + ' '.join(shlex.quote(p) for p in (
                        remote_binary,remote_wrapper,remote_control,remote_state,
                        remote_dir+'/origin.stop')) + ' && rmdir -- ' + shlex.quote(remote_dir),timeout=10)
                except BaseException as exc:
                    cleanup_error=exc
            if cleanup_error is not None:
                raise RuntimeError(f'{primary_error or "Live replica"}; origin cleanup failed: {cleanup_error}') from primary_error
    if primary_error is not None:
        raise primary_error
    _verify_sha(client, expected_sha)
    _probe_api(client)
    stats=json.loads(_exec(client,stat_command,timeout=10).strip())
    log_capacity(stats)
    if stats['free'] < MIN_FREE_BYTES:
        raise RuntimeError('Live replica completed but production headroom exhausted')
    manifest = {**verify_replica(output), 'source_db': str(REMOTE_DATABASE),
                'git_commit': expected_sha, 'started_ts': started,
                'completed_ts': time.time(), 'source': 'LIVE_SQLITE_RSYNC',
                'production_authority': False,'origin_reader_exit_confirmed':True}
    if seed is not None:
        original=seed['storage_manifest']
        manifest['seed']={key:original.get(key) for key in (
            'git_commit','started_ts','completed_ts','uploaded_ts',
            'database_sha256','database_size_bytes','bucket','object_key')}
        manifest['seed']['storage_manifest_sha256']=seed['storage_manifest_sha256']
    return manifest


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
