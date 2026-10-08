import gzip
import hashlib
import io
import json

import pytest

from seiltanzer import edge_family_private_archive as storage


class Missing(Exception):
    response = {'Error': {'Code': 'NoSuchKey'}}


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.corrupt_put = False

    def get_object(self, *, Bucket, Key):
        assert Bucket == storage.BUCKET
        if Key not in self.objects:
            raise Missing()
        raw = self.objects[Key]
        return {'ContentLength': len(raw), 'Body': io.BytesIO(raw)}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        assert Bucket == storage.BUCKET
        self.objects[Key] = Body + (b'corrupt' if self.corrupt_put else b'')

    def get_bucket_versioning(self, *, Bucket):
        return {}


def archive():
    episodes = [{'captured_ts': 123.0, 'exported_ts': 124.0,
                 'source_sha': 'a' * 40, 'review_id': 'private-review'}]
    encoded = json.dumps(episodes, sort_keys=True, separators=(',', ':')).encode()
    return json.dumps({'contract_version': 'edge-family-archive-v1',
                       'episodes': episodes, 'exclusions': [], 'evictions': [],
                       'dataset_sha256': hashlib.sha256(encoded).hexdigest()},
                      sort_keys=True, separators=(',', ':')).encode()


def save(client, raw=None, generation=1, previous=None):
    return storage.store(client, raw or archive(), source_sha='b' * 40,
                         generation=generation, previous_receipt=previous,
                         uploaded_ts=1000.0)


def test_roundtrip_preserves_bytes_native_clocks_and_historical_sha():
    client = MemoryStore()
    assert storage.restore(client) == (None, None)
    receipt = save(client)
    restored, found = storage.restore(client)
    assert restored == archive()
    assert found == receipt
    assert receipt['source_sha'] == 'b' * 40
    assert receipt['production_authority'] is False
    assert receipt['backup_retirement_authority'] is False


def test_alternating_slots_bound_objects_and_preserve_latest_on_failed_upload():
    client = MemoryStore()
    first = save(client)
    second = save(client, generation=2, previous=first)
    assert second['slot'] != first['slot']
    client.corrupt_put = True
    with pytest.raises(ValueError):
        save(client, generation=3, previous=second)
    assert storage.restore(client) == (archive(), second)
    assert len(client.objects) == 3


@pytest.mark.parametrize('generation,previous', [(1, 'same'), (0, 'same'), (2, None)])
def test_stale_generation_or_unrestored_history_cannot_overwrite(generation, previous):
    client = MemoryStore()
    first = save(client)
    before = dict(client.objects)
    with pytest.raises(ValueError):
        save(client, generation=generation, previous=first if previous else None)
    assert client.objects == before


def test_changed_latest_is_rejected_before_inactive_slot_write():
    client = MemoryStore()
    first = save(client)
    save(client, generation=2, previous=first)
    before = dict(client.objects)
    with pytest.raises(ValueError):
        save(client, generation=3, previous=first)
    assert client.objects == before


def test_inaccessible_latest_is_not_initial():
    client = MemoryStore()
    def denied(**kwargs):
        raise PermissionError('denied')
    client.get_object = denied
    with pytest.raises(PermissionError):
        storage.restore(client)


@pytest.mark.parametrize('kind', ['corrupt_latest', 'foreign_key', 'bad_hash', 'bad_source_sha'])
def test_bad_latest_fails_without_falling_back_to_old_slot(kind):
    client = MemoryStore()
    first = save(client)
    save(client, generation=2, previous=first)
    latest = json.loads(client.objects[storage.LATEST_KEY])
    if kind == 'foreign_key':
        latest['object_key'] = 'backups/v1/daily-slot-0/snapshot.sqlite3.gz'
    elif kind == 'bad_hash':
        latest['raw_sha256'] = '0' * 64
    elif kind == 'bad_source_sha':
        latest['source_sha'] = 'invalid'
    client.objects[storage.LATEST_KEY] = b'bad' if kind == 'corrupt_latest' else json.dumps(latest).encode()
    with pytest.raises(ValueError):
        storage.restore(client)


@pytest.mark.parametrize('raw', [b'{"contract_version":"wrong"}',
                               b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'])
def test_invalid_archive_rejected_before_storage_write(raw):
    client = MemoryStore()
    with pytest.raises(ValueError):
        save(client, raw=raw)
    assert not client.objects


def test_bounded_read_and_gzip_decompression(monkeypatch):
    client = MemoryStore()
    receipt = save(client)
    bomb = gzip.compress(b'x' * 5000)
    receipt.update(compressed_size_bytes=len(bomb),
                   compressed_sha256=hashlib.sha256(bomb).hexdigest())
    client.objects[receipt['object_key']] = bomb
    client.objects[storage.LATEST_KEY] = json.dumps(receipt).encode()
    monkeypatch.setattr(storage, 'MAX_RAW_BYTES', 1000)
    with pytest.raises(ValueError):
        storage.restore(client)
    client.objects[storage.LATEST_KEY] = b'x' * (storage.MAX_RECEIPT_BYTES + 1)
    with pytest.raises(ValueError):
        storage.restore(client)


def test_versioned_bucket_is_refused_before_write():
    client = MemoryStore()
    client.get_bucket_versioning = lambda **kwargs: {'Status': 'Enabled'}
    with pytest.raises(ValueError):
        save(client)
    assert not client.objects


@pytest.mark.parametrize('suffix', [b'trailing', gzip.compress(b'second member')])
def test_verified_compressed_hash_cannot_hide_trailing_gzip_members(suffix):
    client = MemoryStore()
    receipt = save(client)
    compressed = client.objects[receipt['object_key']] + suffix
    receipt.update(compressed_size_bytes=len(compressed),
                   compressed_sha256=hashlib.sha256(compressed).hexdigest())
    client.objects[receipt['object_key']] = compressed
    client.objects[storage.LATEST_KEY] = json.dumps(receipt).encode()
    with pytest.raises(ValueError):
        storage.restore(client)


def test_missing_committed_payload_is_not_initial():
    client = MemoryStore()
    receipt = save(client)
    del client.objects[receipt['object_key']]
    with pytest.raises(Missing):
        storage.restore(client)


def test_short_stream_reads_are_accumulated_and_truncation_rejected():
    client = MemoryStore()
    receipt = save(client)
    get = client.get_object
    class ShortReader(io.BytesIO):
        def read(self, amount=-1):
            return super().read(min(amount, 7))
    def short(**kwargs):
        response = get(**kwargs)
        response['Body'] = ShortReader(response['Body'].read())
        return response
    client.get_object = short
    assert storage.restore(client) == (archive(), receipt)
    client.get_object = lambda **kwargs: {'ContentLength': 10, 'Body': io.BytesIO(b'bad')}
    with pytest.raises(ValueError, match='LENGTH_MISMATCH'):
        storage.restore(client)
