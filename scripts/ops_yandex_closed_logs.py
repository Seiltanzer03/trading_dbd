"""One-off closed-system-log archiving; no application/database/history writes."""
from __future__ import annotations

import argparse
import grp
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
import time

import ede_ledger_archive as archive
from production_ede_offload import _connect, _exec, _verify_sha

LOG_ROOT = Path('/var/log')
JOURNAL_ROOT = LOG_ROOT / 'journal'
DIAG_ROOT = Path('/opt/actions-runner/_diag')
MAX_FILE_BYTES = 80 * 1024**2
MAX_TOTAL_BYTES = 400 * 1024**2


def check_scope(source):
    source = Path(source)
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError('Symlinked log scope')
    journal = (source.parent.parent == JOURNAL_ROOT
               and re.fullmatch('[0-9a-f]{32}', source.parent.name)
               and re.fullmatch(r'(system|user-\d+)@[0-9a-f-]+\.journal', source.name))
    diagnostic = (source.parent == DIAG_ROOT and re.fullmatch(
        r'(Runner|Worker)_\d{8}-\d{6}-utc\.log', source.name))
    rotated = source == LOG_ROOT / 'btmp.1'
    if not (journal or diagnostic or rotated) or not source.is_file():
        raise ValueError('Only closed journal/runner logs and btmp.1 are allowed')
    if not 0 < source.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError('Log outside bounded file budget')
    return source


def transport_name(source):
    return 'closed-log-' + hashlib.sha256(str(source).encode()).hexdigest()[:24] + '.bin'


def utmp_gid():
    return grp.getgrnam('utmp').gr_gid


def rotate_btmp(source, *, rotate, is_closed):
    source = Path(source)
    if (source != LOG_ROOT / 'btmp' or not source.is_file()
            or any(p.is_symlink() for p in (source, *source.parents))):
        raise ValueError('Unsafe active btmp path')
    before = source.stat()
    if (stat.S_IMODE(before.st_mode) not in (0o600, 0o660) or before.st_uid != 0
            or before.st_gid != utmp_gid() or not 0 < before.st_size <= MAX_FILE_BYTES):
        raise ValueError('Unexpected btmp ownership/mode/size')
    destination = source.with_name('btmp.1')
    if destination.exists() or destination.is_symlink() or Path(str(destination)+'.gz').exists() or Path(str(destination)+'.gz').is_symlink():
        raise ValueError('Existing rotated file must first be preserved')
    rotate(source)
    old, new = destination.stat(), source.stat()
    if ((old.st_dev, old.st_ino) != (before.st_dev, before.st_ino)
            or old.st_size < before.st_size or new.st_ino == before.st_ino
            or (new.st_uid, new.st_gid, stat.S_IMODE(new.st_mode)) != (0, utmp_gid(), stat.S_IMODE(before.st_mode))
            or not is_closed(destination)):
        raise ValueError('Rotation did not preserve closed inode and active ownership')
    return check_scope(destination)


def retire(source, manifest, *, is_closed):
    source = check_scope(source)
    archive.validate(manifest)
    if (manifest.get('source_name') != transport_name(source)
            or manifest.get('original_path') != str(source)):
        raise ValueError('Cloud receipt belongs to another source')
    before = archive.signature(source)
    if (not is_closed(source) or before[2] != manifest['source_size_bytes']
            or archive.digest(source) != manifest['source_sha256']
            or archive.signature(source) != before or not is_closed(source)
            or archive.signature(source) != before):
        raise ValueError('Archived log changed or is open')
    source.unlink()
    return {'retired_bytes': before[2], 'all_bytes_preserved_in_cloud': True,
            'original_path': str(source), 'source_sha256': manifest['source_sha256']}


def write_receipt(source, manifest):
    source = check_scope(source)
    archive.validate(manifest)
    if (manifest.get('original_path') != str(source)
            or manifest.get('source_name') != transport_name(source)):
        raise ValueError('Receipt source identity mismatch')
    path = Path(str(source)+'.cloud-'+manifest['source_sha256']+'.archive.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(manifest, sort_keys=True)+'\n')
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path


def remote(client, expression):
    # Pure functions only; remote host receives neither AWS keys nor local code.
    helper = Path(archive.__file__).read_text()
    definitions = '\n'.join(inspect.getsource(fn) for fn in (check_scope, transport_name, utmp_gid, rotate_btmp, retire, write_receipt))
    constants = ('LOG_ROOT=Path("/var/log")\nJOURNAL_ROOT=LOG_ROOT/"journal"\n'
                 'DIAG_ROOT=Path("/opt/actions-runner/_diag")\nMAX_FILE_BYTES=80*1024**2\n')
    closed = '''
import subprocess
def is_closed(path):
    result=subprocess.run(['lsof','--',str(path)],capture_output=True,timeout=10)
    if result.returncode not in (0,1): raise ValueError('Closed-file proof unavailable')
    return result.returncode == 1
'''
    namespace = '\nimport grp, stat, types\narchive=types.SimpleNamespace(validate=validate,signature=signature,digest=digest)\n'
    code = helper + namespace + constants + definitions + closed + '\n' + expression
    return json.loads(_exec(client, 'python3 -c ' + shlex.quote(code), timeout=180))


def candidates(client):
    return remote(client, '''
import time
paths=list(JOURNAL_ROOT.glob('*/*@*.journal'))+list(DIAG_ROOT.glob('*.log'))
paths.append(LOG_ROOT/'btmp.1')
selected=[]
for p in sorted(paths):
    try: check_scope(p)
    except ValueError: continue
    if p.parent==DIAG_ROOT and p.stat().st_mtime>time.time()-86400: continue
    if is_closed(p): selected.append({'path':str(p),'size':p.stat().st_size})
print(json.dumps(selected))
''')


def archive_one(client, source, sha, workspace):
    before = remote(client, 'p=check_scope(' + repr(source) + ')\n'
                    'assert is_closed(p)\nprint(json.dumps({"signature":signature(p),"sha256":digest(p)}))')
    local = workspace / transport_name(source)
    with client.open_sftp() as sftp:
        sftp.get(source, str(local))
    if local.stat().st_size != before['signature'][2] or archive.digest(local) != before['sha256']:
        raise ValueError('Downloaded original differs')
    packed = local.with_name(local.name + '.gz')
    manifest = archive.prepare(local, packed, expected_sha=sha)
    manifest['original_path'] = source
    manifest = archive.upload(archive.s3_client(), packed, manifest)
    proof = remote(client, 'p=check_scope(' + repr(source) + ')\nmanifest=' + repr(manifest) + '\n'
                   'assert signature(p)==tuple(' + repr(before['signature']) + ')\n'
                   'assert is_closed(p) and digest(p)==manifest["source_sha256"]\n'
                   'write_receipt(p,manifest)\n'
                   'print(json.dumps(retire(p,manifest,is_closed=is_closed)))')
    local.unlink()
    packed.unlink()
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--rotate-btmp', action='store_true')
    args = parser.parse_args()
    client = _connect(os.environ['SSH_PASSWORD'])
    try:
        _verify_sha(client, args.expected_sha)
        selected = candidates(client)
        if sum(row['size'] for row in selected) + (MAX_FILE_BYTES if args.rotate_btmp else 0) > MAX_TOTAL_BYTES:
            raise ValueError('Closed logs exceed bounded total budget')
        print('CLOSED_LOG_CANDIDATES=' + json.dumps(selected), flush=True)
        if args.apply:
            with tempfile.TemporaryDirectory(prefix='verified-yandex-logs-') as tmp:
                for row in selected:
                    proof = archive_one(client, row['path'], args.expected_sha, Path(tmp))
                    print('CLOSED_LOG_ARCHIVED=' + json.dumps(proof), flush=True)
                if args.rotate_btmp:
                    # Standard rename/create rotation; never copytruncate, delete
                    # an old archive, or signal/restart the terminal or services.
                    source = remote(client, '''
import tempfile, subprocess
def rotate(path):
    with tempfile.TemporaryDirectory(prefix='verified-btmp-rotation-') as tmp:
        config=Path(tmp)/'config'
        mode=format(stat.S_IMODE(path.stat().st_mode),'04o')
        config.write_text('/var/log/btmp {\\nrotate -1\\ncreate '+mode+' root utmp\\nnocompress\\nmissingok\\nnotifempty\\n}\\n')
        config.chmod(0o600)
        subprocess.run(['/usr/sbin/logrotate','--force','--state',str(Path(tmp)/'state'),str(config)],check=True,capture_output=True,timeout=30)
print(json.dumps(str(rotate_btmp(LOG_ROOT/'btmp',rotate=rotate,is_closed=is_closed))))
''')
                    proof = archive_one(client, source, args.expected_sha, Path(tmp))
                    print('ROTATED_BTMP_ARCHIVED=' + json.dumps(proof), flush=True)
        _verify_sha(client, args.expected_sha)
        print(_exec(client, 'df -B1 /; systemctl is-active seiltanzer', timeout=10))
    finally:
        client.close()


if __name__ == '__main__':
    main()
