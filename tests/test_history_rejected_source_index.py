"""Explicit rejected-source metadata never substitutes for full source payloads."""
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from seiltanzer.config import Settings
from seiltanzer.g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION, _sync_membership
from seiltanzer.passive_learning import PassiveLearningEngine
from test_g1a_dataset_contract import _insert_resolved


def projection_module():
    path = Path(__file__).resolve().parents[1]/'seiltanzer/history_runtime_projection.py'
    spec = importlib.util.spec_from_file_location('_rejected_projection_standalone', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rejected_source(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path/'source.db'), Settings(), cache=None)
    _insert_resolved(engine, 'old-ifd', anchor='old', captured=1700000000., target=1700000900.)
    with engine._conn:
        engine._conn.execute("UPDATE passive_market_observations SET resolution_status='insufficient_future_data'")
    _sync_membership(engine, limit=100)
    output = sqlite3.connect(':memory:')
    yield engine, output
    output.close()
    engine.close()


def create(module, engine, output):
    return module.create_rejected_source_index(output, engine._conn, [1],
        dataset_contract_version=G1_DATASET_CONTRACT_VERSION, deadline=time.monotonic()+5)


def test_real_rejected_source_produces_only_verified_metadata_and_immutable_index(rejected_source):
    engine, output = rejected_source
    module = projection_module()
    before = [tuple(r) for r in engine._conn.execute('SELECT * FROM passive_market_observations')]
    proof = create(module, engine, output)
    assert proof['row_count'] == 1
    assert proof['production_authority'] is False
    assert module.validate_rejected_source_index(output, proof, retired_rowids=[1]) == proof
    columns = [r[1] for r in output.execute('PRAGMA table_info(archived_passive_rejections)')]
    assert not {'features_json', 'forecast_json', 'outcome_json'} & set(columns)
    assert output.execute('SELECT observation_id,instrument,resolution_status FROM archived_passive_rejections').fetchone() == (
        'old-ifd', 'NAS100', 'insufficient_future_data')
    for sql in ("UPDATE archived_passive_rejections SET instrument='OTHER'",
                'DELETE FROM archived_passive_rejections',
                'INSERT INTO archived_passive_rejections SELECT * FROM archived_passive_rejections'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            output.execute(sql)
    assert before == [tuple(r) for r in engine._conn.execute('SELECT * FROM passive_market_observations')]


@pytest.mark.parametrize('hazard', ['unprocessed', 'changed_source', 'resolved', 'eligible', 'wrong_contract'])
def test_unproved_or_admitted_source_cannot_be_archived(rejected_source, hazard):
    engine, output = rejected_source
    module = projection_module()
    if hazard in ('unprocessed', 'wrong_contract', 'eligible'):
        # Construct an independent source variant without changing the live
        # registry's immutable guards. These are disposable test databases.
        for (name,) in engine._conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name='g1_dataset_membership'").fetchall():
            engine._conn.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        if hazard == 'unprocessed':
            engine._conn.execute('DELETE FROM g1_dataset_membership')
        elif hazard == 'eligible':
            engine._conn.execute('UPDATE g1_dataset_membership SET forecast_eval_eligible=1')
        else:
            engine._conn.execute("UPDATE g1_dataset_membership SET dataset_contract_version='old-version'")
    elif hazard == 'resolved':
        engine._conn.execute("UPDATE passive_market_observations SET resolution_status='resolved'")
    else:
        engine._conn.execute('UPDATE passive_market_observations SET resolved_ts=resolved_ts+1')
    with pytest.raises(ValueError, match='rejected source'):
        create(module, engine, output)
    assert output.execute("SELECT count(*) FROM sqlite_schema WHERE name='archived_passive_rejections'").fetchone() == (0,)


@pytest.mark.parametrize('corruption', ['missing_row', 'different_mapping', 'removed_guard'])
def test_projection_tampering_is_rejected_before_reader_use(rejected_source, corruption):
    engine, output = rejected_source
    module = projection_module()
    proof = create(module, engine, output)
    guards = output.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'").fetchall()
    for name, _ in guards:
        output.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
    if corruption == 'missing_row':
        output.execute('DELETE FROM archived_passive_rejections')
    elif corruption == 'different_mapping':
        output.execute("UPDATE archived_passive_rejections SET instrument='OTHER'")
    if corruption != 'removed_guard':
        for _, sql in guards:
            output.execute(sql)
    with pytest.raises(ValueError, match='rejected source index'):
        module.validate_rejected_source_index(output, proof, retired_rowids=[1])


def test_bad_second_source_rolls_back_the_entire_derived_index(rejected_source):
    engine, output = rejected_source
    module = projection_module()
    _insert_resolved(engine, 'still-resolved', anchor='resolved', captured=1700001000., target=1700001900.)
    _sync_membership(engine, limit=100)
    with pytest.raises(ValueError, match='rejected source'):
        module.create_rejected_source_index(output, engine._conn, [1, 2],
            dataset_contract_version=G1_DATASET_CONTRACT_VERSION, deadline=time.monotonic()+5)
    assert output.execute("SELECT count(*) FROM sqlite_schema WHERE name='archived_passive_rejections'").fetchone() == (0,)


def test_expired_index_budget_cannot_create_derived_storage(rejected_source):
    engine, output = rejected_source
    module = projection_module()
    with pytest.raises(ValueError, match='deadline'):
        module.create_rejected_source_index(output, engine._conn, [1],
            dataset_contract_version=G1_DATASET_CONTRACT_VERSION, deadline=0)
    assert output.execute("SELECT count(*) FROM sqlite_schema WHERE name='archived_passive_rejections'").fetchone() == (0,)
