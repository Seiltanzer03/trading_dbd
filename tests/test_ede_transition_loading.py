"""Real SQLite loading regressions; no empirical edge is fabricated."""
from copy import deepcopy
from pathlib import Path
import json

from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.transition_search import augment_rows_from_frozen_v3
from seiltanzer.edge_discovery.baseline_rows import baseline_eligible_rows
from seiltanzer.edge_discovery.dataset_fingerprint import research_dataset_fingerprint
from test_edge_discovery_engine import _ProspectiveRuntime, _prospective_features
from test_ede_v13_transition import Runtime, _frozen, _row


class PayloadCursor:
    def __init__(self, cursor, owner):
        self.cursor, self.owner = cursor, owner

    def _record(self, rows):
        self.owner.loaded.extend(str(row['observation_id']) for row in rows)
        self.owner.max_batch = max(self.owner.max_batch, len(rows))
        return rows

    def fetchall(self):
        raise AssertionError('unbounded observation payload fetchall')

    def fetchmany(self, size=1):
        return self._record(self.cursor.fetchmany(size))

    def __iter__(self):
        return self

    def __next__(self):
        row = next(self.cursor)
        self._record([row])
        return row

    def close(self):
        self.cursor.close()


class PayloadConnection:
    def __init__(self, conn):
        self.conn, self.loaded, self.max_batch = conn, [], 0

    def execute(self, sql, parameters=()):
        cursor = self.conn.execute(sql, parameters)
        if 'FROM g1s_observations' in sql and 'SELECT' in sql:
            return PayloadCursor(cursor, self)
        return cursor

    def __getattr__(self, key):
        return getattr(self.conn, key)


def test_augmentation_reads_only_requested_payloads_and_keeps_causal_refusals():
    runtime = Runtime()
    for index in range(145):
        runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?)',
            (str(index), 100., 15, _frozen(99.)))
    runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?)',
        ('future', 100., 15, _frozen(101.)))
    rows = [_row('1'), _row('future'), _row('2', captured_ts=102.)]
    expected = deepcopy(rows)
    coverage = augment_rows_from_frozen_v3(runtime, expected)
    tracked = PayloadConnection(runtime._conn)
    runtime._conn = tracked
    actual = augment_rows_from_frozen_v3(runtime, rows)
    assert rows == expected and actual == coverage
    assert set(tracked.loaded) == {'1', '2', 'future'}
    assert tracked.max_batch <= 64


def test_adapter_streams_payloads_without_truncating_rows_or_order(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from production_ede_v13_audit import ReadOnlyRuntime
    import sqlite3
    runtime = _ProspectiveRuntime()
    for index in range(145):
        runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
            (str(index), 100.+index, 1000.+index, 'NAS100', 15,
             _prospective_features(100.+index)))
    runtime._conn.commit()
    expected = ProspectiveFeatureAdapter(runtime, available_asof=500.).rows(
        resolved_only=False, strict=False, horizon_minutes=15)
    database = tmp_path / 'immutable.sqlite3'
    destination = sqlite3.connect(database)
    runtime._conn.backup(destination)
    destination.close()
    immutable = ReadOnlyRuntime(database)
    tracked = PayloadConnection(immutable._conn)
    immutable._conn = tracked
    try:
        actual = ProspectiveFeatureAdapter(immutable, available_asof=500.).rows(
            resolved_only=False, strict=False, horizon_minutes=15)
    finally:
        immutable.close()
    assert actual == expected
    assert len(actual) == len(tracked.loaded) == 145
    assert tracked.max_batch <= 64


def test_live_source_extraction_is_atomic_against_same_connection_resolution_write():
    runtime = _ProspectiveRuntime()
    runtime._conn.execute('CREATE INDEX source_order ON g1s_observations(captured_ts,instrument,horizon_minutes)')
    for index in range(145):
        runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
            (str(index), 100.+index, 1000.+index, 'NAS100', 15,
             _prospective_features(100.+index)))
    runtime._conn.commit()
    adapter = ProspectiveFeatureAdapter(runtime, available_asof=500.)
    source = iter(adapter._source_rows(horizon_minutes=15))
    first = next(source)
    with runtime._lock:
        runtime._conn.execute('INSERT INTO g1s_resolutions VALUES (?,?,?,?,?,?,?)',
            ('144', 1200., .001, 'UP', .002, -.001, 'OK'))
        runtime._conn.commit()
    extracted = [first, *source]
    assert len(extracted) == 145
    assert extracted[-1]['observation_id'] == '144'
    assert extracted[-1]['resolved_ts'] is None
    assert runtime._conn.execute('SELECT resolved_ts FROM g1s_resolutions').fetchone()[0] == 1200.


def test_horizon_local_transition_inputs_preserve_coverage_gate_and_fingerprint(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    from scripts import production_ede_transition_audit as audit
    runtime = _ProspectiveRuntime()
    for horizon in (15, 30, 60, 120, 240):
        for index in range(12):
            for instrument in ('NAS100', 'SP500'):
                t0 = 100. + index * 60
                oid = f'{horizon}-{index}-{instrument}'
                frozen = json.loads(_prospective_features(t0))
                frozen.update(json.loads(_frozen(t0)))
                runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
                    (oid, t0, t0 + 900, instrument, horizon, json.dumps(frozen)))
                if index < 9:
                    runtime._conn.execute('INSERT INTO g1s_resolutions VALUES (?,?,?,?,?,?,?)',
                        (oid, t0+900, .001, 'UP' if index % 2 else 'DOWN', .002, -.001, 'OK'))
    runtime._conn.commit()
    adapter = ProspectiveFeatureAdapter(runtime, available_asof=10000.)
    all_rows = adapter.rows(resolved_only=False, strict=False)
    expected_coverage = augment_rows_from_frozen_v3(runtime, all_rows)
    resolved = [r for r in all_rows if r['outcome_available'] and r['horizon_minutes'] in (15,30,60)]
    expected_rows, expected_gate = baseline_eligible_rows(resolved)
    expected_sha = research_dataset_fingerprint(expected_rows,
        eligible_feature_ids=audit._transition_available_feature_ids(expected_rows))
    requested = []
    original = adapter.rows

    def tracked_rows(**kwargs):
        requested.append(kwargs.get('horizon_minutes'))
        return original(**kwargs)

    adapter.rows = tracked_rows
    rows, coverage, gate = audit._load_transition_inputs(runtime, adapter)
    assert requested == [15, 30, 60]
    assert rows == expected_rows
    assert coverage == expected_coverage and gate == expected_gate
    assert research_dataset_fingerprint(rows,
        eligible_feature_ids=audit._transition_available_feature_ids(rows)) == expected_sha


def test_augmentation_crosses_id_batches_without_losing_duplicate_inputs():
    runtime = Runtime()
    for index in range(145):
        runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?)',
            (str(index), 100., 30, _frozen(99.)))
    runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?)',
        ('unsupported', 100., 120, _frozen(99.)))
    rows = [_row(str(index)) for index in range(145)]
    rows += [_row('1'), _row('unsupported'), _row('missing')]
    tracked = PayloadConnection(runtime._conn)
    runtime._conn = tracked
    coverage = augment_rows_from_frozen_v3(runtime, rows)
    assert all(r['ede_features']['regime.macro_transition_velocity'] == 1.2 for r in rows[:146])
    assert 'regime.macro_transition_velocity' not in rows[-2]['ede_features']
    assert 'regime.macro_transition_velocity' not in rows[-1]['ede_features']
    assert len(tracked.loaded) == 145
    assert coverage['coverage_by_horizon']['15']['regime.macro_transition_velocity'] == 146
