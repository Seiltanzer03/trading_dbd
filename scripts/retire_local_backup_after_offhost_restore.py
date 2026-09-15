#!/usr/bin/env python3
"""Remove one exact local backup only after a matching full off-host restore."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex

from production_ede_offload import _connect, _exec, _probe_api


REMOTE_SCRIPT = r'''
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

backup_id, expected_sha, raw_size = sys.argv[1:]
expected_size = int(raw_size)
if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-local-[0-9]{6}", backup_id):
    raise SystemExit("unsafe backup id")
root = Path("/opt/seiltanzer/data/backups/local")
database = root / (backup_id + ".sqlite3")
manifest_path = root / (backup_id + ".manifest.json")
if not database.exists() and not manifest_path.exists():
    print("LOCAL_BACKUP_ALREADY_RETIRED=1")
    raise SystemExit(0)
if not database.is_file() or not manifest_path.is_file():
    raise SystemExit("local backup pair is incomplete")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if not all((
    manifest.get("backup_id") == backup_id,
    manifest.get("database_file") == database.name,
    manifest.get("database_sha256") == expected_sha,
    int(manifest.get("database_size_bytes") or 0) == expected_size,
    manifest.get("verified") is True,
    manifest.get("source_db") == "/opt/seiltanzer/data/trades.db",
)):
    raise SystemExit("local backup manifest does not match restored object")
if database.stat().st_size != expected_size:
    raise SystemExit("local backup size changed")
connection = sqlite3.connect(
    database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=120.0
)
try:
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise SystemExit("local backup quick_check failed")
finally:
    connection.close()
digest = hashlib.sha256()
with database.open("rb") as stream:
    for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
        digest.update(chunk)
if digest.hexdigest() != expected_sha:
    raise SystemExit("local backup SHA changed")
before = os.statvfs(root).f_bavail * os.statvfs(root).f_frsize
allocated = int(database.stat().st_blocks) * 512
database.unlink()
manifest_path.unlink()
os.sync()
after = os.statvfs(root).f_bavail * os.statvfs(root).f_frsize
if database.exists() or manifest_path.exists() or after <= before:
    raise SystemExit("exact local backup retirement did not release space")
print("LOCAL_BACKUP_RETIRED=" + json.dumps({
    "backup_id": backup_id,
    "database_sha256": expected_sha,
    "database_size_bytes": expected_size,
    "filesystem_allocated_bytes": allocated,
    "free_before_bytes": before,
    "free_after_bytes": after,
    "recoverable_from_verified_offhost_restore": True,
}, sort_keys=True))
'''


def retire(*, password: str, restore_result: Path) -> dict:
    result = json.loads(restore_result.read_text(encoding="utf-8"))
    if not all((
        result.get("restore_contract") == "trading-dbd-offhost-full-restore-v1",
        result.get("full_restore_verified") is True,
        result.get("sqlite_quick_check") == "ok",
        result.get("immutable_sqlite_read") is True,
        result.get("bucket") == "trading-dbd-backups-2026",
    )):
        raise RuntimeError("full off-host restore proof is missing")
    backup_id = str(result.get("backup_id") or "")
    database_sha = str(result.get("database_sha256") or "")
    database_size = int(result.get("database_size_bytes") or 0)
    if (
        not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-local-[0-9]{6}", backup_id)
        or len(database_sha) != 64
        or database_size <= 0
    ):
        raise RuntimeError("restore proof identity is invalid")

    client = _connect(password)
    try:
        _probe_api(client)
        command = " ".join([
            "python3", "-c", shlex.quote(REMOTE_SCRIPT),
            shlex.quote(backup_id), shlex.quote(database_sha),
            shlex.quote(str(database_size)),
        ])
        output = _exec(client, command, timeout=900)
        _probe_api(client)
    finally:
        client.close()
    if "LOCAL_BACKUP_ALREADY_RETIRED=1" in output:
        return {"backup_id": backup_id, "already_retired": True}
    marker = "LOCAL_BACKUP_RETIRED="
    line = next((item for item in output.splitlines() if item.startswith(marker)), None)
    if line is None:
        raise RuntimeError("remote retirement proof is missing")
    value = json.loads(line[len(marker):])
    if value.get("backup_id") != backup_id or value.get("database_sha256") != database_sha:
        raise RuntimeError("remote retirement proof identity mismatch")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password", required=True)
    parser.add_argument("--restore-result", type=Path, required=True)
    args = parser.parse_args()
    result = retire(password=args.password, restore_result=args.restore_result)
    print("OFFHOST_RESTORED_LOCAL_BACKUP_RETIREMENT=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
