#!/usr/bin/env python3
"""Stream a verified immutable SQLite snapshot into private S3 storage."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import time
import zlib

PART_SIZE = 64 * 1024 * 1024
READ_SIZE = 4 * 1024 * 1024


def compressed_parts(path: Path, part_size: int = PART_SIZE):
    compressor = zlib.compressobj(level=6, wbits=31)
    pending = bytearray()
    with path.open('rb') as stream:
        for raw in iter(lambda: stream.read(READ_SIZE), b''):
            pending.extend(compressor.compress(raw))
            while len(pending) >= part_size:
                yield bytes(pending[:part_size])
                del pending[:part_size]
    pending.extend(compressor.flush())
    if pending:
        yield bytes(pending)


def upload(database: Path, source_manifest: Path, *, bucket: str, key: str) -> dict:
    import boto3
    from botocore.config import Config

    manifest = json.loads(source_manifest.read_text())
    size = int(manifest.get('database_size_bytes', -1))
    digest = str(manifest.get('database_sha256') or '')
    if database.stat().st_size != size or len(digest) != 64:
        raise RuntimeError('source snapshot manifest does not match database metadata')
    client = boto3.client('s3', endpoint_url='https://storage.yandexcloud.net',
                          region_name='ru-central1',
                          config=Config(connect_timeout=10, read_timeout=120,
                                        retries={'max_attempts': 3}))
    versioning = client.get_bucket_versioning(Bucket=bucket)
    if versioning.get('Status') == 'Enabled':
        raise RuntimeError('bucket versioning must be disabled for bounded seven-slot retention')
    metadata = {'source-sha256': digest, 'source-size': str(size),
                'git-commit': str(manifest.get('git_commit') or ''),
                'codec': 'gzip', 'contract': 'trading-dbd-offhost-backup-v1'}
    created = client.create_multipart_upload(
        Bucket=bucket, Key=key, ContentType='application/gzip', Metadata=metadata)
    upload_id = created['UploadId']
    parts = []
    compressed_digest = hashlib.sha256()
    compressed_size = 0
    first_sample = b''
    last_sample = b''
    try:
        for number, part in enumerate(compressed_parts(database), 1):
            compressed_digest.update(part); compressed_size += len(part)
            if number == 1:
                first_sample = part[:1024 * 1024]
            last_sample = part[-1024 * 1024:]
            response = client.upload_part(Bucket=bucket, Key=key, UploadId=upload_id,
                                          PartNumber=number, Body=part,
                                          ContentMD5=base64.b64encode(hashlib.md5(part).digest()).decode())
            parts.append({'PartNumber': number, 'ETag': response['ETag']})
        if not parts:
            raise RuntimeError('compressed snapshot is empty')
        client.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
            MultipartUpload={'Parts': parts})
    except BaseException:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise
    head = client.head_object(Bucket=bucket, Key=key)
    if int(head['ContentLength']) != compressed_size or head.get('Metadata') != metadata:
        raise RuntimeError('uploaded snapshot HEAD verification failed')
    first = client.get_object(Bucket=bucket, Key=key,
                              Range=f'bytes=0-{len(first_sample)-1}')['Body'].read()
    start = max(0, compressed_size - len(last_sample))
    last = client.get_object(Bucket=bucket, Key=key,
                             Range=f'bytes={start}-{compressed_size-1}')['Body'].read()
    if first != first_sample or last != last_sample:
        raise RuntimeError('uploaded snapshot range verification failed')
    result = {**manifest, 'backup_contract': 'trading-dbd-offhost-backup-v1',
              'bucket': bucket, 'object_key': key, 'codec': 'gzip',
              'compressed_size_bytes': compressed_size,
              'compressed_sha256': compressed_digest.hexdigest(),
              'multipart_part_count': len(parts), 'uploaded_ts': time.time(),
              'verified_head': True, 'verified_boundary_get': True}
    manifest_key = key + '.manifest.json'
    body = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode()
    client.put_object(Bucket=bucket, Key=manifest_key, Body=body,
                      ContentType='application/json',
                      ContentMD5=base64.b64encode(hashlib.md5(body).digest()).decode())
    check = client.get_object(Bucket=bucket, Key=manifest_key)['Body'].read()
    if check != body:
        raise RuntimeError('uploaded backup manifest verification failed')
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--output-manifest', type=Path, required=True)
    args = parser.parse_args()
    result = upload(args.database, args.source_manifest, bucket=args.bucket, key=args.key)
    args.output_manifest.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print('OFFHOST_BACKUP_VERIFIED=1')
    print('OFFHOST_BACKUP_OBJECT=' + result['object_key'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
