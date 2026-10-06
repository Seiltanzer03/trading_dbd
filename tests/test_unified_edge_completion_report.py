import importlib
import importlib.util

from seiltanzer.config import ALL_INSTRUMENTS


def module():
    name = 'scripts.build_unified_edge_completion_report'
    assert importlib.util.find_spec(name), 'finite completion report missing'
    return importlib.import_module(name)


def test_missing_instruments_and_horizons_remain_explicit():
    report = module().build_report({}, {}, expected_sha='a'*40)
    assert len(report['math_matrix']) == len(ALL_INSTRUMENTS)*6*4
    assert all(row['search_status'] == 'NOT_REPORTED' and not row['selected_supported_model']
               for row in report['math_matrix'])
    assert report['historical_profit_proven'] is False


def test_searched_horizon_and_working_model_are_not_the_same():
    math = {'published_for_sha': 'b'*40, 'instruments': {'NAS100': {
        'search_completed': True, 'horizon_minutes': 15,
        'candidate_audit': {'15': {'status': 'VALIDATED_FOR_SELECTION'},
                            '120': {'status': 'INSUFFICIENT_SAMPLE'}},
        'diagnostics': {'direction': {'working_supported': True}}}},
        'instrument_matrix': {'NAS100': {'management_role': 'DIRECTION_SOFT_RANKING'}}}
    report = module().build_report(math, {}, expected_sha='a'*40)
    rows = [r for r in report['math_matrix'] if r['instrument'] == 'NAS100' and r['target'] == 'direction']
    assert next(r for r in rows if r['horizon_minutes'] == 15)['selected_supported_model'] is True
    assert next(r for r in rows if r['horizon_minutes'] == 120)['selected_supported_model'] is False
    assert report['input_generations']['math'] == 'HISTORICAL_OTHER_SHA'
    assert report['current_release_efficacy_proven'] is False


def test_diagnostic_mapping_and_overlapping_replays_are_not_profit_or_runtime_authority():
    math = {'instruments': {'BTCUSD': {'search_completed': True, 'horizon_minutes': 15,
        'candidate_audit': {'15': {'status': 'VALIDATED_FOR_SELECTION'}},
        'diagnostics': {'direction': {'working_supported': True}}}},
        'instrument_matrix': {'BTCUSD': {'management_role': 'DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED'}}}
    comparison = {'review_n': 32, 'summary': {'balanced': {
        'model_scenarios': {'mean_expected_net_r': .2}, 'observed_path_replay': {
            'paired_review_n': 32, 'paired_distinct_trade_n': 4,
            'mean_paired_delta_vs_hold_r': .03, 'portfolio_drawdown_available': False}}}}
    report = module().build_report(math, comparison, expected_sha='a'*40)
    selected = next(r for r in report['math_matrix'] if r['instrument'] == 'BTCUSD'
                    and r['target'] == 'direction' and r['horizon_minutes'] == 15)
    assert selected['selected_supported_model'] is True and selected['mapping_blocks_working_use'] is True
    assert report['schemes']['balanced']['observed_path_replay']['paired_distinct_trade_n'] == 4
    assert report['historical_profit_proven'] is False
    assert '32' in module().render_markdown(report)
