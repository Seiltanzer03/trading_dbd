from pathlib import Path
import importlib.util
import io
import json
import hashlib
from types import SimpleNamespace

import pytest

from test_restore_verified_snapshot import Client, MODULE as verifier, _live_fixture

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/managed_verified_seed.py'
SPEC = importlib.util.spec_from_file_location('managed_verified_seed', SCRIPT)
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


class ManagedClient(Client):
    def download_fileobj(self, bucket, key, output, *, Config):
        assert bucket == self.manifest['bucket'] and key == self.key
        self.staging = output
        self.config = Config
        output.write(self.archive)


def restore(tmp_path, client, manifest):
    return verifier.restore(bucket=manifest['bucket'], key=manifest['object_key'],
        destination=tmp_path/'seed.db', result_path=tmp_path/'seed.json',
        seed_only=True, client=transport.ManagedRestoreClient(client, temp_dir=tmp_path))


def test_managed_download_preserves_canonical_seed_bytes_and_epoch(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    client = ManagedClient(key=key, archive=archive, manifest=manifest)
    result = restore(tmp_path, client, manifest)
    assert result['seed_verified'] is True
    assert result['storage_manifest'] == manifest
    assert result['full_restore_verified'] is False
    assert result['production_authority'] is False
    assert (tmp_path/'seed.db').read_bytes() == (tmp_path/'source.sqlite3').read_bytes()
    assert client.staging.closed


def test_partial_transfer_failure_closes_staging_and_writes_no_receipt(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    class Failed(ManagedClient):
        def download_fileobj(self, bucket, key, output, **kwargs):
            self.staging = output
            output.write(self.archive[:4])
            raise OSError('bounded transfer exhausted')
    client = Failed(key=key, archive=archive, manifest=manifest)
    with pytest.raises(OSError, match='bounded transfer exhausted'):
        restore(tmp_path, client, manifest)
    assert client.staging.closed
    assert not (tmp_path/'seed.db').exists()
    assert not (tmp_path/'seed.json').exists()


def test_corrupt_managed_download_still_fails_original_hash_gate(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    client = ManagedClient(key=key, archive=archive, manifest=manifest)
    manifest['database_sha256'] = '0'*64
    with pytest.raises(RuntimeError, match='hash or size mismatch'):
        restore(tmp_path, client, manifest)
    assert client.staging.closed
    assert not (tmp_path/'seed.db').exists()
    assert not (tmp_path/'seed.json').exists()


def test_compressed_staging_requires_worker_headroom_before_download(tmp_path, monkeypatch):
    key, archive, manifest = _live_fixture(tmp_path)
    client = ManagedClient(key=key, archive=archive, manifest=manifest)
    monkeypatch.setattr(transport.shutil, 'disk_usage', lambda p: SimpleNamespace(free=1))
    with pytest.raises(RuntimeError, match='worker staging'):
        restore(tmp_path, client, manifest)
    assert not hasattr(client, 'staging')
    assert not (tmp_path/'seed.db').exists()
    assert not (tmp_path/'seed.json').exists()


def test_real_sdk_retries_broken_body_with_same_identity_and_verifies_seed(tmp_path, monkeypatch):
    import boto3
    from botocore.response import StreamingBody
    from botocore.stub import Stubber
    from urllib3.exceptions import ProtocolError
    from boto3.s3.transfer import TransferConfig
    key, archive, manifest = _live_fixture(tmp_path)
    # Exercise the real multipart path on a tiny immutable fixture.
    monkeypatch.setattr('boto3.s3.transfer.TransferConfig', lambda **kwargs:
        TransferConfig(**{**kwargs, 'multipart_threshold': 1,
                          'multipart_chunksize': len(archive), 'use_threads': False}))
    sdk = boto3.client('s3', region_name='ru-central1',
        endpoint_url='https://example.invalid', aws_access_key_id='test',
        aws_secret_access_key='test')
    etag = '"'+hashlib.md5(archive).hexdigest()+'"'
    head = {**Client(key=key, archive=archive, manifest=manifest).head_object(
        Bucket=manifest['bucket'], Key=key), 'ETag': etag}
    class Broken(io.BytesIO):
        def read(self, *args):
            raise ProtocolError('connection broken', OSError('incomplete response'))
    raw = Broken(archive)
    params = []
    sdk.meta.events.register('before-parameter-build.s3.GetObject',
        lambda params, **kwargs: capture(params))
    def capture(value): params.append(dict(value))
    with Stubber(sdk) as stub:
        stub.add_response('get_object', {'Body': StreamingBody(io.BytesIO(json.dumps(manifest).encode()), len(json.dumps(manifest).encode()))})
        stub.add_response('head_object', head)
        stub.add_response('head_object', head)
        stub.add_response('get_object', {'Body': StreamingBody(raw, len(archive)), 'ContentLength': len(archive), 'ETag': etag})
        stub.add_response('get_object', {'Body': StreamingBody(io.BytesIO(archive), len(archive)), 'ContentLength': len(archive), 'ETag': etag})
        result = restore(tmp_path, sdk, manifest)
        stub.assert_no_pending_responses()
    requests = [p for p in params if p['Key'] == key]
    assert len(requests) == 2 and requests[0] == requests[1]
    assert all(p['IfMatch'] == etag for p in requests)
    raw.close()
    assert result['seed_verified'] is True and result['storage_manifest'] == manifest
    assert (tmp_path/'seed.db').read_bytes() == (tmp_path/'source.sqlite3').read_bytes()
