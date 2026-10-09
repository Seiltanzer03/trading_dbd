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


def _live_fixture(tmp_path):
    key, archive, manifest = _fixture(tmp_path)
    manifest.pop('backup_id')
    manifest.pop('critical_table_counts')
    manifest.update(source='LIVE_SQLITE_RSYNC', source_db='/opt/seiltanzer/data/trades.db',
                    started_ts=100.0, completed_ts=110.0, uploaded_ts=120.0,
                    production_authority=False)
    return key, archive, manifest


def test_live_seed_preserves_old_epoch_without_full_recovery_claim(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    result = MODULE.restore(bucket=manifest['bucket'], key=key,
        destination=tmp_path/'seed.db', result_path=tmp_path/'seed.json',
        seed_only=True, client=Client(key=key, archive=archive, manifest=manifest))
    assert result['restore_contract'] == 'trading-dbd-live-seed-v1'
    assert result['seed_verified'] is True
    assert result['full_restore_verified'] is False
    assert result['storage_manifest'] == manifest
    assert result['storage_manifest']['git_commit'] == 'a'*40
    assert result['storage_manifest']['started_ts'] == 100.0
    assert result['production_authority'] is False


@pytest.mark.parametrize('field,value', [('source','OTHER'),('source_db','/tmp/other.db'),
    ('started_ts',None),('completed_ts',99),('uploaded_ts',109),('git_commit','bad'),
    ('production_authority',True)])
def test_live_seed_rejects_unverified_source_epoch(tmp_path,field,value):
    key, archive, manifest = _live_fixture(tmp_path)
    manifest[field] = value
    with pytest.raises(RuntimeError, match='live seed'):
        MODULE.restore(bucket=manifest['bucket'], key=key,
            destination=tmp_path/'seed.db', result_path=tmp_path/'seed.json',
            seed_only=True, client=Client(key=key, archive=archive, manifest=manifest))
    assert not (tmp_path/'seed.json').exists()


def test_live_slot_is_still_refused_for_full_backup_retirement(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    with pytest.raises(RuntimeError, match='manifest contract'):
        MODULE.restore(bucket=manifest['bucket'], key=key,
            destination=tmp_path/'restore.db', result_path=tmp_path/'restore.json',
            client=Client(key=key, archive=archive, manifest=manifest))


def test_live_seed_corrupt_archive_never_writes_receipt(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    manifest['database_sha256'] = '0'*64
    with pytest.raises(RuntimeError, match='hash or size mismatch'):
        MODULE.restore(bucket=manifest['bucket'], key=key,
            destination=tmp_path/'seed.db', result_path=tmp_path/'seed.json',
            seed_only=True, client=Client(key=key, archive=archive, manifest=manifest))
    assert not (tmp_path/'seed.db').exists()
    assert not (tmp_path/'seed.json').exists()


def test_snapshot_drill_restores_live_cloud_bytes_without_local_retirement_authority(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    result = MODULE.restore(bucket=manifest['bucket'], key=key,
        destination=tmp_path/'drill.db', result_path=tmp_path/'drill.json',
        snapshot_drill=True, client=Client(key=key, archive=archive, manifest=manifest))
    assert result['restore_contract'] == 'trading-dbd-offhost-snapshot-drill-v1'
    assert result['snapshot_restore_verified'] is True
    assert result['full_restore_verified'] is False
    assert result['backup_id'] == ''
    assert result['production_authority'] is False
    assert result['storage_manifest'] == manifest
    assert result['sqlite_quick_check'] == 'ok'
    assert (tmp_path/'drill.db').read_bytes() == (tmp_path/'source.sqlite3').read_bytes()


@pytest.mark.parametrize('mutation', ['source', 'epoch', 'hash'])
def test_snapshot_drill_rejects_invalid_live_snapshot_without_receipt(tmp_path, mutation):
    key, archive, manifest = _live_fixture(tmp_path)
    if mutation == 'source':
        manifest['source_db'] = '/tmp/other.db'
    elif mutation == 'epoch':
        manifest['completed_ts'] = 99
    else:
        manifest['database_sha256'] = '0'*64
    with pytest.raises(RuntimeError):
        MODULE.restore(bucket=manifest['bucket'], key=key,
            destination=tmp_path/'drill.db', result_path=tmp_path/'drill.json',
            snapshot_drill=True, client=Client(key=key, archive=archive, manifest=manifest))
    assert not (tmp_path/'drill.json').exists()


def test_snapshot_drill_cannot_be_used_to_verify_a_local_retirement_target(tmp_path):
    key, archive, manifest = _live_fixture(tmp_path)
    with pytest.raises(ValueError, match='retirement'):
        MODULE.restore(bucket=manifest['bucket'], key=key,
            expected_backup_id='20260905T180524Z-local-524722',
            destination=tmp_path/'drill.db', result_path=tmp_path/'drill.json',
            snapshot_drill=True, client=Client(key=key, archive=archive, manifest=manifest))


def test_snapshot_drill_accepts_legacy_cloud_backup_under_original_full_contract(tmp_path):
    key, archive, manifest = _fixture(tmp_path)
    result = MODULE.restore(bucket=manifest['bucket'], key=key,
        destination=tmp_path/'drill.db', result_path=tmp_path/'drill.json',
        snapshot_drill=True, client=Client(key=key, archive=archive, manifest=manifest))
    assert result['full_restore_verified'] is True
    assert result['restore_contract'] == 'trading-dbd-offhost-full-restore-v1'
    assert result['backup_id'] == manifest['backup_id']
