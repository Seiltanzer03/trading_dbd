"""Materialized presentation must retain authoritative Q calibration gates."""
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from seiltanzer import g1_shadow_runtime as authoritative
from seiltanzer import research_scalability as bounded
from seiltanzer.g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION, _effective_n_nonoverlap


def engine():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE g1_q_capture_attempts(attempt_origin,observation_created,attempt_ts,
            created_observation_id,blocker_code,relation,provider);
        CREATE TABLE passive_market_observations(observation_id,forecast_json,outcome_json,
            captured_ts,target_ts,instrument,resolution_status);
        CREATE TABLE g1_dataset_membership(observation_id,dataset_contract_version,
            q_to_p_eligible,forecast_eval_eligible,dependency_group_id);
        CREATE TABLE g1_contract_errors(dataset_contract_version,observation_id,error_type);
        CREATE TABLE g1c_shadow_models(model_id);
        CREATE TABLE g1c_shadow_predictions(prediction_id);
        CREATE TABLE g1c_fit_runs(fit_run_id);
        CREATE TABLE g1c_contract_errors(error_type);
    ''')
    return SimpleNamespace(_conn=conn,_lock=threading.RLock(),
        _g1_effective_n=lambda rows,aggregate=False:_effective_n_nonoverlap(rows,aggregate=aggregate))


def seed(runtime, n=240):
    for i in range(n):
        ts=1790985600+(i%4)*86400+(i//4)*300
        expiry=1791417600+(i%2)*86400
        forecast={'terminal_q_cdf':{'support':[-.01,0,.01], 'cdf':[0,.2+(i%20)*.02,1]},
                  'source_expiry_ts_utc':expiry}
        # Canonical label is future_log_return; older nested terminal can disagree.
        outcome={'future_log_return':.01 if i%2 else -.01,
                 'terminal':{'terminal_log_return':.01}}
        runtime._conn.execute('INSERT INTO passive_market_observations VALUES(?,?,?,?,?,?,?)',
            (str(i),json.dumps(forecast),json.dumps(outcome),ts,ts+60,'NAS100','resolved'))
        runtime._conn.execute('INSERT INTO g1_dataset_membership VALUES(?,?,?,?,?)',
            (str(i),G1_DATASET_CONTRACT_VERSION,1,1,str(i)))


def test_fit_readiness_is_not_a_fitted_model():
    stats={'raw_n':100,'effective_n':100,'positive_n':50,'negative_n':50,'unique_q_n':20}
    assert bounded._threshold_status(stats,'PLATT')['status']=='READY_TO_FIT'


@pytest.mark.parametrize('error_n',[0,1])
def test_materialized_g1d_keeps_periods_expiries_labels_and_errors(error_n):
    runtime=engine();seed(runtime)
    for _ in range(error_n):runtime._conn.execute('INSERT INTO g1c_contract_errors VALUES(?)',('SOURCE_MUTATED',))
    status=bounded._light_g1c_status(runtime)
    observed=status['g1d_readiness']['observed']
    assert observed['temporal_period_n']==4
    assert observed['expiry_cluster_n']==2
    assert observed['positive_n']==observed['negative_n']==120
    assert status['g1d_readiness']==authoritative._g1d_status(observed,error_n)
    assert status['ready_for_g1d'] is (error_n == 0)
    assert status['g1d_readiness']['blockers']==(['CRITICAL_CONTRACT_ERRORS'] if error_n else [])
    assert status['frozen_model_n']==0
    assert not status['calibrator_fitted']
    assert not status['production_authority']
    runtime._conn.close()


def test_materialized_stats_exclude_mutated_and_invalid_q_rows():
    runtime=engine();seed(runtime,4)
    runtime._conn.execute('INSERT INTO g1_contract_errors VALUES(?,?,?)',
        (G1_DATASET_CONTRACT_VERSION,'0','SOURCE_MUTATED'))
    runtime._conn.execute("UPDATE passive_market_observations SET forecast_json='{}' WHERE observation_id='1'")
    status=bounded._light_g1c_status(runtime)
    assert status['q_eligible']==2
    assert status['effective_q_n']==2
    runtime._conn.close()
