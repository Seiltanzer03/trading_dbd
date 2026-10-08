#!/usr/bin/env python3
"""Trusted serialized private archive job; public output contains counters only."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import time

from scripts.run_edge_family_pipeline import run_pipeline, write_outputs, encode_json
from seiltanzer.edge_family_archive import _loads, MAX_ARCHIVE_BYTES
from seiltanzer.edge_family_private_archive import restore, store

PUBLIC_FIELDS = {'version', 'code_sha', 'archive_state', 'archive_generation',
                 'archive_committed', 'archive_episode_count', 'exported_review_count',
                 'active_model_count', 'packaged_model_count', 'reason', 'production_activation_performed',
                 'production_or_database_writes'}


def run_job(client, *, expected_sha, generation, workspace, exporter, clock=time.time):
    if not isinstance(expected_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        raise ValueError('INVALID_EXPECTED_SHA')
    if type(generation) is not int or generation <= 0:
        raise ValueError('INVALID_RUN_GENERATION')
    # Restore on every attempt, including after an ambiguous prior commit.
    # Corrupt/inaccessible storage must fail before acquiring a production reader.
    raw, previous = restore(client)
    if previous is not None and generation <= previous['generation']:
        raise ValueError('RUN_GENERATION_NOT_NEWER')
    reviews = exporter()
    if (not isinstance(reviews, dict) or reviews.get('read_only') is not True
            or any(reviews.get(key) for key in ('synthetic', 'demo', 'is_demo', 'synthetic_demo'))
            or not isinstance(reviews.get('reviews'), list) or len(reviews['reviews']) > 32):
        raise ValueError('INVALID_ACTUAL_EXPORT')
    result = run_pipeline(reviews, expected_sha=expected_sha,
                          previous_archive=_loads(raw) if raw is not None else None,
                          trained_at=clock())
    result['diagnostics']['archive_retention'] = 'private Object Storage; bounded two-slot archive'
    workspace = Path(workspace)
    write_outputs(result, workspace/'private-output')
    with (workspace/'private-output/archive.json').open('rb') as stream:
        updated = stream.read(MAX_ARCHIVE_BYTES + 1)
    receipt = store(client, updated, source_sha=expected_sha, generation=generation,
                    previous_receipt=previous, uploaded_ts=clock())
    model_count = result['diagnostics']['active_model_count']
    # Construct a new public object. Private exclusions/IDs/training rows never
    # pass through, even when a diagnostic reason itself contains private data.
    summary = {'version': 'edge-family-private-job-v1', 'code_sha': expected_sha,
               'archive_state': 'RESTORED' if previous is not None else 'INITIAL',
               'archive_generation': receipt['generation'], 'archive_committed': True,
               'archive_episode_count': len(result['archive']['episodes']),
               'exported_review_count': len(reviews['reviews']), 'active_model_count': 0,
               'packaged_model_count': model_count,
               'reason': 'VALIDATED_MODELS_PACKAGED' if model_count else 'NO_VALIDATED_MODEL',
               'production_activation_performed': False, 'production_or_database_writes': 0}
    (workspace/'public_summary.json').write_bytes(encode_json(summary, max_bytes=16_000))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--generation', type=int, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Legacy SSH helpers print retry exceptions and remote stdout/stderr.
        # Suppress dependency output at this single-process public CLI boundary,
        # without buffering unbounded private diagnostics in memory.
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            import boto3
            from botocore.config import Config
            from scripts.export_unified_edge_reviews import export_actual
            args.workspace.mkdir(parents=True, exist_ok=False)
            client = boto3.client('s3', endpoint_url='https://storage.yandexcloud.net',
                                  region_name='ru-central1', config=Config(
                                      connect_timeout=10, read_timeout=120,
                                      retries={'max_attempts': 3}))
            def exporter():
                return export_actual(args.workspace/'actual_reviews.json', maximum=32,
                                     password=os.environ.get('SSH_PASSWORD'), expected_sha=args.expected_sha)
            summary = run_job(client, expected_sha=args.expected_sha, generation=args.generation,
                              workspace=args.workspace, exporter=exporter)
    except Exception:
        # Provider/export exceptions may contain response bodies or account IDs.
        # The public workflow receives only this stable failure code.
        raise SystemExit('PRIVATE_FAMILY_PIPELINE_FAILED') from None
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
