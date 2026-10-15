"""Lossless EDE ledger archives in the existing private Yandex bucket.

All compression and cloud verification run off-host. Source research bytes are
opaque: no candidate identities, source clocks or evidence are rewritten.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

BUCKET = 'trading-dbd-backups-2026'
PREFIX = 'research/ede-ledger-archives/v1/'
CONTRACT = 'ede-lossless-ledger-archive-v1'
MAX_BYTES = 2 * 1024 ** 3
CHUNK = 1024 ** 2
MIN_FREE_BYTES = 1024 ** 3


def require_capacity(directory, additional_bytes):
    if additional_bytes < 0 or shutil.disk_usage(directory).free < MIN_FREE_BYTES + additional_bytes:
        raise ValueError('Archive staging lacks the unchanged 1GiB reserve')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()


def signature(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Archive source must be one regular non-symlink file')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def prepare(source, archive, *, expected_sha):
    source, archive = Path(source), Path(archive)
    before = signature(source)
    if not 0 < before[2] <= MAX_BYTES or not re.fullmatch('[0-9a-f]{40}', expected_sha):
        raise ValueError('Archive source size or code identity invalid')
    require_capacity(archive.parent, before[2] + CHUNK)
    raw_hash = hashlib.sha256()
    with source.open('rb') as stream, archive.open('xb') as packed:
        with gzip.GzipFile(fileobj=packed, mode='wb', filename='', mtime=0) as encoded:
            for chunk in iter(lambda: stream.read(CHUNK), b''):
                raw_hash.update(chunk)
                encoded.write(chunk)
        packed.flush()
        os.fsync(packed.fileno())
    if signature(source) != before or digest(source) != raw_hash.hexdigest():
        raise ValueError('Archive source changed during compression')
    value = {
        'contract': CONTRACT, 'bucket': BUCKET,
        'object_key': PREFIX + source.name + '/' + raw_hash.hexdigest() + '.gz',
        'source_name': source.name, 'source_sha256': raw_hash.hexdigest(),
        'source_size_bytes': before[2], 'compressed_sha256': digest(archive),
        'compressed_size_bytes': archive.stat().st_size,
        'archived_by_sha': expected_sha, 'production_authority': False,
        'verified_full_get': False,
    }
    return value


def validate(value, *, require_verified=True):
    if not isinstance(value, dict) or not all((
        value.get('contract') == CONTRACT, value.get('bucket') == BUCKET,
        value.get('production_authority') is False,
        re.fullmatch('[0-9a-f]{64}', str(value.get('source_sha256') or '')),
        re.fullmatch('[0-9a-f]{64}', str(value.get('compressed_sha256') or '')),
        re.fullmatch('[0-9a-f]{40}', str(value.get('archived_by_sha') or '')),
        re.fullmatch('[A-Za-z0-9_.-]{1,100}', str(value.get('source_name') or '')),
        value.get('object_key') == PREFIX + str(value.get('source_name')) + '/' + str(value.get('source_sha256')) + '.gz',
    )):
        raise ValueError('Invalid archive identity')
    for key in ('source_size_bytes', 'compressed_size_bytes'):
        if type(value.get(key)) is not int or not 0 < value[key] <= MAX_BYTES:
            raise ValueError('Invalid archive size')
    if require_verified and value.get('verified_full_get') is not True:
        raise ValueError('Archive has no full restore proof')


def _metadata(manifest):
    return {'contract': CONTRACT, 'source-sha256': manifest['source_sha256'],
            'compressed-sha256': manifest['compressed_sha256'],
            'source-size': str(manifest['source_size_bytes'])}


class _HashReader:
    def __init__(self, stream, maximum):
        self.stream, self.maximum = stream, maximum
        self.hash, self.size = hashlib.sha256(), 0

    def read(self, amount=-1):
        if amount < 0:
            amount = CHUNK
        data = self.stream.read(min(amount, CHUNK))
        self.size += len(data)
        if self.size > self.maximum:
            raise ValueError('Compressed archive exceeds declared size')
        self.hash.update(data)
        return data


def restore(client, manifest, output, *, require_verified=True):
    validate(manifest, require_verified=require_verified)
    head = client.head_object(Bucket=BUCKET, Key=manifest['object_key'])
    metadata = {str(k).lower(): str(v) for k, v in head.get('Metadata', {}).items()}
    if head.get('ContentLength') != manifest['compressed_size_bytes'] or metadata != _metadata(manifest):
        raise ValueError('Cloud archive metadata mismatch')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    require_capacity(output.parent, manifest['source_size_bytes'])
    stream = client.get_object(Bucket=BUCKET, Key=manifest['object_key'])['Body']
    reader = _HashReader(stream, manifest['compressed_size_bytes'])
    raw_hash, raw_size = hashlib.sha256(), 0
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as target:
            temporary = Path(target.name)
            with gzip.GzipFile(fileobj=reader, mode='rb') as decoded:
                for chunk in iter(lambda: decoded.read(CHUNK), b''):
                    raw_size += len(chunk)
                    if raw_size > manifest['source_size_bytes']:
                        raise ValueError('Decoded archive exceeds declared size')
                    raw_hash.update(chunk)
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if (raw_size != manifest['source_size_bytes'] or raw_hash.hexdigest() != manifest['source_sha256']
                or reader.size != manifest['compressed_size_bytes']
                or reader.hash.hexdigest() != manifest['compressed_sha256']):
            raise ValueError('Full cloud restore hash or size mismatch')
        os.replace(temporary, output)
        temporary = None
    finally:
        stream.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def upload(client, archive, manifest):
    validate(manifest, require_verified=False)
    archive = Path(archive)
    if archive.stat().st_size != manifest['compressed_size_bytes'] or digest(archive) != manifest['compressed_sha256']:
        raise ValueError('Archive changed before upload')
    client.upload_file(str(archive), BUCKET, manifest['object_key'],
                       ExtraArgs={'ContentType': 'application/gzip', 'Metadata': _metadata(manifest)})
    with tempfile.TemporaryDirectory(prefix='ede-archive-verify-') as temporary:
        restore(client, manifest, Path(temporary) / 'original', require_verified=False)
    receipt = {**manifest, 'verified_full_get': True}
    body = (json.dumps(receipt, sort_keys=True) + '\n').encode()
    receipt_key = manifest['object_key'] + '.manifest.json'
    client.put_object(Bucket=BUCKET, Key=receipt_key, Body=body, ContentType='application/json')
    with client.get_object(Bucket=BUCKET, Key=receipt_key)['Body'] as check:
        if check.read(len(body) + 1) != body:
            raise ValueError('Cloud archive receipt readback mismatch')
    return receipt


def restore_local(archive, manifest, output):
    archive = Path(archive)
    class LocalCopy:
        def head_object(self, **kw):
            return {'ContentLength': archive.stat().st_size, 'Metadata': _metadata(manifest)}
        def get_object(self, **kw):
            return {'Body': archive.open('rb')}
    restore(LocalCopy(), manifest, output)


def retire_plain(source, archive, manifest):
    """Release only a verified byte prefix preserved in cloud and local gzip."""
    validate(manifest)
    source, archive = Path(source), Path(archive)
    if source.name != manifest.get('source_name') or archive.name != source.name + '.gz':
        raise ValueError('Unsafe retirement identity')
    if not source.exists():
        return {'retired_bytes': 0}
    before = signature(source)
    original_hash = digest(source)
    if digest(archive) != manifest['compressed_sha256']:
        raise ValueError('Installed gzip hash mismatch')
    prefix_hash, raw_hash, seen = hashlib.sha256(), hashlib.sha256(), 0
    with gzip.open(archive, 'rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            prefix_hash.update(chunk[:max(0, min(len(chunk), before[2] - seen))])
            raw_hash.update(chunk)
            seen += len(chunk)
            if seen > manifest['source_size_bytes']:
                raise ValueError('Installed gzip exceeds declared size')
    if (seen != manifest['source_size_bytes'] or seen < before[2]
            or raw_hash.hexdigest() != manifest['source_sha256']
            or prefix_hash.hexdigest() != original_hash
            or signature(source) != before or digest(source) != original_hash):
        raise ValueError('Legacy research bytes not preserved or changed')
    source.unlink()
    return {'retired_bytes': before[2], 'original_sha256': original_hash,
            'all_bytes_preserved': True}


def retire_cached_ledger(archive, manifest, *, is_closed):
    """Release only the optional gzip cache, retaining its verified cloud receipt."""
    validate(manifest)
    archive = Path(archive)
    name = 'ede_v13_candidate_registry.jsonl'
    if manifest['source_name'] != name or archive.name != name + '.gz':
        raise ValueError('Only the archived candidate-ledger cache may be released')
    source, receipt = archive.with_name(name), archive.with_name(name + '.archive.json')
    receipt_before = signature(receipt)
    if receipt_before[2] > 65536 or json.loads(receipt.read_bytes()) != manifest:
        raise ValueError('Installed archive receipt changed or exceeds bound')
    receipt_hash = digest(receipt)
    if source.exists() or source.is_symlink():
        raise ValueError('Plaintext ledger remains authoritative; preserve its cache')
    if not archive.exists() and not archive.is_symlink():
        return {'retired_cache_bytes': 0, 'all_bytes_preserved_in_cloud': True}
    before = signature(archive)
    if (before[2] != manifest['compressed_size_bytes']
            or digest(archive) != manifest['compressed_sha256'] or not is_closed(archive)):
        raise ValueError('Cached ledger changed or is open')
    if (not is_closed(archive) or source.exists() or source.is_symlink()
            or signature(archive) != before or digest(archive) != manifest['compressed_sha256']
            or signature(receipt) != receipt_before or digest(receipt) != receipt_hash):
        raise ValueError('Cached ledger or its authority changed during verification')
    archive.unlink()
    fd = os.open(archive.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {'retired_cache_bytes': before[2], 'all_bytes_preserved_in_cloud': True}


def retire_installer(source, manifest):
    validate(manifest)
    source = Path(source)
    if source.name != 'actions-runner-linux-x64-2.322.0.tar.gz' or manifest.get('source_name') != source.name:
        raise ValueError('Only the known retired installer may be released')
    before = signature(source)
    if before[2] != manifest['source_size_bytes'] or digest(source) != manifest['source_sha256'] or signature(source) != before:
        raise ValueError('Retired installer changed after cloud verification')
    source.unlink()
    return {'retired_bytes': before[2], 'all_bytes_preserved_in_cloud': True}


def retire_closed_archive(source, manifest, *, is_closed):
    validate(manifest)
    source = Path(source)
    if source.name not in ('btmp.1', 'syslog.1', 'auth.log.1') or manifest.get('source_name') != source.name:
        raise ValueError('Only fixed closed rotated logs may be archived')
    before = signature(source)
    if (not is_closed(source) or before[2] != manifest['source_size_bytes']
            or digest(source) != manifest['source_sha256'] or signature(source) != before
            or not is_closed(source)):
        raise ValueError('Closed log changed or is in use')
    source.unlink()
    return {'retired_bytes': before[2], 'all_bytes_preserved_in_cloud': True}


def s3_client():
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url='https://storage.yandexcloud.net', region_name='ru-central1',
                        config=Config(connect_timeout=10, read_timeout=120, retries={'max_attempts': 3}))


def compress_closed(source, *, is_closed):
    """Compress a closed rotated file locally; retain all bytes in standard gzip."""
    source = Path(source)
    before = signature(source)
    target = source.with_name(source.name + '.gz')
    if target.exists() or target.is_symlink() or not is_closed(source):
        raise ValueError('Source open or gzip destination already exists')
    temporary = source.with_name(source.name + '.gzip-pending-' + uuid.uuid4().hex)
    try:
        manifest = prepare(source, temporary, expected_sha='0' * 40)
        raw_hash, raw_size = hashlib.sha256(), 0
        with gzip.open(temporary, 'rb') as decoded:
            for chunk in iter(lambda: decoded.read(CHUNK), b''):
                raw_size += len(chunk)
                raw_hash.update(chunk)
        if (raw_hash.hexdigest() != manifest['source_sha256'] or raw_size != before[2]
                or signature(source) != before or not is_closed(source)
                or digest(source) != manifest['source_sha256']):
            raise ValueError('Closed source changed during verification')
        temporary.chmod(source.stat().st_mode & 0o777)
        os.chown(temporary, source.stat().st_uid, source.stat().st_gid)
        os.link(temporary, target)
        source.unlink()
        fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {'source_name': source.name, 'source_sha256': manifest['source_sha256'],
                'bytes_released': before[2] - target.stat().st_size, 'all_bytes_preserved': True}
    finally:
        temporary.unlink(missing_ok=True)
