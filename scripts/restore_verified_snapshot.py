#!/usr/bin/env python3
"""Restore and fully verify a private Object Storage SQLite backup off-host."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
import zlib


ENDPOINT = "https://storage.yandexcloud.net"
CONTRACT = "trading-dbd-offhost-backup-v1"
PREFIX = "backups/v1/daily-slot-"
READ_SIZE = 4 * 1024 * 1024
MAX_DATABASE_BYTES = 200 * 1024**3


def _metadata(value: dict | None) -> dict[str, str]:
    return {str(key).lower(): str(item) for key, item in (value or {}).items()}


def _read_json_object(client: Any, *, bucket: str, key: str) -> dict[str, Any]:
    response = client.get_object(Bucket=bucket, Key=key)
    with response["Body"] as stream:
        body = stream.read()
    if len(body) > 1024 * 1024:
        raise RuntimeError("off-host manifest exceeds bounded size")
    try:
        value = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("off-host manifest is invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("off-host manifest must be an object")
    return value


def _latest_manifest(client: Any, *, bucket: str) -> tuple[str, dict[str, Any]]:
    response = client.list_objects_v2(Bucket=bucket, Prefix=PREFIX, MaxKeys=100)
    keys = sorted(
        str(item.get("Key") or "")
        for item in response.get("Contents") or []
        if str(item.get("Key") or "").endswith(
            "/snapshot.sqlite3.gz.manifest.json"
        )
    )
    if not keys or len(keys) > 7:
        raise RuntimeError("expected one to seven bounded backup slot manifests")
    candidates = [
        (key, _read_json_object(client, bucket=bucket, key=key)) for key in keys
    ]
    return max(candidates, key=lambda item: float(item[1].get("uploaded_ts") or 0.0))


def restore(
    *, bucket: str, destination: Path, result_path: Path,
    key: str | None = None, expected_backup_id: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    if client is None:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3", endpoint_url=ENDPOINT, region_name="ru-central1",
            config=Config(
                connect_timeout=10, read_timeout=120,
                retries={"max_attempts": 3},
            ),
        )
    if destination.exists() or any(
        Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")
    ):
        raise ValueError("restore destination must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    if key is None:
        manifest_key, manifest = _latest_manifest(client, bucket=bucket)
        key = str(manifest.get("object_key") or "")
    else:
        manifest_key = key + ".manifest.json"
        manifest = _read_json_object(client, bucket=bucket, key=manifest_key)

    database_size = int(manifest.get("database_size_bytes") or 0)
    compressed_size = int(manifest.get("compressed_size_bytes") or 0)
    database_sha = str(manifest.get("database_sha256") or "").lower()
    compressed_sha = str(manifest.get("compressed_sha256") or "").lower()
    backup_id = str(manifest.get("backup_id") or "")
    if not all((
        manifest.get("backup_contract") == CONTRACT,
        manifest.get("bucket") == bucket,
        manifest.get("object_key") == key,
        manifest.get("codec") == "gzip",
        manifest.get("verified_head") is True,
        manifest.get("verified_boundary_get") is True,
        0 < database_size <= MAX_DATABASE_BYTES,
        0 < compressed_size <= MAX_DATABASE_BYTES,
        len(database_sha) == 64,
        len(compressed_sha) == 64,
        bool(backup_id),
    )):
        raise RuntimeError("off-host backup manifest contract mismatch")
    if expected_backup_id is not None and backup_id != expected_backup_id:
        raise RuntimeError("off-host backup id does not match retirement target")

    head = client.head_object(Bucket=bucket, Key=key)
    expected_metadata = {
        "source-sha256": database_sha,
        "source-size": str(database_size),
        "git-commit": str(manifest.get("git_commit") or ""),
        "codec": "gzip",
        "contract": CONTRACT,
    }
    if (
        int(head.get("ContentLength") or -1) != compressed_size
        or _metadata(head.get("Metadata")) != expected_metadata
    ):
        raise RuntimeError("off-host object HEAD does not match manifest")

    response = client.get_object(Bucket=bucket, Key=key)
    compressed_digest = hashlib.sha256()
    database_digest = hashlib.sha256()
    compressed_seen = 0
    database_seen = 0
    inflater = zlib.decompressobj(wbits=31)
    try:
        with response["Body"] as stream, destination.open("xb") as output:
            while True:
                chunk = stream.read(READ_SIZE)
                if not chunk:
                    break
                compressed_seen += len(chunk)
                if compressed_seen > compressed_size:
                    raise RuntimeError("compressed object exceeds declared size")
                compressed_digest.update(chunk)
                restored = inflater.decompress(chunk)
                if restored:
                    database_seen += len(restored)
                    if database_seen > database_size:
                        raise RuntimeError("restored database exceeds declared size")
                    database_digest.update(restored)
                    output.write(restored)
            restored = inflater.flush()
            if restored:
                database_seen += len(restored)
                if database_seen > database_size:
                    raise RuntimeError("restored database exceeds declared size")
                database_digest.update(restored)
                output.write(restored)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

    if not inflater.eof or inflater.unused_data:
        destination.unlink(missing_ok=True)
        raise RuntimeError("gzip stream did not end cleanly")
    if (
        compressed_seen != compressed_size
        or compressed_digest.hexdigest() != compressed_sha
        or database_seen != database_size
        or database_digest.hexdigest() != database_sha
        or destination.stat().st_size != database_size
    ):
        destination.unlink(missing_ok=True)
        raise RuntimeError("full off-host restore hash or size mismatch")

    connection = sqlite3.connect(
        destination.resolve().as_uri() + "?mode=ro&immutable=1",
        uri=True, timeout=120.0,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        connection.close()
    if check != "ok":
        raise RuntimeError(f"restored database quick_check failed: {check}")
    critical = manifest.get("critical_table_counts")
    if not isinstance(critical, dict) or not critical or not set(critical) <= tables:
        raise RuntimeError("restored database is missing critical manifest tables")
    if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise RuntimeError("restore verification created mutable SQLite sidecars")

    result = {
        "restore_contract": "trading-dbd-offhost-full-restore-v1",
        "full_restore_verified": True,
        "bucket": bucket,
        "object_key": key,
        "manifest_key": manifest_key,
        "backup_id": backup_id,
        "database_sha256": database_sha,
        "database_size_bytes": database_size,
        "compressed_sha256": compressed_sha,
        "compressed_size_bytes": compressed_size,
        "sqlite_quick_check": check,
        "critical_table_count": len(critical),
        "immutable_sqlite_read": True,
        "restored_database_retained_on_runner": True,
        "production_authority": False,
    }
    result_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key")
    parser.add_argument("--expected-backup-id")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result = restore(
        bucket=args.bucket, key=args.key,
        expected_backup_id=args.expected_backup_id,
        destination=args.destination, result_path=args.result,
    )
    print("OFFHOST_FULL_RESTORE_VERIFIED=1")
    print("OFFHOST_RESTORE_BACKUP_ID=" + result["backup_id"])
    print("OFFHOST_RESTORE_OBJECT=" + result["object_key"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
