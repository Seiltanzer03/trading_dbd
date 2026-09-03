from pathlib import Path
from types import SimpleNamespace

import errno
import sqlite3

import pytest

from seiltanzer import storage_availability_guard as availability
from seiltanzer import storage_runtime as storage


def _manager(tmp_path: Path) -> storage.StorageManager:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "trades.db"
    conn = sqlite3.connect(source)
    try:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO trades(payload) VALUES ('authoritative')")
        conn.commit()
    finally:
        conn.close()
    return storage.StorageManager(
        SimpleNamespace(data_dir=str(data), trades_db=str(source)),
        git_commit="availability-test-sha",
    )


def test_degraded_prestart_reuses_verified_backup_without_touching_source(tmp_path):
    manager = _manager(tmp_path)
    result = manager.create_backup(kind="local", reason="prestart")
    before_source_sha = storage._sha256(manager.db_path)
    manifest_before = storage._read_json(Path(result.manifest_path))
    assert manifest_before is not None
    original_created = float(manifest_before["created_ts"])

    reused = availability._reuse_verified_for_degraded_prestart(
        manager,
        directory=manager.local_dir,
        original_error=OSError(errno.ENOSPC, "production disk full"),
    )

    manifest_after = storage._read_json(Path(result.manifest_path))
    assert reused.backup_id == result.backup_id
    assert reused.created_ts == original_created
    assert manifest_after is not None
    assert float(manifest_after["created_ts"]) == original_created
    assert storage._sha256(manager.db_path) == before_source_sha
    assert manager._prestart_integrity_ready is True
    assert manager._startup_integrity["ok"] is True
    assert manager._startup_integrity["durability_degraded"] is True
    assert manager._startup_integrity["reason"] == availability.DEGRADED_REUSE_REASON
    action = manager._recovery_actions[-1]
    assert action["authoritative_db_deleted"] is False
    assert action["authoritative_db_modified"] is False
    assert action["backup_sha256_reverified"] is True


def test_degraded_prestart_refuses_missing_recovery_point(tmp_path):
    manager = _manager(tmp_path)
    original = OSError(errno.ENOSPC, "no room")
    with pytest.raises(OSError) as exc:
        availability._reuse_verified_for_degraded_prestart(
            manager,
            directory=manager.local_dir,
            original_error=original,
        )
    assert exc.value is original


def test_background_impossibility_is_proved_without_full_database_scan(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path)
    monkeypatch.setattr(availability, "_available_bytes", lambda _directory: 1000)
    calls = {"n": 0}

    def lightweight(_source):
        calls["n"] += 1
        return {
            "page_size": 4096,
            "page_count": 100,
            "freelist_count": 0,
            "reclaimable_bytes": 0,
            "compact_required_bytes": 10_000,
        }

    monkeypatch.setattr(availability, "_lightweight_compact_plan", lightweight)
    impossible, plan = availability._background_copy_is_impossible(
        manager, manager.local_dir
    )
    assert impossible is True
    assert calls["n"] == 1
    assert plan["reclaimable_bytes"] == 0
    assert plan["available_bytes"] == 1000


def test_background_impossibility_allows_compaction_when_it_can_fit(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path)
    monkeypatch.setattr(availability, "_available_bytes", lambda _directory: 5000)
    monkeypatch.setattr(
        availability,
        "_lightweight_compact_plan",
        lambda _source: {
            "page_size": 4096,
            "page_count": 100,
            "freelist_count": 50,
            "reclaimable_bytes": 4096 * 50,
            "compact_required_bytes": 4000,
        },
    )
    impossible, plan = availability._background_copy_is_impossible(
        manager, manager.local_dir
    )
    assert impossible is False
    assert plan["reclaimable_bytes"] > 0
