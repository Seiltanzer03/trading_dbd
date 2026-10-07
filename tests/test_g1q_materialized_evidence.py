"""Bounded Q telemetry describes actual background evidence, not fitting authority."""
from test_g1c_materialized_readiness import engine, seed
from seiltanzer import research_scalability as bounded


def captures(runtime, n):
    for i in range(n):
        runtime._conn.execute('INSERT INTO g1_q_capture_attempts VALUES(?,?,?,?,?,?,?)',
                              ('background_collector',1,1790985600+i,str(i),None,'self','TEST'))


def test_different_anchors_with_overlapping_native_expiry_are_not_independent():
    runtime=engine();seed(runtime,40);captures(runtime,40)
    runtime._conn.execute('UPDATE passive_market_observations SET captured_ts=1790985600,'
                          'target_ts=1791072000')
    status=bounded._light_q_status(runtime)
    assert status['q_to_p_eligible_n']==40
    assert status['unique_q_anchor_n']==40
    assert status['effective_q_n']==1
    assert status['evidence_status']=='INSUFFICIENT'
    runtime._conn.close()


def test_only_traceable_clean_background_evidence_counts():
    runtime=engine();seed(runtime,4);captures(runtime,3)
    runtime._conn.execute("UPDATE g1_dataset_membership SET forecast_eval_eligible=0 WHERE observation_id='1'")
    runtime._conn.execute('INSERT INTO g1_contract_errors VALUES(?,?,?)',
                          (bounded.G1_DATASET_CONTRACT_VERSION,'2','SOURCE_MUTATED'))
    status=bounded._light_q_status(runtime)
    assert status['q_to_p_eligible_n']==1
    assert status['effective_q_n']==1
    runtime._conn.close()


def test_evidence_maturity_needs_elapsed_days_as_well_as_sample_count():
    runtime=engine();seed(runtime,120);captures(runtime,120)
    runtime._conn.execute('UPDATE passive_market_observations '
                          'SET captured_ts=1790985600+CAST(observation_id AS INTEGER)*120,'
                          'target_ts=1790985660+CAST(observation_id AS INTEGER)*120')
    status=bounded._light_q_status(runtime)
    assert status['effective_q_n']==120
    assert status['evidence_status']=='EARLY'
    runtime._conn.close()


def test_repeat_attempt_does_not_create_an_extra_resolved_or_unresolved_observation():
    runtime=engine();seed(runtime,1);captures(runtime,1);captures(runtime,1)
    status=bounded._light_q_status(runtime)
    assert status['successful_q_capture_n']==2
    assert status['resolved_q_observation_n']==1
    assert status['unresolved_q_capture_n']==0
    assert status['q_to_p_eligible_n']==1
    runtime._conn.close()
