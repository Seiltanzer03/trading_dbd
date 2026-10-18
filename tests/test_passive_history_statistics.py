"""Historical diagnostics preserve the existing literal SQL-match semantics."""
import sqlite3

from seiltanzer import passive_history_statistics as statistics


def test_literal_history_statistics_and_empty_subset_are_exact():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE passive_market_observations('
        'feature_contract_version TEXT,resolution_status TEXT,forecast_json TEXT,outcome_json TEXT)')
    conn.executemany('INSERT INTO passive_market_observations VALUES(?,?,?,?)', [
        ('old-f2', 'insufficient_future_data', '{"sigma_h_return":null}', None),
        ('current', 'insufficient_future_data', '{"horizon_kind":"fixed_trading_time","sigma_h_return":0.2}',
         '{"clean_label":true,"ambiguous_first_touch":true}'),
        ('current', 'resolved', '{"horizon_kind":"option_native_expiry","probability_measure":"risk_neutral_Q_terminal"}',
         '{"path_granularity":"point_only"}'),
        ('passive-observation-f3-v1', 'pending', '{}', None),
        ('passive-observation-f31-v1', 'insufficient_future_data', '{}', None),
    ])
    result = statistics.passive_history_statistics(conn, current_feature_version='current')
    assert result == dict(raw_n=5, resolution_counts={'insufficient_future_data':3,'pending':1,'resolved':1},
        feature_version_counts={'current':2,'old-f2':1,'passive-observation-f3-v1':1,'passive-observation-f31-v1':1},
        legacy_f2_n=1, legacy_f3_n=1, legacy_f31_n=1, current_f32_n=2,
        fixed_horizon_raw_n=1, option_native_raw_n=1, terminal_q_eligible_n=1, terminal_q_resolved_n=1,
        first_touch_clean_n=1, first_touch_ambiguous_n=1, first_touch_point_only_n=1,
        sigma_valid_n=1, sigma_missing_n=4)
    conn.execute('CREATE TEMP VIEW history_retired_sources AS SELECT * FROM passive_market_observations WHERE 0')
    empty = statistics.passive_history_statistics(conn, current_feature_version='current', source='retired')
    assert empty['raw_n'] == 0
    assert empty['resolution_counts'] == empty['feature_version_counts'] == {}
    assert all(value == 0 for key,value in empty.items() if key not in ('resolution_counts','feature_version_counts'))
    conn.close()
