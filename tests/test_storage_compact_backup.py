from pathlib import Path
from types import SimpleNamespace

import sqlite3

from seiltanzer import storage_disk_guard as guard
from seiltanzer import storage_refinement as refinement
from seiltanzer import storage_runtime as storage
from seiltanzer import storage_single_slot_rotation as rotation


def _build_fragmented_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        for table in storage.CRITICAL_TABLES:
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" '
                "(id INTEGER PRIMARY KEY, payload BLOB)"
            )
            conn.execute(
                f'INSERT INTO "{table}" (payload) VALUES (?)',
                (b"critical",),
            )
        conn.execute(
            "CREATE TABLE padding (id INTEGER PRIMARY KEY, payload BLOB)"
        )
        payload = b"x" * (32 * 1024)
        conn.executemany(
            "INSERT INTO padding (payload) VALUES (?)",
            ((payload,) for _ in range(1024)),
        )
        conn.commit()
        conn.execute("DELETE FROM padding")
        conn.commit()
    finally:
        conn.close()


def _manager(data_dir: Path, source: Path, backup_dir: Path) -> storage.StorageManager:
    settings = SimpleNamespace(data_dir=str(data_dir), trades_db=str(source))
    manager = storage.StorageManager(settings, git_commit="compact-test-sha")
    manager.local_dir = backup_dir.resolve()
    return manager


def test_compact_backup_is_verified_and_does_not_modify_source(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "trades.db"
    backup_dir = data_dir / "backups" / "local"
    backup_dir.mkdir(parents=True)
    _build_fragmented_database(source)

    source_sha_before = storage._sha256(source)
    source_size_before = source.stat().st_size
    source_counts = storage._table_counts(source)
    plan = guard._compact_snapshot_plan(source)

    assert plan["freelist_count"] > 0
    assert plan["reclaimable_bytes"] > 0
    assert plan["logical_used_bytes"] < plan["logical_database_bytes"]

    manager = _manager(data_dir, source, backup_dir)
    result = guard._create_compact_backup(
        manager,
        kind="local",
        reason="prestart",
        plan=plan,
    )

    backup = Path(result.database_path)
    manifest = storage._read_json(Path(result.manifest_path))
    assert result.verified is True
    assert backup.is_file()
    assert backup.stat().st_size < source_size_before
    assert storage._sha256(source) == source_sha_before
    assert storage._table_counts(source) == source_counts
    assert storage._table_counts(backup) == source_counts
    assert storage._sqlite_integrity(backup, full=True) == (True, "ok")
    assert manager._prestart_integrity_ready is True
    assert manifest is not None
    assert manifest["verified"] is True
    assert manifest["snapshot_mode"] == "vacuum_into_compact"
    assert manifest["git_commit"] == "compact-test-sha"
    assert manifest["database_sha256"] == storage._sha256(backup)
    assert manifest["database_size_bytes"] == backup.stat().st_size
    assert manifest["compact_plan"]["reclaimable_bytes"] > 0
    assert manifest["storage_refinement_version"] == refinement.REFINEMENT_VERSION
    assert manifest["schema_sha256"] == refinement._schema_identity(backup)[1]
    assert manifest["manifest_payload_sha256"] == refinement._manifest_hash(manifest)


def test_single_slot_rotation_sizes_replacement_from_compact_plan(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "trades.db"
    backup_dir = data_dir / "backups" / "local"
    backup_dir.mkdir(parents=True)
    _build_fragmented_database(source)

    manager = _manager(data_dir, source, backup_dir)
    plan = guard._compact_snapshot_plan(source)
    result = guard._create_compact_backup(
        manager,
        kind="local",
        reason="prestart",
        plan=plan,
    )

    replacement_required, replacement_mode, replacement_plan = (
        rotation._replacement_requirement(manager)
    )
    raw_required = source.stat().st_size + guard.MIN_BACKUP_HEADROOM_BYTES
    backup_size = Path(result.database_path).stat().st_size
    simulated_available = max(0, replacement_required - backup_size)

    assert replacement_mode == "compact"
    assert replacement_plan is not None
    assert replacement_required < raw_required
    assert simulated_available + backup_size >= replacement_required
    assert simulated_available + backup_size < raw_required
    slot = rotation._validated_single_slot(
        manager,
        backup_dir,
        required_bytes=replacement_required,
        available_bytes=simulated_available,
    )
    assert slot is not None
    assert slot["backup_id"] == result.backup_id
    assert slot["source_integrity"] == "ok"
