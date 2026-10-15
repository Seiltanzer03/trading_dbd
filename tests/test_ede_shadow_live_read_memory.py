"""Live shadow must stream raw JSON from an owned, consistent WAL reader."""
import json
import sqlite3
import threading
import tracemalloc
from types import SimpleNamespace

import pytest

from seiltanzer.edge_discovery import shadow_runtime as shadow
from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.dataset_fingerprint import research_dataset_fingerprint
from test_edge_discovery_engine import _ProspectiveRuntime, _prospective_features
from test_ede_transition_loading import PayloadConnection


def engine_with_history(tmp_path, count=145, padding=0):
    seed = _ProspectiveRuntime()
    for index in range(count):
        frozen = json.loads(_prospective_features(100. + index))
        if padding:
            frozen['unused_payload'] = 'x' * padding
        seed._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
            (str(index), 100. + index, 1000. + index, 'NAS100',
             (15, 30, 60, 120, 240)[index % 5], json.dumps(frozen)))
    seed._conn.commit()
    writer = sqlite3.connect(tmp_path / 'live.sqlite3')
    seed._conn.backup(writer)
    seed._conn.close()
    writer.row_factory = sqlite3.Row
    writer.execute('PRAGMA journal_mode=WAL')
    writer.commit()
    runtime = SimpleNamespace(_conn=writer, _lock=threading.RLock())
    return SimpleNamespace(short_horizon=runtime,
        settings=SimpleNamespace(data_dir=tmp_path)), writer


def prepare_shadow(monkeypatch):
    monkeypatch.setattr(shadow, '_run_llm_edge_lifecycle',
        lambda engine, now: {'status': 'UNCHANGED'})
    monkeypatch.setattr(shadow, '_load_latest_audit', lambda path: {
        'frozen_evidence': {'frozen_at': 1.}, 'selective_search': {}})


def test_production_entry_streams_owned_reader_and_preserves_full_rows(tmp_path, monkeypatch):
    engine, writer = engine_with_history(tmp_path)
    prepare_shadow(monkeypatch)
    expected = ProspectiveFeatureAdapter(engine.short_horizon, available_asof=500.).rows(
        resolved_only=False, strict=False)
    readers, inputs, transformed = [], [], []

    class RecordingAdapter(ProspectiveFeatureAdapter):
        def rows(self, **kwargs):
            rows = super().rows(**kwargs)
            transformed.extend(rows)
            return rows

    def adapter(runtime, **kwargs):
        assert runtime is not engine.short_horizon, 'live shadow still uses shared atomic payload extraction'
        assert runtime._consistent_read_snapshot and runtime._conn.in_transaction
        assert not getattr(runtime, '_immutable_snapshot', False)
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            runtime._conn.execute('DELETE FROM g1s_observations')
        runtime._conn = PayloadConnection(runtime._conn)
        readers.append(runtime._conn)
        return RecordingAdapter(runtime, available_asof=500.)

    original = shadow.create_shadow_predictions
    def create(ledger, **kwargs):
        inputs.extend(kwargs['pending_rows'])
        return original(ledger, **kwargs)

    monkeypatch.setattr(shadow, 'ProspectiveFeatureAdapter', adapter)
    monkeypatch.setattr(shadow, 'create_shadow_predictions', create)
    try:
        report = shadow.materialize_runtime_shadow(engine, now=500.)
        assert report['refreshed'] and report['pending_rows_seen'] == 87
        assert transformed == expected
        assert inputs == [row for row in expected
                          if row['horizon_minutes'] in shadow.SELECTIVE_HORIZONS]
        ids = ('price.ret_5m', 'regime.trend')
        assert research_dataset_fingerprint(transformed, eligible_feature_ids=ids) == research_dataset_fingerprint(expected, eligible_feature_ids=ids)
        assert len(readers[0].loaded) == 145 and readers[0].max_batch <= 32
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            readers[0].execute('SELECT 1')
        assert writer.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0] == 145
    finally:
        writer.close()


def test_reader_retains_snapshot_during_concurrent_live_commit(tmp_path, monkeypatch):
    engine, writer = engine_with_history(tmp_path)
    prepare_shadow(monkeypatch)
    seen = []

    class ConcurrentAdapter(ProspectiveFeatureAdapter):
        def rows(self, **kwargs):
            writer.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
                ('concurrent', 200., 1200., 'NAS100', 15, _prospective_features(200.)))
            writer.commit()
            rows = super().rows(**kwargs)
            seen.extend(row['observation_id'] for row in rows)
            return rows

    monkeypatch.setattr(shadow, 'ProspectiveFeatureAdapter', ConcurrentAdapter)
    try:
        report = shadow.materialize_runtime_shadow(engine, now=500.)
        assert 'concurrent' not in seen, 'same-connection write entered the supposedly frozen cohort'
        assert len(seen) == 145 and report['pending_rows_seen'] == 87
        assert writer.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0] == 146
        assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    finally:
        writer.close()


def test_reader_closes_on_feature_failure_without_publishing_partial_cache(tmp_path, monkeypatch):
    engine, writer = engine_with_history(tmp_path)
    prepare_shadow(monkeypatch)
    readers = []
    class FailingAdapter(ProspectiveFeatureAdapter):
        def rows(self, **kwargs):
            readers.append(self.runtime._conn)
            raise ValueError('feature failure')
    monkeypatch.setattr(shadow, 'ProspectiveFeatureAdapter', FailingAdapter)
    try:
        with pytest.raises(ValueError, match='feature failure'):
            shadow.materialize_runtime_shadow(engine, now=500.)
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            readers[0].execute('SELECT 1')
        assert writer.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0] == 145
        assert not list(tmp_path.rglob('*shadow*'))
    finally:
        writer.close()


def test_raw_payload_peak_is_bounded_by_batch_not_full_cohort(tmp_path, monkeypatch):
    engine, writer = engine_with_history(tmp_path, count=96, padding=128 * 1024)
    prepare_shadow(monkeypatch)
    consumed = []
    class PayloadOnlyAdapter(ProspectiveFeatureAdapter):
        def rows(self, **kwargs):
            for row in self._source_rows():
                consumed.append(row['observation_id'])
            return []
    monkeypatch.setattr(shadow, 'ProspectiveFeatureAdapter', PayloadOnlyAdapter)
    try:
        tracemalloc.start()
        try:
            shadow.materialize_runtime_shadow(engine, now=500.)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(consumed) == 96
        assert peak < 6 * 1024 ** 2, f'raw cohort retained: peak={peak}'
    finally:
        writer.close()


@pytest.mark.parametrize('refusal', ['time', 'disk'])
def test_resource_refusal_never_publishes_partial_shadow(tmp_path, monkeypatch, refusal):
    engine, writer = engine_with_history(tmp_path)
    prepare_shadow(monkeypatch)
    if refusal == 'time':
        monkeypatch.setattr(shadow, '_SHADOW_READ_BUDGET_SEC', 0., raising=False)
    else:
        monkeypatch.setattr(shadow, '_shadow_read_has_headroom', lambda path: False, raising=False)
    try:
        with pytest.raises(RuntimeError, match='SHADOW_READ_.*BUDGET'):
            shadow.materialize_runtime_shadow(engine, now=500.)
        assert not list(tmp_path.rglob('*shadow*'))
        assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    finally:
        writer.close()


@pytest.mark.parametrize('refusal', ['time', 'disk'])
def test_budget_change_during_row_processing_closes_reader_before_publication(tmp_path, monkeypatch, refusal):
    engine, writer = engine_with_history(tmp_path)
    prepare_shadow(monkeypatch)
    clock, capacity, readers = [0.], [True], []
    monkeypatch.setattr(shadow, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], time=lambda: 500.))
    monkeypatch.setattr(shadow, '_shadow_read_has_headroom', lambda path: capacity[0])
    class ExhaustingAdapter(ProspectiveFeatureAdapter):
        def rows(self, **kwargs):
            readers.append(self.runtime._conn)
            rows = super().rows(**kwargs)
            if refusal == 'time':
                clock[0] = 31.
            else:
                capacity[0] = False
            return rows
    monkeypatch.setattr(shadow, 'ProspectiveFeatureAdapter', ExhaustingAdapter)
    try:
        with pytest.raises(RuntimeError, match='SHADOW_READ_.*BUDGET'):
            shadow.materialize_runtime_shadow(engine, now=500.)
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            readers[0].execute('SELECT 1')
        assert not list(tmp_path.rglob('*shadow*'))
        assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    finally:
        writer.close()


def test_in_memory_runtime_keeps_its_existing_atomic_history(tmp_path, monkeypatch):
    runtime = _ProspectiveRuntime()
    runtime._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
        ('retained', 100., 1000., 'NAS100', 15, _prospective_features(100.)))
    runtime._conn.commit()
    engine = SimpleNamespace(short_horizon=runtime,
        settings=SimpleNamespace(data_dir=tmp_path))
    prepare_shadow(monkeypatch)
    try:
        report = shadow.materialize_runtime_shadow(engine, now=500.)
        assert report['pending_rows_seen'] == 1
        assert runtime._conn.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0] == 1
    finally:
        runtime._conn.close()


def test_sql_progress_interrupt_closes_snapshot_and_keeps_original_history(tmp_path, monkeypatch):
    engine, writer = engine_with_history(tmp_path)
    prepare_shadow(monkeypatch)
    clock, readers = [0.], []
    monkeypatch.setattr(shadow, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], time=lambda: 500.))
    class ExpiringAdapter(ProspectiveFeatureAdapter):
        def rows(self, **kwargs):
            readers.append(self.runtime._conn)
            clock[0] = 31.
            return super().rows(**kwargs)
    monkeypatch.setattr(shadow, 'ProspectiveFeatureAdapter', ExpiringAdapter)
    try:
        with pytest.raises(RuntimeError, match='SHADOW_READ_TIME_BUDGET') as error:
            shadow.materialize_runtime_shadow(engine, now=500.)
        assert isinstance(error.value.__cause__, sqlite3.OperationalError)
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            readers[0].execute('SELECT 1')
        assert writer.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0] == 145
        assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
        assert not list(tmp_path.rglob('*shadow*'))
    finally:
        writer.close()
