"""Bounded private transport of existing archive bytes, without model authority.

Callers MUST serialize writers across restore/pipeline/store (one constant
workflow concurrency group, cancel-in-progress false). Pointer comparison
detects stale sequential callers; it is not a distributed compare-and-swap.
No source capture/availability/label clock is rewritten by this transport.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import math
import re
import zlib

from seiltanzer.edge_family_archive import (
    CONTRACT_VERSION, MAX_ARCHIVE_BYTES, MAX_EPISODES, _encoded, _loads,
)

BUCKET = 'trading-dbd-backups-2026'
PREFIX = 'edge-family/v1'
LATEST_KEY = PREFIX + '/latest.json'
CONTRACT = 'edge-family-private-transport-v1'
MAX_RAW_BYTES = MAX_ARCHIVE_BYTES
MAX_COMPRESSED_BYTES = MAX_ARCHIVE_BYTES + 128_000
MAX_RECEIPT_BYTES = 16_000


def _sha(value, size):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{' + str(size) + '}', value) is not None


def _archive(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RAW_BYTES:
        raise ValueError('ARCHIVE_BYTE_BOUND')
    value = _loads(raw)
    if not isinstance(value, dict):
        raise ValueError('ARCHIVE_OBJECT_REQUIRED')
    # Re-encoding catches overflowed finite JSON literals as well as cycles.
    _encoded(value, MAX_RAW_BYTES)
    records = value.get('episodes')
    if (value.get('contract_version') != CONTRACT_VERSION
            or not isinstance(records, list) or len(records) > MAX_EPISODES
            or hashlib.sha256(_encoded(records, MAX_RAW_BYTES)).hexdigest() != value.get('dataset_sha256')):
        raise ValueError('ARCHIVE_CONTRACT_MISMATCH')
    # The existing pipeline remains the owner of causal record validation.
    return raw


def _receipt(value):
    keys = {'contract', 'bucket', 'object_key', 'slot', 'generation', 'source_sha',
            'raw_size_bytes', 'raw_sha256', 'compressed_size_bytes',
            'compressed_sha256', 'uploaded_ts', 'production_authority',
            'backup_retirement_authority'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('RECEIPT_SCHEMA_MISMATCH')
    clock = value['uploaded_ts']
    if not all((
        value['contract'] == CONTRACT, value['bucket'] == BUCKET,
        type(value['slot']) is int and value['slot'] in (0, 1),
        value['object_key'] == f"{PREFIX}/slot-{value['slot']}/archive.json.gz",
        type(value['generation']) is int and value['generation'] > 0,
        _sha(value['source_sha'], 40), _sha(value['raw_sha256'], 64),
        _sha(value['compressed_sha256'], 64),
        type(value['raw_size_bytes']) is int and 0 < value['raw_size_bytes'] <= MAX_RAW_BYTES,
        type(value['compressed_size_bytes']) is int and 0 < value['compressed_size_bytes'] <= MAX_COMPRESSED_BYTES,
        type(clock) in (int, float) and math.isfinite(clock) and clock > 0,
        value['production_authority'] is False,
        value['backup_retirement_authority'] is False,
    )):
        raise ValueError('RECEIPT_CONTRACT_MISMATCH')
    return value


def _read(client, key, limit):
    response = client.get_object(Bucket=BUCKET, Key=key)
    with response['Body'] as stream:
        length = response.get('ContentLength')
        if type(length) is not int or not 0 < length <= limit:
            raise ValueError('STORAGE_BODY_BOUND')
        chunks, size = [], 0
        while True:
            chunk = stream.read(min(1024 * 1024, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError('STORAGE_BODY_BOUND')
            chunks.append(chunk)
        if size != length:
            raise ValueError('STORAGE_BODY_LENGTH_MISMATCH')
    return b''.join(chunks)


def _latest(client):
    try:
        raw = _read(client, LATEST_KEY, MAX_RECEIPT_BYTES)
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') == 'NoSuchKey':
            return None
        raise
    return _receipt(_loads(raw))


def _restore_payload(client, receipt):
    compressed = _read(client, receipt['object_key'], MAX_COMPRESSED_BYTES)
    if (len(compressed) != receipt['compressed_size_bytes']
            or hashlib.sha256(compressed).hexdigest() != receipt['compressed_sha256']):
        raise ValueError('COMPRESSED_ARCHIVE_MISMATCH')
    decoder = zlib.decompressobj(wbits=31)
    try:
        raw = decoder.decompress(compressed, MAX_RAW_BYTES + 1)
    except zlib.error as exc:
        raise ValueError('INVALID_ARCHIVE_GZIP') from exc
    if (len(raw) > MAX_RAW_BYTES or not decoder.eof or decoder.unconsumed_tail
            or decoder.unused_data or len(raw) != receipt['raw_size_bytes']
            or hashlib.sha256(raw).hexdigest() != receipt['raw_sha256']):
        raise ValueError('RAW_ARCHIVE_MISMATCH')
    return _archive(raw)


def restore(client):
    """Return INITIAL only for an explicitly missing latest pointer."""
    receipt = _latest(client)
    if receipt is None:
        return None, None
    return _restore_payload(client, receipt), receipt


def _put(client, key, raw, content_type):
    client.put_object(Bucket=BUCKET, Key=key, Body=raw, ContentType=content_type,
                      ContentMD5=base64.b64encode(hashlib.md5(raw).digest()).decode())


def store(client, raw, *, source_sha, generation, previous_receipt, uploaded_ts):
    """Commit verified inactive slot, then latest; caller must serialize writers."""
    _archive(raw)
    compressed = gzip.compress(raw, mtime=0)
    slot = 0 if previous_receipt is None else 1 - _receipt(previous_receipt)['slot']
    receipt = _receipt({
        'contract': CONTRACT, 'bucket': BUCKET,
        'object_key': f'{PREFIX}/slot-{slot}/archive.json.gz',
        'slot': slot, 'generation': generation, 'source_sha': source_sha,
        'raw_size_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'compressed_size_bytes': len(compressed),
        'compressed_sha256': hashlib.sha256(compressed).hexdigest(),
        'uploaded_ts': uploaded_ts, 'production_authority': False,
        'backup_retirement_authority': False,
    })
    if previous_receipt is not None and generation <= previous_receipt['generation']:
        raise ValueError('ARCHIVE_GENERATION_NOT_NEWER')
    if client.get_bucket_versioning(Bucket=BUCKET).get('Status') == 'Enabled':
        raise ValueError('VERSIONING_BREAKS_BOUNDED_RETENTION')
    if _latest(client) != previous_receipt:
        raise ValueError('LATEST_CHANGED_RESTORE_REQUIRED')
    _put(client, receipt['object_key'], compressed, 'application/gzip')
    if _restore_payload(client, receipt) != raw:
        raise ValueError('ARCHIVE_READBACK_MISMATCH')
    if _latest(client) != previous_receipt:
        raise ValueError('LATEST_CHANGED_BEFORE_COMMIT')
    encoded = _encoded(receipt, MAX_RECEIPT_BYTES)
    _put(client, LATEST_KEY, encoded, 'application/json')
    if _read(client, LATEST_KEY, MAX_RECEIPT_BYTES) != encoded:
        raise ValueError('LATEST_READBACK_MISMATCH')
    return receipt
