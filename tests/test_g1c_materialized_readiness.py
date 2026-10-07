"""Materialized presentation must retain authoritative Q calibration gates."""
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from seiltanzer import g1_shadow_runtime as authoritative
from seiltanzer import research_scalability as bounded
from seiltanzer import g1_shadow_refinement as integrity
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
            q_to_p_eligible,forecast_eval_eligible,dependency_group_id,
            base_cohort_id,base_cohort_json);
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
        cohort={'instrument':'NAS100','q_relation':'native','proxy_transform':'direct'}
        runtime._conn.execute('INSERT INTO g1_dataset_membership VALUES(?,?,?,?,?,?,?)',
            (str(i),G1_DATASET_CONTRACT_VERSION,1,1,str(i),'native',json.dumps(cohort)))


def test_fit_readiness_is_not_a_fitted_model():
    stats={'raw_n':100,'effective_n':100,'positive_n':50,'negative_n':50,'unique_q_n':20}
    assert bounded._threshold_status(stats,'PLATT')['status']=='READY_TO_FIT'


@pytest.mark.parametrize('error_n',[0,1])
def test_materialized_g1d_keeps_periods_expiries_labels_and_errors(error_n):
    runtime=engine();seed(runtime)
    for _ in range(error_n):runtime._conn.execute('INSERT INTO g1c_contract_errors VALUES(?)',('Q_CONTRACT_MISMATCH',))
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


def test_mixed_q_semantics_do_not_manufacture_fit_readiness():
    runtime=engine();seed(runtime,80)
    cohort={'instrument':'NAS100','q_relation':'proxy','proxy_transform':'inverse'}
    runtime._conn.execute('UPDATE g1_dataset_membership SET base_cohort_id=?,base_cohort_json=? '
                          'WHERE CAST(observation_id AS INTEGER)>=40',('inverse',json.dumps(cohort)))
    status=bounded._light_g1c_status(runtime)
    assert status['q_eligible']==80
    assert status['fit_readiness']['platt']['ready'] is False
    assert status['shadow_model_fitting_allowed'] is False
    assert status['fit_readiness']['platt']['scope_n']==6
    assert status['q_semantic_pooling']=='separated_by_q_relation_and_proxy_transform'
    runtime._conn.close()


def test_summary_matches_authoritative_semantic_readiness_and_critical_types():
    runtime=engine();seed(runtime)
    runtime._conn.execute('INSERT INTO g1c_contract_errors VALUES(?)',('INSUFFICIENT_EVIDENCE',))
    runtime._conn.execute('INSERT INTO g1c_contract_errors VALUES(?)',('MODEL_SHA_MISMATCH',))
    rows=[]
    for record in runtime._conn.execute('SELECT p.*,g.base_cohort_id,g.base_cohort_json,'
                                        'g.dependency_group_id FROM passive_market_observations p '
                                        'JOIN g1_dataset_membership g USING(observation_id)'):
        row=dict(record)
        row['forecast']=json.loads(row['forecast_json'])
        row['outcome']=json.loads(row['outcome_json'])
        row['base_cohort']=json.loads(row['base_cohort_json'])
        row.update(raw_q=authoritative._g1b._q_up_probability(row),
                   outcome_y=authoritative._g1b._future_direction(row))
        rows.append(row)
    status=bounded._light_g1c_status(runtime)
    assert status['fit_readiness']==integrity._scope_fit_readiness(runtime,rows)
    assert status['contract_error_n']==2
    assert status['critical_contract_error_n']==1
    assert status['critical_contract_error_types']==sorted(integrity._CRITICAL_ERRORS)
    assert status['g1d_readiness']==authoritative._g1d_status(authoritative._stats(runtime,rows),1)
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
