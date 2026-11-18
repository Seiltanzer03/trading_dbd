import sqlite3
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from seiltanzer.macro_numeric_data import NumericMacroStore, ReleaseWrite
from test_fomc_latest_http_nonblocking import _app


def _seed(store):
    payload = {'period': '2026-10', 'headline_mom_pct': .35,
               'headline_yoy_pct': None,
               'unavailable_fields': {'headline_yoy_pct': {
                   'reason': 'OFFICIAL_REFERENCE_OBSERVATION_UNAVAILABLE',
                   'official_marker': '-', 'reference_period': '2025-10'}}}
    url = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'
    store.ingest(ReleaseWrite('CPI', '2026-10', 'BLS', url, 1000., payload))
    store.ingest(ReleaseWrite('CPI', '2026-10', 'BLS', url, 1500., payload,
                             ({'series_id': 'CUUR0000SA0', 'fetched_at': 1500.},)))


@pytest.mark.parametrize('cutoff', [999., 1200., 1600.])
def test_http_reads_committed_causal_release_before_research_writer_releases(
        tmp_path, monkeypatch, cutoff):
    app = _app(tmp_path, monkeypatch)
    store = app.state.macro_numeric_store
    _seed(store)
    expected = store.latest_admissible('CPI', cutoff)
    acquired, release, completed = (threading.Event() for _ in range(3))
    responses = []

    def writer():
        with app.state.engine.short_horizon._lock:
            acquired.set()
            release.wait(3)

    def reader():
        try:
            responses.append(TestClient(app).get('/api/research/macro/latest',
                             params={'family': 'CPI', 'captured_ts': cutoff}))
        finally:
            completed.set()

    writing = threading.Thread(target=writer)
    reading = threading.Thread(target=reader)
    writing.start()
    assert acquired.wait(1)
    reading.start()
    try:
        assert completed.wait(1), 'Numeric macro HTTP waited for the research writer'
        assert responses[0].status_code == 200
        assert responses[0].json() == expected
    finally:
        release.set()
        writing.join(3)
        reading.join(3)
        app.state.engine.close()


def _file_store(path):
    connection = sqlite3.connect(path, check_same_thread=False)
    return NumericMacroStore(SimpleNamespace(_conn=connection, _lock=threading.RLock()))


def test_real_sqlite_exclusive_writer_is_unavailable_then_recovers(tmp_path):
    store = _file_store(tmp_path / 'macro.db')
    blocker = sqlite3.connect(tmp_path / 'macro.db')
    try:
        _seed(store)
        expected = store.latest_admissible('CPI', 1600.)
        blocker.execute('BEGIN EXCLUSIVE')
        result = store.latest_admissible('CPI', 1600., nonblocking=True)
        assert result == {'status': 'UNAVAILABLE', 'family': 'CPI',
                          'reason': 'NUMERIC_MACRO_RELEASE_READ_BUSY'}
        blocker.rollback()
        assert store.latest_admissible('CPI', 1600., nonblocking=True) == expected
    finally:
        blocker.close()
        store._conn.close()


@pytest.mark.parametrize('replacement', [False, True])
def test_missing_or_replaced_file_cannot_supply_other_database(tmp_path, replacement):
    path = tmp_path / 'macro.db'
    store = _file_store(path)
    try:
        _seed(store)
        path.rename(tmp_path / 'old.db')
        if replacement:
            other = _file_store(path)
            _seed(other)
            other._conn.close()
        result = store.latest_admissible('CPI', 1600., nonblocking=True)
        assert result['status'] == 'UNAVAILABLE'
        assert result['reason'] == 'NUMERIC_MACRO_RELEASE_DATABASE_CHANGED'
        assert path.exists() is replacement
    finally:
        store._conn.close()


@pytest.mark.parametrize('raw,reason', [('{' , 'NUMERIC_MACRO_RELEASE_READ_INVALID'),
                                      ('[]', 'NUMERIC_MACRO_RELEASE_READ_INVALID'),
                                      ('{"value":NaN}', 'NUMERIC_MACRO_RELEASE_READ_INVALID'),
                                      ('{"value":1e400}', 'NUMERIC_MACRO_RELEASE_READ_INVALID'),
                                      ('x' * 140000, 'NUMERIC_MACRO_RELEASE_READ_SIZE_BOUND')])
def test_invalid_or_oversized_row_fails_closed(tmp_path, raw, reason):
    store = _file_store(tmp_path / 'macro.db')
    try:
        _seed(store)
        with store._conn:
            store._conn.execute('UPDATE macro_numeric_releases SET payload_json=?', (raw,))
        result = store.latest_admissible('CPI', 1600., nonblocking=True)
        assert result == {'status': 'UNAVAILABLE', 'family': 'CPI', 'reason': reason}
    finally:
        store._conn.close()


def test_read_deadline_interrupts_real_sqlite_sort(tmp_path, monkeypatch):
    from seiltanzer import macro_numeric_data as module
    store = _file_store(tmp_path / 'macro.db')
    try:
        _seed(store)
        with store._conn:
            store._conn.execute('''WITH RECURSIVE n(x) AS (
                SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<3000)
                INSERT INTO macro_numeric_releases
                SELECT 'tie-'||x,family,period,source,source_url,fetched_at,
                available_at,payload_json,'hash-'||x,contract_version,created_ts+x,
                latest_source_receipt_json,first_source_receipt_json
                FROM macro_numeric_releases,n WHERE release_id NOT LIKE 'tie-%' ''')
        clock = iter([0.])
        monkeypatch.setattr(module, 'time', SimpleNamespace(
            monotonic=lambda: next(clock, 1.)))
        assert store.latest_admissible('CPI', 1600., nonblocking=True) == {
            'status': 'UNAVAILABLE', 'family': 'CPI',
            'reason': 'NUMERIC_MACRO_RELEASE_READ_DEADLINE'}
    finally:
        store._conn.close()


def test_wal_reader_never_exposes_uncommitted_replacement_payload(tmp_path):
    path = tmp_path / 'macro.db'
    store = _file_store(path)
    writer = sqlite3.connect(path)
    try:
        store._conn.execute('PRAGMA journal_mode=WAL')
        _seed(store)
        expected = store.latest_admissible('CPI', 1600.)
        writer.execute("UPDATE macro_numeric_releases SET payload_json='{}'")
        assert store.latest_admissible('CPI', 1600., nonblocking=True) == expected
        writer.rollback()
    finally:
        writer.close()
        store._conn.close()


def test_memory_reader_uses_original_store_and_reports_busy():
    runtime = SimpleNamespace(_conn=sqlite3.connect(':memory:', check_same_thread=False),
                              _lock=threading.RLock())
    store = NumericMacroStore(runtime)
    acquired, release = threading.Event(), threading.Event()
    def hold():
        with runtime._lock:
            acquired.set()
            release.wait(2)
    try:
        _seed(store)
        expected = store.latest_admissible('CPI', 1600.)
        worker = threading.Thread(target=hold)
        worker.start()
        assert acquired.wait(1)
        try:
            assert store.latest_admissible('CPI', 1600., nonblocking=True) == {
                'status': 'UNAVAILABLE', 'family': 'CPI',
                'reason': 'NUMERIC_MACRO_RELEASE_STORE_BUSY'}
        finally:
            release.set()
            worker.join(2)
        assert store.latest_admissible('CPI', 1600., nonblocking=True) == expected
    finally:
        store._conn.close()
