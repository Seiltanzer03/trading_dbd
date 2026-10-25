import gzip
import os
import importlib.util
import hashlib
import io
import json
import sqlite3
import threading
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    'upload_verified_snapshot', Path(__file__).resolve().parents[1] / 'scripts/upload_verified_snapshot.py')
module = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(module)


def test_production_part_size_is_safe_for_slow_yandex_uploads():
    assert module.PART_SIZE == 16 * 1024 * 1024
    assert module.PART_SIZE >= 5 * 1024 * 1024


def test_normalized_metadata_accepts_yandex_preserved_key_casing():
    assert module.normalized_metadata({
        'Source-Sha256': 'abc',
        'Source-Size': '42',
    }) == {
        'source-sha256': 'abc',
        'source-size': '42',
    }


def test_streaming_parts_round_trip_without_staging_archive(tmp_path):
    source = tmp_path / 'database.sqlite3'
    payload = os.urandom(20000) + b'authoritative-row' * 1000
    source.write_bytes(payload)
    parts = list(module.compressed_parts(source, part_size=1024))
    assert len(parts) > 1
    assert all(len(part) == 1024 for part in parts[:-1])
    assert gzip.decompress(b''.join(parts)) == payload


class _ObjectStore:
    """External transport boundary; real uploader owns ordering and lifecycle."""
    def __init__(self, *, fail=False, overlap=False):
        self.parts = {}
        self.active = 0
        self.peak = 0
        self.finished = 0
        self.abort_active = None
        self.completed = False
        self.manifest = None
        self.fail = fail
        self.overlap = overlap
        self.other_started = threading.Event()
        self.other_finished = threading.Event()
        self.lock = threading.Lock()

    def get_bucket_versioning(self, **kwargs):
        return {}

    def create_multipart_upload(self, **kwargs):
        self.metadata = kwargs['Metadata']
        return {'UploadId': 'test-only-upload'}

    def upload_part(self, **kwargs):
        number, body = kwargs['PartNumber'], kwargs['Body']
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if number != 1:
                self.other_started.set()
            if number == 1 and self.overlap:
                assert self.other_finished.wait(2), 'slow part blocked every other upload'
            if number == 2 and self.fail:
                raise RuntimeError('transport part rejected')
            with self.lock:
                self.parts[number] = body
            return {'ETag': hashlib.md5(body).hexdigest()}
        finally:
            with self.lock:
                self.active -= 1
                self.finished += 1
            if number != 1:
                self.other_finished.set()

    def complete_multipart_upload(self, **kwargs):
        assert self.active == 0
        rows = kwargs['MultipartUpload']['Parts']
        assert [r['PartNumber'] for r in rows] == list(range(1, len(self.parts) + 1))
        assert all(r['ETag'] == hashlib.md5(self.parts[r['PartNumber']]).hexdigest() for r in rows)
        self.object = b''.join(self.parts[n] for n in range(1, len(self.parts) + 1))
        self.completed = True

    def abort_multipart_upload(self, **kwargs):
        self.abort_active = self.active

    def head_object(self, **kwargs):
        assert self.completed
        return {'ContentLength': len(self.object), 'Metadata': self.metadata}

    def get_object(self, **kwargs):
        if kwargs['Key'].endswith('.manifest.json'):
            return {'Body': io.BytesIO(self.manifest)}
        start, end = (int(n) for n in kwargs['Range'].removeprefix('bytes=').split('-'))
        return {'Body': io.BytesIO(self.object[start:end + 1])}

    def put_object(self, **kwargs):
        self.manifest = kwargs['Body']


def _upload_fixture(tmp_path, monkeypatch, store, *, bounded=False):
    import boto3
    source = tmp_path / 'verified.sqlite3'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE proof(value BLOB)')
        conn.execute('INSERT INTO proof VALUES (?)', (os.urandom(16000),))
    raw = source.read_bytes()
    compressed = gzip.compress(raw, mtime=0)
    chunks = [compressed[n:n + 1024] for n in range(0, len(compressed), 1024)]
    manifest = tmp_path / 'source.manifest.json'
    manifest.write_text(json.dumps({'database_size_bytes': len(raw),
                                   'database_sha256': hashlib.sha256(raw).hexdigest()}))
    def stream(path):
        assert path == source
        for count, chunk in enumerate(chunks, 1):
            if bounded:
                assert count - store.finished <= 3, 'stream queued more than three part payloads'
            yield chunk
    monkeypatch.setattr(module, 'compressed_parts', stream)
    monkeypatch.setattr(boto3, 'client', lambda *args, **kwargs: store)
    return source, manifest, raw, compressed


def test_slow_part_does_not_serialize_upload_and_completed_object_keeps_stream_identity(tmp_path, monkeypatch):
    store = _ObjectStore(overlap=True)
    source, manifest, raw, compressed = _upload_fixture(tmp_path, monkeypatch, store, bounded=True)
    result = module.upload(source, manifest, bucket='test-only', key='snapshot.gz')
    assert 2 <= store.peak <= 3
    assert next(iter(store.parts)) != 1
    assert gzip.decompress(store.object) == raw
    assert store.object == compressed
    assert result['compressed_sha256'] == hashlib.sha256(compressed).hexdigest()
    assert result['verified_head'] is True and result['verified_boundary_get'] is True
    assert json.loads(store.manifest) == result
    assert store.abort_active is None


def test_part_failure_waits_for_writers_and_aborts_without_completion_or_manifest(tmp_path, monkeypatch):
    store = _ObjectStore(fail=True, overlap=True)
    source, manifest, _, _ = _upload_fixture(tmp_path, monkeypatch, store)
    with pytest.raises(RuntimeError, match='transport part rejected'):
        module.upload(source, manifest, bucket='test-only', key='snapshot.gz')
    assert store.abort_active == 0
    assert not store.completed
    assert store.manifest is None
