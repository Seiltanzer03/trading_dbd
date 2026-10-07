"""Regression coverage for the complete production management contract."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from seiltanzer import ai_policy_v2, ai_policy_v4, ai_verdict
from seiltanzer import ai_snapshot_budget_guard as budget
from seiltanzer.active_management import POLICIES, render_active_management, select_active_management
from seiltanzer.ai_policy_base import PolicyInputs, simulate_option_paths
from seiltanzer.ai_runtime_report_v20 import _control_summary, _decision_weights
from seiltanzer.decision_research import canonical_snapshot
from seiltanzer.extended_policy_evaluation import evaluate_extended_action
from seiltanzer.llm_decision_shadow import append_shadow_section, audit_report_claims
from seiltanzer.llm_shadow_working_action import build_working_action
from seiltanzer.management_contract import calculation_audit, decision_reliability
from test_extended_policy_evaluation import _snapshot, _action


def snapshot():
    row = _snapshot()
    row.update(captured_ts=1_790_795_000, trade_id=1, strategy={'direction': 'long'})
    row['policy_manager']['strategy_next_step'] = {'next_rung_r': 1.5, 'next_rung_price': 115}
    row['policy_manager']['option_derivative_state'] = {'gex_geometry': {
        'distance_to_zero_gamma': -.5, 'distance_to_call_wall_r': 3}}
    return row


def test_cached_main_path_costs_match_fresh_stress_costs_without_resampling():
    data = snapshot()['policy_manager']['inputs']
    inputs = PolicyInputs(**{**data, 'rungs': tuple(data['rungs'])})
    sim = simulate_option_paths(inputs, n_paths=400, n_steps=60, seed=19)
    engine = SimpleNamespace(authoritative_execution_mc=lambda _: sim)
    costs = {'immediate_full_close_r': .02, 'deferred_full_close_r': .03}
    token = ai_policy_v4._COST_CTX.set(costs)
    try:
        main, reused = ai_policy_v2._main_policy_run(engine, inputs)
        fresh, _ = ai_policy_v4._run_once(inputs, n_paths=400, n_steps=60, seed=19)
    finally:
        ai_policy_v4._COST_CTX.reset(token)
    assert reused is sim
    for policy in main:
        for key in ('expected_final_r', 'cvar10_r', 'p_final_profit', 'execution_cost_r'):
            assert main[policy][key] == fresh[policy][key]
        assert main[policy]['outcomes_include_execution_costs'] is True
    assert main['EXIT']['expected_final_r_net'] == .98
    assert main['HOLD']['execution_cost_r'] == .03
    assert main['CLOSE_50']['execution_cost_r'] == .025


def test_first_snapshot_budget_pass_preserves_net_cost_audit_before_facade_capture():
    row = snapshot()
    data = row['policy_manager']['inputs']
    inputs = PolicyInputs(**{**data, 'rungs': tuple(data['rungs'])})
    sim = simulate_option_paths(inputs, n_paths=400, n_steps=60, seed=19)
    policies = ai_policy_v4.metrics_from_execution_paths(
        sim, inputs, row['policy_manager']['execution_cost_model'])
    row['policy_manager']['policies'] = policies
    row['retained_context'] = 'x' * 51_000
    for policy in policies.values():
        policy['redundant_debug_workspace'] = 'x' * 5000
    before = calculation_audit(row)
    assert before['status'] == 'AVAILABLE'
    # This base pass runs before the facade can capture report integrity.
    ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18(row)
    assert row['snapshot_budget']['original_bytes'] > 60_000
    assert row['snapshot_budget']['final_bytes'] < 60_000
    assert calculation_audit(row) == before
    ai_verdict._enforce_snapshot_budget_with_report_integrity(row)
    assert calculation_audit(row) == before


@pytest.mark.parametrize('compact', ['normal', 'strict', 'emergency'])
def test_low_quality_survives_every_budget_tier_and_blocks_override(compact):
    row = snapshot()
    row['policy_manager']['evidence']['data_quality']['reliability'] = {
        'level': 'низкая', 'reasons': ['delayed options']}
    if compact == 'normal':
        row['policy_manager']['evidence']['debug'] = 'x' * 80_000
        ai_verdict._enforce_snapshot_budget_with_report_integrity(row)
    else:
        budget._strict_authoritative_compaction(row)
        if compact == 'emergency':
            budget._emergency_authoritative_compaction(row, ai_verdict)
    assert decision_reliability(row)['level'] == 'низкая'
    assert evaluate_extended_action(row, _action())['reason'] == 'LOW_DATA_RELIABILITY_FOR_EXTENDED_OVERRIDE'
    assert 'надёжность данных: низкая' in _control_summary(row)
    assert row['policy_manager']['execution_cost_model']['deferred_full_close_r'] == .01


def test_missing_quality_fails_closed_and_conflicting_quality_uses_worst():
    row = snapshot()
    row['policy_manager'].pop('evidence')
    assert evaluate_extended_action(row, _action())['reason'] == 'DATA_RELIABILITY_UNAVAILABLE'
    row['metric_coverage'] = {'summary': {'reliability': {'level': 'низкая'}}}
    row['policy_manager']['decision_reliability'] = {'level': 'высокая'}
    assert decision_reliability(row)['level'] == 'низкая'


@pytest.mark.parametrize('policy', POLICIES)
def test_every_extended_action_has_real_parameters_and_counterfactual(policy):
    row = snapshot()
    action = build_working_action(row, {'status': 'ok', 'policy': policy, 'confidence': .65})
    assert action['status'] == 'READY_FOR_MANUAL_CONFIRMATION'
    result = evaluate_extended_action(row, action)
    # Eligibility depends on economics, never on an unimplemented execution contract.
    assert result['reason'] in {'NO_MATERIAL_ROBUST_EXPECTED_GAIN', 'ROBUST_EXPECTED_GAIN_AND_CVAR_PASS', 'VARIANT_CVAR_BELOW_HARD_FLOOR'}
    assert result['paths'] > 0
    assert result['expected_variant_net_r'] == pytest.approx(result['expected_variant_gross_r'] - .01, abs=1e-5)
    assert result['hard_net_floor_r'] == -1.01
    assert result['automatic_execution_allowed'] is False


def test_spike_after_user_example_drawdown_uses_new_maximum_and_not_old_trigger():
    row = snapshot()
    row['trade_geometry'].update(entry=30500, original_stop=30396, active_risk_barrier=30500,
                                 current=30590.7, final_take=30770)
    row['policy_manager']['inputs'].update(r0=90.7/104, max_r=1.634, T=270/104,
                                           stop_r=0, rungs=[1.75, 2, 2.25])
    action = build_working_action(row, {'status': 'ok', 'policy': 'SCALE_OUT_ON_SPIKE', 'confidence': .65})
    assert action['parameters']['target_r'] > 1.634
    result = evaluate_extended_action(row, action)
    assert result['reason'] != 'SPIKE_TRIGGER_ALREADY_CROSSED_OR_OUTSIDE_TAKE'
    assert 'expected_delta_vs_hold_r' in result


def test_rejected_economic_variant_reports_actual_numbers_and_whole_trade_delta():
    row = snapshot()
    select_active_management(row)
    rows = row['active_management_candidates']
    text = render_active_management(rows, .5)
    for policy in POLICIES:
        assert f'{policy}:' in text
    assert 'Expected net: HOLD' in text
    assert 'Нижняя MC-граница' in text
    assert 'CVaR10 net worst seed' in text
    assert 'ΔExpected для исходного объёма сделки' in text
    assert 'резервная оценка' in text
    assert 'источник fallback test' in text


def test_time_stop_future_instruction_is_saved_but_future_source_data_is_rejected():
    row = snapshot()
    deadline = row['captured_ts'] + 60
    row['active_management_candidates'] = [{'policy': 'TIME_STOP', 'parameters': {'deadline_ts': deadline}}]
    row['effective_management_decision'] = {'policy': 'TIME_STOP', 'parameters': {'deadline_ts': deadline}}
    row['llm_shadow_decision'] = {'policy': 'TIME_STOP', 'working_action': {
        'policy': 'TIME_STOP', 'parameters': {'deadline_ts': deadline}}}
    row['selected_management_action'] = {'policy': 'TIME_STOP', 'working_action': {
        'policy': 'TIME_STOP', 'parameters': {'deadline_ts': deadline}}}
    row['policy_manager']['management_decision'] = {'policy': 'TIME_STOP',
        'parameters': {'deadline_ts': deadline}}
    row['policy_manager']['unified_edge_ensemble'] = {'candidates': [
        {'policy': 'TIME_STOP', 'parameters': {'deadline_ts': deadline},
         'quant_evaluation': {'policy': 'TIME_STOP', 'parameters': {'deadline_ts': deadline}}}]}
    assert canonical_snapshot(row)['production_policy'] == 'TIME_STOP'
    row['market_state'] = {'policy': 'TIME_STOP', 'parameters': {'deadline_ts': deadline}}
    with pytest.raises(ValueError, match='market_state.parameters.deadline_ts'):
        canonical_snapshot(row)
    row.pop('market_state')
    row['active_management_candidates'][0]['source_ts'] = deadline
    with pytest.raises(ValueError, match='source_ts'):
        canonical_snapshot(row)


def test_llm_contradictions_are_withheld_while_raw_claims_remain_auditable():
    row = snapshot()
    row['policy_manager']['decision_reliability'] = {'level': 'низкая'}
    claims = ['Политика HOLD устойчива в стресс-тестах', 'Низкая надежность данных запрещает активное сокращение']
    shadow = {'status': 'ok', 'policy': 'HOLD', 'key_evidence': claims,
              'confidence': 0, 'reason_ru': 'HOLD', 'quant_policy': 'HOLD'}
    audit_report_claims(row, shadow)
    assert shadow['key_evidence'] == claims
    text = append_shadow_section('Report', shadow)
    assert claims[0] not in text and claims[1] not in text
    assert len(shadow['report_claim_conflicts']) == 2
    assert 'не определяет победителя' in _decision_weights(row, shadow)


def test_delayed_option_wall_cannot_extend_take_even_with_forged_ready_parameters():
    row = snapshot()
    row['policy_manager']['inputs']['chain_status'] = 'delayed'
    assert build_working_action(row, {'status': 'ok', 'policy': 'EXTEND_TAKE', 'confidence': .65})['reason'] == 'OPTION_WALL_NOT_A_VERIFIED_EXECUTION_ANCHOR'
    assert evaluate_extended_action(row, _action('EXTEND_TAKE', take_price=140))['reason'] == 'OPTION_WALL_NOT_A_VERIFIED_EXECUTION_ANCHOR'


def test_flat_current_llm_is_not_described_as_an_active_voice():
    report = _decision_weights({'policy_manager': {'unified_edge_ensemble': {
        'components': [{'component_id': 'current_llm', 'available': False,
                        'reason': 'CURRENT_LLM_NO_RELATIVE_PREFERENCE'}]}}},
        {'status': 'ok', 'policy_scores': {'HOLD': 0., 'EXIT': 0.}})
    assert 'голос не участвует; активный вес 0' in report
    assert 'CURRENT_LLM_NO_RELATIVE_PREFERENCE' in report


def test_flat_llm_before_final_ensemble_is_not_an_active_voice():
    report = _decision_weights({'policy_manager': {}},
        {'status': 'ok', 'policy_scores': {'HOLD': 0., 'EXIT': 0.}})
    assert 'CURRENT_LLM_NO_RELATIVE_PREFERENCE' in report
    assert 'голос не участвует; активный вес 0' in report
