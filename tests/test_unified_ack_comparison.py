from scripts.run_unified_edge_comparison import run_comparison


def test_actual_ack_times_are_reported_separately_without_profit_or_cost_inference():
    source = {"read_only": True, "exported_ts": 200., "reviews": [],
              "execution_ack_observations": [
                  {"decision_id": "one", "stage": "armed", "acknowledged_ts": 120., "acknowledgement_delay_sec": 20.},
                  {"decision_id": "one", "stage": "cancelled", "acknowledged_ts": 140., "acknowledgement_delay_sec": 40.},
                  {"decision_id": "future", "stage": "executed", "acknowledged_ts": 201., "acknowledgement_delay_sec": 1.}]}
    result = run_comparison(source)
    observed = result["actual_execution_observations"]
    assert observed["observation_n"] == 2
    assert observed["distinct_decision_n"] == 1
    assert observed["stages"] == {"armed": 1, "cancelled": 1}
    assert observed["mean_acknowledgement_delay_sec"] == 30.
    assert observed["manual_latency_cost_r"] is None
    assert observed["actual_broker_fill_time_verified_n"] == 0
    assert result["historical_profit_proven"] is False


def test_rapid_cancellation_uses_observed_arm_to_cancel_pair_not_ack_delay():
    source = {'read_only': True, 'exported_ts': 500., 'reviews': [],
              'execution_ack_observations': [
                  {'decision_id': 'rapid', 'stage': 'cancelled', 'acknowledged_ts': 160.},
                  {'decision_id': 'rapid', 'stage': 'armed', 'acknowledged_ts': 100.},
                  {'decision_id': 'slow', 'stage': 'armed', 'acknowledged_ts': 100.},
                  {'decision_id': 'slow', 'stage': 'cancelled', 'acknowledged_ts': 161.},
                  {'decision_id': 'missing-arm', 'stage': 'cancelled', 'acknowledged_ts': 200.},
                  {'decision_id': 'bad-order', 'stage': 'armed', 'acknowledged_ts': 200.},
                  {'decision_id': 'bad-order', 'stage': 'cancelled', 'acknowledged_ts': 150.}]}
    observed = run_comparison(source)['actual_execution_observations']
    assert observed.get('rapid_cancellation_threshold_sec') == 60.
    assert observed['armed_then_cancelled_pair_n'] == 2
    assert observed['rapid_cancellation_n'] == 1
    assert observed['rapid_cancellation_frequency'] == .5
    assert observed['cancellation_timing_unavailable_n'] == 2
    assert observed['mean_arm_to_cancel_sec'] == 60.5
    assert observed['acknowledgement_delay_available_n'] == 0
    assert observed['mean_acknowledgement_delay_sec'] is None
    assert observed['manual_latency_cost_r'] is None
    assert observed['actual_broker_fill_time_verified_n'] == 0


def test_ack_future_created_clock_and_impossible_delay_are_not_reported_as_actual_latency():
    source = {'read_only': True, 'exported_ts': 200., 'reviews': [],
              'execution_ack_observations': [
                  {'decision_id': 'future-created', 'stage': 'armed', 'decision_created_ts': 201.,
                   'acknowledged_ts': 150., 'acknowledgement_delay_sec': 1.},
                  {'decision_id': 'bad-delay', 'stage': 'armed', 'decision_created_ts': 100.,
                   'acknowledged_ts': 150., 'acknowledgement_delay_sec': 1000.},
                  {'decision_id': 'valid', 'stage': 'armed', 'decision_created_ts': 100.,
                   'acknowledged_ts': 160., 'acknowledgement_delay_sec': 60.}]}
    observed = run_comparison(source)['actual_execution_observations']
    assert observed['observation_n'] == 2
    assert observed['acknowledgement_delay_available_n'] == 1
    assert observed['mean_acknowledgement_delay_sec'] == 60.


def test_future_export_cutoff_does_not_admit_future_receipts():
    import time
    now = time.time()
    source = {'read_only': True, 'exported_ts': now + 1000., 'reviews': [],
              'execution_ack_observations': [
                  {'decision_id': 'future', 'stage': 'executed', 'acknowledged_ts': now + 500.}]}
    observed = run_comparison(source)['actual_execution_observations']
    assert observed['observation_n'] == 0
    assert observed['mean_acknowledgement_delay_sec'] is None
