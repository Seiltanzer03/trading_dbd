"""Two explicit bounded archive operations; never opens or edits trades.db."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import tempfile

from production_ede_offload import _connect, _exec, _verify_sha, REMOTE_RESEARCH
import ede_ledger_archive as archive

INSTALLER = '/opt/actions-runner/actions-runner-linux-x64-2.322.0.tar.gz'
LEDGER = str(REMOTE_RESEARCH / 'ede_v13_candidate_registry.jsonl')


def remote(client, expression):
    # Execute reviewed pure helpers in memory, with no application file writes.
    helper = Path(archive.__file__).read_text()
    return json.loads(_exec(client, 'python3 -c ' + shlex.quote(helper + '\n' + expression), timeout=180))


def copy_to_cloud(client, source, expected_sha, workspace):
    evidence = remote(client,
        'p=Path(' + repr(source) + ')\n'
        'print(json.dumps({"signature":signature(p),"sha256":digest(p)}))')
    local = workspace / Path(source).name
    with client.open_sftp() as sftp:
        sftp.get(source, str(local))
    if local.stat().st_size != evidence['signature'][2] or archive.digest(local) != evidence['sha256']:
        raise ValueError('Downloaded original archive mismatch')
    packed = workspace / (local.name + '.gz')
    manifest = archive.upload(archive.s3_client(), packed,
                              archive.prepare(local, packed, expected_sha=expected_sha))
    return packed, manifest


def capacity(client, expected_sha, workspace):
    # The retired public installer is recoverable from its fully read-back object.
    exists = _exec(client, 'test -f ' + shlex.quote(INSTALLER) + ' && echo PRESENT || echo ABSENT', timeout=10).strip()
    if exists == 'PRESENT':
        _, manifest = copy_to_cloud(client, INSTALLER, expected_sha, workspace)
        receipt = INSTALLER + '.cloud-archive.json'
        proof = remote(client,
            'manifest=' + repr(manifest) + '\n'
            'Path(' + repr(receipt) + ').write_text(json.dumps(manifest,sort_keys=True)+"\\n")\n'
            'print(json.dumps(retire_installer(' + repr(INSTALLER) + ',manifest)))')
        print('RUNNER_INSTALLER_ARCHIVED=' + json.dumps(proof), flush=True)
    elif exists != 'ABSENT':
        raise ValueError('Installer presence response invalid')
    # Zero large VPS staging writes: closed rotated archives go directly to the
    # existing private bucket via the runner. Active logs and all DBs are excluded.
    closed_function = '''
import subprocess
def closed(path):
    result=subprocess.run(['lsof','--',str(path)],capture_output=True,timeout=10)
    if result.returncode not in (0,1): raise ValueError('lsof refused closed-file proof')
    return result.returncode==1
'''
    for source in ('/var/log/btmp.1', '/var/log/syslog.1', '/var/log/auth.log.1'):
        present = remote(client, closed_function + '\n' +
            'p=Path(' + repr(source) + ')\nprint(json.dumps(p.exists() and closed(p)))')
        if not present:
            continue
        _, manifest = copy_to_cloud(client, source, expected_sha, workspace)
        proof = remote(client, closed_function + '\n' +
            'manifest=' + repr(manifest) + '\n' +
            'Path(' + repr(source + '.cloud-archive.json') + ').write_text(json.dumps(manifest,sort_keys=True)+"\\n")\n' +
            'print(json.dumps(retire_closed_archive(' + repr(source) + ',manifest,is_closed=closed)))')
        print('CLOSED_LOG_ARCHIVED=' + json.dumps(proof), flush=True)


def ledger(client, expected_sha, workspace):
    # Old installed consumers cannot mistake compressed history for an empty ledger.
    _exec(client, 'test -f /opt/seiltanzer/scripts/ede_ledger_archive.py', timeout=10)
    packed, manifest = copy_to_cloud(client, LEDGER, expected_sha, workspace)
    from production_ede_offload import _assert_remote_ledger_prefix, _remote_ledger_identity, _require_remote_archive_capacity
    before = _assert_remote_ledger_prefix(client, workspace / Path(LEDGER).name)
    _require_remote_archive_capacity(client, packed.stat().st_size + 65536)
    with client.open_sftp() as sftp:
        for suffix, local in (('.gz', packed),):
            temporary = LEDGER + suffix + '.cloud-pending'
            sftp.put(str(local), temporary)
            sftp.posix_rename(temporary, LEDGER + suffix)
        receipt = workspace / 'receipt.json'
        receipt.write_text(json.dumps(manifest, sort_keys=True) + '\n')
        if _remote_ledger_identity(client) != before:
            raise ValueError('Ledger changed before authoritative receipt switch')
        sftp.put(str(receipt), LEDGER + '.archive.json.cloud-pending')
        sftp.posix_rename(LEDGER + '.archive.json.cloud-pending', LEDGER + '.archive.json')
    proof = remote(client, 'print(json.dumps(retire_plain(' + repr(LEDGER) + ',' +
                   repr(LEDGER + '.gz') + ',' + repr(manifest) + ')))')
    print('EDE_LEDGER_ARCHIVED=' + json.dumps(proof), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--stage', choices=('capacity', 'ledger'), required=True)
    args = parser.parse_args()
    client = _connect(os.environ['SSH_PASSWORD'])
    try:
        _verify_sha(client, args.expected_sha)
        with tempfile.TemporaryDirectory(prefix='verified-yandex-archive-') as temporary:
            (capacity if args.stage == 'capacity' else ledger)(client, args.expected_sha, Path(temporary))
        _verify_sha(client, args.expected_sha)
        print(_exec(client, "df -B1 /; systemctl show seiltanzer -p ActiveState -p SubState -p NRestarts; journalctl -u seiltanzer --no-pager -n 30 | sed -n '/STARTUP_PHASE/p'", timeout=30))
    finally:
        client.close()


if __name__ == '__main__':
    main()
