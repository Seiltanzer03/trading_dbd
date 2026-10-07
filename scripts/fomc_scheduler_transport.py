#!/usr/bin/env python3
"""Dedicated FOMC forced-command transport; never grants shell or SFTP access."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from seiltanzer.fomc_prospective_capture import (  # noqa: E402
    CONTRACT, MAX_AGE_SEC, MAX_CAPTURE_BYTES, validate_capture,
)
from seiltanzer.runtime_git_identity import runtime_git_sha  # noqa: E402

PUBLICATION_CONTRACT = 'active-edge-exact-sha-publication-v1'
REMOTE_NAME = 'fomc_prospective_latest.json'
COMMAND = re.compile(r'fomc-publish-v1 ([0-9a-f]{40}) ([A-Za-z0-9_-]{1,80})\Z')


def _healthy():
    try:
        with urlopen('http://127.0.0.1:8790/api/state', timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def receive(stream, *, command, directory, identity=runtime_git_sha,
            healthy=_healthy, clock=time.time):
    match = COMMAND.fullmatch(command)
    if match is None:
        raise ValueError('FOMC_TRANSPORT_COMMAND_REJECTED')
    sha, run_id = match.groups()
    if identity() != sha:
        raise ValueError('FOMC_TRANSPORT_SHA_MISMATCH')
    if not healthy():
        raise ValueError('FOMC_TRANSPORT_HEALTH_REFUSAL')
    raw = stream.read(MAX_CAPTURE_BYTES + 1)
    if not raw or len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError('FOMC_TRANSPORT_BYTE_BOUND')
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or payload.get('production_authority') is not False
            or payload.get('contract_version') != CONTRACT
            or payload.get('code_sha') != sha or payload.get('published_for_sha') != sha
            or payload.get('publication_contract_version') != PUBLICATION_CONTRACT
            or payload.get('publication_run_id') != run_id):
        raise ValueError('FOMC_TRANSPORT_ENVELOPE_REJECTED')
    now = clock()
    if payload.get('status') == 'UNAVAILABLE':
        captured = payload.get('captured_ts')
        if (isinstance(captured, bool) or not isinstance(captured, (int, float))
                or not math.isfinite(captured) or not 0 <= now - captured <= MAX_AGE_SEC
                or not isinstance(payload.get('reason'), str)
                or not 0 < len(payload['reason']) <= 256):
            raise ValueError('FOMC_TRANSPORT_REFUSAL_INVALID')
    else:
        validate_capture(payload, expected_sha=sha, now=now)
    # Recheck after bounded input/validation: a deployment may have started.
    if identity() != sha:
        raise ValueError('FOMC_TRANSPORT_SHA_CHANGED')
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError('FOMC_TRANSPORT_DESTINATION_MISSING')
    # Both independent SSH and GitHub publication use this same receiver.
    with (directory / '.fomc-publication.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if identity() != sha:
            raise ValueError('FOMC_TRANSPORT_SHA_CHANGED')
        destination = directory / REMOTE_NAME
        try:
            with destination.open('rb') as previous_file:
                previous_raw = previous_file.read(MAX_CAPTURE_BYTES + 1)
            previous = json.loads(previous_raw)
        except (FileNotFoundError, ValueError):
            previous = None
        if isinstance(previous, dict) and previous.get('published_for_sha') == sha:
            previous_ts = previous.get('captured_ts')
            if (not isinstance(previous_ts, bool) and isinstance(previous_ts, (int, float))
                    and math.isfinite(previous_ts) and previous_ts >= payload['captured_ts']):
                raise ValueError('FOMC_TRANSPORT_OUT_OF_ORDER')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.fomc-', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), 0o644)
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return dict(contract_version='fomc-delivery-receipt-v1', received_ts=clock(),
                capture_ts=payload['captured_ts'], capture_status=payload.get('status', 'OK'),
                published_for_sha=sha, publication_run_id=run_id,
                payload_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                production_authority=False, materialization_observed=False)


def verify_receipt(raw, *, sha, run_id, payload):
    receipt = json.loads(raw)
    if (not isinstance(receipt, dict) or receipt.get('published_for_sha') != sha
            or receipt.get('publication_run_id') != run_id
            or receipt.get('payload_sha256') != hashlib.sha256(payload).hexdigest()):
        raise ValueError('FOMC_TRANSPORT_RECEIPT_MISMATCH')
    return receipt


def deliver_with_client(client, report, *, sha, run_id):
    """Existing GitHub password transport shares the ordered FOMC receiver."""
    command = f'fomc-publish-v1 {sha} {run_id}'
    if COMMAND.fullmatch(command) is None:
        raise ValueError('FOMC_TRANSPORT_COMMAND_REJECTED')
    raw = Path(report).read_bytes()
    if not 0 < len(raw) <= MAX_CAPTURE_BYTES:
        raise ValueError('FOMC_TRANSPORT_BYTE_BOUND')
    remote = ('cd /opt/seiltanzer && SSH_ORIGINAL_COMMAND=' + shlex.quote(command)
              + ' /opt/seiltanzer/.venv/bin/python scripts/fomc_scheduler_transport.py receive')
    stdin, stdout, stderr = client.exec_command(remote, timeout=30)
    stdin.write(raw)
    stdin.flush()
    stdin.channel.shutdown_write()
    response = stdout.read()
    error = stderr.read()
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError('FOMC_TRANSPORT_REMOTE_REFUSAL: ' + error.decode('utf-8', 'replace')[-500:])
    return verify_receipt(response, sha=sha, run_id=run_id, payload=raw)


def deliver(report, *, sha, run_id, host, user, key, known_hosts):
    command = f'fomc-publish-v1 {sha} {run_id}'
    if (COMMAND.fullmatch(command) is None
            or re.fullmatch(r'[A-Za-z0-9_.:-]+', host) is None
            or re.fullmatch(r'[A-Za-z0-9_]+', user) is None):
        raise ValueError('FOMC_TRANSPORT_TARGET_INVALID')
    raw = Path(report).read_bytes()
    if not 0 < len(raw) <= MAX_CAPTURE_BYTES:
        raise ValueError('FOMC_TRANSPORT_BYTE_BOUND')
    result = subprocess.run([
        'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={Path(known_hosts).resolve()}',
        '-o', 'ConnectTimeout=8', '-o', 'ConnectionAttempts=1',
        '-i', str(Path(key).resolve()), f'{user}@{host}', command,
    ], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=90)
    return verify_receipt(result.stdout, sha=sha, run_id=run_id, payload=raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    modes.add_parser('receive')
    sender = modes.add_parser('send')
    sender.add_argument('--expected-sha', required=True)
    sender.add_argument('--host', required=True)
    sender.add_argument('--user', default='root')
    sender.add_argument('--key', required=True)
    sender.add_argument('--known-hosts', required=True)
    sender.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.mode == 'receive':
        # Bound stalled stdin from an authenticated key; no original command is executed.
        signal.alarm(20)
        receipt = receive(sys.stdin.buffer, command=os.environ.get('SSH_ORIGINAL_COMMAND', ''),
                          directory=ROOT / 'data/research')
    else:
        if runtime_git_sha() != args.expected_sha:
            raise ValueError('FOMC_SCHEDULER_CHECKOUT_SHA_MISMATCH')
        run_id = 'spare-' + str(time.time_ns())
        report = Path(args.output)
        # Never deliver yesterday's file if the builder fails before producing a refusal.
        report.unlink(missing_ok=True)
        result = subprocess.run([sys.executable, '-m', 'scripts.build_fomc_prospective_capture',
                                 '--expected-sha', args.expected_sha, '--output', str(report)],
                                cwd=ROOT, timeout=90)
        from publish_active_edge_report import _stamp_report
        _stamp_report(report, expected_sha=args.expected_sha, run_id=run_id)
        receipt = deliver(report, sha=args.expected_sha, run_id=run_id, host=args.host,
                          user=args.user, key=args.key, known_hosts=args.known_hosts)
        print(json.dumps(receipt, sort_keys=True))
        return result.returncode
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
