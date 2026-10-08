"""Real SQLite/private archive controls; fixtures are synthetic, never market evidence."""
import hashlib
import inspect
import json
import sqlite3

import pytest

from scripts.export_unified_edge_reviews import export_reviews, remote_program
from scripts.run_private_edge_family_pipeline import run_job
from seiltanzer.edge_family_private_archive import restore
from test_edge_family_private_archive import MemoryStore


SHA = 'a' * 40


def database():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE decision_snapshots(review_id TEXT PRIMARY KEY,trade_id INTEGER,captured_ts REAL,snapshot_json TEXT,snapshot_sha256 TEXT,production_policy TEXT)')
    c.execute('CREATE TABLE decision_path_points(review_id TEXT,ts REAL,price REAL,r REAL,PRIMARY KEY(review_id,ts))')
    return c


def insert(c, identity, ts):
    snapshot = {'captured_ts': ts, 'trade_id': 7, 'instrument': 'NAS100',
                'policy_manager': {'inputs': {'horizon_minutes': 1}}}
    raw = json.dumps(snapshot, sort_keys=True)
    c.execute('INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?)',
              (identity, 7, ts, raw, hashlib.sha256(raw.encode()).hexdigest(), 'HOLD'))
    c.execute('INSERT INTO decision_path_points VALUES(?,?,?,?)', (identity, ts, 100., 0.))
    return raw


def requested_export(c, requests, maximum=32):
    # Baseline can execute normally and fails on its lost historical observation.
    kwargs = {'refresh_requests': requests} if 'refresh_requests' in inspect.signature(export_reviews).parameters else {}
    return export_reviews(c, maximum, **kwargs)


def test_old_review_outside_recent_window_refreshes_actual_path_without_writes():
    c = database()
    raw = insert(c, 'old-private', 100.)
    for n in range(513):
        insert(c, f'new-{n}', 1000. + n)
    c.execute('INSERT INTO decision_path_points VALUES(?,?,?,?)', ('old-private', 170., 101., 1.))
    c.execute('PRAGMA query_only=ON')
    before = c.total_changes
    result = requested_export(c, [{'review_id': 'old-private', 'last_path_ts': 100.}])
    old = [r for r in result['reviews'] if r['review_id'] == 'old-private']
    assert len(old) == 1, 'historical review lost once outside newest 512 snapshots'
    assert old[0]['snapshot_json'] == raw
    assert [p['ts'] for p in old[0]['path_points']] == [100., 170.]
    assert len(result['reviews']) == 32
    assert c.total_changes == before


def test_refresh_reserves_new_reviews_and_deduplicates_overlap():
    c = database()
    requests = []
    for n in range(32):
        identity = f'old-{n}'
        insert(c, identity, 100. + n)
        c.execute('INSERT INTO decision_path_points VALUES(?,?,?,?)', (identity, 200. + n, 101., 1.))
        requests.append({'review_id': identity, 'last_path_ts': 100. + n})
    for n in range(513):
        insert(c, f'new-{n}', 1000. + n)
    result = requested_export(c, requests)
    identities = [r['review_id'] for r in result['reviews']]
    assert len([r for r in identities if r.startswith('old-')]) == 16
    assert len([r for r in identities if r.startswith('new-')]) == 16
    assert len(set(identities)) == 32
    # An unchanged old row must not consume a refresh slot.
    result = requested_export(c, [{'review_id': 'old-0', 'last_path_ts': 200.}])
    assert 'old-0' not in {r['review_id'] for r in result['reviews']}


@pytest.mark.parametrize('requests', [[{'review_id': 'x', 'last_path_ts': float('nan')}],
    [{'review_id': 'x', 'last_path_ts': 1.}] * 513,
    [{'review_id': 'x', 'last_path_ts': 1., 'private': 'unexpected'}]])
def test_invalid_refresh_request_fails_before_database_read(requests):
    c = database()
    assert 'refresh_requests' in inspect.signature(export_reviews).parameters
    c.close()
    with pytest.raises(ValueError):
        export_reviews(c, 32, refresh_requests=requests)


def test_restored_private_history_refreshes_without_replacing_original_snapshot(tmp_path):
    c = database()
    raw = insert(c, 'old-private', 100.)
    client = MemoryStore()
    first = export_reviews(c, 1)
    run_job(client, expected_sha=SHA, generation=1, workspace=tmp_path/'first',
            exporter=lambda: first, clock=lambda: first['exported_ts'] + 1)
    c.execute('INSERT INTO decision_path_points VALUES(?,?,?,?)', ('old-private', 170., 101., 1.))
    for n in range(513):
        insert(c, f'new-{n}', 1000. + n)
    kwargs = {'history_exporter': lambda requests: requested_export(c, requests)} if 'history_exporter' in inspect.signature(run_job).parameters else {}
    summary = run_job(client, expected_sha=SHA, generation=2, workspace=tmp_path/'second',
                      exporter=lambda: export_reviews(c), **kwargs)
    saved, _ = restore(client)
    old = next(r for r in json.loads(saved)['episodes'] if r['review_id'] == 'old-private')
    assert [p['ts'] for p in old['path_points']] == [100., 170.], 'restored history never enters export selection'
    assert old['snapshot_json'] == raw
    assert 'old-private' not in json.dumps(summary)
    assert summary['production_or_database_writes'] == 0


def test_remote_stdlib_program_executes_refresh_selection(tmp_path):
    path = tmp_path/'reviews.sqlite3'
    c = database()
    insert(c, 'old-private', 100.)
    c.execute('INSERT INTO decision_path_points VALUES(?,?,?,?)', ('old-private', 170., 101., 1.))
    for n in range(513):
        insert(c, f'new-{n}', 1000. + n)
    disk = sqlite3.connect(path)
    c.commit()
    c.backup(disk)
    disk.close()
    assert 'refresh_requests' in inspect.signature(remote_program).parameters
    code = remote_program(32, refresh_requests=[{'review_id': 'old-private', 'last_path_ts': 100.}])
    # Only relocate the existing read-only database path; execute real stdlib program.
    code = code.replace('/opt/seiltanzer/data/trades.db', str(path))
    import subprocess, sys, gzip, base64
    done = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    report = json.loads(gzip.decompress(base64.b64decode(done.stdout)))
    assert 'old-private' in {r['review_id'] for r in report['reviews']}


def test_completed_or_unusable_frozen_horizon_does_not_request_refresh():
    from scripts.run_private_edge_family_pipeline import pending_refresh_requests
    from seiltanzer.edge_family_archive import assemble_archive
    from test_edge_family_archive import episode, export, canonical
    rows = []
    for n, manager in enumerate([{'inputs': {'horizon_minutes': 1}}, [1], {'inputs': [1]},
                                  {'inputs': {'horizon_minutes': 0}}]):
        row = episode(str(n))
        snap = json.loads(row['snapshot_json'])
        snap['policy_manager'] = manager
        row['snapshot_json'] = canonical(snap).decode()
        row['snapshot_sha256'] = hashlib.sha256(row['snapshot_json'].encode()).hexdigest()
        row['path_points'].append({'ts': 170., 'price': 102., 'r': 2.})
        rows.append(row)
    archive = assemble_archive([export(*rows)])
    assert pending_refresh_requests(archive) == []


def test_unrepresentable_archived_horizon_does_not_block_other_refresh_requests():
    from scripts.run_private_edge_family_pipeline import pending_refresh_requests
    from seiltanzer.edge_family_archive import assemble_archive
    from test_edge_family_archive import episode, export, canonical
    rows = []
    for identity, horizon in [('unusable', 10**400), ('valid', 1.)]:
        row = episode(identity)
        snap = json.loads(row['snapshot_json'])
        snap['policy_manager'] = {'inputs': {'horizon_minutes': horizon}}
        row['snapshot_json'] = canonical(snap).decode()
        row['snapshot_sha256'] = hashlib.sha256(row['snapshot_json'].encode()).hexdigest()
        rows.append(row)
    archive = assemble_archive([export(*rows)])
    assert len(archive['episodes']) == 2
    assert pending_refresh_requests(archive) == [{'review_id': 'valid', 'last_path_ts': 110.}]


def test_refresh_counter_cannot_copy_private_export_text_to_public_output(tmp_path):
    report = {'read_only': True, 'reviews': [], 'exported_ts': 100.,
              'refreshed_review_count': 'private-account-id'}
    client = MemoryStore()
    with pytest.raises(ValueError):
        run_job(client, expected_sha=SHA, generation=1, workspace=tmp_path,
                exporter=lambda: report, clock=lambda: 200.)
    assert not client.objects
