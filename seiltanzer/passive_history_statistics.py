"""Additive passive diagnostics shared by full-history and retired-row scans.

Predicates preserve PassiveLearningEngine.status's literal SQL LIKE semantics.
They do not infer forecast validity, eligibility or model authority.
"""
SOURCES = {'hot': 'main.passive_market_observations', 'retired': 'temp.history_retired_sources'}
CONTRACT = 'passive-history-statistics-v1'


def history_totals(conn, *, source='hot'):
    table = SOURCES[source]
    statuses = {str(r[0]): int(r[1]) for r in conn.execute(
        f'SELECT resolution_status,COUNT(*) FROM {table} GROUP BY resolution_status')}
    versions = {str(r[0]): int(r[1]) for r in conn.execute(
        f'SELECT feature_contract_version,COUNT(*) FROM {table} GROUP BY feature_contract_version')}
    return dict(raw_n=sum(statuses.values()), resolution_counts=statuses, feature_version_counts=versions)


def passive_history_statistics(conn, *, current_feature_version, source='hot'):
    table = SOURCES[source]
    result = history_totals(conn, source=source)
    # Aggregate in one pass; each condition is the existing separate COUNT
    # predicate. JSON whitespace and NULL behavior are intentionally unchanged.
    predicates = {
        'legacy_f2_n': ("feature_contract_version LIKE '%f2%'", ()),
        'legacy_f3_n': ("feature_contract_version='passive-observation-f3-v1'", ()),
        'legacy_f31_n': ("feature_contract_version='passive-observation-f31-v1'", ()),
        'current_f32_n': ('feature_contract_version=?', (current_feature_version,)),
        'fixed_horizon_raw_n': ('feature_contract_version=? AND forecast_json LIKE ?',
                               (current_feature_version, '%"horizon_kind":"fixed_trading_time"%')),
        'option_native_raw_n': ('feature_contract_version=? AND forecast_json LIKE ?',
                               (current_feature_version, '%"horizon_kind":"option_native_expiry"%')),
        'terminal_q_eligible_n': ('forecast_json LIKE ?', ('%"probability_measure":"risk_neutral_Q_terminal"%',)),
        'terminal_q_resolved_n': ("resolution_status='resolved' AND forecast_json LIKE ?",
                                ('%"probability_measure":"risk_neutral_Q_terminal"%',)),
        'first_touch_clean_n': ('outcome_json LIKE ?', ('%"clean_label":true%',)),
        'first_touch_ambiguous_n': ('outcome_json LIKE ?', ('%"ambiguous_first_touch":true%',)),
        'first_touch_point_only_n': ('outcome_json LIKE ?', ('%"path_granularity":"point_only"%',)),
        'sigma_valid_n': ('forecast_json LIKE ? AND forecast_json NOT LIKE ?',
                          ('%"sigma_h_return":%', '%"sigma_h_return":null%')),
    }
    expressions = ','.join('SUM(CASE WHEN '+sql+' THEN 1 ELSE 0 END)' for sql, _ in predicates.values())
    args = tuple(value for _, values in predicates.values() for value in values)
    counts = conn.execute('SELECT '+expressions+' FROM '+table, args).fetchone()
    result.update((key, int(count or 0)) for key, count in zip(predicates, counts))
    result['sigma_missing_n'] = result['raw_n']-result['sigma_valid_n']
    return result
