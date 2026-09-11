import gzip
import os
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'upload_verified_snapshot', Path(__file__).resolve().parents[1] / 'scripts/upload_verified_snapshot.py')
module = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(module)


def test_streaming_parts_round_trip_without_staging_archive(tmp_path):
    source = tmp_path / 'database.sqlite3'
    payload = os.urandom(20000) + b'authoritative-row' * 1000
    source.write_bytes(payload)
    parts = list(module.compressed_parts(source, part_size=1024))
    assert len(parts) > 1
    assert all(len(part) == 1024 for part in parts[:-1])
    assert gzip.decompress(b''.join(parts)) == payload
