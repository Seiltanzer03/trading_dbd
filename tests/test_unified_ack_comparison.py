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
