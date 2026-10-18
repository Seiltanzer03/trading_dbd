"""Publish one off-host immutable image and verify its complete cloud bytes.

Content verification is separate from SQLite validity and production authority.
No service control, live capture, deletion or rotating backup slot is present.
"""
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import zlib

from scripts import history_image_transport as images
from scripts.sqlite_history_partition import _identity
from scripts.upload_verified_snapshot import normalized_metadata


def yandex_image_client():
    import boto3
    from botocore.config import Config
    return boto3.client('s3',endpoint_url='https://storage.yandexcloud.net',region_name='ru-central1',
        config=Config(signature_version='s3v4',connect_timeout=10,read_timeout=120,
                      retries={'total_max_attempts':1,'mode':'standard'},s3={'addressing_style':'path'}))


def _check(deadline):
    images._check(deadline)


def _pack(source, target, identity, deadline):
    raw_digest = hashlib.sha256(); compressed_digest = hashlib.sha256()
    raw_size = compressed_size = 0
    encoder = zlib.compressobj(level=6,wbits=31)
    fd = os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as output, source.open('rb') as input_stream:
        while True:
            _check(deadline)
            raw = input_stream.read(images.CHUNK_BYTES)
            if not raw:
                break
            raw_size += len(raw)
            if raw_size > identity['database_size_bytes']:
                raise images.ImageTransportError('SOURCE_IMAGE_SIZE_MISMATCH')
            raw_digest.update(raw)
            packed = encoder.compress(raw)
            compressed_digest.update(packed);compressed_size += len(packed)
            output.write(packed)
        packed = encoder.flush()
        compressed_digest.update(packed);compressed_size += len(packed)
        output.write(packed);output.flush();os.fsync(output.fileno())
    _check(deadline)
    if (raw_size != identity['database_size_bytes']
            or raw_digest.hexdigest() != identity['database_sha256']):
        raise images.ImageTransportError('SOURCE_IMAGE_IDENTITY_MISMATCH')
    return compressed_digest.hexdigest(), compressed_size


def publish_image(database, identity, *, run_id, kind, client, deadline, opener=None):
    """Return safe full-GET pins; signed URL exists only in private local input."""
    try:
        return _publish(database,identity,run_id=run_id,kind=kind,client=client,
                        deadline=deadline,opener=opener)
    except images.ImageTransportError:
        raise
    except Exception:
        raise images.ImageTransportError('IMAGE_PUBLICATION_FAILED') from None


def _publish(database, identity, *, run_id, kind, client, deadline, opener):
    from boto3.s3.transfer import TransferConfig
    if (type(deadline) not in (int,float) or not math.isfinite(deadline)
            or type(run_id) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',run_id)
            or kind not in ('original','hot') or type(identity) is not dict
            or set(identity) != {'database_sha256','database_size_bytes'}
            or type(identity.get('database_sha256')) is not str
            or not re.fullmatch('[0-9a-f]{64}',identity['database_sha256'])
            or type(identity.get('database_size_bytes')) is not int
            or not 4096 <= identity['database_size_bytes'] <= images.MAX_BYTES):
        raise images.ImageTransportError('INVALID_IMAGE_PUBLICATION_INPUT')
    identity = dict(identity)
    _check(deadline)
    source = Path(database).absolute()
    if source != source.resolve() or source.is_relative_to(Path('/opt/seiltanzer')):
        raise images.ImageTransportError('OFFHOST_IMMUTABLE_SOURCE_REQUIRED')
    before = _identity(source)
    if before['size'] != identity['database_size_bytes']:
        raise images.ImageTransportError('SOURCE_IMAGE_SIZE_MISMATCH')
    if shutil.disk_usage(source.parent).free < before['size']+32*1024**2:
        raise images.ImageTransportError('OFFHOST_IMAGE_STAGING_RESERVE_REQUIRED')
    config = client.meta.config
    if (client.meta.endpoint_url != 'https://storage.yandexcloud.net'
            or not 0 < config.connect_timeout <= 10 or not 0 < config.read_timeout <= 120
            or config.retries.get('total_max_attempts') != 1):
        raise images.ImageTransportError('UNBOUNDED_OR_UNTRUSTED_IMAGE_CLIENT')
    with tempfile.TemporaryDirectory(prefix='.cutover-image-',dir=source.parent) as directory:
        packed = Path(directory)/'image.sqlite3.gz'
        compressed_hash, compressed_size = _pack(source,packed,identity,deadline)
        if _identity(source) != before:
            raise images.ImageTransportError('SOURCE_IMAGE_CHANGED')
        key = f'research/sqlite-cutover/v1/{run_id}/{kind}/{identity["database_sha256"]}/{compressed_hash}.sqlite3.gz'
        receipt = dict(contract=images.CONTRACT,run_id=run_id,kind=kind,bucket=images.BUCKET,
            object_key=key,codec='gzip',**identity,compressed_sha256=compressed_hash,
            compressed_size_bytes=compressed_size)
        metadata = {'contract':images.CONTRACT,'database-sha256':identity['database_sha256'],
            'database-size':str(identity['database_size_bytes']),'compressed-sha256':compressed_hash,
            'compressed-size':str(compressed_size)}
        _check(deadline)
        client.upload_file(str(packed),images.BUCKET,key,
            ExtraArgs={'ContentType':'application/gzip','Metadata':metadata},
            Config=TransferConfig(multipart_chunksize=16*1024**2,max_concurrency=1,use_threads=False),
            Callback=lambda transferred:_check(deadline))
        _check(deadline)
        if _identity(source) != before:
            raise images.ImageTransportError('SOURCE_IMAGE_CHANGED')
        head = client.head_object(Bucket=images.BUCKET,Key=key)
        _check(deadline)
        if (type(head.get('ContentLength')) is not int or head['ContentLength'] != compressed_size
                or normalized_metadata(head.get('Metadata')) != metadata):
            raise images.ImageTransportError('PUBLISHED_IMAGE_HEAD_MISMATCH')
        url = client.generate_presigned_url('get_object',Params={'Bucket':images.BUCKET,'Key':key},
            ExpiresIn=min(43200,max(1,int(deadline-time.monotonic())+1)))
        adapter = images.SignedImageTransport({kind:dict(url=url,receipt=receipt)},
                                              deadline=deadline,opener=opener)
        verified = adapter.verify_image(kind)
        if _identity(source) != before:
            raise images.ImageTransportError('SOURCE_IMAGE_CHANGED')
        return verified
