#!/usr/bin/env python3
"""Bounded, read-only export of frozen reviews and separately held outcomes."""
from __future__ import annotations

import argparse
import base64
import gzip
import inspect
import io
import json
import os
from pathlib import Path
import shlex

from scripts.production_ede_offload import _connect


def select_reviews(metadata, maximum):
    """Round-robin instruments, visiting newest/oldest/middle before neighbours."""
    groups = {}
    for row in metadata:
        groups.setdefault(row.get('instrument') or 'UNKNOWN', []).append(row)
    queues = {}
    for instrument, values in groups.items():
        values.sort(key=lambda item: (item['captured_ts'], item['review_id']))
        pending = [(0, len(values) - 1)]
        ordered, seen = [], set()
        for index in (len(values) - 1, 0):
            if index not in seen:
                ordered.append(values[index]); seen.add(index)
        while pending:
            left, right = pending.pop(0)
            if left > right:
                continue
            middle = (left + right) // 2
            if middle not in seen:
                ordered.append(values[middle]); seen.add(middle)
            pending.extend(((left, middle - 1), (middle + 1, right)))
        queues[instrument] = ordered
    result = []
    while len(result) < maximum and any(queues.values()):
        for instrument in sorted(queues):
            if queues[instrument] and len(result) < maximum:
                result.append(queues[instrument].pop(0))
    return sorted(result, key=lambda item: (item['captured_ts'], item['review_id']))


def export_reviews(connection, maximum=32):
    """Caller opens a read-only transaction; never load all historical payloads."""
    if not 1 <= maximum <= 32:
        raise ValueError('review limit must be between 1 and 32')
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'decision_snapshots' not in tables:
        return {'read_only': True, 'reviews': [], 'reason': 'DECISION_SNAPSHOTS_UNAVAILABLE'}
    metadata = [dict(row) for row in connection.execute('''
        SELECT review_id,trade_id,captured_ts,
          coalesce(json_extract(snapshot_json,'$.strategy.instrument'),
                   json_extract(snapshot_json,'$.instrument'),'UNKNOWN') AS instrument
        FROM decision_snapshots WHERE length(snapshot_json)<=2000000
        ORDER BY rowid DESC LIMIT 512''')]
    reviews = []
    for item in select_reviews(metadata, maximum):
        row = dict(connection.execute('''SELECT review_id,trade_id,captured_ts,
          snapshot_json,snapshot_sha256,production_policy
          FROM decision_snapshots WHERE review_id=?''', (item['review_id'],)).fetchone())
        row['instrument'] = item['instrument']
        points = []
        if 'decision_path_points' in tables:
            points = [dict(r) for r in connection.execute('''
              SELECT ts,price,r FROM decision_path_points WHERE review_id=? AND ts>=?
              ORDER BY ts LIMIT 6001''', (item['review_id'], item['captured_ts']))]
        row['path_truncated'] = len(points) > 6000
        row['path_points'] = points[:6000]
        replay = None
        if 'decision_replays' in tables:
            value = connection.execute('''SELECT resolved_ts,resolution_kind,replay_json
              FROM decision_replays WHERE review_id=? AND length(replay_json)<=2000000''',
              (item['review_id'],)).fetchone()
            replay = dict(value) if value else None
        row['stored_replay'] = replay
        reviews.append(row)
    return {'read_only': True, 'reviews': reviews, 'recent_metadata_n': len(metadata),
            'selection': 'max32_instrument_round_robin_time_spread_from_latest512_rowids',
            'max_snapshot_bytes': 2000000, 'max_points_per_review': 6000,
            'outcomes_separate_from_decision_inputs': True}


def remote_program(maximum):
    # Only these stdlib functions run remotely. Training and pricing stay off-host.
    return ('import sqlite3,json,gzip,base64,time\n'
            + inspect.getsource(select_reviews) + '\n' + inspect.getsource(export_reviews)
            + "\nc=sqlite3.connect('file:/opt/seiltanzer/data/trades.db?mode=ro',uri=True,timeout=3)\n"
              'c.row_factory=sqlite3.Row\nc.execute("PRAGMA query_only=ON")\n'
              'started=time.monotonic()\n'
              'c.set_progress_handler(lambda: int(time.monotonic()-started>25),10000)\n'
              'c.execute("BEGIN")\n'
            + f'report=export_reviews(c,{int(maximum)})\n'
              'c.close()\nreport["exported_ts"]=time.time()\n'
              'raw=json.dumps(report,allow_nan=False).encode()\n'
              'if len(raw)>96000000:raise ValueError("export exceeds bound")\n'
              'print(base64.b64encode(gzip.compress(raw)).decode())\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-reviews', type=int, default=32, choices=range(1, 33))
    args = parser.parse_args()
    password = os.environ.get('SSH_PASSWORD')
    if not password:
        raise ValueError('SSH_PASSWORD environment variable is required')
    client = _connect(password)
    try:
        _, stdout, stderr = client.exec_command(
            'python3 -c ' + shlex.quote(remote_program(args.max_reviews)), timeout=40)
        payload = stdout.read(32_000_001)
        error = stderr.read(1000).decode('utf8', 'replace')
        if stdout.channel.recv_exit_status() or len(payload) > 32_000_000:
            raise RuntimeError('bounded read-only export failed: ' + error[:500])
        compressed = base64.b64decode(payload.strip(), validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as reader:
            raw = reader.read(96_000_001)
        if len(raw) > 96_000_000:
            raise ValueError('export exceeds bound')
        report = json.loads(raw)
        if report.get('read_only') is not True or len(report.get('reviews', [])) > 32:
            raise ValueError('invalid review export')
        Path(args.output).write_bytes(raw)
        print(json.dumps({'exported_reviews': len(report['reviews']), 'read_only': True}))
    finally:
        client.close()


if __name__ == '__main__':
    main()
