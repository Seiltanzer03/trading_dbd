"""Storage rollups consume the owning telemetry formula without loading all rows."""
from seiltanzer import measurement_path_runtime as measurement
from test_history_rejected_source_index import rejected_source


def test_streamed_counter_preserves_pinned_ifd_diagnostics(rejected_source):
    engine, _ = rejected_source
    expected = dict(horizon_contract_error_n=1, q_semantic_contract_error_n=0,
        proxy_mapping_error_n=0, path_contract_error_n=0, time_contract_error_n=0,
        terminal_pit_valid_n=0, first_touch_clean_n=0, inverse_proxy_observation_n=0,
        runtime_current_n=1, runtime_background_origin_n=1, pristine_candidate_n=1,
        background_collector_origin_n=1, manual_origin_n=0, test_origin_n=0, replay_origin_n=0)
    seen = []
    def rows():
        for row in engine._conn.execute('SELECT * FROM passive_market_observations'):
            seen.append(row['observation_id'])
            yield dict(row)
    assert measurement.runtime_counters_for_rows(rows()) == expected
    assert seen == ['old-ifd']
    assert measurement._runtime_counters(engine) == expected


def test_streamed_counter_keeps_origin_and_bad_json_accounting():
    rows = iter([{'observation_origin':'manual', 'forecast_json':'invalid'},
                 {'observation_origin':'test', 'forecast_json':'{}'}])
    result = measurement.runtime_counters_for_rows(rows)
    assert result['manual_origin_n'] == 1
    assert result['test_origin_n'] == 1
    assert result['runtime_current_n'] == 0
