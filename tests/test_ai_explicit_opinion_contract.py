"""Actual438 semantic failure and final extended decision lifecycle regressions."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from seiltanzer import ai_runtime_report_v20 as report
from seiltanzer.ai_provider_contract import provider_response_format
from test_ai_structured_provider_contract import shadow


def explicit():
    result = shadow()
    del result['policy_scores']
    result['opinion_contract'] = 'single-explicit-choice-v1'
    return result


def test_actual_schema_accepts_one_explicit_choice_without_a_redundant_vector():
    validator = Draft202012Validator(provider_response_format()['json_schema']['schema'])
    validator.validate({'shadow_decision': explicit()})
    bad = explicit(); bad['policy_scores'] = [0.] * 12
    with pytest.raises(ValidationError):
        validator.validate({'shadow_decision': bad})


def test_explicit_choice_parses_and_votes_without_inventing_other_action_scores():
    from test_unified_edge_ensemble import snapshot
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    wire = explicit(); before = deepcopy(wire)
    _, parsed = report._provider_payload(json.dumps({'shadow_decision': wire}))
    assert 'policy_scores' not in parsed
    assert parsed['opinion_contract'] == 'single-explicit-choice-v1'
    audit = build_unified_ensemble(snapshot(), {**parsed, 'status': 'ok'})
    current = next(c for c in audit['components'] if c['component_id'] == 'current_llm')
    assert current['scores'] == {'HOLD': 1.}
    assert current['effective_weight'] > 0.
    assert wire == before


def test_actual_provider_reply_keeps_categorical_audit_and_nonzero_role(monkeypatch):
    import httpx
    from seiltanzer import ai_provider_transport as transport
    from test_ai_provider_explanation import _snapshot
    from seiltanzer.decision_research import validate_no_future_timestamps
    def post(url, **kwargs):
        wire = explicit()
        Draft202012Validator(kwargs['body']['response_format']['json_schema']['schema']).validate(
            {'shadow_decision': wire})
        return httpx.Response(200, json={'choices': [{'message': {'content':
            json.dumps({'shadow_decision': wire})}}]}, request=httpx.Request('POST', url))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline-fixture')
    monkeypatch.delenv('OPENROUTER_PROXY', raising=False)
    monkeypatch.setattr(transport, 'post_json', post)
    frozen = _snapshot()
    frozen['policy_manager']['selection_rule'] = {'eligible': ['HOLD'], 'cvar_floor_r': -1.}
    llm = report.request_explanation_with_shadow(frozen, authoritative_snapshot=frozen)['llm_shadow_decision']
    assert llm['policy'] == 'HOLD'
    assert llm['opinion_contract'] == 'single-explicit-choice-v1'
    assert 'policy_scores' not in llm
    assert llm['input_contract']['all_policy_scores_required'] is False
    assert llm['input_contract']['opinion_contract'] == 'single-explicit-choice-v1'
    assert 'активный вес 0' not in report._decision_weights(frozen, llm)
    validate_no_future_timestamps({'captured_ts': frozen['captured_ts'],
        'llm_shadow_decision': llm}, frozen['captured_ts'])


@pytest.mark.parametrize('marker', [None, '', 'unknown', True])
def test_no_vector_requires_explicit_negotiated_contract(marker):
    bad = explicit()
    if marker is None:
        del bad['opinion_contract']
    else:
        bad['opinion_contract'] = marker
    with pytest.raises(RuntimeError):
        report._provider_payload(json.dumps({'shadow_decision': bad}))


@pytest.mark.parametrize('scores', [None, {}, [0.] * 12])
def test_explicit_contract_cannot_smuggle_a_second_global_opinion(scores):
    bad = explicit(); bad['policy_scores'] = scores
    with pytest.raises(RuntimeError):
        report._provider_payload(json.dumps({'shadow_decision': bad}))


def test_final_family_blend_cannot_reverse_an_explicit_hold_opinion():
    from test_edge_family_working import frozen_all, opinion
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    frozen = frozen_all()
    llm = dict(status='ok', policy='HOLD', captured_ts=frozen['captured_ts'],
        opinion_contract='single-explicit-choice-v1', family_assessments={
            'value_carry': opinion('value_carry', {'HOLD': -1., 'CLOSE_10': 1.})})
    before = deepcopy(llm)
    audit = build_unified_ensemble(frozen, llm)
    current = next(c for c in audit['components'] if c['component_id'] == 'current_llm')
    assert current['effective_weight'] == 0.
    assert current['reason'] == 'CURRENT_LLM_POLICY_SCORE_CONTRADICTION'
    assert all(c['contribution'] == 0. for row in audit['candidates']
               for c in row['component_contributions'] if c['component_id'] == 'current_llm')
    assert llm == before


def test_real_legacy438_scores_remain_rejected_without_repair():
    from test_unified_edge_ensemble import snapshot
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    wire = shadow()
    wire['policy_scores'] = [-.6508878755648053, .01, .01, .5139727288842622,
                             1., .01, .01, .01, .01, .01, .01, .01]
    before = deepcopy(wire)
    _, parsed = report._provider_payload(json.dumps({'shadow_decision': wire}))
    assert parsed['policy_scores']['HOLD'] == -.6508878755648053
    assert parsed['policy_scores']['EXIT'] == 1.
    audit = build_unified_ensemble(snapshot(), {**parsed, 'status': 'ok'})
    current = next(c for c in audit['components'] if c['component_id'] == 'current_llm')
    assert current['effective_weight'] == 0.
    assert current['reason'] == 'CURRENT_LLM_POLICY_SCORE_CONTRADICTION'
    assert wire == before


@pytest.mark.parametrize('repeat', [False, True])
def test_registered_extended_action_owns_continuity_after_temporary_hold(tmp_path, repeat):
    from seiltanzer.app import _extended_manual_decision
    from seiltanzer.position_state import PositionLedger
    from test_decision_publication import _snapshot, _trade
    from test_llm_shadow_action_execution import _shadow
    ledger, trade = PositionLedger(str(tmp_path / 'trades.db')), _trade()
    first = _snapshot(ledger, trade, 'HOLD', 2000.)
    ledger.register_decision(first, 'first', trade)
    old = ledger.register_shadow_action(first, 'first', trade,
        _shadow('MOVE_TO_BE', {'stop_price': 100.}))
    fresh = _snapshot(ledger, trade, 'HOLD', 2001.)
    base = fresh['policy_manager']['management_decision']
    base['continuity'] = 'active_ai_plan_cancelled_by_current_arbiter'
    # Replaying the same registered identity uses its original capture. A new
    # capture deliberately creates a new ID even with unchanged parameters.
    action = ledger.register_shadow_action(first if repeat else fresh, 'second', trade,
        _shadow('MOVE_TO_BE' if repeat else 'TIGHTEN_STOP',
                {'stop_price': 100. if repeat else 102.}))
    result = _extended_manual_decision(base, action)
    assert result['previous']['decision_id'] == old['action_id']
    assert result['continuity'] == ('continue_same_pending_decision' if repeat else
                                    'unified_action_replaces_previous_plan')
    assert result['decision_id'] == action['action_id']
    assert ledger.state(trade)['active_stop_price'] == 90.
    ledger.close()
