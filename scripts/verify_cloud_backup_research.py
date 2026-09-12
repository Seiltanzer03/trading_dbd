"""One-shot full cloud restore drill and read-only hypothesis verification."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import zlib

import boto3
from botocore.config import Config

from production_ede_v13_audit import ReadOnlyRuntime
from seiltanzer import llm_edge_evaluator as evaluator


def main():
    bucket = 'trading-dbd-backups-2026'
    key = os.environ['BACKUP_KEY']
    destination = Path(os.environ['RUNNER_TEMP']) / 'restored.sqlite3'
    report_path = Path(os.environ['RUNNER_TEMP']) / 'verified-research.json'
    client = boto3.client('s3', endpoint_url='https://storage.yandexcloud.net',
                          region_name='ru-central1',
                          config=Config(connect_timeout=10, read_timeout=120))
    manifest = json.loads(client.get_object(Bucket=bucket, Key=key+'.manifest.json')['Body'].read())
    assert manifest['git_commit'] == os.environ['EXPECTED_SHA']
    assert manifest['object_key'] == key and manifest['bucket'] == bucket
    assert not destination.exists()
    assert shutil.disk_usage(destination.parent).free > manifest['database_size_bytes'] + 1024**3
    compressed_hash, raw_hash = hashlib.sha256(), hashlib.sha256()
    compressed_size = raw_size = 0
    decoder = zlib.decompressobj(31)
    body = client.get_object(Bucket=bucket, Key=key)['Body']
    with destination.open('xb') as output:
        for part in body.iter_chunks(chunk_size=64*1024):
            compressed_hash.update(part)
            compressed_size += len(part)
            raw = decoder.decompress(part)
            raw_size += len(raw)
            assert raw_size <= manifest['database_size_bytes']
            raw_hash.update(raw)
            output.write(raw)
        tail = decoder.flush()
        raw_hash.update(tail)
        raw_size += len(tail)
        output.write(tail)
    body.close()
    assert decoder.eof and not decoder.unused_data
    assert compressed_size == manifest['compressed_size_bytes']
    assert compressed_hash.hexdigest() == manifest['compressed_sha256']
    assert raw_size == manifest['database_size_bytes']
    assert raw_hash.hexdigest() == manifest['database_sha256']
    runtime = ReadOnlyRuntime(destination)
    assert runtime._conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    print('FULL_CLOUD_RESTORE_VERIFIED', raw_size, compressed_size, flush=True)
    report = {'source_manifest': manifest, 'full_cloud_restore_verified': True, 'runs': []}
    runs = runtime._conn.execute(
        "SELECT * FROM llm_edge_research_runs WHERE hypothesis_ids_json != '[]' "
        "ORDER BY created_ts DESC LIMIT 6").fetchall()
    for run in runs:
        ids = json.loads(run['hypothesis_ids_json'])
        hypotheses = evaluator._load_hypotheses(runtime, ids)
        cutoff = float(run['created_ts'])
        rows = evaluator._resolved_rows_at_cutoff(runtime, cutoff, {h['horizon_minutes'] for h in hypotheses})
        specs = evaluator._specs(rows)
        results = [evaluator._evaluate_one(rows, h, cutoff_ts=cutoff, specs=specs) for h in hypotheses]
        evaluator._apply_multiple_testing(results)
        report['runs'].append({'run_id': run['run_id'], 'cutoff_ts': cutoff,
                               'hypothesis_ids': ids, 'results': results})
        report_path.write_text(json.dumps(report, sort_keys=True, allow_nan=False))
        for result in results:
            print(json.dumps({k: result.get(k) for k in ('hypothesis_id', 'target_id', 'raw_rows',
                'target_rows', 'fold_count', 'status', 'reason')}), flush=True)
        del rows
    runtime.close()


if __name__ == '__main__':
    main()
