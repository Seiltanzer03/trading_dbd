"""Private cloud streams verify real SQLite bytes without exposing signed URLs."""
import copy
import gzip
import hashlib
import importlib
import io
import sqlite3
import time
from types import SimpleNamespace

import pytest

from test_sqlite_history_transaction import setup_transaction


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {'Content-Length': str(len(body)), **(headers or {})}


def image(raw, kind='original'):
    packed = gzip.compress(raw, mtime=0)
    raw_hash = hashlib.sha256(raw).hexdigest()
    compressed_hash = hashlib.sha256(packed).hexdigest()
    key = f'research/sqlite-cutover/v1/run-001/{kind}/{raw_hash}/{compressed_hash}.sqlite3.gz'
    receipt = dict(contract='sqlite-history-image-v1',run_id='run-001',kind=kind,
        bucket='trading-dbd-backups-2026',object_key=key,codec='gzip',
        database_sha256=raw_hash,database_size_bytes=len(raw),
        compressed_sha256=compressed_hash,compressed_size_bytes=len(packed))
    return dict(url='https://storage.yandexcloud.net/'+receipt['bucket']+'/'+key+
                    '?X-Amz-Signature=PRIVATE-TEST-SECRET',receipt=receipt), packed


@pytest.fixture
def subject(tmp_path):
    module = importlib.import_module('scripts.history_image_transport')
    path = tmp_path/'actual.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE preserved(id INTEGER PRIMARY KEY, value BLOB)')
        conn.execute('INSERT INTO preserved VALUES(?,?)',(12,b'actual-source'*150000))
    raw = path.read_bytes()
    entry, packed = image(raw)
    return SimpleNamespace(module=module, raw=raw, entry=entry, packed=packed)


def transport(subject, *, entry=None, response=None, deadline=None, opener=None):
    response = Response(subject.packed) if response is None else response
    return subject.module.SignedImageTransport({'original':entry or subject.entry},
        deadline=time.monotonic()+5 if deadline is None else deadline,
        opener=opener or (lambda request,timeout: response)), response


def test_complete_gzip_sqlite_read_binds_both_hashes_and_safe_receipt(subject):
    adapter, response = transport(subject)
    receipt = adapter.verify_image('original')
    assert receipt == dict(subject.entry['receipt'],full_get_verified=True)
    assert response.closed
    assert 'PRIVATE-TEST-SECRET' not in repr(adapter)+str(receipt)


def test_stream_matches_existing_transaction_and_closes_on_abort(subject):
    adapter, response = transport(subject)
    with adapter.open_image('original') as stream:
        assert stream.read(1234) == subject.raw[:1234]
    assert response.closed


@pytest.mark.parametrize('hazard',['raw_hash','compressed_hash','short','extra_raw','extra_compressed','length','status','encoding'])
def test_corrupt_or_unsupported_stream_never_produces_full_receipt(subject,hazard):
    entry = copy.deepcopy(subject.entry)
    packed = subject.packed
    headers = {}; status = 200
    if hazard == 'raw_hash':
        entry['receipt']['database_sha256'] = 'f'*64
    elif hazard == 'compressed_hash':
        entry['receipt']['compressed_sha256'] = 'f'*64
    elif hazard == 'short':
        packed = packed[:-8]
    elif hazard == 'extra_raw':
        packed = gzip.compress(subject.raw+b'x',mtime=0)
    elif hazard == 'extra_compressed':
        packed += b'\0'
    elif hazard == 'length':
        headers['Content-Length'] = str(len(packed)+1)
    elif hazard == 'status':
        status = 206
    else:
        headers['Content-Encoding'] = 'gzip'
    # Hash mutations must also mutate the key/URL so input validation succeeds;
    # the actual transferred content then fails its pinned proof.
    if hazard in ('raw_hash','compressed_hash'):
        key = ('research/sqlite-cutover/v1/run-001/original/'+entry['receipt']['database_sha256']+
               '/'+entry['receipt']['compressed_sha256']+'.sqlite3.gz')
        entry['receipt']['object_key'] = key
        entry['url'] = 'https://storage.yandexcloud.net/trading-dbd-backups-2026/'+key+'?X-Amz-Signature=PRIVATE-TEST-SECRET'
    response = Response(packed,status=status,headers=headers)
    with pytest.raises(ValueError) as failure:
        adapter,_ = transport(subject,entry=entry,response=response)
        adapter.verify_image('original')
    assert 'PRIVATE-TEST-SECRET' not in str(failure.value)
    assert response.closed


@pytest.mark.parametrize('hazard',['http','host','userinfo','port','fragment','encoded_path','wrong_key','extra_receipt'])
def test_unsafe_private_origin_or_receipt_is_refused_before_network(subject,hazard):
    entry = copy.deepcopy(subject.entry)
    if hazard == 'http': entry['url'] = entry['url'].replace('https:','http:')
    elif hazard == 'host': entry['url'] = entry['url'].replace('storage.yandexcloud.net','storage.yandexcloud.net.evil.invalid')
    elif hazard == 'userinfo': entry['url'] = entry['url'].replace('https://','https://secret@')
    elif hazard == 'port': entry['url'] = entry['url'].replace('.net/','.net:443/')
    elif hazard == 'fragment': entry['url'] += '#private'
    elif hazard == 'encoded_path': entry['url'] = entry['url'].replace('/research/','/%72esearch/')
    elif hazard == 'wrong_key': entry['receipt']['object_key'] += 'wrong'
    else: entry['receipt']['url'] = entry['url']
    def network(*args,**kwargs):
        pytest.fail('unsafe input reached network')
    with pytest.raises(ValueError) as failure:
        transport(subject,entry=entry,opener=network)
    assert 'PRIVATE-TEST-SECRET' not in str(failure.value)


def test_caller_mutation_cannot_replace_pinned_original(subject):
    entry = copy.deepcopy(subject.entry)
    adapter,_ = transport(subject,entry=entry)
    entry['receipt']['database_sha256'] = 'f'*64
    entry['url'] = 'https://evil.invalid/?PRIVATE-TEST-SECRET'
    assert adapter.verify_image('original')['database_sha256'] == hashlib.sha256(subject.raw).hexdigest()


def test_expired_or_stalled_transfer_has_finite_deadline(subject,monkeypatch):
    with pytest.raises(ValueError):
        adapter,_ = transport(subject,deadline=time.monotonic()-1)
        adapter.verify_image('original')
    clock = [0.0]
    monkeypatch.setattr(subject.module.time,'monotonic',lambda:clock[0])
    class Slow(Response):
        def read(self,*args):
            clock[0] = 6.0
            return super().read(*args)
    adapter,response = transport(subject,response=Slow(subject.packed),deadline=5.0)
    with pytest.raises(ValueError): adapter.verify_image('original')
    assert response.closed


def test_unbounded_decoded_reads_are_refused(subject):
    adapter,_ = transport(subject)
    with adapter.open_image('original') as stream:
        for size in (-1,0,1024**2+1,True):
            with pytest.raises(ValueError): stream.read(size)


def test_network_error_and_redirect_do_not_expose_signed_query(subject):
    def broken(*args,**kwargs):
        raise OSError(subject.entry['url'])
    adapter,_ = transport(subject,opener=broken)
    with pytest.raises(ValueError) as failure: adapter.verify_image('original')
    assert 'PRIVATE-TEST-SECRET' not in str(failure.value)
    handler = subject.module.NoRedirect()
    with pytest.raises(ValueError) as failure:
        handler.redirect_request(None,None,302,'redirect',{},subject.entry['url'])
    assert 'PRIVATE-TEST-SECRET' not in str(failure.value)


def test_cloud_format_adapter_is_consumed_by_real_sqlite_transaction(subject,setup_transaction):
    from scripts import sqlite_history_transaction as transaction
    root,plan,_,control,original,hot = setup_transaction
    inputs = {}; bodies = {}
    for kind,raw in (('original',original),('hot',hot)):
        entry,packed = image(raw,kind)
        inputs[kind] = entry; bodies[entry['url']] = packed
    adapter = subject.module.SignedImageTransport(inputs,deadline=time.monotonic()+5,
        opener=lambda request,timeout:Response(bodies[request.full_url]))
    result = transaction.execute_cutover(root,plan,transport=adapter,control=control)
    assert result['phase'] == 'COMMITTED'
    assert (root/'trades.db').read_bytes() == hot
    assert control.starts == 1


def test_corrupted_cloud_hot_stream_restores_actual_original(subject,setup_transaction):
    from scripts import sqlite_history_transaction as transaction
    root,plan,_,control,original,hot = setup_transaction
    inputs = {}; bodies = {}; requests = []
    for kind,raw in (('original',original),('hot',hot)):
        entry,packed = image(raw,kind)
        inputs[kind] = entry
        if kind == 'hot':
            packed = packed[:-8]+bytes([packed[-8]^1])+packed[-7:]
        bodies[entry['url']] = packed
    def opener(request,timeout):
        assert 0 < timeout <= 10
        requests.append(request.full_url)
        return Response(bodies[request.full_url])
    adapter = subject.module.SignedImageTransport(inputs,deadline=time.monotonic()+5,opener=opener)
    result = transaction.execute_cutover(root,plan,transport=adapter,control=control)
    assert result['phase'] == 'ORIGINAL_RESTORED'
    assert (root/'trades.db').read_bytes() == original
    assert requests == [inputs['hot']['url'],inputs['original']['url']]
    assert control.starts == 1


def test_transport_preserves_consumer_fence_loss_type(subject):
    from scripts.sqlite_history_transaction import FencingError
    adapter,response = transport(subject)
    with pytest.raises(FencingError):
        with adapter.open_image('original'):
            raise FencingError('WRITER_RETURNED')
    assert response.closed
