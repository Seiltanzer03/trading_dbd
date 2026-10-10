from copy import deepcopy

from seiltanzer.ai_report_semantics_guard import authoritative_current_price_available, repair_report_semantics
from seiltanzer.unified_edge_ensemble import build_unified_ensemble
from seiltanzer.unified_edge_audit import compact_unified_ensemble, render_unified_ensemble_lines
from test_edge_family_working import frozen_all, assessment, opinion


def blocked_snapshot():
    frozen = frozen_all()
    frozen['trade_geometry'] = {'current': 100., 'entry': 99., 'original_stop': 98.}
    manager = frozen['policy_manager']
    manager['input_audit'] = {'available_count': 10, 'total_count': 10, 'rows': {
        'instrument_price': {'available': True, 'status': 'delayed',
                             'source': 'yfinance ^NDX 1d', 'production_authority': False}}}
    manager['management_decision'] = {'policy': 'HOLD', 'execution_status': 'strategy_active'}
    manager['unified_edge_ensemble'] = build_unified_ensemble(frozen)
    return frozen


def test_guarded_hold_cannot_be_rendered_as_confirmed_market_preference():
    frozen = blocked_snapshot()
    before = deepcopy(frozen)
    report = '**ДЕЙСТВИЕ СЕЙЧАС** — HOLD ПОДТВЕРЖДЁН.\nСтатус: HOLD подтверждён.\n\n**ПОЧЕМУ ВЫБРАНО** —\nHOLD выгоднее рынка.\n'
    result = repair_report_semantics(report, frozen)
    assert 'HOLD ПОДТВЕРЖДЁН' not in result
    assert 'HOLD подтверждён' not in result
    assert 'единственное допустимое действие' in result
    assert 'не доказательство рыночного преимущества' in result
    assert frozen == before


def test_indicative_price_repair_names_yahoo_not_an_unrelated_provider():
    frozen = blocked_snapshot()
    text = repair_report_semantics('**ДЕЙСТВИЕ СЕЙЧАС** — HOLD ПОДТВЕРЖДЁН.', frozen)
    assert 'yfinance ^NDX 1d' in text
    assert 'Bybit' not in text


def test_admissible_alternative_prevents_the_guarded_hold_label():
    frozen = blocked_snapshot()
    action = next(row for row in frozen['policy_manager']['unified_edge_ensemble']['candidates']
                  if row['policy'] == 'CLOSE_10')
    action.update(eligible=True, ranking_eligible=False, ranking_reason='NO_MATERIAL_NET_OR_BOUNDED_TAIL_BENEFIT')
    text = '\n'.join(render_unified_ensemble_lines(frozen['policy_manager']['unified_edge_ensemble']))
    assert 'единственное допустимое действие' not in text
    assert 'Допущено вмешательств: 1/11' in text


def test_only_hold_audit_cannot_claim_weight_scheme_robustness():
    compact = compact_unified_ensemble(blocked_snapshot()['policy_manager']['unified_edge_ensemble'])
    assert compact['selection_context']['mode'] == 'HOLD_ONLY_ADMISSIBLE'
    assert compact['selection_context']['admissible_intervention_count'] == 0
    report = '\n'.join(render_unified_ensemble_lines(compact))
    assert 'совпадение схем не доказывает устойчивость' in report


def test_price_gate_keeps_original_action_admission_reason():
    frozen = blocked_snapshot()
    raw = frozen['policy_manager']['unified_edge_ensemble']
    action = next(row for row in raw['candidates'] if row['policy'] == 'MOVE_TO_BE')
    assert action['reason'] == 'AUTHORITATIVE_PRICE_UNAVAILABLE'
    assert action['admission_reason'] == 'ACTION_PARAMETERS_OR_EVALUATION_UNAVAILABLE'
    compact = compact_unified_ensemble(raw)
    action = next(row for row in compact['candidates'] if row['policy'] == 'MOVE_TO_BE')
    assert action['admission_reason'] == 'ACTION_PARAMETERS_OR_EVALUATION_UNAVAILABLE'


def test_indicative_yahoo_price_cannot_authorize_provider_even_without_flag():
    frozen = blocked_snapshot()
    del frozen['policy_manager']['input_audit']['rows']['instrument_price']['production_authority']
    assert authoritative_current_price_available(frozen) is False


def test_workspace_fullness_does_not_claim_ensemble_readiness(monkeypatch):
    from seiltanzer import ai_runtime_report_v20 as report
    monkeypatch.setattr(report, '_BASE_QUALITY_LINES', lambda snapshot: [
        'Покрытие decision metrics: 12/12. Input audit: 10/10.'], raising=False)
    frozen = blocked_snapshot()
    frozen['trade_geometry'].update(take_first=.4, stop_or_be_first=.4, no_touch=.2)
    frozen['policy_manager']['scenario_geometry'] = {'scenario_count': 6500}
    text = '\n'.join(report._quality_lines(frozen))
    assert 'Операционная численная доступность: PARTIAL' in text
    assert 'authoritative_price=UNAVAILABLE' in text
    assert 'Активные компоненты ансамбля:' in text
    assert 'Обученные прогнозы семейств: 0/8' in text
    assert 'Контрактное наличие групп входов (не торговый допуск): 10/10' in text


def test_working_families_are_not_preemptively_labelled_zero_by_flat_global_view():
    from seiltanzer.ai_runtime_report_v20 import _decision_weights
    llm = assessment(policy_scores={'HOLD': 0., 'CLOSE_10': 0.},
                     family_assessments={'macro': opinion('macro')})
    text = _decision_weights(frozen_all(), llm)
    assert 'активный вес 0; причина CURRENT_LLM_NO_RELATIVE_PREFERENCE' not in text
    assert 'семейные оценки проходят отдельный допуск' in text


def test_no_slippage_tail_is_explicitly_conditional_not_stop_guarantee():
    raw = blocked_snapshot()['policy_manager']['unified_edge_ensemble']
    raw['scenario_bank'] = {'execution_assumption': 'piecewise_linear_barrier_fill_no_slippage'}
    text = '\n'.join(render_unified_ensemble_lines(raw))
    assert 'гэп и проскальзывание не включены' in text
    assert 'стоп/БУ не гарантирует' in text


def test_operational_hold_without_admitted_hold_is_not_sole_admissible():
    frozen = blocked_snapshot()
    manager = frozen['policy_manager']
    manager['risk_constraint'] = {}
    manager['selection_rule'].pop('cvar_floor_r', None)
    raw = build_unified_ensemble(frozen)
    assert raw['selected_policy'] is None
    assert not any(row['eligible'] for row in raw['candidates'])
    raw['selected_policy'] = 'HOLD'  # Publication retains the operational strategy.
    manager['unified_edge_ensemble'] = raw
    assert compact_unified_ensemble(raw)['selection_context']['mode'] != 'HOLD_ONLY_ADMISSIBLE'
    assert 'единственное допустимое действие' not in repair_report_semantics('**ДЕЙСТВИЕ СЕЙЧАС** — HOLD.', frozen)


def test_missing_hold_candidate_cannot_establish_sole_admission():
    raw = blocked_snapshot()['policy_manager']['unified_edge_ensemble']
    raw['candidates'] = [row for row in raw['candidates'] if row['policy'] != 'HOLD']
    assert compact_unified_ensemble(raw)['selection_context']['mode'] == 'NOT_REPORTED'


def test_repeated_compaction_keeps_truncation_and_cannot_infer_sole_hold():
    raw = blocked_snapshot()['policy_manager']['unified_edge_ensemble']
    hold = next(row for row in raw['candidates'] if row['policy'] == 'HOLD')
    raw['candidates'] = [hold] + [dict(policy='CLOSE_10', eligible=False) for _ in range(31)] + [
        dict(policy='CLOSE_25', eligible=True, ranking_eligible=False)]
    once = compact_unified_ensemble(raw)
    twice = compact_unified_ensemble(once)
    assert twice['candidates_truncated_count'] == 1
    assert once['selection_context'] == twice['selection_context'] == {'mode': 'NOT_REPORTED'}
    assert 'единственное допустимое действие' not in '\n'.join(render_unified_ensemble_lines(once))
