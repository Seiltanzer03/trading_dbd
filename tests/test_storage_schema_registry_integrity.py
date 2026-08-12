from __future__ import annotations

import sqlite3

from seiltanzer import storage_runtime as storage
from seiltanzer.config import Settings
from seiltanzer.storage_schema_registry_integrity import (
    current_research_critical_tables,
    install_storage_schema_registry_integrity,
)


def test_registry_restore_readds_dynamic_g1s_and_management_tables():
    original = tuple(storage.CRITICAL_TABLES)
    try:
        storage.CRITICAL_TABLES = ("trades",)
        restored = install_storage_schema_registry_integrity()
        required = current_research_critical_tables()
        assert "g1s_observations" in required
        assert "g1s_return_models" in required
        assert "g1s_probability_calibrators" in required
        assert "g1m_local_outcomes" in required
        assert "g1m_management_observations" in required
        assert set(required).issubset(set(restored))
    finally:
        storage.CRITICAL_TABLES = original


def test_post_schema_backup_manifest_can_count_every_research_table(tmp_path):
    original = tuple(storage.CRITICAL_TABLES)
    try:
        storage.CRITICAL_TABLES = ("trades",)
        required = current_research_critical_tables()
        install_storage_schema_registry_integrity()

        settings = Settings(demo=True, data_dir=str(tmp_path))
        conn = sqlite3.connect(settings.trades_db)
        try:
            conn.execute("CREATE TABLE trades(id INTEGER PRIMARY KEY)")
            for table in required:
                if table == "trades":
                    continue
                conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}"(id INTEGER PRIMARY KEY)')
            conn.commit()
        finally:
            conn.close()

        manager = storage.StorageManager(settings, git_commit="schema-test")
        result = manager.create_backup(kind="local", reason="g1s-schema-identity")
        manifest = next(
            item for item in manager.backups()["local"]
            if item["backup_id"] == result.backup_id
        )
        counts = manifest["critical_table_counts"]
        for table in required:
            assert table in counts
            assert counts[table] is not None
    finally:
        storage.CRITICAL_TABLES = original
