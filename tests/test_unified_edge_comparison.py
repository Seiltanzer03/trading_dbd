from copy import deepcopy
import hashlib
import json
import sqlite3
import time

import pytest

from scripts.export_unified_edge_reviews import export_reviews, remote_program, select_reviews
from scripts.run_unified_edge_comparison import observed_replay, run_comparison, summarize
from test_extended_policy_evaluation import _snapshot
from test_unified_edge_ensemble import snapshot as voting_snapshot


def frozen():
    value = _snapshot()
    value.update(captured_ts=1_900_000_000., trade_id=4, strategy={'instrument': 'NAS100'})
    value['position_state'] = {'remaining_position_fraction': .8, 'realized_r_weighted': .2}
    # No path generation needed for the voting unit tests.
    value['policy_manager']['inputs']['option_available'] = False
    value['policy_manager'].update({key: deepcopy(item) for key, item in voting_snapshot()['policy_manager'].items()})
    value['llm_shadow_decision'] = {'status': 'ok', 'policy': 'CLOSE_10', 'quant_policy': 'CLOSE_25'}
    return value


def record(snapshot=None, points=None):
    value = snapshot or frozen()
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    captured = value['captured_ts']
    return {'review_id': 'review-fixture', 'trade_id': value['trade_id'], 'captured_ts': captured,
            'snapshot_json': raw, 'snapshot_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'production_policy': 'CLOSE_25', 'path_truncated': False,
            'path_points': points or [{'ts': captured, 'r': 1., 'price': 110.},
                {'ts': captured + 300, 'r': .5, 'price': 105.},
                {'ts': captured + 14400, 'r': -.8, 'price': 92.}],
            'stored_replay': {'resolved_ts': captured + 14401, 'resolution_kind': 'manual_close',
                              'replay_json': '{}'}}


def choice(policy='HOLD', **parameters):
    return {'policy': policy, 'candidate_id': policy, 'parameters': parameters}


def with_rollover(value, offsets=(50., 120.), same_timestamp_rule='fills_before_rollover'):
    captured = value['captured_ts']
    value['broker_rollover_schedule'] = {
        'source_verified': True, 'source_id': 'fixture-broker-frozen-quote',
        'trade_id': value['trade_id'],
        'instrument': 'NAS100', 'observed_ts': captured - 10., 'received_ts': captured - 5.,
        'quality': 1., 'max_age_sec': 60., 'currency': 'USD', 'risk_currency_per_unit': 10.,
        'charge_basis': 'per_unit_of_remaining_position',
        'coverage_start_epoch': captured,
        'coverage_end_epoch': captured + value['policy_manager']['inputs']['horizon_minutes'] * 60.,
        'same_timestamp_rule': same_timestamp_rule, 'included_in_base_costs': False,
        'events': [{'scheduled_epoch': captured + offset, 'charge_currency_per_unit': 1.}
                   for offset in offsets]}
    return value


def test_observed_replay_rejects_rollover_quote_from_another_trade():
    value = with_rollover(frozen())
    value['broker_rollover_schedule']['trade_id'] += 1
    replay = observed_replay(value, record(value), choice('HOLD'))
    assert replay['available'] is False
    assert replay['reason'] == 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'


def test_actual_snapshot_comparison_is_frozen_and_ledger_absence_is_not_zero():
    source = {'read_only': True, 'reviews': [record()]}
    original = deepcopy(source)
    report = run_comparison(source, expected_sha='a' * 40)
    assert source == original
    assert report['review_n'] == 1
    assert report['paid_llm_calls'] == report['production_or_database_writes'] == 0
    review = report['reviews'][0]
    assert set(review['schemes']) == {'balanced', 'llm20', 'quant100', 'legacy_control'}
    assert review['legacy_llm_quant_anchoring'] is True
    assert review['schemes']['legacy_control']['selection']['selected_policy'] == 'CLOSE_25'
    assert review['schemes']['quant100']['observed_path']['available'] is True
    assert review['observed_hold_control']['available'] is True
    for summary in report['summary'].values():
        assert summary['historical_economic_completeness']['ledger_profit_net_r'] is None
        assert summary['historical_economic_completeness']['available'] is False
        assert summary['observed_path_replay']['paired_verified_broker_rollover_quote_n'] == 0
        assert summary['observed_path_replay']['paired_broker_rollover_quote_unavailable_n'] == 1
    assert report['historical_profit_proven'] is False
    json.dumps(report, allow_nan=False)


def test_tampered_and_known_future_snapshots_are_rejected_without_pricing():
    broken = record()
    broken['snapshot_json'] += ' '
    future = frozen()
    future['knownfuture_observation'] = {'observed_ts': future['captured_ts'] + 120}
    report = run_comparison({'read_only': True, 'reviews': [broken, record(future)]})
    assert report['review_n'] == 0
    assert 'SHA256' in report['rejected'][0]['reason']
    assert 'post-capture' in report['rejected'][1]['reason']
    assert report['summary']['balanced']['observed_path_replay']['mean_net_r_on_remaining'] is None


def test_held_out_path_never_enters_vote_snapshot(monkeypatch):
    from scripts import run_unified_edge_comparison as runner
    original = runner.build_unified_ensemble
    seen = []
    def vote(snapshot, llm):
        seen.append(deepcopy(snapshot))
        assert 'path_points' not in snapshot and 'stored_replay' not in snapshot
        return original(snapshot, llm)
    monkeypatch.setattr(runner, 'build_unified_ensemble', vote)
    source = record()
    report = run_comparison({'read_only': True, 'reviews': [source]})
    assert report['review_n'] == 1
    assert seen == [json.loads(source['snapshot_json'])]


def test_llm_scores_do_not_retroactively_establish_independent_input_contract():
    value = frozen()
    value['captured_ts'] = time.time() - 100.
    value['llm_shadow_decision']['policy_scores'] = {'HOLD': .9, 'EXIT': .1}
    review = run_comparison({'read_only': True, 'reviews': [record(value)]})['reviews'][0]
    assert review['legacy_llm_quant_anchoring'] is True
    assert review['llm_independent_input_contract'] is False
    value['llm_shadow_decision']['input_contract'] = {
        'version': 'independent-llm-transport-v1', 'quant_selection_masked': False}
    report = run_comparison({'read_only': True, 'reviews': [record(value)]})
    assert report['review_n'] == 0
    assert 'independent LLM frozen input integrity failure' in report['rejected'][0]['reason']
    shadow = value['llm_shadow_decision']
    shadow['input_contract']['quant_selection_masked'] = True
    # Marker flags without hashed actual input/output still prove nothing.
    shadow['selection_masked'] = True
    report = run_comparison({'read_only': True, 'reviews': [record(value)]})
    assert report['review_n'] == 0
    assert 'independent LLM frozen input integrity failure' in report['rejected'][0]['reason']
    frozen_input = json.dumps({'captured_ts': value['captured_ts']})
    response = json.dumps({'policy_scores': shadow['policy_scores']})
    shadow['input_captured_ts'] = value['captured_ts']
    shadow['input_contract'].update(input_captured_ts=value['captured_ts'], frozen_input_json=frozen_input,
                                   frozen_input_sha256=hashlib.sha256(frozen_input.encode()).hexdigest(),
                                   provider_request_json=json.dumps({'messages': [{'role': 'user', 'content': frozen_input}]}))
    # Valid input integrity without an actual response is not an independent
    # stored provider experiment either.
    review = run_comparison({'read_only': True, 'reviews': [record(value)]})['reviews'][0]
    assert review['llm_independent_input_contract'] is False
    shadow.update(provider_response_json=response, provider_response_sha256=hashlib.sha256(response.encode()).hexdigest(),
                  provider_response_received_ts=value['captured_ts'] + 4.)
    # Hashed provider JSON without a finite causal receipt clock is not an
    # actual completed independent response at this decision capture.
    for bad_receipt in (None, value['captured_ts'] - 1.):
        shadow['provider_response_received_ts'] = bad_receipt
        review = run_comparison({'read_only': True, 'reviews': [record(value)]})['reviews'][0]
        assert review['llm_independent_input_contract'] is False
        assert review['legacy_llm_quant_anchoring'] is True
    shadow['provider_response_received_ts'] = value['captured_ts'] + 4.
    review = run_comparison({'read_only': True, 'reviews': [record(value)]})['reviews'][0]
    assert review['legacy_llm_quant_anchoring'] is False
    assert review['llm_independent_input_contract'] is True


@pytest.mark.parametrize('policy,parameters', [
    ('MOVE_TO_BE', {'stop_price': 100.}),
    ('TIGHTEN_STOP', {'stop_price': 105.}),
    ('TRAIL_GAMMA_FLIP', {'stop_price': 105.}),
    ('REDUCE_TAKE', {'take_price': 125.}),
    ('EXTEND_TAKE', {'take_price': 135.}),
    ('SCALE_OUT_ON_SPIKE', {'trigger_price': 112., 'close_fraction': .25}),
])
def test_all_frozen_extended_geometries_use_observed_execution(policy, parameters):
    value = frozen()
    value['policy_manager']['inputs'].update(rungs=[], be_after=10.)
    replay = observed_replay(value, record(value), choice(policy, **parameters))
    assert replay['available'] is True
    assert replay['ledger_settled_profit_available'] is False
    assert replay['execution_cost_r'] == .01
    if policy in ('TIGHTEN_STOP', 'TRAIL_GAMMA_FLIP'):
        assert replay['net_r_on_remaining'] == pytest.approx(.49)


def test_time_stop_uses_actual_irregular_timestamps_and_does_not_leak_deadline():
    value = frozen()
    captured = value['captured_ts']
    points = [{'ts': captured, 'r': 1.}, {'ts': captured + 10, 'r': 1.1},
              {'ts': captured + 600, 'r': -.2}]
    replay = observed_replay(value, record(value, points), choice('TIME_STOP', deadline_ts=captured + 60))
    expected = 1.1 + (-.2 - 1.1) * 50 / 590 - .01
    assert replay['net_r_on_remaining'] == pytest.approx(expected)
    assert replay['maximum_observation_gap_sec'] == 590


def test_missing_costs_points_exposure_and_extended_continuation_are_explicit():
    value = frozen()
    value['policy_manager']['execution_cost_model'] = {}
    assert observed_replay(value, record(value), choice())['reason'] == 'EXPLICIT_EXECUTION_COSTS_UNAVAILABLE'
    value = frozen()
    source = record(value)
    source['path_points'] = source['path_points'][1:]
    assert observed_replay(value, source, choice())['available'] is False
    source = record(value)
    source['path_truncated'] = True
    assert observed_replay(value, source, choice())['available'] is True
    value.pop('position_state')
    assert observed_replay(value, record(value), choice())['net_r_on_original_position'] is None
    source = record(value)
    source['path_points'] = source['path_points'][:2]
    assert observed_replay(value, source, choice('EXTEND_TAKE', take_price=135.))['reason'] == 'EXTENDED_COUNTERFACTUAL_CONTINUATION_UNOBSERVED'


def test_truncated_prefix_only_settles_actual_terminal_actions_not_prefix_mark_to_market():
    value = frozen()
    value['policy_manager']['inputs'].update(horizon_minutes=7200., rungs=[], be_after=10.)
    captured = value['captured_ts']
    source = record(value, [{'ts': captured, 'r': 1.}, {'ts': captured + 300, 'r': .4}])
    source['path_truncated'] = True
    # A later stored resolution does not resolve a missing continuation.
    for policy in ('HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50'):
        assert observed_replay(value, source, choice(policy))['reason'] == 'TRUNCATED_PREFIX_WITHOUT_TERMINAL_RESOLUTION'
    tightened = observed_replay(value, source, choice('TIGHTEN_STOP', stop_price=105.))
    assert tightened['available'] is True
    assert tightened['exit_reason'] == 'stop'
    assert tightened['net_r_on_remaining'] == pytest.approx(.49)
    assert tightened['terminal_resolution_within_observed_prefix'] is True
    assert tightened['full_forecast_horizon_observed'] is False
    immediate = observed_replay(value, source, choice('EXIT'))
    assert immediate['net_r_on_remaining'] == pytest.approx(.99)
    assert immediate['endpoint_semantics'] == 'immediate_capture_exit'
    source['stored_replay'] = None
    assert observed_replay(value, source, choice('TIGHTEN_STOP', stop_price=105.))['available'] is True
    assert observed_replay(value, source, choice('TIME_STOP', deadline_ts=captured + 60))['available'] is True
    assert observed_replay(value, source, choice('TIME_STOP', deadline_ts=captured + 600))['reason'] == 'TIME_STOP_DEADLINE_BEYOND_OBSERVED_PATH'


def test_immediate_exit_requires_measured_capture_but_not_future_continuation():
    value = frozen()
    source = record(value, [{'ts': value['captured_ts'], 'r': 1.}])
    source.update(path_truncated=True, stored_replay=None)
    replay = observed_replay(value, source, choice('EXIT'))
    assert replay['available'] is True
    assert replay['net_r_on_remaining'] == pytest.approx(.99)
    assert replay['maximum_observation_gap_sec'] == 0.
    assert observed_replay(value, source, choice('CLOSE_50'))['available'] is False
    source['path_points'] = []
    assert observed_replay(value, source, choice('EXIT'))['available'] is False


def test_truncated_observed_sequence_preserves_be_arm_then_reversal():
    value = frozen()
    value['policy_manager']['inputs'].update(horizon_minutes=7200., rungs=[], be_after=1.5)
    captured = value['captured_ts']
    source = record(value, [{'ts': captured, 'r': 1.}, {'ts': captured + 10, 'r': 1.6},
                            {'ts': captured + 20, 'r': -.2}, {'ts': captured + 30, 'r': 2.}])
    source.update(path_truncated=True, stored_replay=None)
    replay = observed_replay(value, source, choice())
    assert replay['available'] is True
    assert replay['exit_reason'] == 'breakeven'
    assert replay['net_r_on_remaining'] == pytest.approx(-.01)


def test_time_deadline_unobserved_but_barrier_resolves_in_observed_prefix():
    value = frozen()
    value['policy_manager']['inputs'].update(horizon_minutes=7200., rungs=[], be_after=10.)
    captured = value['captured_ts']
    source = record(value, [{'ts': captured, 'r': 1.}, {'ts': captured + 10, 'r': -1.2}])
    source.update(path_truncated=True, stored_replay=None)
    replay = observed_replay(value, source, choice('TIME_STOP', deadline_ts=captured + 600))
    assert replay['available'] is True
    assert replay['exit_reason'] == 'stop'


def test_frozen_broker_rollover_follows_actual_time_deadline_not_point_indices():
    value = with_rollover(frozen())
    value['policy_manager']['inputs'].update(rungs=[], be_after=10.)
    captured = value['captured_ts']
    source = record(value, [{'ts': captured, 'r': 1.}, {'ts': captured + 10, 'r': 1.1},
                            {'ts': captured + 600, 'r': -.2}])
    source['path_truncated'] = True
    replay = observed_replay(value, source, choice('TIME_STOP', deadline_ts=captured + 60))
    assert replay['broker_rollover_cost_r'] == pytest.approx(.1)
    expected_gross = 1.1 + (-.2 - 1.1) * 50 / 590
    assert replay['net_r_on_remaining'] == pytest.approx(expected_gross - .01 - .1)
    assert replay['net_cost_scope'] == 'FROZEN_EXECUTION_COSTS_AND_VERIFIED_BROKER_ROLLOVER'
    assert observed_replay(value, source, choice('EXIT'))['broker_rollover_cost_r'] == 0.


def test_broker_rollover_scales_partial_immediate_close_and_rejects_invalid_quote():
    value = with_rollover(frozen())
    value['policy_manager']['inputs'].update(rungs=[], be_after=10.)
    replay = observed_replay(value, record(value), choice('CLOSE_50'))
    assert replay['broker_rollover_cost_r'] == pytest.approx(.1)
    assert replay['execution_cost_r'] == pytest.approx(.11)
    value['broker_rollover_schedule']['received_ts'] = value['captured_ts'] + 1.
    assert observed_replay(value, record(value), choice())['reason'] == 'BROKER_ROLLOVER_SOURCE_IDENTITY_OR_CLOCK_INVALID'
    value.pop('broker_rollover_schedule')
    replay = observed_replay(value, record(value), choice())
    assert replay['broker_rollover_cost_r'] is None
    assert replay['broker_rollover_audit']['missing_quote_is_zero_carry'] is False
    assert replay['net_cost_scope'] == 'FROZEN_EXECUTION_COSTS_ONLY_ROLLOVER_UNAVAILABLE'


def test_observed_and_common_bank_use_identical_rollover_economics_on_same_path():
    from seiltanzer.unified_candidate_economics import price_unified_candidates
    value = with_rollover(frozen())
    manager = value['policy_manager']
    manager['inputs'].update(option_available=True, rungs=[], be_after=10.)
    captured = value['captured_ts']
    horizon = manager['inputs']['horizon_minutes']
    path = [1., 1.2, 1.4]
    manager['unified_scenario_bank'] = {
        'r_paths': [path], 'weights': [1.], 'state_space': 'R_multiple_of_initial_risk',
        'horizon_minutes': horizon, 'captured_ts': captured,
        'distribution_kind': 'weighted_empirical', 'measure': 'historical_conditional_P',
        'source': 'unit-test-identical-frozen-path'}
    source = record(value, [{'ts': captured + i * horizon * 30., 'r': r} for i, r in enumerate(path)])
    candidates = [choice(policy) for policy in ('HOLD', 'CLOSE_50', 'EXIT')]
    candidates.append(choice('TIME_STOP', deadline_ts=captured + 60.))
    model = price_unified_candidates(value, candidates)
    assert model['available'] is True
    for candidate in candidates:
        actual = observed_replay(value, source, candidate)
        scenario = model['candidates'][candidate['candidate_id']]
        assert actual['available'] is True
        assert actual['net_r_on_remaining'] == pytest.approx(scenario['expected_net_r'])
        assert actual['broker_rollover_cost_r'] == pytest.approx(scenario['expected_rollover_cost_r'])
        assert actual['execution_cost_r'] == pytest.approx(scenario['execution_cost_r'])


def test_prior_gross_with_unknown_ledger_costs_is_never_total_net():
    value = frozen()
    value['position_state']['realized_costs_status'] = 'UNAVAILABLE'
    source = record(value)
    source['stored_replay']['replay_json'] = json.dumps({'policies': {'HOLD': {'net_realized_r': -.448}}})
    replay = observed_replay(value, source, choice())
    assert replay['net_r_on_original_position'] is None
    assert replay['original_position_net_basis'] == 'UNAVAILABLE_PRIOR_NET_COSTS_OR_EXPOSURE'
    assert replay['prior_gross_plus_future_modeled_net_r'] == pytest.approx(-.448)
    assert replay['prior_gross_plus_future_modeled_net_difference_vs_stored_r'] == pytest.approx(0.)
    assert replay['stored_base_replay_basis'] == 'legacy_prior_gross_plus_future_modeled_net_not_verified_total_net'
    assert 'stored_base_replay_net_r' not in replay and 'stored_replay_difference_r' not in replay
    # Even a numeric fee estimate cannot override an unavailable ledger status.
    value['position_state']['realized_costs_r_weighted'] = .03
    assert observed_replay(value, record(value), choice())['net_r_on_original_position'] is None
    value['position_state']['realized_costs_status'] = 'AVAILABLE'
    replay = observed_replay(value, record(value), choice())
    assert replay['net_r_on_original_position'] == pytest.approx(-.478)
    assert replay['original_position_net_basis'] == 'prior_net_plus_future_modeled_net'
    assert replay['ledger_settled_profit_available'] is False


def test_no_prior_fills_require_explicit_not_applicable_cost_status():
    value = frozen()
    value['position_state'] = {'remaining_position_fraction': 1., 'realized_r_weighted': 0.}
    assert observed_replay(value, record(value), choice())['net_r_on_original_position'] is None
    value['position_state']['realized_costs_status'] = 'NOT_APPLICABLE'
    replay = observed_replay(value, record(value), choice())
    assert replay['net_r_on_original_position'] == replay['net_r_on_remaining']


def test_sampling_is_bounded_and_includes_instrument_and_time_spread():
    metadata = [{'review_id': f'{instrument}-{ts}', 'instrument': instrument, 'captured_ts': ts}
                for instrument in ('NAS100', 'XAUUSD', 'SP500') for ts in range(40)]
    selected = select_reviews(metadata, 9)
    assert len(selected) == 9
    for instrument in ('NAS100', 'XAUUSD', 'SP500'):
        assert {r['captured_ts'] for r in selected if r['instrument'] == instrument} == {0, 19, 39}


def test_sqlite_export_reads_only_and_separates_outcomes():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE decision_snapshots(review_id TEXT PRIMARY KEY,trade_id INTEGER,captured_ts REAL,snapshot_json TEXT,snapshot_sha256 TEXT,production_policy TEXT)')
    c.execute('CREATE TABLE decision_path_points(review_id TEXT,ts REAL,price REAL,r REAL,PRIMARY KEY(review_id,ts))')
    c.execute('CREATE TABLE decision_replays(review_id TEXT PRIMARY KEY,resolved_ts REAL,resolution_kind TEXT,replay_json TEXT)')
    value = record()
    c.execute('INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?)', tuple(value[key] for key in ('review_id', 'trade_id', 'captured_ts', 'snapshot_json', 'snapshot_sha256', 'production_policy')))
    c.executemany('INSERT INTO decision_path_points VALUES(?,?,?,?)', [(value['review_id'], p['ts'], p.get('price'), p['r']) for p in value['path_points']])
    c.execute('PRAGMA query_only=ON')
    before = c.total_changes
    report = export_reviews(c, 1)
    assert c.total_changes == before
    assert report['reviews'][0]['snapshot_json'] == value['snapshot_json']
    assert len(report['reviews'][0]['path_points']) == 3
    assert report['outcomes_separate_from_decision_inputs'] is True
    with pytest.raises(ValueError):
        export_reviews(c, 33)
    compile(remote_program(32), '<remote-stdlib-export>', 'exec')
    assert '?mode=ro' in remote_program(32)


def test_same_complete_cohort_for_every_scheme():
    report = run_comparison({'read_only': True, 'reviews': [record()]})
    reviews = report['reviews']
    reviews[0]['schemes']['llm20']['observed_path'] = {'available': False}
    assert all(row['observed_path_replay']['paired_review_n'] == 0 for row in summarize(reviews).values())


def test_legacy_is_in_summary_and_missing_legacy_excludes_identical_core_cohort():
    reviews = run_comparison({'read_only': True, 'reviews': [record()]})['reviews']
    summary = summarize(reviews)
    assert 'legacy_control' in summary
    assert summary['legacy_control']['observed_path_replay']['mean_net_r_on_remaining'] == pytest.approx(-.36)
    reviews[0]['schemes'].pop('legacy_control')
    summary = summarize(reviews)
    for scheme in ('balanced', 'llm20', 'quant100', 'legacy_control'):
        assert summary[scheme]['observed_path_replay']['paired_review_n'] == 0
        assert summary[scheme]['observed_path_replay']['excluded_review_n'] == 1
    assert summary['balanced']['model_scenarios']['review_n'] == 1
    assert summary['legacy_control']['model_scenarios']['review_n'] == 0


def test_ablation_replays_its_own_choice_and_reports_separate_denominator():
    reviews = run_comparison({'read_only': True, 'reviews': [record()]})['reviews']
    ablations = reviews[0].get('ablations', {})
    assert 'without_quantitative_base' in ablations
    row = ablations['without_quantitative_base']
    assert row['selection']['selected_policy'] == 'CLOSE_10'
    assert row['observed_path']['net_r_on_remaining'] == pytest.approx(-.63)
    summary = summarize(reviews)
    assert summary['without_quantitative_base']['observed_path_replay']['mean_paired_delta_vs_quant100_r'] == pytest.approx(-.27)
    row['observed_path'] = {'available': False}
    summary = summarize(reviews)
    assert summary['without_quantitative_base']['observed_path_replay']['paired_review_n'] == 0
    assert summary['balanced']['observed_path_replay']['paired_review_n'] == 1


def test_selected_action_distribution_includes_hold_and_unavailable_denominator():
    first, second = record(), record()
    first['production_policy'], second['production_policy'] = 'HOLD', None
    report = run_comparison({'read_only': True, 'reviews': [first, second]})
    distribution = report['summary'].get('legacy_control', {}).get('selected_action_distribution', {})
    assert distribution.get('counts', {}).get('HOLD') == 1
    assert distribution['selected_review_n'] == 1
    assert distribution['unavailable_review_n'] == 1
    assert distribution['frequencies']['HOLD'] == 1.


def test_grouped_report_labels_missing_regime_and_only_actual_signal_families():
    report = run_comparison({'read_only': True, 'reviews': [record()]})
    groups = report.get('grouped_summary', {})
    assert set(groups) == {'instrument', 'regime', 'horizon_minutes', 'family'}
    assert groups['instrument']['NAS100']['review_n'] == 1
    assert groups['regime']['UNAVAILABLE']['review_n'] == 1
    assert groups['horizon_minutes']['240']['review_n'] == 1
    assert groups['family']['price_path']['review_n'] == 1
    assert 'macro' not in groups['family']  # Adapter presence is not a signal.
    assert groups['family']['price_path']['summary']['legacy_control']['observed_path_replay']['paired_review_n'] == 1


def test_overlapping_review_episodes_never_form_portfolio_drawdown():
    first, second = record(), record()
    second.update(review_id='review-later', production_policy='EXIT')
    report = run_comparison({'read_only': True, 'reviews': [first, second]})
    risk = report['summary']['balanced']['observed_path_replay']
    assert risk.get('portfolio_max_drawdown_r', 'missing') is None
    assert risk['portfolio_drawdown_available'] is False
    assert risk['descriptive_worst_episode_net_r_on_remaining'] == pytest.approx(-.36)
    assert risk['paired_review_n'] == 2 and risk['paired_distinct_trade_n'] == 1


def test_chronological_policy_reversal_counts_decisions_without_inferring_execution():
    sources = []
    for i, policy in enumerate(('HOLD', 'CLOSE_25', 'HOLD')):
        value = frozen()
        value['captured_ts'] += i * 30.
        row = record(value)
        row.update(review_id=f'review-{i}', production_policy=policy)
        sources.append(row)
    report = run_comparison({'read_only': True, 'reviews': list(reversed(sources))}, materiality_r=.5)
    changes = report.get('review_transitions', {}).get('legacy_control', {})
    assert changes.get('adjacent_review_pair_n') == 2
    assert changes['decision_change_n'] == changes['policy_change_n'] == 2
    assert changes['policy_reversal_n'] == 1
    assert changes['policy_reversal_opportunity_n'] == 1
    assert changes['executions_inferred'] is False
    metrics = report['summary']['legacy_control']['observed_path_replay']
    assert metrics['materiality_threshold_r'] == .5
    assert metrics['economically_immaterial_intervention_n_vs_hold'] == 1
    assert report['report_parameters']['threshold_optimality_claim'] is False


def test_nonzero_rollover_is_separate_from_execution_subtotal_and_delta():
    value = with_rollover(frozen())
    costs = value['policy_manager']['execution_cost_model']
    costs.update(assumed=False, components={channel: {'components_r': {
        name: .0025 for name in ('commission', 'spread', 'slippage', 'manual_latency')}}
        for channel in ('immediate', 'deferred')})
    report = run_comparison({'read_only': True, 'reviews': [record(value)]})
    summary = report['summary']['legacy_control']
    breakdown = summary['cost_breakdown']
    assert breakdown['frozen_execution_total']['mean_r'] == pytest.approx(.01)
    assert sum(breakdown[name]['mean_r'] for name in
               ('commission', 'spread', 'slippage', 'manual_latency')) == pytest.approx(.01)
    assert breakdown['broker_rollover']['mean_r'] == pytest.approx(.15)
    assert breakdown['frozen_execution_plus_rollover_total']['mean_r'] == pytest.approx(.16)
    path = report['reviews'][0]['schemes']['legacy_control']['observed_path']
    assert path['execution_cost_r'] == pytest.approx(.16)  # compatibility total
    assert path['frozen_execution_cost_r'] == pytest.approx(.01)
    replay = summary['observed_path_replay']
    assert replay['mean_additional_frozen_execution_cost_vs_hold_r'] == pytest.approx(0.)
    assert replay['mean_additional_broker_rollover_cost_vs_hold_r'] == pytest.approx(-.05)
    assert replay['mean_additional_frozen_execution_plus_rollover_cost_vs_hold_r'] == pytest.approx(-.05)


def test_unknown_rollover_keeps_execution_subtotal_but_not_combined_cost_or_delta():
    report = run_comparison({'read_only': True, 'reviews': [record()]})
    summary = report['summary']['legacy_control']
    assert summary['cost_breakdown']['frozen_execution_total']['mean_r'] == pytest.approx(.01)
    combined = summary['cost_breakdown']['frozen_execution_plus_rollover_total']
    assert combined['mean_r'] is None
    assert combined['unavailable_review_n'] == 1
    path = report['reviews'][0]['schemes']['legacy_control']['observed_path']
    assert path['frozen_execution_cost_r'] == pytest.approx(.01)
    replay = summary['observed_path_replay']
    assert replay['mean_additional_frozen_execution_cost_vs_hold_r'] == pytest.approx(0.)
    assert replay['mean_additional_broker_rollover_cost_vs_hold_r'] is None
    assert replay['mean_additional_frozen_execution_plus_rollover_cost_vs_hold_r'] is None
    assert replay['additional_rollover_cost_available_intervention_n'] == 0
    assert replay['additional_combined_cost_available_intervention_n'] == 0


@pytest.mark.parametrize('bad_id', [123, None, '', [], {}])
def test_malformed_review_identity_is_rejected_without_losing_tied_valid_review(bad_id):
    valid = record()
    bad = record()
    bad['review_id'] = bad_id
    report = run_comparison({'read_only': True, 'reviews': [valid, bad]})
    assert report['review_n'] == 1
    assert len(report['rejected']) == 1
    assert 'REVIEW_ID' in report['rejected'][0]['reason']
    assert report['review_transitions']['legacy_control']['adjacent_review_pair_n'] == 0


def test_cost_breakdown_distinguishes_frozen_total_from_unknown_components():
    report = run_comparison({'read_only': True, 'reviews': [record()]})
    costs = report['summary']['balanced'].get('cost_breakdown', {})
    assert costs.get('frozen_execution_total', {}).get('mean_r') == .01
    assert costs['frozen_execution_total']['available_review_n'] == 1
    for component in ('commission', 'spread', 'slippage', 'manual_latency', 'broker_rollover'):
        assert costs[component]['mean_r'] is None
        assert costs[component]['available_review_n'] == 0
    assert costs['settled_actual_total_r'] is None


def test_tied_transition_chronology_never_uses_mixed_identity_as_tiebreaker():
    from scripts.run_unified_edge_comparison import review_transitions
    rows = run_comparison({'read_only': True, 'reviews': [record()]})['reviews']
    rows.append(deepcopy(rows[0]))
    rows[1]['review_id'] = 123
    observed = review_transitions(rows)['legacy_control']
    assert observed['ambiguous_timestamp_review_n'] == 2
    assert observed['adjacent_review_pair_n'] == 0


@pytest.mark.parametrize('threshold', [-.01, float('nan'), True, None])
def test_invalid_report_materiality_threshold_is_rejected(threshold):
    with pytest.raises(ValueError, match='materiality'):
        run_comparison({'read_only': True, 'reviews': []}, materiality_r=threshold)


def test_explicit_frozen_cost_components_scale_with_selected_close_fraction():
    value = frozen()
    costs = value['policy_manager']['execution_cost_model']
    costs.update(immediate_full_close_r=.01, deferred_full_close_r=.02, assumed=False,
        components={'immediate': {'components_r': {'spread': .001, 'commission': .002,
            'slippage': .003, 'manual_latency': .004}},
            'deferred': {'components_r': {'spread': .002, 'commission': .004,
            'slippage': .006, 'manual_latency': .008}}})
    replay = observed_replay(value, record(value), choice('CLOSE_25'))
    assert replay.get('commission_cost_r') == pytest.approx(.0035)
    assert replay['spread_cost_r'] == pytest.approx(.00175)
    assert replay['slippage_cost_r'] == pytest.approx(.00525)
    assert replay['manual_latency_cost_r'] == pytest.approx(.007)
    assert replay['execution_cost_r'] == pytest.approx(.0175)
    assert replay['ledger_settled_profit_available'] is False
    costs['components']['immediate']['components_r']['commission'] = .9
    bad = observed_replay(value, record(value), choice('CLOSE_25'))
    assert bad['commission_cost_r'] is None


def test_missing_choice_and_same_capture_time_break_transition_sequence():
    reviews = run_comparison({'read_only': True, 'reviews': [record()]})['reviews']
    rows = []
    for i, policy in enumerate(('HOLD', None, 'CLOSE_25', 'HOLD')):
        row = deepcopy(reviews[0])
        row.update(review_id=str(i), captured_ts=float(i))
        row['schemes']['legacy_control']['selection'].update(selected_policy=policy, selected_candidate_id=policy)
        rows.append(row)
    from scripts.run_unified_edge_comparison import review_transitions
    observed = review_transitions(rows)['legacy_control']
    assert observed['adjacent_review_pair_n'] == 1
    assert observed['policy_reversal_opportunity_n'] == 0
    rows[3]['captured_ts'] = rows[2]['captured_ts']
    observed = review_transitions(rows)['legacy_control']
    assert observed['adjacent_review_pair_n'] == 0
    assert observed['ambiguous_timestamp_review_n'] == 2


def test_model_summary_reports_numeric_observation_denominators():
    reviews = run_comparison({'read_only': True, 'reviews': [record()]})['reviews']
    reviews.append(deepcopy(reviews[0]))
    reviews[1]['schemes']['balanced']['selection']['expected_net_r'] = None
    model = summarize(reviews)['balanced']['model_scenarios']
    assert model.get('expected_net_r_available_review_n') == 1
    assert model['cvar10_net_r_available_review_n'] == 2


def test_model_expected_gain_is_separate_from_observed_episode_gain():
    summary = run_comparison({'read_only': True, 'reviews': [record()]})['summary']
    for scheme in ('balanced', 'legacy_control'):
        model = summary[scheme]['model_scenarios']
        assert model.get('mean_selected_delta_expected_vs_hold_r') == pytest.approx(.06)
        assert model['delta_expected_vs_hold_available_review_n'] == 1
        assert summary[scheme]['observed_path_replay']['mean_paired_delta_vs_hold_r'] == pytest.approx(.45)


def test_unsupported_production_policy_is_not_counted_as_a_selected_action():
    row = record()
    row['production_policy'] = 'UNKNOWN_POLICY'
    observed = run_comparison({'read_only': True, 'reviews': [row]})['summary'].get('legacy_control', {})
    distribution = observed.get('selected_action_distribution', {})
    assert distribution.get('selected_review_n') == 0
    assert distribution['unavailable_review_n'] == 1


@pytest.mark.parametrize('components', [[], 'bad', {'immediate': []}, {'immediate': {'components_r': 'bad'}}])
def test_malformed_optional_cost_decomposition_does_not_lose_valid_path(components):
    value = frozen()
    value['policy_manager']['execution_cost_model'].update(assumed=False, components=components)
    replay = observed_replay(value, record(value), choice('CLOSE_25'))
    assert replay['available'] is True
    assert replay['commission_cost_r'] is None


def test_absent_intervention_flag_retains_path_but_reduces_model_flag_denominator():
    reviews = run_comparison({'read_only': True, 'reviews': [record()]})['reviews']
    reviews[0]['schemes']['balanced']['selection'].pop('intervention')
    summary = summarize(reviews)['balanced']
    assert summary['model_scenarios']['intervention_frequency'] is None
    assert summary['model_scenarios']['intervention_flag_available_review_n'] == 0
    assert summary['observed_path_replay']['paired_review_n'] == 1


@pytest.mark.parametrize('horizon_minutes', [1., 7200., 85152.])
def test_export_uses_actual_frozen_horizon_and_one_bracketing_observation(horizon_minutes):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE decision_snapshots(review_id TEXT PRIMARY KEY,trade_id INTEGER,captured_ts REAL,snapshot_json TEXT,snapshot_sha256 TEXT,production_policy TEXT)')
    c.execute('CREATE TABLE decision_path_points(review_id TEXT,ts REAL,price REAL,r REAL,PRIMARY KEY(review_id,ts))')
    snapshot = frozen()
    snapshot['policy_manager']['inputs']['horizon_minutes'] = horizon_minutes
    value = record(snapshot)
    c.execute('INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?)', tuple(value[key] for key in ('review_id', 'trade_id', 'captured_ts', 'snapshot_json', 'snapshot_sha256', 'production_policy')))
    captured = value['captured_ts']
    horizon = captured + horizon_minutes * 60.
    measured = [(captured, 1.), (captured + 10, 1.6), (captured + 20, -.2),
                (horizon - 1., 2.), (horizon + 1., 2.1), (horizon + 2., 2.2)]
    c.executemany('INSERT INTO decision_path_points VALUES(?,?,?,?)',
                  [(value['review_id'], ts, 100. + 10. * r, r) for ts, r in measured])
    c.execute('PRAGMA query_only=ON')
    result = export_reviews(c, 1)['reviews'][0]
    assert result['path_query_horizon_end_ts'] == horizon
    assert [(p['ts'], p['r']) for p in result['path_points']] == measured[:-1]
    assert result['path_truncated'] is False


def test_export_large_horizon_keeps_every_first_point_and_marks_truncation():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE decision_snapshots(review_id TEXT PRIMARY KEY,trade_id INTEGER,captured_ts REAL,snapshot_json TEXT,snapshot_sha256 TEXT,production_policy TEXT)')
    c.execute('CREATE TABLE decision_path_points(review_id TEXT,ts REAL,price REAL,r REAL,PRIMARY KEY(review_id,ts))')
    snapshot = frozen()
    snapshot['policy_manager']['inputs']['horizon_minutes'] = 7200.
    value = record(snapshot)
    c.execute('INSERT INTO decision_snapshots VALUES(?,?,?,?,?,?)', tuple(value[key] for key in ('review_id', 'trade_id', 'captured_ts', 'snapshot_json', 'snapshot_sha256', 'production_policy')))
    captured = value['captured_ts']
    c.executemany('INSERT INTO decision_path_points VALUES(?,?,?,?)',
                  [(value['review_id'], captured + i, None, float(i % 3)) for i in range(6005)])
    c.execute('PRAGMA query_only=ON')
    result = export_reviews(c, 1)['reviews'][0]
    assert result['path_truncated'] is True
    assert len(result['path_points']) == 6000
    assert [p['ts'] for p in result['path_points']] == [captured + i for i in range(6000)]
    assert result['path_query_horizon_end_ts'] == captured + 432000.
