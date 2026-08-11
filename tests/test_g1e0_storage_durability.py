from __future__ import annotations

import sqlite3
from pathlib import Path

from seiltanzer.config import Settings
from seiltanzer.engine import Engine
from seiltanzer.storage_refinement import install_storage_refinement
from seiltanzer.storage_runtime import (
    BACKUP_CONTRACT_VERSION,
    StorageManager,
    prepare_storage,
)

install_storage_refinement()


def _minimal_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY, status TEXT, result_r REAL)")
        conn.execute("INSERT INTO trades(id,status,result_r) VALUES(1,'closed',1.25)")
        conn.commit()
    finally:
        conn.close()


def test_prepare_storage_creates_verified_prestart_backup(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    db = Path(settings.trades_db)
    _minimal_db(db)

    manager = prepare_storage(settings, git_commit="test-sha")
    report = manager.backups()

    assert report["local"]
    latest = report["local"][0]
    assert latest["backup_contract_version"] == BACKUP_CONTRACT_VERSION
    assert latest["verified"] is True
    assert latest["reason"] == "prestart"
    assert latest["git_commit"] == "test-sha"
    assert latest["critical_table_counts"]["trades"] == 1
    assert "passive_market_observations" in latest["critical_table_counts"]
    assert "g1_q_capture_attempts" in latest["critical_table_counts"]
    assert latest["encryption_verified"] is False
    backup_path = manager.local_dir / latest["database_file"]
    assert backup_path.exists()


def test_unclean_then_clean_shutdown_marker_is_detected(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _minimal_db(Path(settings.trades_db))
    first = StorageManager(settings)
    first.mark_startup()

    second = StorageManager(settings)
    assert second.previous_shutdown == "UNCLEAN"

    second.mark_startup()
    second.mark_clean_shutdown()
    third = StorageManager(settings)
    assert third.previous_shutdown == "CLEAN"


def test_verified_backup_can_restore_destroyed_database(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    db = Path(settings.trades_db)
    _minimal_db(db)
    manager = StorageManager(settings)
    result = manager.create_backup(kind="local", reason="test")

    # Destroy the working copy; restoration must use only the frozen verified backup.
    db.unlink()
    restored = StorageManager.restore_verified_backup(
        backup_db=result.database_path,
        manifest_path=result.manifest_path,
        destination_db=db,
        preserve_existing=True,
    )
    assert restored["ok"] is True
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT result_r FROM trades WHERE id=1").fetchone()[0] == 1.25
    finally:
        conn.close()


def test_backup_sha_tamper_is_rejected(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    db = Path(settings.trades_db)
    _minimal_db(db)
    manager = StorageManager(settings)
    result = manager.create_backup(kind="local", reason="test")
    with open(result.database_path, "ab") as fh:
        fh.write(b"tamper")

    try:
        StorageManager.restore_verified_backup(
            backup_db=result.database_path,
            manifest_path=result.manifest_path,
            destination_db=tmp_path / "restored.db",
        )
    except ValueError as exc:
        assert "SHA256" in str(exc)
    else:
        raise AssertionError("tampered backup must be rejected")


def test_reconcile_repairs_closed_trade_position_gap_after_crash(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    engine = Engine(settings)
    try:
        trade = engine.journal.open_trade(
            setup=1, instrument="NAS100", direction="long",
            entry=100.0, stop=90.0, take=125.0,
            notes="durability-test", zones=[],
        )
        engine.position.open_trade(trade)
        # Simulate a crash after journal close commit but before position terminal event.
        engine.journal.close_trade(int(trade["id"]), 1.5, "closed")
        before = engine.position.state(trade)
        assert before["remaining_position_fraction"] == 1.0

        manager = StorageManager(settings)
        actions = manager.reconcile_economic_state(engine)
        after = engine.position.state(trade)
        assert after["remaining_position_fraction"] == 0.0
        assert any(item["action"] == "RECOVER_CLOSED_POSITION_REMAINDER" for item in actions)
    finally:
        engine.close()


def test_reconcile_does_not_create_position_events_for_historical_backfill(tmp_path):
    settings = Settings(demo=True, data_dir=str(tmp_path))
    engine = Engine(settings)
    try:
        historic = engine.journal.add_closed(
            setup=1, direction="long", entry=100.0, stop=90.0,
            take=125.0, result_r=1.0, notes="historic-backfill",
        )
        with engine.position._lock:
            before = engine.position._conn.execute(
                "SELECT COUNT(*) FROM position_management_events WHERE trade_id=?",
                (int(historic["id"]),),
            ).fetchone()[0]
        assert before == 0

        actions = StorageManager(settings).reconcile_economic_state(engine)
        with engine.position._lock:
            after = engine.position._conn.execute(
                "SELECT COUNT(*) FROM position_management_events WHERE trade_id=?",
                (int(historic["id"]),),
            ).fetchone()[0]
        assert after == 0
        assert not any(item.get("trade_id") == int(historic["id"]) for item in actions)
    finally:
        engine.close()


def test_status_is_local_backup_only_without_offhost_target(tmp_path, monkeypatch):
    monkeypatch.delenv("SEILTANZER_OFFHOST_BACKUP_DIR", raising=False)
    settings = Settings(demo=True, data_dir=str(tmp_path))
    _minimal_db(Path(settings.trades_db))
    manager = StorageManager(settings)
    manager.create_backup(kind="local", reason="test")
    status = manager.status()
    assert status["sqlite_integrity"]["ok"] is True
    assert status["health"] == "LOCAL_BACKUP_ONLY"
    assert status["persistent_db_authority"] is True
    assert status["ram_authority"] is False
