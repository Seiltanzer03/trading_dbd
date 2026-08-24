from __future__ import annotations

import hashlib
from pathlib import Path

from seiltanzer import storage_restore_drill as drill


def test_restore_copy_bounds_writeback_and_preserves_exact_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    payload = bytes(range(100))
    source.write_bytes(payload)

    monkeypatch.setattr(drill, "COPY_CHUNK_BYTES", 16)
    monkeypatch.setattr(drill, "WRITEBACK_WINDOW_BYTES", 32)

    sync_calls: list[str] = []
    dropped: list[tuple[str, int, int]] = []

    original_sync = drill._sync_data

    def recording_sync(fh):
        sync_calls.append(str(fh.name))
        original_sync(fh)

    def recording_drop(fh, offset: int, length: int) -> bool:
        dropped.append((str(fh.name), offset, length))
        return True

    monkeypatch.setattr(drill, "_sync_data", recording_sync)
    monkeypatch.setattr(drill, "_drop_file_cache", recording_drop)

    digest, copied, cache_eviction_verified = drill._copy_source_with_sha256(
        source, destination
    )

    assert copied == len(payload)
    assert cache_eviction_verified is True
    assert destination.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert len(sync_calls) == 3

    source_drops = [(offset, length) for name, offset, length in dropped if name == str(source)]
    destination_drops = [
        (offset, length) for name, offset, length in dropped if name == str(destination)
    ]
    assert source_drops == [
        (0, 16), (16, 16), (32, 16), (48, 16), (64, 16), (80, 16), (96, 4),
    ]
    assert destination_drops == [(0, 32), (32, 32), (64, 32), (96, 4)]


def test_restored_hash_scan_drops_consumed_cache_ranges(tmp_path, monkeypatch):
    restored = tmp_path / "restored.sqlite3"
    payload = b"byte-identical-restore-proof" * 5
    restored.write_bytes(payload)

    monkeypatch.setattr(drill, "COPY_CHUNK_BYTES", 13)
    dropped: list[tuple[int, int]] = []

    def recording_drop(_fh, offset: int, length: int) -> bool:
        dropped.append((offset, length))
        return True

    monkeypatch.setattr(drill, "_drop_file_cache", recording_drop)

    digest, cache_eviction_verified = drill._sha256_streaming_no_cache(restored)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert cache_eviction_verified is True
    assert dropped
    assert dropped[0] == (0, 13)
    assert sum(length for _, length in dropped) == len(payload)
    assert dropped[-1][0] + dropped[-1][1] == len(payload)


def test_restore_report_exposes_bounded_page_cache_contract(tmp_path, monkeypatch):
    source = tmp_path / "backup.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    payload = b"sqlite-backup-bytes" * 20
    source.write_bytes(payload)

    monkeypatch.setattr(drill, "COPY_CHUNK_BYTES", 32)
    monkeypatch.setattr(drill, "WRITEBACK_WINDOW_BYTES", 64)

    digest, copied, copy_cache_eviction_verified = drill._copy_source_with_sha256(
        source, destination
    )
    restored_digest, hash_cache_eviction_verified = (
        drill._sha256_streaming_no_cache(destination)
    )

    assert digest == restored_digest == hashlib.sha256(payload).hexdigest()
    assert copied == len(payload)
    assert copy_cache_eviction_verified is True
    assert hash_cache_eviction_verified is True
    assert drill.WRITEBACK_WINDOW_BYTES == 64


def test_failed_cache_eviction_is_reported_for_copy_and_hash(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    source.write_bytes(b"verified-backup-bytes" * 8)

    monkeypatch.setattr(drill, "COPY_CHUNK_BYTES", 16)
    monkeypatch.setattr(drill, "WRITEBACK_WINDOW_BYTES", 32)
    monkeypatch.setattr(drill, "_drop_file_cache", lambda *_args: False)

    _, _, copy_cache_eviction_verified = drill._copy_source_with_sha256(
        source, destination
    )
    _, hash_cache_eviction_verified = drill._sha256_streaming_no_cache(destination)

    assert copy_cache_eviction_verified is False
    assert hash_cache_eviction_verified is False
