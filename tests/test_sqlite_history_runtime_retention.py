"""Runtime readers must not silently lose original parent rows or recent input."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import sqlite_history_cutover as cutover


@pytest.fixture
def runtime_source():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE passive_market_observations('
                 'observation_id TEXT PRIMARY KEY,instrument TEXT,captured_ts REAL,'
                 'horizon_minutes INTEGER,evidence_eligible INTEGER,created_ts REAL DEFAULT 1)')
    conn.execute('CREATE TABLE g1s_observations(source_observation_id TEXT)')
    conn.execute('CREATE TABLE g1_dataset_membership(observation_id TEXT)')
    conn.execute('CREATE TABLE virtual_position_observations(passive_observation_id TEXT)')
    conn.execute('CREATE TEMP TABLE _cutover_protected_obs(observation_id TEXT PRIMARY KEY)')
    for code in ('A', 'B'):
        conn.executemany('INSERT INTO passive_market_observations(observation_id,instrument,captured_ts,horizon_minutes,evidence_eligible) VALUES(?,?,?,?,?)',
                         [(f'{code}-{n}', code, float(n), 60, 0) for n in range(1, 1003)])
    yield conn
    conn.close()


def protected(conn):
    return {r[0] for r in conn.execute('SELECT observation_id FROM temp._cutover_protected_obs')}


def test_retains_all_reader_parents_including_rejected_and_completed(runtime_source):
    conn = runtime_source
    conn.execute("INSERT INTO g1s_observations VALUES('A-1')")
    conn.execute("INSERT INTO g1_dataset_membership VALUES('B-1')")
    conn.execute("INSERT INTO virtual_position_observations VALUES('A-2')")
    cutover._populate_runtime_protected_observation_ids(conn, 'main')
    assert {'A-1', 'B-1', 'A-2'} <= protected(conn)
    assert 'B-2' not in protected(conn)


def test_retains_actual_endpoint_limit_per_instrument_and_all_boundary_ties(runtime_source):
    conn = runtime_source
    conn.execute("INSERT INTO passive_market_observations(observation_id,instrument,captured_ts,horizon_minutes,evidence_eligible) VALUES('A-tie','A',3,60,0)")
    cutover._populate_runtime_protected_observation_ids(conn, 'main')
    ids = protected(conn)
    assert all(f'{code}-{n}' in ids for code in ('A', 'B') for n in range(3, 1003))
    assert 'A-tie' in ids
    assert not {'A-1', 'A-2', 'B-1', 'B-2'} & ids


def test_retains_old_trigger_anchor_and_latest_eligible_health_fact(runtime_source):
    conn = runtime_source
    conn.execute("UPDATE passive_market_observations SET horizon_minutes=15 WHERE observation_id='A-1'")
    conn.execute("UPDATE passive_market_observations SET evidence_eligible=1 WHERE observation_id='B-1'")
    cutover._populate_runtime_protected_observation_ids(conn, 'main')
    assert {'A-1', 'B-1'} <= protected(conn)


def test_retains_all_recent_health_inputs_even_when_more_than_1000(runtime_source):
    import time
    conn = runtime_source
    conn.execute('UPDATE passive_market_observations SET captured_ts=captured_ts+?', (time.time()-1003,))
    cutover._populate_runtime_protected_observation_ids(conn, 'main')
    assert len(protected(conn)) == 2004


@pytest.mark.parametrize('missing', ['g1s_observations', 'g1_dataset_membership', 'virtual_position_observations'])
def test_unknown_runtime_dependency_schema_refuses_retirement(runtime_source, missing):
    conn = runtime_source
    conn.execute('DROP TABLE '+missing)
    with pytest.raises(ValueError, match='runtime dependency schema'):
        cutover._populate_runtime_protected_observation_ids(conn, 'main')


def test_expired_reference_budget_preserves_existing_protection(runtime_source):
    with pytest.raises(ValueError, match='deadline'):
        cutover._populate_runtime_protected_observation_ids(runtime_source, 'main', deadline=0)
    assert protected(runtime_source) == set()


def test_retains_recent_creation_budget_even_with_an_old_capture_clock(runtime_source):
    import time
    conn = runtime_source
    conn.execute("UPDATE passive_market_observations SET created_ts=? WHERE observation_id='B-1'", (time.time(),))
    cutover._populate_runtime_protected_observation_ids(conn, 'main')
    assert 'B-1' in protected(conn)
