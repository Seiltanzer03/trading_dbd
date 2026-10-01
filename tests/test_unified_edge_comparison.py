from copy import deepcopy
import hashlib
import json
import sqlite3

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
    assert observed_replay(value, source, choice())['reason'] == 'OBSERVED_PATH_EXCEEDS_BOUND'
    value.pop('position_state')
    assert observed_replay(value, record(value), choice())['net_r_on_original_position'] is None
    source = record(value)
    source['path_points'] = source['path_points'][:2]
    assert observed_replay(value, source, choice('EXTEND_TAKE', take_price=135.))['reason'] == 'EXTENDED_COUNTERFACTUAL_CONTINUATION_UNOBSERVED'


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
