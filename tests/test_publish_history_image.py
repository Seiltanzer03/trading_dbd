"""Cloud I/O is injected; publication consumes real SQLite and gzip bytes."""
import gzip
import hashlib
import importlib
import io
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest


class MemoryClient:
    def __init__(self):
        self.meta = SimpleNamespace(endpoint_url='https://storage.yandexcloud.net',
            config=SimpleNamespace(connect_timeout=10,read_timeout=120,retries={'total_max_attempts':1}))
        self.objects = {}; self.calls = []; self.hazard = None; self.source = None

    def upload_file(self,path,bucket,key,ExtraArgs,Config,Callback):
        assert Config.use_threads is False
        assert Config.multipart_chunksize == 16*1024**2
        assert Path(path).stat().st_mode & 0o077 == 0
        if self.hazard == 'upload_failure':
            raise OSError('PRIVATE-PROVIDER-SECRET')
        self.calls.append((bucket,key))
        self.objects[key] = (Path(path).read_bytes(),ExtraArgs['Metadata'])
        Callback(len(self.objects[key][0]))
        if self.hazard == 'source_mutation':
            self.source.write_bytes(self.source.read_bytes()+b'changed')

    def head_object(self,**kwargs):
        body,metadata = self.objects[kwargs['Key']]
        return dict(ContentLength=len(body)+(1 if self.hazard == 'head' else 0),
                    Metadata={k.title():v for k,v in metadata.items()})

    def generate_presigned_url(self,operation,Params,ExpiresIn):
        assert operation == 'get_object'
        assert Params['Bucket'] == 'trading-dbd-backups-2026'
        assert 1 <= ExpiresIn <= 43200
        return 'https://storage.yandexcloud.net/'+Params['Bucket']+'/'+Params['Key']+'?X-Amz-Signature=PRIVATE-PROVIDER-SECRET'

    def opener(self,request,timeout):
        key = request.full_url.split('/trading-dbd-backups-2026/',1)[1].split('?',1)[0]
        body = self.objects[key][0]
        if self.hazard == 'get': body = body[:-1]+bytes([body[-1]^1])
        response = io.BytesIO(body)
        response.status = 200
        response.headers = {'Content-Length':str(len(body))}
        return response


@pytest.fixture
def subject(tmp_path):
    module = importlib.import_module('scripts.publish_history_image')
    path = tmp_path/'original.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE observations(id INTEGER PRIMARY KEY,payload TEXT)')
        conn.execute('INSERT INTO observations VALUES(?,?)',(55,'actual historical data'))
    raw = path.read_bytes()
    identity = dict(database_sha256=hashlib.sha256(raw).hexdigest(),database_size_bytes=len(raw))
    client = MemoryClient();client.source = path
    return SimpleNamespace(module=module,path=path,raw=raw,identity=identity,client=client)


def publish(subject,**kwargs):
    return subject.module.publish_image(subject.path,subject.identity,run_id='run-001',kind='original',
        client=subject.client,deadline=time.monotonic()+5,opener=subject.client.opener,**kwargs)


def test_actual_sqlite_is_addressed_by_both_hashes_and_fully_read_back(subject):
    receipt = publish(subject)
    bucket,key = subject.client.calls[0]
    packed = subject.client.objects[key][0]
    assert gzip.decompress(packed) == subject.raw
    assert receipt['full_get_verified'] is True
    assert receipt['database_sha256'] == hashlib.sha256(subject.raw).hexdigest()
    assert receipt['compressed_sha256'] == hashlib.sha256(packed).hexdigest()
    assert key == ('research/sqlite-cutover/v1/run-001/original/'+receipt['database_sha256']+
                   '/'+receipt['compressed_sha256']+'.sqlite3.gz')
    assert bucket == receipt['bucket'] == 'trading-dbd-backups-2026'
    assert 'PRIVATE-PROVIDER-SECRET' not in str(receipt)
    assert subject.path.read_bytes() == subject.raw
    assert not list(subject.path.parent.glob('.cutover-image-*'))


@pytest.mark.parametrize('hazard',['raw_hash','raw_size','sidecar','symlink','endpoint','request_budget','capacity'])
def test_invalid_source_or_resource_never_reaches_upload(subject,hazard,monkeypatch):
    if hazard == 'raw_hash':subject.identity['database_sha256'] = 'f'*64
    elif hazard == 'raw_size':subject.identity['database_size_bytes'] += 1
    elif hazard == 'sidecar':Path(str(subject.path)+'-wal').write_bytes(b'x')
    elif hazard == 'symlink':
        real = subject.path.with_suffix('.real');subject.path.rename(real);subject.path.symlink_to(real)
    elif hazard == 'endpoint':subject.client.meta.endpoint_url = 'https://other.invalid'
    elif hazard == 'request_budget':subject.client.meta.config.retries = {'total_max_attempts':5}
    else:monkeypatch.setattr(subject.module.shutil,'disk_usage',lambda _:SimpleNamespace(free=1))
    with pytest.raises(ValueError):publish(subject)
    assert subject.client.calls == []
    assert not list(subject.path.parent.glob('.cutover-image-*'))


@pytest.mark.parametrize('hazard',['source_mutation','upload_failure','head','get'])
def test_publication_or_readback_failure_never_mints_receipt_and_cleans_staging(subject,hazard):
    subject.client.hazard = hazard
    with pytest.raises(ValueError) as failure:publish(subject)
    assert 'PRIVATE-PROVIDER-SECRET' not in str(failure.value)
    assert not list(subject.path.parent.glob('.cutover-image-*'))


def test_factory_uses_existing_yandex_endpoint_and_finite_path_style_client(subject,monkeypatch):
    monkeypatch.setenv('AWS_ACCESS_KEY_ID','test-only')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','test-only')
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED','true')
    client = subject.module.yandex_image_client()
    assert client.meta.endpoint_url == 'https://storage.yandexcloud.net'
    assert client.meta.config.signature_version == 's3v4'
    assert client.meta.config.s3['addressing_style'] == 'path'
    assert client.meta.config.connect_timeout == 10
    assert client.meta.config.read_timeout == 120
    assert client.meta.config.retries['total_max_attempts'] == 1
