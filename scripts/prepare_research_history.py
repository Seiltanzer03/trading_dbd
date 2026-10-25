#!/usr/bin/env python3
"""Select a fully verified immutable input for ordinary off-host research.

The raw hot snapshot stays untouched and remains the backup object. Only a
marked hot snapshot needs authenticated cold reads and a new worker output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.history_storage_loader import history_state
from scripts import sqlite_history_partition as archive
from scripts.restore_verified_snapshot import manifest_digest
from scripts.sqlite_history_union import MAX_CATALOG_BYTES, hydrate_current_snapshot
from scripts.verify_immutable_snapshot import verify


def prepare_research_snapshot(database, manifest_path, destination, *, client=None):
    database, manifest_path, destination = map(Path, (database, manifest_path, destination))
    output_manifest = Path(str(destination)+'.manifest.json')
    if os.path.lexists(destination) or os.path.lexists(output_manifest):
        raise FileExistsError('Research history output already exists')
    archive._identity(database)
    manifest = verify(database, manifest_path)
    fields = history_state.snapshot_history_manifest(database, manifest)
    if not fields:
        if 'cold_history_catalog_sha256' in manifest:
            raise ValueError('Research history pin requires an embedded retirement state')
        return dict(database=str(database.resolve()), manifest=str(manifest_path.resolve()),
                    history_union_prepared=False, production_authority=False)

    if client is None:
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url='https://storage.yandexcloud.net',
                              region_name='ru-central1', config=Config(
                                  connect_timeout=10, read_timeout=120,
                                  retries={'max_attempts': 2}))
    try:
        response = client.get_object(Bucket=history_state.BUCKET,
                                     Key=fields['cold_history_catalog_object_key'])
        with response['Body'] as stream:
            raw = stream.read(MAX_CATALOG_BYTES+1)
    except Exception:
        raise RuntimeError('Pinned history catalog could not be read') from None
    if not 0 < len(raw) <= MAX_CATALOG_BYTES:
        raise ValueError('Pinned history catalog exceeds byte bound')
    if hashlib.sha256(raw).hexdigest() != fields['cold_history_catalog_sha256']:
        raise ValueError('Pinned history catalog SHA256 differs')

    proof = dict(immutable_sqlite_read=True, immutable_snapshot_verified=True,
                 verified_local_file=archive._identity(database),
                 database_sha256=manifest['database_sha256'],
                 database_size_bytes=manifest['database_size_bytes'],
                 storage_manifest=manifest, storage_manifest_sha256=manifest_digest(manifest))
    result = hydrate_current_snapshot(database, destination, source_receipt=proof,
                                      catalog_bytes=raw, client=client)
    # Retain source clocks/generation, but never copy a hot-only count, cold
    # storage marker, or raw backup identity into the complete analytical file.
    retained = ('git_commit', 'source_db', 'started_ts', 'completed_ts', 'created_ts', 'run_id')
    combined = {key: manifest[key] for key in retained if key in manifest}
    combined.update(source='VERIFIED_CURRENT_HOT_COLD_UNION',
                    database_sha256=result['database_sha256'],
                    database_size_bytes=result['database_size_bytes'],
                    raw_hot_database_sha256=manifest['database_sha256'],
                    cold_catalog_used_sha256=result['catalog_sha256'],
                    catalog_output_bound_bytes=result['catalog_output_bound_bytes'],
                    effective_output_bound_bytes=result['effective_output_bound_bytes'],
                    current_hot_rows_preserved=True, original_cold_table_roots_verified=True,
                    history_union_verified=True, analytical_snapshot_only=True,
                    production_authority=False)
    created_manifest = False
    try:
        with output_manifest.open('x', encoding='utf8') as stream:
            created_manifest = True
            os.chmod(output_manifest, 0o600)
            json.dump(combined, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        verify(destination, output_manifest)
    except BaseException:
        # Only outputs whose identities we own; unrelated racing files survive.
        if destination.exists() and archive._identity(destination) == result['verified_local_file']:
            destination.unlink()
        if created_manifest:
            output_manifest.unlink(missing_ok=True)
        raise
    return dict(database=str(destination.resolve()), manifest=str(output_manifest.resolve()),
                history_union_prepared=True, catalog_sha256=result['catalog_sha256'],
                current_hot_rows_preserved=True, original_cold_table_roots_verified=True,
                production_authority=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    result = prepare_research_snapshot(args.database, args.manifest, args.destination)
    if args.github_output:
        if any(char in result[key] for key in ('database', 'manifest') for char in ('\n', '\r')):
            raise ValueError('Research output path contains a line break')
        with args.github_output.open('a', encoding='utf8') as stream:
            stream.write('database='+result['database']+'\nmanifest='+result['manifest']+'\n')
    print('VERIFIED_RESEARCH_HISTORY_INPUT='+json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
