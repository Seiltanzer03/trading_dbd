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
