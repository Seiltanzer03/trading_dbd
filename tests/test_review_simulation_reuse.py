"""A single review may reuse drivers, never an earlier risk admission."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
import json
import time

import httpx
import pytest

from seiltanzer import extended_policy_evaluation as evaluation
from seiltanzer import ai_provider_guard as guard
from seiltanzer import ai_provider_transport as transport
from seiltanzer import ai_runtime_report_v20 as report
from seiltanzer.llm_decision_shadow import VALID_POLICIES, finalize_extended_shadow
from test_extended_policy_evaluation import _snapshot, _action


def scope():
    # Old code must fail on actual repeated simulations, not a missing import.
    return getattr(evaluation, 'review_evaluation_scope', nullcontext)()


def observed_simulator(monkeypatch, delay=0.):
    calls = []
    original = evaluation.simulate_option_paths

    def simulate(inputs, **kwargs):
        calls.append((inputs, kwargs['seed']))
        if delay:
            time.sleep(delay)
        return original(inputs, **kwargs)

    monkeypatch.setattr(evaluation, 'simulate_option_paths', simulate)
    return calls


def test_same_shadow_action_reuses_real_drivers_without_changing_risk_result(monkeypatch):
    snapshot = _snapshot()
    before = deepcopy(snapshot)
    calls = observed_simulator(monkeypatch)
    with scope():
        expected = evaluation.evaluate_extended_action(snapshot, _action())
        shadow = dict(policy='MOVE_TO_BE', status='ok', confidence=.8,
                      blocked_by_hard_guard=False, working_action=_action())
        finalize_extended_shadow(snapshot, shadow)
    assert shadow['quant_evaluation'] == expected
    assert len(calls) == 4, 'the LLM must not replay an already evaluated action'
    assert snapshot == before


@pytest.mark.parametrize('policy,parameters', [
    ('TIME_STOP', dict(deadline_ts=1_900_007_200.)),
    ('SCALE_OUT_ON_SPIKE', dict(trigger_price=117.5, target_r=1.75, close_fraction=.25)),
])
def test_conditional_replays_are_reused_with_all_execution_parameters(monkeypatch, policy, parameters):
    snapshot = _snapshot()
    snapshot['captured_ts'] = 1_900_000_000.
    calls = []
    original = evaluation._conditional_outcomes

    def observed(*args):
        calls.append(args[-1])
        return original(*args)

    monkeypatch.setattr(evaluation, '_conditional_outcomes', observed)
    with scope():
        first = evaluation.evaluate_extended_action(snapshot, _action(policy, **parameters))
        assert evaluation.evaluate_extended_action(snapshot, _action(policy, **parameters)) == first
    assert len(calls) == 2


def test_costs_and_hard_floor_are_rechecked_after_reusing_drivers(monkeypatch):
    snapshot = _snapshot()
    calls = observed_simulator(monkeypatch)
    with scope():
        first = evaluation.evaluate_extended_action(snapshot, _action())
        snapshot['policy_manager']['execution_cost_model']['deferred_full_close_r'] = .02
        repriced = evaluation.evaluate_extended_action(snapshot, _action())
        assert repriced['expected_variant_net_r'] == pytest.approx(first['expected_variant_net_r']-.01)
        assert repriced['worst_seed_cvar10_net_r'] == pytest.approx(first['worst_seed_cvar10_net_r']-.01)
        snapshot['policy_manager']['risk_constraint']['gross_cvar_floor_r'] = 10.
        rejected = evaluation.evaluate_extended_action(snapshot, _action())
        assert rejected['reason'] == 'VARIANT_CVAR_BELOW_HARD_FLOOR'
        assert rejected['production_authority'] is False
    assert len(calls) == 4


def test_source_loss_still_blocks_before_any_cached_result(monkeypatch):
    snapshot = _snapshot()
    calls = observed_simulator(monkeypatch)
    with scope():
        evaluation.evaluate_extended_action(snapshot, _action())
        snapshot['policy_manager']['input_audit']['rows']['instrument_price']['available'] = False
        result = evaluation.evaluate_extended_action(snapshot, _action())
    assert result['reason'] == 'AUTHORITATIVE_INSTRUMENT_PRICE_UNAVAILABLE'
    assert result['production_authority'] is False
    assert len(calls) == 4


def test_changed_model_inputs_and_targets_do_not_reuse_old_paths(monkeypatch):
    snapshot = _snapshot()
    calls = observed_simulator(monkeypatch)
    with scope():
        evaluation.evaluate_extended_action(snapshot, _action())
        evaluation.evaluate_extended_action(snapshot, _action('TIGHTEN_STOP', stop_price=105.))
        assert len(calls) == 6  # HOLD shared; the changed stop needs new paths.
        snapshot['policy_manager']['inputs']['drift_R'] = .2
        evaluation.evaluate_extended_action(snapshot, _action())
        assert len(calls) == 10


def test_new_review_never_consumes_previous_review_paths(monkeypatch):
    snapshot = _snapshot()
    calls = observed_simulator(monkeypatch)
    for _ in range(2):
        with scope():
            evaluation.evaluate_extended_action(snapshot, _action())
            evaluation.evaluate_extended_action(snapshot, _action())
    assert len(calls) == 8


def test_valid_provider_response_survives_its_budget_without_duplicate_replay(monkeypatch):
    from test_ai_provider_explanation import _snapshot as provider_snapshot
    snapshot = _snapshot()
    snapshot.update(captured_ts=1234., trade_id=7, strategy={'direction': 'long'})
    snapshot['policy_manager']['policies'] = provider_snapshot()['policy_manager']['policies']
    content = json.dumps({'shadow_decision': dict(policy='MOVE_TO_BE', confidence=.8,
        policy_scores={p: float(p == 'MOVE_TO_BE') for p in VALID_POLICIES},
        reason_ru='Оценка риска.', key_evidence=[], counter_evidence=[],
        evidence_families=[], invalidation_conditions=[], family_assessments=[])})
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline-fixture')
    monkeypatch.setattr(transport, 'post_json', lambda url, **kwargs: httpx.Response(
        200, json={'model': 'offline-model', 'choices': [{'message': {'content': content}}]},
        request=httpx.Request('POST', url)))
    calls = observed_simulator(monkeypatch, delay=.04)
    with ThreadPoolExecutor(max_workers=1) as pool, scope():
        expected = evaluation.evaluate_extended_action(snapshot, _action())
        result = guard.bounded_provider_call(lambda value: report.request_explanation_with_shadow(
            value, authoritative_snapshot=value), snapshot, executor=pool, timeout_sec=.10)
    assert result['model'] == 'offline-model'
    assert result['llm_shadow_decision']['quant_evaluation'] == expected
    assert len(calls) == 4
    assert guard.DEFAULT_PROVIDER_TIMEOUT_SEC == 6.


def test_actual_api_propagates_one_scope_through_both_worker_pools(tmp_path, monkeypatch):
    from seiltanzer import active_management
    from test_ai_verdict_api import _client
    snapshot, action = _snapshot(), _action()
    calls = observed_simulator(monkeypatch)
    original = active_management.select_active_management
    expected = []

    def before_provider(value):
        expected.append(evaluation.evaluate_extended_action(snapshot, action))
        return original(value)

    def provider(_):
        with ThreadPoolExecutor(max_workers=1) as pool:
            actual = guard.bounded_provider_call(lambda value:
                evaluation.evaluate_extended_action(value, action), snapshot, executor=pool)
        assert actual == expected[0]
        return dict(verdict='validated local review', model='offline-fixture')

    monkeypatch.setattr(active_management, 'select_active_management', before_provider)
    app, client = _client(tmp_path, monkeypatch, provider)
    try:
        response = client.post('/api/ai/verdict')
        assert response.status_code == 200, response.text
        assert response.json()['mode'] == 'llm'
        assert len(calls) == 4
    finally:
        app.state.engine.close()
