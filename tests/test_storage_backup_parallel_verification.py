from __future__ import annotations

import threading

from seiltanzer import storage_runtime


def test_backup_snapshot_checks_share_one_read_window(tmp_path, monkeypatch):
    snapshot = tmp_path / "immutable.sqlite3"
    source = tmp_path / "source.sqlite3"
    snapshot.write_bytes(b"immutable")
    source.write_bytes(b"source")
    started: set[str] = set()
    lock = threading.Lock()
    all_started = threading.Event()

    def rendezvous(name: str, result):
        with lock:
            started.add(name)
            if len(started) == 4:
                all_started.set()
        assert all_started.wait(2.0), started
        return result

    monkeypatch.setattr(
        storage_runtime, "_sqlite_integrity",
        lambda path, *, full: rendezvous(
            "snapshot_integrity" if full else "source_quick_check",
            (True, "ok"),
        ),
    )
    monkeypatch.setattr(
        storage_runtime, "_sha256",
        lambda path: rendezvous("sha256", "a" * 64),
    )
    monkeypatch.setattr(
        storage_runtime, "_table_counts",
        lambda path: rendezvous("counts", {"trades": 7}),
    )

    assert storage_runtime._verify_backup_snapshot(
        snapshot,
        startup_source=source,
    ) == (
        True, "ok", "a" * 64, {"trades": 7}, (True, "ok"),
    )
    assert started == {
        "snapshot_integrity", "source_quick_check", "sha256", "counts",
    }
