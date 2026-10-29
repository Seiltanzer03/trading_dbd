#!/usr/bin/env python3
"""Restore and fully verify a private Object Storage SQLite backup off-host."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path
from typing import Any
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.history_storage_loader import history_state


ENDPOINT = "https://storage.yandexcloud.net"
CONTRACT = "trading-dbd-offhost-backup-v1"
PREFIX = "backups/v1/daily-slot-"
READ_SIZE = 4 * 1024 * 1024
MAX_DATABASE_BYTES = 200 * 1024**3
LIVE_SEED_CONTRACT = 'trading-dbd-live-seed-v1'
MAX_CAPACITY_OBJECTS = 4096


def _local_file_identity(path, *, info=None):
    info = path.lstat() if info is None else info
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError('restored database changed file kind or links')
    return {'path': str(path.resolve()), 'device': info.st_dev,
            'inode': info.st_ino, 'size': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


def collect_capacity(connection, *, max_seconds: float = 180.0) -> dict:
    """Stream page accounting; interrupted measurements remain lower bounds.

    No GROUP BY or disk-sized sort: every completed page can be retained within
    the same deadline. Exact totals are published only after cursor exhaustion.
    """
    if not math.isfinite(max_seconds) or not 0 <= max_seconds <= 180:
        raise ValueError('capacity scan budget must be between 0 and 180 seconds')
    page_size = int(connection.execute('PRAGMA page_size').fetchone()[0])
    page_count = int(connection.execute('PRAGMA page_count').fetchone()[0])
    free_pages = int(connection.execute('PRAGMA freelist_count').fetchone()[0])
    report = {
        'contract_version': 'sqlite-offhost-capacity-v1',
        'status': 'COMPLETE', 'page_size': page_size, 'page_count': page_count,
        'freelist_count': free_pages, 'logical_database_bytes': page_count*page_size,
        'free_page_bytes': free_pages*page_size,
        'used_page_bytes': (page_count-free_pages)*page_size,
        'allocated_object_bytes': None, 'unattributed_used_bytes': None,
        'object_count': None, 'objects_truncated': False, 'largest_objects': [],
        'scan_method': 'streaming_btree_pages',
        'observed_allocated_bytes': 0, 'observed_object_count': 0,
        'largest_objects_are_lower_bounds': True,
        'production_authority': False, 'data_reduction_authorized': False,
    }
    deadline = time.monotonic()+max_seconds
    expired = lambda: time.monotonic() >= deadline
    connection.set_progress_handler(expired, 1000)
    objects, total = {}, 0
    try:
        if expired():
            raise sqlite3.OperationalError('interrupted')
        metadata = {name: (kind, owner) for name, kind, owner in connection.execute(
            "SELECT name,type,tbl_name FROM sqlite_schema "
            "WHERE type IN ('table','index') AND rootpage>0 "
            f"LIMIT {MAX_CAPACITY_OBJECTS + 1}")}
        rows = connection.execute('SELECT name,pgsize,payload FROM dbstat')
        for name, allocated, payload in rows:
            if expired():
                raise sqlite3.OperationalError('interrupted')
            if name not in objects:
                if len(objects) >= MAX_CAPACITY_OBJECTS:
                    report['status'] = 'OBJECT_LIMIT_REACHED'
                    break
                kind, owner = metadata.get(name, (
                    'internal' if name == 'sqlite_schema' else 'unknown', None))
                objects[name] = {'name': name, 'object_type': kind,
                    'owner_table': owner, 'allocated_bytes': 0,
                    'payload_bytes': 0, 'pages': 0}
            row = objects[name]
            row['allocated_bytes'] += int(allocated)
            row['payload_bytes'] += int(payload)
            row['pages'] += 1
            total += int(allocated)
            if total > report['used_page_bytes']:
                raise RuntimeError('capacity allocation exceeds SQLite used pages')
    except sqlite3.OperationalError as exc:
        if str(exc) == 'no such table: dbstat':
            report['status'] = 'DBSTAT_UNAVAILABLE'
        elif str(exc) == 'interrupted' and expired():
            report['status'] = 'TABLE_SCAN_TIMED_OUT'
        else:
            raise
    finally:
        connection.set_progress_handler(None, 0)
    complete = report['status'] == 'COMPLETE'
    report.update(observed_allocated_bytes=total, observed_object_count=len(objects),
                  largest_objects_are_lower_bounds=not complete,
                  objects_truncated=len(objects) > 64 or report['status'] == 'OBJECT_LIMIT_REACHED',
                  largest_objects=sorted(objects.values(),
                      key=lambda row: (-row['allocated_bytes'], row['name']))[:64])
    if complete:
        report.update(allocated_object_bytes=total, object_count=len(objects),
                      unattributed_used_bytes=report['used_page_bytes']-total)
    return report


def manifest_digest(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def validate_live_seed_manifest(manifest: dict) -> None:
    clocks = [manifest.get(key) for key in ('started_ts', 'completed_ts', 'uploaded_ts')]
    sha = manifest.get('git_commit')
    if not all((
        manifest.get('source') == 'LIVE_SQLITE_RSYNC',
        manifest.get('source_db') == '/opt/seiltanzer/data/trades.db',
        manifest.get('production_authority') is False,
        isinstance(sha, str) and len(sha) == 40 and all(c in '0123456789abcdef' for c in sha),
        all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in clocks),
    )) or not clocks[0] <= clocks[1] <= clocks[2]:
        raise RuntimeError('live seed source/epoch contract mismatch')


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
    client: Any | None = None, seed_only: bool = False,
    snapshot_drill: bool = False, storage_report: bool = False,
) -> dict[str, Any]:
    if snapshot_drill and seed_only:
        raise ValueError('snapshot drill and live seed are distinct restore modes')
    if snapshot_drill and expected_backup_id is not None:
        raise ValueError('snapshot drill cannot verify a local retirement target')
    if client is None:
        import boto3
        from botocore.config import Config
        from managed_verified_seed import ManagedRestoreClient

        sdk_client = boto3.client(
            "s3", endpoint_url=ENDPOINT, region_name="ru-central1",
            config=Config(
                connect_timeout=10, read_timeout=120,
                retries={"max_attempts": 3},
            ),
        )
        client = ManagedRestoreClient(sdk_client, temp_dir=destination.parent)
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
    backup_contract = history_state.offhost_backup_contract(manifest)
    live_snapshot_drill = snapshot_drill and manifest.get('source') == 'LIVE_SQLITE_RSYNC'
    if seed_only or live_snapshot_drill:
        if expected_backup_id is not None:
            raise ValueError('live seed cannot verify a retirement target')
        validate_live_seed_manifest(manifest)
    if not all((
        manifest.get("backup_contract") == backup_contract,
        manifest.get("bucket") == bucket,
        manifest.get("object_key") == key,
        manifest.get("codec") == "gzip",
        manifest.get("verified_head") is True,
        manifest.get("verified_boundary_get") is True,
        0 < database_size <= MAX_DATABASE_BYTES,
        0 < compressed_size <= MAX_DATABASE_BYTES,
        len(database_sha) == 64,
        len(compressed_sha) == 64,
        seed_only or live_snapshot_drill or bool(backup_id),
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
        "contract": backup_contract,
    }
    if history_state.PAYLOAD_PIN_FIELD in manifest:
        expected_metadata['json-payload-storage-sha256'] = manifest[history_state.PAYLOAD_PIN_FIELD]
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
            output.flush()
            # Bind the streamed digest to the file descriptor we actually wrote,
            # before any later quick_check/capacity callback can change the file.
            verified_local_file = _local_file_identity(destination, info=os.fstat(output.fileno()))
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

    if _local_file_identity(destination) != verified_local_file:
        raise RuntimeError('restored database changed after verified write')
    connection = sqlite3.connect(
        destination.resolve().as_uri() + "?mode=ro&immutable=1",
        uri=True, timeout=120.0,
    )
    capacity = None
    try:
        connection.execute("PRAGMA query_only=ON")
        check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        history_fields = history_state.validate_manifest_history(connection, manifest)
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if check == 'ok' and storage_report:
            capacity = collect_capacity(connection)
    finally:
        connection.close()
    if check != "ok":
        raise RuntimeError(f"restored database quick_check failed: {check}")
    critical = manifest.get("critical_table_counts")
    if not seed_only and not live_snapshot_drill and (
        not isinstance(critical, dict) or not critical or not set(critical) <= tables
    ):
        raise RuntimeError("restored database is missing critical manifest tables")
    if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise RuntimeError("restore verification created mutable SQLite sidecars")

    result = {
        "restore_contract": (
            LIVE_SEED_CONTRACT if seed_only else
            'trading-dbd-offhost-snapshot-drill-v1' if live_snapshot_drill else
            'trading-dbd-offhost-full-restore-v1'
        ),
        "full_restore_verified": not seed_only and not live_snapshot_drill,
        "bucket": bucket,
        "object_key": key,
        "manifest_key": manifest_key,
        "backup_id": backup_id,
        "database_sha256": database_sha,
        "database_size_bytes": database_size,
        "compressed_sha256": compressed_sha,
        "compressed_size_bytes": compressed_size,
        "sqlite_quick_check": check,
        "critical_table_count": len(critical) if isinstance(critical, dict) else 0,
        "immutable_sqlite_read": True,
        "restored_database_retained_on_runner": True,
        "production_authority": False,
    }
    if seed_only:
        result.update(seed_verified=True, storage_manifest=manifest,
                      storage_manifest_sha256=manifest_digest(manifest))
    if history_fields:
        result.update(history_fields, storage_manifest=manifest,
                      storage_manifest_sha256=manifest_digest(manifest))
    if live_snapshot_drill:
        # The complete bytes and SQLite were verified off-host. No invented local
        # backup ID, schema-complete manifest or retirement authority is granted.
        result.update(snapshot_restore_verified=True, storage_manifest=manifest,
                      storage_manifest_sha256=manifest_digest(manifest))
    if capacity is not None:
        result['capacity_report'] = capacity
    # Local continuation token, not portable cloud identity or retirement power.
    # Subsequent bounded partition work can bind to this same verified file
    # without hashing the entire nineteen-gigabyte input once per chunk.
    if _local_file_identity(destination) != verified_local_file:
        raise RuntimeError('restored database changed during verification')
    # Timestamps can share one filesystem clock tick even across a real write.
    # A continuation token must bind to the bytes remaining AFTER verification,
    # not merely the earlier download stream. One bounded off-host read here
    # avoids rehashing the complete source separately for every history chunk.
    final_digest, final_size = hashlib.sha256(), 0
    with destination.open('rb') as verified_input:
        if _local_file_identity(destination, info=os.fstat(verified_input.fileno())) != verified_local_file:
            raise RuntimeError('restored database changed before final byte verification')
        while chunk := verified_input.read(READ_SIZE):
            final_size += len(chunk)
            if final_size > database_size:
                raise RuntimeError('restored database changed size during final byte verification')
            final_digest.update(chunk)
    if (final_size != database_size or final_digest.hexdigest() != database_sha
            or _local_file_identity(destination) != verified_local_file):
        raise RuntimeError('restored database changed during final byte verification')
    result['verified_local_file'] = verified_local_file
    result_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key")
    parser.add_argument("--expected-backup-id")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument('--storage-report', action='store_true',
                       help='measure worker SQLite pages and object sizes; no production mutation')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--live-seed', action='store_true')
    modes.add_argument('--snapshot-drill', action='store_true',
                       help='verify live cloud snapshot bytes without local retirement authority')
    args = parser.parse_args()
    result = restore(
        bucket=args.bucket, key=args.key,
        expected_backup_id=args.expected_backup_id,
        destination=args.destination, result_path=args.result,
        seed_only=args.live_seed, snapshot_drill=args.snapshot_drill,
        storage_report=args.storage_report,
    )
    print('OFFHOST_LIVE_SEED_VERIFIED=1' if args.live_seed else
          'OFFHOST_SNAPSHOT_RESTORE_VERIFIED=1' if result.get('snapshot_restore_verified') else
          "OFFHOST_FULL_RESTORE_VERIFIED=1")
    print("OFFHOST_RESTORE_BACKUP_ID=" + result["backup_id"])
    print("OFFHOST_RESTORE_OBJECT=" + result["object_key"])
    if args.storage_report:
        print('OFFHOST_CAPACITY_SNAPSHOT_SHA256=' + result['database_sha256'])
        manifest = result.get('storage_manifest') or {}
        print('OFFHOST_CAPACITY_SOURCE=' + json.dumps({key: manifest.get(key) for key in
              ('git_commit', 'source', 'started_ts', 'completed_ts', 'uploaded_ts')}, sort_keys=True))
        print('OFFHOST_CAPACITY_REPORT=' + json.dumps(result['capacity_report'], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
