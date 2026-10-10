from copy import deepcopy
import json

import pytest

from seiltanzer.edge_family_adapters import FAMILIES, build_edge_family_evidence
from test_edge_family_adapters import T0, family_fixture
from test_unified_edge_ensemble import snapshot as management_snapshot


def frozen_all():
    value = management_snapshot()
    value['captured_ts'] = T0
    value['edge_family_sources'] = {family: family_fixture(family)[0] for family in FAMILIES}
    return value


def opinion(family, scores=None):
    return {'feature_names': [family_fixture(family)[1]],
            'policy_scores': scores or {'CLOSE_10': 1., 'CLOSE_25': -1.},
            'reason_ru': 'Рабочая интерпретация наблюдаемого признака.'}


def assessment(**values):
    return {'status': 'ok', 'policy': 'CLOSE_25', 'captured_ts': T0,
            'policy_scores': {'HOLD': 0., 'CLOSE_10': -1., 'CLOSE_25': 1.},
            'family_assessments': {family: opinion(family) for family in FAMILIES}, **values}


def working(frozen, llm, **kwargs):
    from seiltanzer.edge_family_working import working_family_preferences
    families = build_edge_family_evidence(frozen)['families']
    return working_family_preferences(families, llm, **kwargs)


@pytest.mark.parametrize('family', FAMILIES)
def test_each_family_has_source_bound_working_opinion_without_net_model(family):
    frozen = frozen_all()
    llm = assessment(family_assessments={family: opinion(family)})
    before = deepcopy((frozen, llm))
    result = working(frozen, llm)
    row = result['families'][family]
    assert row['working_assessment_available'] is True
    assert row['working_assessment_kind'] == 'CURRENT_LLM_SOURCE_INTERPRETATION'
    assert row['forecast_available'] is False
    assert row['working_policy_scores']['CLOSE_10'] == 1.
    assert result['scores']['CLOSE_10'] == pytest.approx(0.)
    assert result['attributions']['CLOSE_10'][family] == pytest.approx(.5)
    assert (frozen, llm) == before
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('change', ['missing', 'stale', 'future', 'wrong_instrument', 'unseen_feature'])
def test_unavailable_or_unseen_family_facts_cannot_gain_working_vote(change):
    frozen = frozen_all()
    llm = assessment(family_assessments={'macro': opinion('macro')})
    source = frozen['edge_family_sources']['macro']
    if change == 'missing':
        del frozen['edge_family_sources']['macro']
    elif change == 'stale':
        source['observed_ts'] = T0 - 100 * 86400
    elif change == 'future':
        source['received_ts'] = T0 + 1
    elif change == 'wrong_instrument':
        source['instrument'] = 'XAU'
    else:
        llm['family_assessments']['macro']['feature_names'] = ['invented.edge']
    result = working(frozen, llm)
    assert result['accepted_count'] == 0
    assert result['scores'] == llm['policy_scores']
    assert result['families']['macro']['working_assessment_available'] is False
    assert result['families']['macro']['working_assessment_reason']


@pytest.mark.parametrize('scores', [{'HOLD': True}, {'HOLD': float('nan')}, {'HOLD': 2.}, {'BUY': 1.}])
def test_malformed_family_scores_are_rejected(scores):
    from seiltanzer.edge_family_working import parse_family_assessments
    with pytest.raises(RuntimeError, match='invalid_family_assessments'):
        parse_family_assessments({'macro': opinion('macro', scores)})


def test_shared_source_families_form_one_group_including_overlapping_lineage():
    frozen = frozen_all()
    for family in ('macro', 'option', 'value_carry'):
        frozen['edge_family_sources'][family]['dependency_group'] = 'shared-release'
    # Option uses its adapter-owned chain family, while macro/value share a release.
    llm = assessment(family_assessments={
        'macro': opinion('macro', {'CLOSE_10': 1.}),
        'value_carry': opinion('value_carry', {'CLOSE_10': 1.}),
        'intermarket': opinion('intermarket', {'CLOSE_10': -1.})})
    result = working(frozen, llm)
    assert result['scores']['CLOSE_10'] == pytest.approx(-.5)
    assert result['attributions']['CLOSE_10']['macro'] == pytest.approx(.125)
    assert result['attributions']['CLOSE_10']['value_carry'] == pytest.approx(.125)
    assert result['attributions']['CLOSE_10']['intermarket'] == pytest.approx(-.25)


def test_sparse_family_opinion_does_not_turn_unscored_actions_into_zero():
    result = working(frozen_all(), assessment(family_assessments={
        'macro': opinion('macro', {'CLOSE_10': 1.})}))
    assert result['scores']['CLOSE_25'] == 1.
    assert 'EXIT' not in result['scores']
    assert 'CLOSE_25' not in result['attributions']


def test_family_only_opinion_and_exclusion_preserve_null_legacy_scores():
    frozen = frozen_all()
    llm = assessment(policy_scores={}, family_assessments={'macro': opinion('macro', {'CLOSE_10': 1.})})
    assert working(frozen, llm)['scores'] == {'CLOSE_10': 1.}
    assert working(frozen, llm, excluded_family='macro')['scores'] == {}


@pytest.mark.parametrize('level', [-1., 0., 1.])
def test_flat_global_level_cannot_suppress_sparse_family_preference(level):
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    llm = assessment(policy_scores={key: level for key in ('HOLD', 'CLOSE_10', 'CLOSE_25')},
                     family_assessments={'macro': opinion('macro', {'CLOSE_10': 1.})})
    result = working(frozen_all(), llm)
    assert result['scores'] == {'CLOSE_10': 1.}
    assert result['attribution_weights']['CLOSE_10']['macro'] == 1.
    assert build_unified_ensemble(frozen_all(), llm)['selected_policy'] == 'CLOSE_10'


def test_family_ablation_preserves_common_bank_and_unrelated_dependency_weight(monkeypatch):
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    import seiltanzer.unified_candidate_economics as economics
    frozen = frozen_all()
    frozen['policy_manager']['unified_component_assessments'] = [{
        'component_id': 'active_edge', 'available': True, 'quality': 1.,
        'scores': {'CLOSE_10': .4, 'CLOSE_25': -.4},
        'source_ids': ['authoritative_execution_paths'],
        'evidence_family_ids': ['independent-model']}]
    monkeypatch.setattr(economics, 'price_unified_candidates', lambda snapshot, candidates: {
        'available': True, 'bank': {'source': 'actual-bank', 'bank_id': 'abc'}, 'version': 'bank-v1',
        'candidates': {row['candidate_id']: {'available': True, 'hard_risk_pass': True}
                       for row in candidates}})
    llm = assessment(policy_scores={key: 0. for key in ('HOLD', 'CLOSE_10', 'CLOSE_25')},
                     family_assessments={'macro': opinion('macro', {'HOLD': 1.})})
    with_family = build_unified_ensemble(frozen, llm)
    without_family = build_unified_ensemble(frozen, {**llm, 'family_assessments': {}})
    reported = with_family['family_counterfactuals'][0]
    assert without_family['selected_policy'] == 'CLOSE_10'
    assert reported['selected_candidate_id'] == without_family['selected_candidate_id']


@pytest.mark.parametrize('status', ['unavailable', 'blocked'])
def test_provider_failure_cannot_leave_family_votes_active(status):
    result = working(frozen_all(), assessment(status=status))
    assert result['accepted_count'] == 0
    assert all(not row['working_assessment_available'] for row in result['families'].values())


def test_source_bound_family_opinion_changes_ranking_inside_existing_llm_budget():
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    frozen = frozen_all()
    llm = assessment(policy_scores={'HOLD': 0., 'CLOSE_10': 0., 'CLOSE_25': 0.},
                     family_assessments={'macro': opinion('macro')})
    baseline = build_unified_ensemble(frozen)
    actual = build_unified_ensemble(frozen, llm)
    assert baseline['selected_policy'] == 'CLOSE_25'
    assert actual['selected_policy'] == 'CLOSE_10'
    assert sum(row['effective_weight'] for row in actual['components']) == pytest.approx(1.)
    current = next(row for row in actual['components'] if row['component_id'] == 'current_llm')
    assert current['effective_weight'] <= current['nominal_weight']
    assert len(actual['candidates']) == 12
    assert actual['historical_profit_proven'] is False
    frozen['policy_manager']['policies']['CLOSE_10']['cvar10_r'] = -.5
    guarded = build_unified_ensemble(frozen, llm)
    assert guarded['selected_policy'] != 'CLOSE_10'
    assert next(row for row in guarded['candidates'] if row['policy'] == 'CLOSE_10')['eligible'] is False


def test_payload_parser_preserves_optional_source_bound_family_opinions():
    from seiltanzer.llm_decision_shadow import _validate_model_payload, VALID_POLICIES
    payload = dict(policy='HOLD', confidence=.4, reason_ru='Ограниченная оценка.',
                   policy_scores={key: float(key == 'HOLD') for key in VALID_POLICIES},
                   family_assessments={'macro': opinion('macro'), 'event': {}})
    assert _validate_model_payload(payload)['family_assessments'] == payload['family_assessments']


def test_family_assessments_survive_combined_single_provider_response(monkeypatch):
    import seiltanzer.ai_runtime_report_v20 as report
    from seiltanzer.llm_decision_shadow import VALID_POLICIES
    frozen = frozen_all()
    frozen['policy_manager']['input_audit'] = {'rows': {'instrument_price': {'available': True, 'status': 'live'}}}
    frozen['trade_geometry'] = {'current': 100.}
    payload = {'explanation_ru': 'Оценка доступных фактов снимка.', 'shadow_decision': {
        'policy': 'HOLD', 'confidence': .4, 'reason_ru': 'Ограниченная оценка.',
        'policy_scores': {key: float(key == 'HOLD') for key in VALID_POLICIES},
        'family_assessments': {'macro': opinion('macro')}}}
    content = json.dumps(payload, ensure_ascii=False)
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'choices': [{'message': {'content': content}}], 'model': 'test'}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs['timeout'] == 8

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            calls.append(kwargs['json'])
            return Response()

    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(report.httpx, 'Client', Client)
    monkeypatch.setattr(report.ai_verdict, 'render_policy_report', lambda value: 'DETERMINISTIC')
    monkeypatch.setattr(report.ai_verdict, '_validate_model_report', lambda *args: [])
    result = report.request_explanation_with_shadow(frozen, authoritative_snapshot=frozen)
    assert len(calls) == 1
    assert result['llm_shadow_decision']['family_assessments'] == payload['shadow_decision']['family_assessments']
    actual_input = json.loads(calls[0]['messages'][1]['content'].split('\n', 1)[1])
    assert actual_input['edge_family_facts']['macro']['features']['macro.expected_rate_change'] == -.2
    assert 'family_assessments' in calls[0]['messages'][0]['content']
    assert 'management_decision' not in actual_input['policy_manager']


def test_family_contributions_and_ablation_use_the_same_frozen_candidates():
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from seiltanzer.unified_edge_audit import compact_unified_ensemble, render_unified_ensemble_lines
    frozen = frozen_all()
    llm = assessment(policy_scores={'HOLD': 0., 'CLOSE_10': 0., 'CLOSE_25': 0.},
                     family_assessments={'macro': opinion('macro')})
    before = deepcopy((frozen, llm))
    actual = build_unified_ensemble(frozen, llm)
    selected = next(row for row in actual['candidates'] if row['candidate_id'] == actual['selected_candidate_id'])
    family = next(row for row in selected['working_family_contributions'] if row['family_id'] == 'macro')
    current = next(row for row in selected['component_contributions'] if row['component_id'] == 'current_llm')
    assert family['contribution'] == pytest.approx(current['contribution'])
    assert family['effective_weight'] == pytest.approx(current['effective_weight'])
    audit_row = actual['edge_families']['macro']
    assert audit_row['working_contribution'] == family['contribution']
    assert audit_row['forecast_available'] is False
    without = next(row for row in actual['family_counterfactuals'] if row['excluded_family_id'] == 'macro')
    assert without['selected_policy'] == 'CLOSE_25'
    assert without['ablation_scope'] == 'CURRENT_LLM_FAMILY_INTERPRETATION_ONLY'
    assert without['risk_and_cost_evaluation_preserved'] is True
    assert (frozen, llm) == before
    compact = compact_unified_ensemble(actual)
    assert compact['family_counterfactuals'][0] == without
    preserved = next(row for row in compact['edge_families'] if row['family_id'] == 'macro')
    assert preserved['working_contribution'] == audit_row['working_contribution']
    rendered = '\n'.join(render_unified_ensemble_lines(actual))
    assert 'рабочая интерпретация llm' in rendered.lower()
    assert 'Без рабочей оценки macro: CLOSE_25' in rendered


def test_shared_source_bridge_joins_previously_disjoint_families():
    frozen = frozen_all()
    macro = frozen['edge_family_sources']['macro']
    macro['dependency_group'] = 'A'
    extra = {**macro, 'source_id': 'second', 'dependency_group': 'B', 'features': {'macro.second': .1}}
    frozen['edge_family_sources']['macro'] = [macro, extra]
    frozen['edge_family_sources']['value_carry']['dependency_group'] = 'A'
    frozen['edge_family_sources']['intermarket']['dependency_group'] = 'B'
    macro_opinion = opinion('macro', {'CLOSE_10': -1.})
    macro_opinion['feature_names'].append('macro.second')
    llm = assessment(family_assessments={
        'value_carry': opinion('value_carry', {'CLOSE_10': 1.}),
        'intermarket': opinion('intermarket', {'CLOSE_10': 1.}), 'macro': macro_opinion})
    result = working(frozen, llm)
    assert result['scores']['CLOSE_10'] == pytest.approx(-.5 + .5 / 3)
    assert all(weight == pytest.approx(.5 / 3) for weight in result['attribution_weights']['CLOSE_10'].values())


def test_all_eight_working_rows_have_applied_contributions_without_extra_experts():
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    llm = assessment(policy_scores={'HOLD': 0., 'CLOSE_10': 0., 'CLOSE_25': 0.})
    audit = build_unified_ensemble(frozen_all(), llm)
    assert all(row['working_assessment_available'] for row in audit['edge_families'].values())
    assert len(audit['family_counterfactuals']) == 8
    assert len(audit['components']) == 5
    for candidate in audit['candidates']:
        current = next(row for row in candidate['component_contributions'] if row['component_id'] == 'current_llm')
        parts = candidate['working_family_contributions']
        assert len(parts) == 8
        assert sum(row['effective_weight'] for row in parts) <= current['effective_weight'] + 1e-12
        if candidate['policy'] in ('CLOSE_10', 'CLOSE_25'):
            assert sum(row['contribution'] for row in parts) == pytest.approx(current['contribution'])


def test_overridden_llm_pool_does_not_display_unused_family_contributions():
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    frozen = frozen_all()
    frozen['policy_manager']['unified_component_assessments'] = [{
        'component_id': 'current_llm', 'available': True,
        'scores': {'HOLD': 0., 'CLOSE_25': 1.}, 'evidence_family_ids': ['price_path']}]
    actual = build_unified_ensemble(frozen, assessment())
    assert not actual['family_counterfactuals']
    assert all(not row['working_assessment_available'] for row in actual['edge_families'].values())
    assert all(part['contribution'] is None for candidate in actual['candidates']
               for part in candidate['working_family_contributions'])
