from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.production_ede_offload import (
    BACKUP_CONTRACT_VERSION,
    _manifest_payload_sha256,
    _verify_local_exact_backup,
)


def test_verify_local_exact_backup_matches_and_falls_back(tmp_path: pathlib.Path):
    db_file = tmp_path / "test.sqlite3"
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("CREATE TABLE t(x INT)")
        conn.execute("INSERT INTO t VALUES(1)")
        conn.commit()

    db_sha = hashlib.sha256(db_file.read_bytes()).hexdigest()

    manifest_payload = {
        "backup_contract_version": BACKUP_CONTRACT_VERSION,
        "backup_id": "b-test-1",
        "created_ts": 1700000000.0,
        "database_file": "test.sqlite3",
        "database_sha256": db_sha,
        "database_size_bytes": len(db_file.read_bytes()),
        "git_commit": "abc1234",
        "reason": "prestart",
        "source_db": "/opt/seiltanzer/data/trades.db",
        "verified": True,
    }
    manifest_payload["manifest_payload_sha256"] = _manifest_payload_sha256(manifest_payload)
    manifest_file = tmp_path / "test.sqlite3.manifest.json"
    manifest_file.write_text(json.dumps(manifest_payload), encoding="utf-8")

    # 1. Matching expected SHA succeeds
    res_exact = _verify_local_exact_backup(db_file, manifest_file, expected_sha="abc1234")
    assert res_exact["git_commit"] == "abc1234"

    # 2. Non-matching SHA with allow_verified_fallback=True succeeds
    res_fallback = _verify_local_exact_backup(
        db_file, manifest_file, expected_sha="diff5678", allow_verified_fallback=True
    )
    assert res_fallback["git_commit"] == "abc1234"

    # 3. Non-matching SHA with allow_verified_fallback=False raises RuntimeError
    with pytest.raises(RuntimeError, match="expected SHA"):
        _verify_local_exact_backup(
            db_file, manifest_file, expected_sha="diff5678", allow_verified_fallback=False
        )


def test_remote_selector_script_prefers_exact_sha_and_falls_back(tmp_path: pathlib.Path):
    backup_root = tmp_path / "backups"
    backup_root.mkdir()

    # Older backup with git_commit="old_sha"
    db_old = backup_root / "db_old.sqlite3"
    db_old.write_bytes(b"123")
    m_old = {
        "backup_contract_version": BACKUP_CONTRACT_VERSION,
        "backup_id": "b-old",
        "created_ts": 1700000100.0,
        "database_file": "db_old.sqlite3",
        "database_sha256": hashlib.sha256(b"123").hexdigest(),
        "database_size_bytes": 3,
        "git_commit": "old_sha",
        "reason": "prestart",
        "source_db": "/opt/seiltanzer/data/trades.db",
        "verified": True,
    }
    m_old["manifest_payload_sha256"] = _manifest_payload_sha256(m_old)
    (backup_root / "db_old.sqlite3.manifest.json").write_text(json.dumps(m_old), encoding="utf-8")

    offload_py = (ROOT / "scripts/production_ede_offload.py").read_text(encoding="utf-8")
    selector_start = offload_py.index("selector = r'''") + len("selector = r'''")
    selector_end = offload_py.index("'''\n    command = (")
    selector_code = offload_py[selector_start:selector_end]

    env = {
        "BACKUP_ROOT": str(backup_root),
        "SOURCE_DB": "/opt/seiltanzer/data/trades.db",
        "EXPECTED_SHA": "new_sha",
        "MAX_AGE_SECONDS": "999999999",
    }
    proc = subprocess.run([sys.executable, "-c", selector_code], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "EDE_VERIFIED_BACKUP_SELECTION=" in proc.stdout
    selection = json.loads(proc.stdout.split("EDE_VERIFIED_BACKUP_SELECTION=")[1].strip())
    assert selection["backup_id"] == "b-old"
    assert selection["exact_sha_match"] is False

    # Now add the exact-SHA backup with git_commit="new_sha"
    db_new = backup_root / "db_new.sqlite3"
    db_new.write_bytes(b"4567")
    m_new = {
        "backup_contract_version": BACKUP_CONTRACT_VERSION,
        "backup_id": "b-new",
        "created_ts": 1700000200.0,
        "database_file": "db_new.sqlite3",
        "database_sha256": hashlib.sha256(b"4567").hexdigest(),
        "database_size_bytes": 4,
        "git_commit": "new_sha",
        "reason": "prestart",
        "source_db": "/opt/seiltanzer/data/trades.db",
        "verified": True,
    }
    m_new["manifest_payload_sha256"] = _manifest_payload_sha256(m_new)
    (backup_root / "db_new.sqlite3.manifest.json").write_text(json.dumps(m_new), encoding="utf-8")

    proc2 = subprocess.run([sys.executable, "-c", selector_code], env=env, capture_output=True, text=True)
    assert proc2.returncode == 0, proc2.stderr
    selection2 = json.loads(proc2.stdout.split("EDE_VERIFIED_BACKUP_SELECTION=")[1].strip())
    assert selection2["backup_id"] == "b-new"
    assert selection2["exact_sha_match"] is True


def test_remote_selector_respects_fallback_max_age_window(tmp_path: pathlib.Path):
    backup_root = tmp_path / "backups_window"
    backup_root.mkdir()

    import time
    now = time.time()

    # Fallback backup created 2 hours ago (7200 seconds ago)
    db_fallback = backup_root / "db_fallback.sqlite3"
    db_fallback.write_bytes(b"fallback_data")
    m_fallback = {
        "backup_contract_version": BACKUP_CONTRACT_VERSION,
        "backup_id": "b-fallback-2h",
        "created_ts": now - 7200,
        "database_file": "db_fallback.sqlite3",
        "database_sha256": hashlib.sha256(b"fallback_data").hexdigest(),
        "database_size_bytes": len(b"fallback_data"),
        "git_commit": "different_sha",
        "reason": "scheduled",
        "source_db": "/opt/seiltanzer/data/trades.db",
        "verified": True,
    }
    m_fallback["manifest_payload_sha256"] = _manifest_payload_sha256(m_fallback)
    (backup_root / "db_fallback.sqlite3.manifest.json").write_text(json.dumps(m_fallback), encoding="utf-8")

    offload_py = (ROOT / "scripts/production_ede_offload.py").read_text(encoding="utf-8")
    selector_start = offload_py.index("selector = r'''") + len("selector = r'''")
    selector_end = offload_py.index("'''\n    command = (")
    selector_code = offload_py[selector_start:selector_end]

    # With MAX_EXACT_AGE_SECONDS=3600 (1h) and MAX_FALLBACK_AGE_SECONDS=86400 (24h),
    # the 2-hour-old fallback backup MUST be accepted.
    env_ok = {
        "BACKUP_ROOT": str(backup_root),
        "SOURCE_DB": "/opt/seiltanzer/data/trades.db",
        "EXPECTED_SHA": "current_repo_sha",
        "MAX_EXACT_AGE_SECONDS": "3600",
        "MAX_FALLBACK_AGE_SECONDS": "86400",
    }
    proc_ok = subprocess.run([sys.executable, "-c", selector_code], env=env_ok, capture_output=True, text=True)
    assert proc_ok.returncode == 0, proc_ok.stderr
    assert "EDE_VERIFIED_BACKUP_SELECTION=" in proc_ok.stdout
    selection = json.loads(proc_ok.stdout.split("EDE_VERIFIED_BACKUP_SELECTION=")[1].strip())
    assert selection["backup_id"] == "b-fallback-2h"
    assert selection["exact_sha_match"] is False

    # If MAX_FALLBACK_AGE_SECONDS=3600, it would fail closed
    env_strict = {
        "BACKUP_ROOT": str(backup_root),
        "SOURCE_DB": "/opt/seiltanzer/data/trades.db",
        "EXPECTED_SHA": "current_repo_sha",
        "MAX_EXACT_AGE_SECONDS": "3600",
        "MAX_FALLBACK_AGE_SECONDS": "3600",
    }
    proc_strict = subprocess.run([sys.executable, "-c", selector_code], env=env_strict, capture_output=True, text=True)
    assert proc_strict.returncode != 0
    assert "no recent verified local backup" in proc_strict.stderr

