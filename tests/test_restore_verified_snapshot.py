from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import sqlite3
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/restore_verified_snapshot.py"
SPEC = importlib.util.spec_from_file_location("restore_verified_snapshot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Client:
    def __init__(self, *, key: str, archive: bytes, manifest: dict):
        self.key = key
        self.archive = archive
        self.manifest = manifest

    def get_object(self, *, Bucket, Key):
        del Bucket
        if Key == self.key:
            return {"Body": Body(self.archive)}
        assert Key == self.key + ".manifest.json"
        return {"Body": Body(json.dumps(self.manifest).encode())}

    def head_object(self, *, Bucket, Key):
        del Bucket
        assert Key == self.key
        return {
            "ContentLength": len(self.archive),
            "Metadata": {
                "source-sha256": self.manifest["database_sha256"],
                "source-size": str(self.manifest["database_size_bytes"]),
                "git-commit": self.manifest["git_commit"],
                "codec": "gzip",
                "contract": MODULE.CONTRACT,
            },
        }


def _fixture(tmp_path: Path):
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('verified')")
        connection.commit()
    finally:
        connection.close()
    raw = source.read_bytes()
    archive = gzip.compress(raw)
    key = "backups/v1/daily-slot-4/snapshot.sqlite3.gz"
    manifest = {
        "backup_contract": MODULE.CONTRACT,
        "bucket": "trading-dbd-backups-2026",
        "object_key": key,
        "codec": "gzip",
        "verified_head": True,
        "verified_boundary_get": True,
        "backup_id": "20260905T180524Z-local-524722",
        "git_commit": "a" * 40,
        "database_size_bytes": len(raw),
        "database_sha256": hashlib.sha256(raw).hexdigest(),
        "compressed_size_bytes": len(archive),
        "compressed_sha256": hashlib.sha256(archive).hexdigest(),
        "critical_table_counts": {"evidence": 1},
    }
    return key, archive, manifest


def test_full_restore_verifies_hash_quick_check_and_no_sidecars(tmp_path: Path):
    key, archive, manifest = _fixture(tmp_path)
    destination = tmp_path / "restored.sqlite3"
    result_path = tmp_path / "result.json"
    result = MODULE.restore(
        bucket="trading-dbd-backups-2026", key=key,
        expected_backup_id=manifest["backup_id"],
        destination=destination, result_path=result_path,
        client=Client(key=key, archive=archive, manifest=manifest),
    )
    assert result["full_restore_verified"] is True
    assert result["sqlite_quick_check"] == "ok"
    assert result["database_sha256"] == manifest["database_sha256"]
    assert result_path.is_file()
    assert not Path(str(destination) + "-wal").exists()
    assert not Path(str(destination) + "-shm").exists()


def test_full_restore_fails_closed_on_compressed_hash_mismatch(tmp_path: Path):
    key, archive, manifest = _fixture(tmp_path)
    manifest["compressed_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="hash or size mismatch"):
        MODULE.restore(
            bucket="trading-dbd-backups-2026", key=key,
            destination=tmp_path / "restored.sqlite3",
            result_path=tmp_path / "result.json",
            client=Client(key=key, archive=archive, manifest=manifest),
        )
