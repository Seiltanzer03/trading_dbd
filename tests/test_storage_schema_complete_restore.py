from __future__ import annotations

import json
from pathlib import Path

from seiltanzer import storage_runtime as storage
from seiltanzer.storage_restore_drill import schema_complete_verified_backup


class _Manager:
    def __init__(self, directory: Path):
        self.local_dir = directory

    def _verified_manifests(self, directory):
        items = []
        for path in directory.glob("*.manifest.json"):
            value = json.loads(path.read_text())
            if value.get("verified") is True:
                value["manifest_path"] = str(path)
                items.append(value)
        return sorted(items, key=lambda x: float(x.get("created_ts") or 0), reverse=True)


def _manifest(directory: Path, name: str, ts: float, counts):
    path = directory/f"{name}.manifest.json"
    path.write_text(json.dumps({
        "backup_id": name, "created_ts": ts, "verified": True,
        "database_file": f"{name}.sqlite3", "critical_table_counts": counts,
    }))


def test_restore_selection_skips_newer_pre_schema_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CRITICAL_TABLES", ("trades", "g1s_observations"))
    _manifest(tmp_path, "schema", 100.0, {"trades": 2, "g1s_observations": 3})
    _manifest(tmp_path, "prestart", 200.0, {"trades": 2, "g1s_observations": None})
    chosen = schema_complete_verified_backup(_Manager(tmp_path))
    assert chosen is not None
    assert chosen["backup_id"] == "schema"


def test_restore_selection_fails_closed_when_no_current_schema_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CRITICAL_TABLES", ("trades", "g1s_observations"))
    _manifest(tmp_path, "old", 100.0, {"trades": 2, "g1s_observations": None})
    assert schema_complete_verified_backup(_Manager(tmp_path)) is None
