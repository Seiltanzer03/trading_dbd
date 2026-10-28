from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_ai_verdict_v19 import _snapshot
from seiltanzer import ai_verdict_v19 as report


def selected_snapshot(policy='EXIT', status='pending_execution'):
    s = _snapshot()
    s['trade_geometry'].update(current=10705., entry=10649., original_stop=10612.,
        active_risk_barrier=10612., final_take=10743., current_r=56/37)
    p = s['policy_manager']
    p['management_decision'].update(policy=policy, authority='UNIFIED_EDGE_ENSEMBLE',
        execution_status=status, manual_execution_required=status == 'pending_execution',
        instruction_ru='Закрыть 100% текущего остатка позиции.' if policy == 'EXIT' else
                       'Установить стоп 10686.5.' if policy == 'TIGHTEN_STOP' else
                       'Закрыть 25% текущего остатка позиции.' if policy == 'CLOSE_25' else
                       'Не сокращать текущий остаток позиции.',
        decision_id='selected-decision', automatic_execution_allowed=False)
    p['management_arbiter'].update(winner='UNIFIED_EDGE_ENSEMBLE', effective_policy=policy)
    p['unified_edge_ensemble'] = {'selected_policy':policy, 'selected_candidate_id':policy,
        'available':True, 'applied':True, 'components':[],
        'candidates':[{'candidate_id':policy, 'policy':policy, 'eligible':True,
                      'expected_net_r':1.504, 'cvar10_net_r':1.504}]}
    p['input_audit']['rows']={'instrument_price':{'available':True, 'status':'live',
        'source':'TradingView stream OANDA:UK100GBP', 'value':10705.}}
    return s


OLD_REPORT = '''**ДЕЙСТВИЕ СЕЙЧАС** — HOLD ПОДТВЕРЖДЁН.
Арбитр: STRATEGY → HOLD. Старое решение.

**ЕДИНЫЙ ПЛАН МЕНЕДЖМЕНТА** —
Старый план HOLD.

**ПОСЛЕ ИСПОЛНЕНИЯ** —
Нового внепланового исполнения нет.

**ВЕСА И РОЛИ РЕШЕНИЯ** —
Арбитр: STRATEGY; gate=manual_data_conflict.

**LLM SHADOW DECISION · БЕЗ PRODUCTION AUTHORITY** —
Quant: HOLD. Независимый LLM: HOLD; confidence 0.0%.
'''


@pytest.mark.parametrize('policy', ['EXIT','CLOSE_25','TIGHTEN_STOP'])
def test_final_report_owns_every_operational_command_without_mutating_snapshot(policy):
    s = selected_snapshot(policy)
    original = deepcopy(s)
    text = report.normalize_final_report(OLD_REPORT, s)
    assert text.startswith(f'**ДЕЙСТВИЕ СЕЙЧАС** — {policy}:')
    assert 'Арбитр: STRATEGY → HOLD' not in text
    assert 'Нового внепланового исполнения нет.' not in text
    assert 'Арбитр: STRATEGY; gate=' not in text
    assert 'decision_id=selected-decision' in text
    assert 'Независимый LLM: HOLD; confidence 0.0%' in text
    assert 'Базовый quant до ансамбля: HOLD' in text
    assert s == original


def test_repeated_pending_action_is_explicitly_not_an_additional_close():
    s = selected_snapshot()
    s['policy_manager']['management_decision']['continuity']='continue_same_pending_decision'
    text=report.normalize_final_report(OLD_REPORT,s)
    assert 'прежнее действующее решение' in text
    assert 'дополнительное закрытие' in text


@pytest.mark.parametrize('status',['executed_continuation','not_required'])
def test_operational_hold_does_not_inherit_old_exit_command(status):
    s=selected_snapshot('HOLD',status)
    text=report.normalize_final_report(OLD_REPORT.replace('HOLD ПОДТВЕРЖДЁН','EXIT: Закрыть 100%'),s)
    assert text.startswith('**ДЕЙСТВИЕ СЕЙЧАС** — HOLD:')
    assert 'EXIT: Закрыть 100%' not in text
    assert 'повтор' in text.lower()


def test_executed_exit_never_repeats_the_old_closing_instruction():
    s=selected_snapshot('EXIT','executed')
    text=report.normalize_final_report(OLD_REPORT,s)
    command=text.split('**ЕДИНЫЙ ПЛАН МЕНЕДЖМЕНТА**')[0]
    assert 'Закрыть 100%' not in command
    assert 'уже исполненное действие не повторять' in command


def test_stale_event_probabilities_do_not_become_fresh_after_quote_repricing(monkeypatch):
    from test_ai_snapshot_market_refresh import fixture
    from seiltanzer.ai_snapshot_market_refresh import refresh_frozen_comparison
    engine,s,_=fixture(monkeypatch)
    s['trade_geometry'].update(take_first=.63,stop_or_be_first=.37,no_touch=0.,p50_resolution_minutes=83.)
    s['policy_manager']['scenario_geometry']={'scenario_count':6500}
    s['report_integrity']={'scenario_geometry':{'scenario_count':6500}}
    out=refresh_frozen_comparison(engine,s)
    assert all(out['trade_geometry'][k] is None for k in
               ('take_first','stop_or_be_first','no_touch','p50_resolution_minutes'))
    assert out['policy_manager']['scenario_geometry']=={}
    assert out['report_integrity']['scenario_geometry']=={}


def test_numerical_base_availability_is_not_reported_as_source_quality():
    from seiltanzer.unified_edge_audit import render_unified_ensemble_lines
    a=selected_snapshot()['policy_manager']['unified_edge_ensemble']
    a['components']=[{'component_id':'quantitative_base', 'quality':1.,
        'available':True, 'effective_weight':1., 'nominal_weight':.4, 'age_sec':0.}]
    text='\n'.join(render_unified_ensemble_lines(a))
    assert 'численная доступность 100.0%' in text
    assert 'не оценка надёжности источников' in text


@pytest.mark.parametrize('original_ts', [1792419000., None])
def test_quote_refresh_labels_cached_base_selection_without_fresh_tie_causality(original_ts):
    # The saved second report retained CLOSE_50 from the heavy cached build,
    # alongside fresh common-bank values whose best/least intervention is HOLD.
    s = selected_snapshot('TIGHTEN_STOP')
    s['market_refresh'] = {'available': True, 'heavy_policy_recomputed': False,
                           'original_snapshot_ts': original_ts}
    p = s['policy_manager']
    p['recommendation']['raw_optimizer_policy'] = 'CLOSE_50'
    p['policies'] = {'HOLD': {'expected_final_r': .6474991859991861, 'cvar10_r': -.01},
                     'CLOSE_50': {'expected_final_r': .6319364061864061, 'cvar10_r': .3031868131868128}}
    p['selection_rule'].update(eligible=['HOLD', 'CLOSE_50'], best_expected_r=.6474991859991861,
                               cvar_floor_r=-.01, indifference_band_r=.03)
    before = deepcopy(s)
    text = '\n'.join(report._risk_lines(s))
    assert 'ORIGINAL_CACHED_SELECTION: CLOSE_50' in text
    assert f'original_snapshot_ts={original_ts if original_ts is not None else "UNAVAILABLE"}' in text
    assert 'текущего сравнения +0.647R' in text
    assert 'отстаёт на +0.016R' in text
    assert 'базовый оптимизатор не пересчитывался' in text
    assert 'Базовый quant-выбор: CLOSE_50' not in text
    assert 'При разрыве не больше зоны выбирается' not in text
    assert s == before


def test_unified_replacement_does_not_keep_cancellation_continuity(monkeypatch):
    from seiltanzer.app import _unified_operational_choice
    s=selected_snapshot('HOLD','not_required')
    p=s['policy_manager']
    p['management_decision'].update(continuity='active_ai_plan_cancelled_by_current_arbiter',
        previous={'policy':'TIGHTEN_STOP','execution_status':'pending_execution','decision_id':'old-stop'})
    audit={'selected_policy':'EXIT','selected_candidate_id':'EXIT',
           'candidates':[{'policy':'EXIT','candidate_id':'EXIT','eligible':True}]}
    monkeypatch.setattr('seiltanzer.app._refresh_management_decision',lambda e,s,t:
        {**s['policy_manager']['management_decision'],'policy':'EXIT',
         'execution_status':'pending_execution','decision_id':'new-exit'})
    decision,_=_unified_operational_choice(SimpleNamespace(),s,{},audit)
    assert decision['continuity']=='unified_action_replaces_previous_plan'


def test_unified_repeated_pending_reduction_keeps_actual_decision_identity(monkeypatch):
    from seiltanzer.app import _unified_operational_choice
    s = selected_snapshot('HOLD', 'not_required')
    s['policy_manager']['management_decision'].update(
        previous={'policy': 'CLOSE_10', 'execution_status': 'pending_execution',
                  'decision_id': 'pending-close'})
    audit = {'selected_policy': 'CLOSE_10', 'selected_candidate_id': 'CLOSE_10',
             'candidates': [{'policy': 'CLOSE_10', 'candidate_id': 'CLOSE_10', 'eligible': True}]}
    monkeypatch.setattr('seiltanzer.app._refresh_management_decision', lambda e,s,t:
        {**s['policy_manager']['management_decision'], 'policy': 'CLOSE_10',
         'execution_status': 'pending_execution', 'decision_id': 'pending-close',
         'continuity': 'continue_same_pending_decision'})
    decision, _ = _unified_operational_choice(SimpleNamespace(), s, {}, audit)
    assert decision['continuity'] == 'continue_same_pending_decision'
    assert decision['decision_id'] == 'pending-close'


def llm_proposal_snapshot():
    s = selected_snapshot('CLOSE_25')
    s['llm_shadow_decision'] = {'status': 'ok', 'policy': 'MOVE_TO_BE', 'confidence': .8,
        'working_action': {'status': 'READY_FOR_MANUAL_CONFIRMATION',
            'instruction_ru': 'ВРУЧНУЮ ПЕРЕНЕСТИ СТОП В БУ: 4006',
            'parameters': {'stop_price': 4006., 'anchor': 'ENTRY_PRICE'}}}
    row = {'candidate_id': 'be-4006', 'policy': 'MOVE_TO_BE',
        'parameters': {'stop_price': 4006., 'anchor': 'ENTRY_PRICE'},
        'eligible': False, 'reason': 'COMMON_SCENARIO_NO_ROBUST_MATERIAL_BENEFIT',
        'delta_expected_r': .073, 'score': .740,
        'benefit_assessment': {'basis': 'NO_MATERIAL_NET_OR_BOUNDED_TAIL_BENEFIT',
            'sensitivity_basis': 'PAIRED_MODEL_BOUND',
            'expected_test_value_r': -.039, 'indifference_band_r': .03},
        'independent_evaluation': {'status': 'eligible', 'paired_delta_ci95_lower_r': -.0208}}
    s['policy_manager']['unified_edge_ensemble']['candidates'].append(row)
    return s, row


def test_final_shadow_explains_common_refusal_and_removes_old_imperative():
    s, row = llm_proposal_snapshot()
    before = deepcopy(s)
    text = report.normalize_final_report(OLD_REPORT +
        '\nLLM ACTION VARIANT: ВРУЧНУЮ ПЕРЕНЕСТИ СТОП В БУ: 4006.\n', s)
    shadow = text.split('**LLM SHADOW DECISION')[1].split('\n**')[0]
    assert 'ВРУЧНУЮ ПЕРЕНЕСТИ' not in text
    assert 'Итоговый статус предложения MOVE_TO_BE: исключено' in shadow
    assert 'COMMON_SCENARIO_NO_ROBUST_MATERIAL_BENEFIT' in shadow
    assert '-0.039R' in shadow and '-0.030R' in shadow
    assert 'другому банку' in shadow
    assert s == before
    assert report.normalize_final_report(text, s) == text


@pytest.mark.parametrize('kind', ['missing', 'other_parameters', 'ambiguous'])
def test_final_shadow_never_infers_admission_from_policy_name(kind):
    s, row = llm_proposal_snapshot()
    rows = s['policy_manager']['unified_edge_ensemble']['candidates']
    if kind == 'missing': rows.remove(row)
    elif kind == 'other_parameters': row['parameters']['stop_price'] = 4007.
    else: rows.append(deepcopy(row))
    text = report.normalize_final_report(OLD_REPORT, s)
    assert 'Итоговый допуск этих параметров не подтверждён' in text
    assert 'ВРУЧНУЮ ПЕРЕНЕСТИ' not in text


@pytest.mark.parametrize('selected', [False, True])
def test_final_shadow_distinguishes_eligible_proposal_from_selected_plan(selected):
    s, row = llm_proposal_snapshot()
    row.update(eligible=True, reason='PASS')
    if selected:
        p = s['policy_manager']
        p['management_decision']['policy'] = 'MOVE_TO_BE'
        p['management_decision']['instruction_ru'] = 'Установить стоп 4006.'
        p['management_decision']['parameters'] = deepcopy(row['parameters'])
        p['unified_edge_ensemble'].update(selected_policy='MOVE_TO_BE', selected_candidate_id=row['candidate_id'])
    text = report.normalize_final_report(OLD_REPORT, s)
    assert ('Итоговый статус предложения MOVE_TO_BE: выбрано' if selected else
            'Итоговый статус предложения MOVE_TO_BE: допущено, но не выбрано') in text
    assert 'ВРУЧНУЮ ПЕРЕНЕСТИ' not in text


@pytest.mark.parametrize('missing', ['proposal_params', 'row_params', 'empty_both', 'invalid_target',
                                    'eligible_missing', 'eligible_string'])
def test_incomplete_extended_proposal_metadata_never_claims_final_admission(missing):
    s, row = llm_proposal_snapshot()
    action = s['llm_shadow_decision']['working_action']
    row['eligible'] = True
    if missing == 'proposal_params': action.pop('parameters')
    elif missing == 'row_params': row.pop('parameters')
    elif missing == 'empty_both': action['parameters'] = row['parameters'] = {}
    elif missing == 'invalid_target': action['parameters']['stop_price'] = row['parameters']['stop_price'] = True
    elif missing == 'eligible_missing': row.pop('eligible')
    else: row['eligible'] = 'true'
    text = '\n'.join(report._final_llm_proposal_lines(s))
    assert 'Итоговый допуск этих параметров не подтверждён' in text
    assert 'допущено' not in text and 'выбрано' not in text
    assert 'Базовый quant до ансамбля:' in text


@pytest.mark.parametrize('final_params', [None, {'stop_price': 4007., 'anchor': 'ENTRY_PRICE'}])
def test_selected_shadow_must_match_final_execution_parameters(final_params):
    s, row = llm_proposal_snapshot()
    row['eligible'] = True
    p = s['policy_manager']
    p['unified_edge_ensemble'].update(selected_policy='MOVE_TO_BE', selected_candidate_id=row['candidate_id'])
    p['management_decision'].update(policy='MOVE_TO_BE', parameters=final_params)
    text = '\n'.join(report._final_llm_proposal_lines(s))
    assert 'Итоговый допуск этих параметров не подтверждён' in text
    assert 'Итоговый статус предложения MOVE_TO_BE: выбрано' not in text


def test_point_economics_are_not_relabelled_as_mc_lower_bounds():
    s, row = llm_proposal_snapshot()
    row['benefit_assessment']['sensitivity_basis'] = 'QUALIFIED_POINT_ECONOMICS'
    text = '\n'.join(report._final_llm_proposal_lines(s))
    assert 'точечное ΔExpected -0.039R' in text
    assert 'нижняя MC/seed-граница' not in text
