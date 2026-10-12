"""Dedicated real read-only transaction; no fake immutable-source provenance."""
import sqlite3

import pytest

from scripts.production_ede_inventory import _ReadOnlyRuntime
from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from test_edge_discovery_engine import _ProspectiveRuntime,_prospective_features
from test_ede_transition_loading import PayloadConnection


def database(tmp_path):
    seed=_ProspectiveRuntime()
    for i in range(145):
        seed._conn.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
            (str(i),100.+i,1000.+i,'NAS100',15,_prospective_features(100.+i)))
    seed._conn.commit()
    path=tmp_path/'live.sqlite3'
    writer=sqlite3.connect(path);seed._conn.backup(writer)
    writer.execute('PRAGMA journal_mode=WAL');writer.commit()
    return path,writer


def test_inventory_streams_payloads_in_owned_consistent_read_transaction(tmp_path):
    path,writer=database(tmp_path)
    runtime=_ReadOnlyRuntime(path);tracked=PayloadConnection(runtime._conn);runtime._conn=tracked
    try:
        assert runtime._conn.in_transaction
        rows=list(ProspectiveFeatureAdapter(runtime)._source_rows(horizon_minutes=15))
        assert len(rows)==len(tracked.loaded)==145
        assert tracked.max_batch<=32
        assert not getattr(runtime,'_immutable_snapshot',False)
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            runtime._conn.execute('DELETE FROM g1s_observations')
    finally:runtime.close();writer.close()


def test_inventory_preserves_same_snapshot_across_horizons_and_concurrent_writer(tmp_path):
    path,writer=database(tmp_path)
    runtime=_ReadOnlyRuntime(path)
    try:
        initial=runtime._conn.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0]
        writer.execute('INSERT INTO g1s_observations VALUES (?,?,?,?,?,?)',
            ('new',1000.,2000.,'NAS100',30,_prospective_features(1000.)))
        writer.commit()
        adapter=ProspectiveFeatureAdapter(runtime)
        assert list(adapter._source_rows(horizon_minutes=30))==[]
        assert runtime._conn.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0]==initial
        assert writer.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0]==initial+1
    finally:runtime.close();writer.close()


def test_transaction_marker_without_active_transaction_keeps_atomic_fallback(tmp_path):
    path,writer=database(tmp_path)
    runtime=_ReadOnlyRuntime(path)
    runtime._conn.rollback()
    runtime._conn=PayloadConnection(runtime._conn)
    try:
        with pytest.raises(AssertionError,match='unbounded observation payload fetchall'):
            list(ProspectiveFeatureAdapter(runtime)._source_rows(horizon_minutes=15))
    finally:runtime.close();writer.close()


def test_streaming_preserves_all_derived_rows_outcomes_and_feature_fingerprint(tmp_path):
    from seiltanzer.edge_discovery.dataset_fingerprint import research_dataset_fingerprint
    from types import SimpleNamespace
    path,writer=database(tmp_path)
    runtime=_ReadOnlyRuntime(path)
    atomic=SimpleNamespace(_conn=writer,_lock=runtime._lock)
    writer.row_factory=sqlite3.Row
    try:
        expected=ProspectiveFeatureAdapter(atomic,available_asof=500.).rows(
            resolved_only=False,strict=False,horizon_minutes=15)
        runtime._conn=PayloadConnection(runtime._conn)
        actual=ProspectiveFeatureAdapter(runtime,available_asof=500.).rows(
            resolved_only=False,strict=False,horizon_minutes=15)
        assert actual==expected
        ids=('price.ret_5m','regime.trend')
        assert research_dataset_fingerprint(actual,eligible_feature_ids=ids)==research_dataset_fingerprint(expected,eligible_feature_ids=ids)
        assert len(runtime._conn.loaded)==145 and runtime._conn.max_batch<=32
    finally:runtime.close();writer.close()
