"""Offhost inventory must not make a verified WAL-header snapshot mutable."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import yaml

from scripts.production_ede_inventory import _ReadOnlyRuntime
from scripts.prepare_research_history import prepare_research_snapshot
from test_ede_inventory_streaming import database
from test_history_live_read_guard import guarded_source

ROOT = Path(__file__).resolve().parents[1]


def test_actual_inventory_workflow_preserves_snapshot_for_research(tmp_path):
    source_dir = tmp_path / 'ede-source'
    source_dir.mkdir()
    snapshot, writer = database(source_dir)
    writer.close()
    assert not Path(str(snapshot) + '-wal').exists()
    before = snapshot.read_bytes()
    # WAL mode persists in the main file even after its last writer checkpoints.
    assert before[18:20] == b'\x02\x02'
    manifest = Path(str(snapshot) + '.manifest.json')
    manifest.write_text(json.dumps({'database_size_bytes': len(before),
        'database_sha256': hashlib.sha256(before).hexdigest(), 'source': 'TEST'}))
    snapshot.chmod(0o444)
    workflow = yaml.safe_load((ROOT / '.github/workflows/production-ede-v12-audit.yml').read_text())
    step = next(s for s in workflow['jobs']['audit']['steps']
                if s.get('id') == 'coverage_inventory')
    # Execute the actual handoff shell with real SQLite and feature inventory.
    command = step['run'].replace('python scripts/', sys.executable + ' scripts/')
    environment = {**os.environ, 'SOURCE_DB': str(snapshot), 'RUNNER_TEMP': str(tmp_path)}
    environment.pop('PYTHONPATH', None)
    result = subprocess.run(['bash', '-c', command], cwd=ROOT,
        env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'EDE_INVENTORY_SUMMARY=' in result.stdout
    summary = json.loads(next(line.split('=', 1)[1] for line in result.stdout.splitlines()
                              if line.startswith('EDE_INVENTORY_SUMMARY=')))
    assert summary['database_open_mode'] == 'IMMUTABLE_READ_ONLY'
    assert summary['g1s_observations_total'] == 145
    assert snapshot.read_bytes() == before
    assert not any(Path(str(snapshot) + suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))
    prepared = prepare_research_snapshot(snapshot, manifest, tmp_path / 'unused.sqlite3')
    assert prepared['database'] == str(snapshot.resolve())
    assert prepared['history_union_prepared'] is False


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_immutable_inventory_refuses_sidecars_before_sqlite_open(tmp_path, suffix):
    snapshot, writer = database(tmp_path)
    writer.close()
    before = snapshot.read_bytes()
    sidecar = Path(str(snapshot) + suffix)
    sidecar.write_bytes(b'keep unknown mutable state')
    with pytest.raises(ValueError, match='sidecar'):
        _ReadOnlyRuntime(snapshot, verified_immutable_input=True)
    assert snapshot.read_bytes() == before
    assert sidecar.read_bytes() == b'keep unknown mutable state'


def test_default_live_inventory_includes_uncheckpointed_committed_wal(tmp_path):
    snapshot, writer = database(tmp_path)
    first = writer.execute('SELECT * FROM g1s_observations LIMIT 1').fetchone()
    writer.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
                   ('uncheckpointed', first[1] + 2000, first[2] + 2000, *first[3:]))
    writer.commit()
    assert Path(str(snapshot) + '-wal').stat().st_size > 0
    runtime = _ReadOnlyRuntime(snapshot)
    try:
        # These fixture rows are still present through the live WAL connection.
        assert runtime._conn.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0] == 146
        assert not getattr(runtime, '_immutable_snapshot', False)
    finally:
        runtime.close()
        writer.close()


def test_immutable_flag_cannot_bypass_live_production_wal(guarded_source):
    import fcntl
    _, _, live, lock = guarded_source
    with pytest.raises(ValueError, match='Live production'):
        _ReadOnlyRuntime(live, verified_immutable_input=True)
    # The constructor's refusal must also release its actual shared owner lock.
    with lock.open('rb') as exclusive:
        fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
