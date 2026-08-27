from __future__ import annotations

import threading

from seiltanzer import storage_runtime


def test_backup_snapshot_checks_share_one_read_window(tmp_path, monkeypatch):
    snapshot = tmp_path / "immutable.sqlite3"
    snapshot.write_bytes(b"immutable")
    started: set[str] = set()
    lock = threading.Lock()
    all_started = threading.Event()

    def rendezvous(name: str, result):
        with lock:
            started.add(name)
            if len(started) == 3:
                all_started.set()
        assert all_started.wait(2.0), started
        return result

    monkeypatch.setattr(
        storage_runtime, "_sqlite_integrity",
        lambda path, *, full: rendezvous("integrity", (True, "ok")),
    )
    monkeypatch.setattr(
        storage_runtime, "_sha256",
        lambda path: rendezvous("sha256", "a" * 64),
    )
    monkeypatch.setattr(
        storage_runtime, "_table_counts",
        lambda path: rendezvous("counts", {"trades": 7}),
    )

    assert storage_runtime._verify_backup_snapshot(snapshot) == (
        True, "ok", "a" * 64, {"trades": 7},
    )
    assert started == {"integrity", "sha256", "counts"}
