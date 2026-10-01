"""The math criterion remains visible through the management API payload."""
from copy import deepcopy
import json
import math
import time
from types import SimpleNamespace

import pytest

from seiltanzer import active_management, mathematical_edge as edge
from seiltanzer.g1_management_edge_frequency import current_edge_management_payload


def test_payload_reports_scaled_directional_math_and_gated_counterfactual():
    snapshot = {'policy_manager': {
        'active_edge_provisional_weight': {'weight_fraction': .4},
        'llm_edge_exploratory_weight': {'weight_fraction': .2},
        'mathematical_edge': {'available': True, 'instrument': 'NAS100',
            'role': 'PRICE_DIRECTION', 'base_policy_eligible': True,
            'probabilities': {'direction': .2, 'movement': .8}},
        'combined_edge_soft_weight': {'available': True, 'weight_fraction': .4,
            'direction_score': -.5, 'active_component_weight': .2,
            'exploratory_component_weight': .1, 'mathematical_component_weight': .1,
            'mathematical_extended_component_weight': .1},
        'selection_rule': {'combined_edge_soft_weight': {
            'applied': True, 'raw_policy_without_edge': 'HOLD',
            'raw_policy_without_mathematical_edge': 'HOLD',
            'raw_policy_with_edge': 'CLOSE_25'}},
        'recommendation': {'policy': 'HOLD'},
        'management_decision': {'policy': 'HOLD'},
    }}
    original = deepcopy(snapshot)
    payload = current_edge_management_payload(snapshot)
    weights = payload['weights']
    assert weights['active'] == .2 and weights['exploratory'] == .1
    assert weights['mathematical_base'] == weights['mathematical_extended'] == .1
    assert weights['combined'] == weights['combined_base'] == .4
    assert weights['combined_extended'] == .1
    audit = payload['counterfactual']
    assert audit['raw_policy_without_mathematical_edge'] == 'HOLD'
    assert audit['raw_policy_with_mathematical_edge'] == 'CLOSE_25'
    assert audit['mathematical_edge_changed_raw_policy']
    assert payload['action_now'] == 'HOLD'  # Raw preference is still gated.
    assert payload['weight_semantics']['not_expected_return']
    assert snapshot == original


def test_actual_directional_selector_changes_rank_and_exports_math_counterfactual():
    from seiltanzer import ai_policy
    from seiltanzer.active_edge_policy_weight import _PROFILE_CTX
    names = ('HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT')
    metrics = {name: dict(name=name, expected_final_r=value, cvar10_r=-.1)
               for name, value in zip(names, (.1, .103, .1075, .115, .13))}
    original = deepcopy(metrics)
    profile = dict(available=True, base_policy_eligible=True,
        weight_fraction=.072, direction_score=-.48, preferred_close_fraction=.74,
        role='PRICE_DIRECTION', instrument='NAS100')
    combined = edge.combine_math_profile({'available': False, 'weight_fraction': 0.}, profile)
    token = _PROFILE_CTX.set(combined)
    try:
        policy, rule = ai_policy._raw_policy_choice(metrics, 0., cvar_floor=-.5)
    finally:
        _PROFILE_CTX.reset(token)
    audit = rule['combined_edge_soft_weight']
    assert audit['raw_policy_without_mathematical_edge'] == 'HOLD'
    assert policy == audit['raw_policy_with_edge'] == 'CLOSE_10'
    assert metrics == original
    assert rule['best_expected_r'] == .13
    snapshot = {'policy_manager': {'mathematical_edge': profile,
        'combined_edge_soft_weight': combined, 'selection_rule': rule,
        'recommendation': {'policy': 'HOLD'}}}
    payload = current_edge_management_payload(snapshot)
    assert payload['counterfactual']['mathematical_edge_changed_raw_policy']
    assert payload['weights']['mathematical_base'] == .072
    assert payload['action_now'] == 'HOLD'


@pytest.mark.parametrize('instrument', ['NAS100', 'SP500', 'US30', 'GER40',
    'UK100', 'JPY100', 'EURUSD', 'USDCAD', 'XAU', 'XAG'])
def test_movement_only_runtime_weight_reaches_extended_selector_and_api(
    tmp_path, monkeypatch, instrument,
):
    now = time.time()
    end = math.floor(now / 300) * 300
    raw = [[end - (13-i)*300 + minute*60, 100., 101., 99., 100.]
           for i in range(13) for minute in range(5)]
    n = len(edge.FEATURES)
    head = dict(mean=[0.]*n, scale=[1.]*n,
                beta=[math.log(.2/.8)] + [0.]*n, baseline=.5)
    diagnostic = dict(gain_mbit=20., positive_blocks=3, blocks=3,
                      test_n=120, working_supported=True)
    model = dict(instrument=instrument, horizon_minutes=15,
        status='WORKING_SUPPORTED', feature_contract=edge.FEATURE_CONTRACT,
        target_contract=deepcopy(edge.TARGET_CONTRACT), features=list(edge.FEATURES),
        heads={'direction': deepcopy(head), 'movement': deepcopy(head)},
        diagnostics={'direction': {**diagnostic, 'working_supported': False},
                     'movement': diagnostic}, training_cutoff=now-3600)
    model['model_sha256'] = edge.fingerprint(model)
    from seiltanzer import runtime_git_identity
    monkeypatch.setattr(runtime_git_identity, 'runtime_git_sha', lambda: 'a'*40)
    artifact = tmp_path/'research'/'mathematical_edge_latest.json'
    artifact.parent.mkdir()
    artifact.write_text(json.dumps(dict(contract_version=edge.CONTRACT,
        created_ts=now-1, published_for_sha='a'*40, automatic_execution=False,
        production_authority=False, instruments={instrument: model})))
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path),
        market=SimpleNamespace(instrument_code=instrument, intraday_ohlcv=raw))
    profile = edge.runtime_profile(engine, {'ts': now, 'instrument': instrument},
                                  {'instrument': instrument, 'direction': 'long'})
    combined = edge.combine_math_profile({'available': False, 'weight_fraction': 0.}, profile)
    assert profile['role'] == 'LOW_MOVEMENT_TIME_MANAGEMENT'
    assert profile['available'] and not profile['base_policy_eligible']
    assert combined['mathematical_component_weight'] == 0
    assert combined['mathematical_extended_component_weight'] > 0

    snapshot = {'policy_manager': {'mathematical_edge': profile,
        'combined_edge_soft_weight': combined,
        'recommendation': {'policy': 'HOLD', 'raw_optimizer_policy': 'HOLD'},
        'management_decision': {'policy': 'HOLD'}}}
    monkeypatch.setattr(active_management, 'build_working_action',
        lambda snapshot, proposal: {'policy': proposal['policy']})
    # These are already quantified/gated counterfactual assessments. The test
    # isolates ranking, never grants a blocked candidate fresh authority.
    def assessment(snapshot, action):
        policy = action['policy']
        if policy not in {'TIME_STOP', 'TIGHTEN_STOP'}:
            return {'status': 'blocked', 'reason': 'NO_MATERIAL_ROBUST_EXPECTED_GAIN'}
        return {'status': 'eligible', 'paired_delta_ci95_lower_r': .05 if policy == 'TIME_STOP' else .051,
            'worst_seed_cvar10_gross_r': -.5, 'expected_delta_vs_hold_r': .06,
            'production_authority': True}
    monkeypatch.setattr(active_management, 'evaluate_extended_action', assessment)
    candidate = active_management.select_active_management(snapshot)
    assert candidate['policy'] == 'TIME_STOP'
    rows = {r['policy']: r for r in snapshot['active_management_candidates']}
    assert rows['TIME_STOP']['mathematical_edge_ranking_bonus_r'] > 0
    assert rows['TIGHTEN_STOP']['mathematical_edge_ranking_bonus_r'] == 0
    assert rows['TIME_STOP']['expected_delta_vs_hold_r'] == .06
    payload = current_edge_management_payload(snapshot)
    assert payload['available']
    assert payload['weights']['combined_base'] == payload['weights']['mathematical_base'] == 0
    assert payload['weights']['combined_extended'] == payload['weights']['mathematical_extended'] > 0
    assert payload['mathematical_edge']['instrument'] == instrument
    audit = payload['counterfactual']
    assert audit['raw_policy_without_mathematical_edge'] == audit['raw_policy_with_mathematical_edge'] == 'HOLD'
    assert not audit['mathematical_edge_changed_raw_policy']
    assert audit['extended_policy_without_mathematical_edge'] == 'TIGHTEN_STOP'
    assert audit['extended_policy_with_mathematical_edge'] == 'TIME_STOP'
    assert audit['mathematical_edge_changed_extended_candidate']
    assert payload['blocked_reason'] is None


def test_missing_audit_is_unavailable_and_blocked_candidate_is_excluded():
    snapshot = {'policy_manager': {
        'mathematical_edge': {'available': True, 'base_policy_eligible': True},
        'combined_edge_soft_weight': {'available': True, 'weight_fraction': .1,
            'mathematical_component_weight': .1,
            'mathematical_extended_component_weight': .1},
        'recommendation': {'policy': 'HOLD'},
    }, 'active_management_candidates': [
        {'policy': 'EXTEND_TAKE', 'status': 'blocked',
         'paired_delta_ci95_lower_r': 100., 'worst_seed_cvar10_gross_r': 100.,
         'mathematical_edge_ranking_bonus_r': 100.},
        {'policy': 'TIME_STOP', 'status': 'eligible',
         'paired_delta_ci95_lower_r': .05, 'worst_seed_cvar10_gross_r': -.5,
         'mathematical_edge_ranking_bonus_r': .001},
    ]}
    payload = current_edge_management_payload(snapshot)
    audit = payload['counterfactual']
    assert not audit['mathematical_base_comparison_available']
    assert audit['raw_policy_without_mathematical_edge'] is None
    assert audit['raw_policy_with_mathematical_edge'] is None
    assert audit['extended_policy_without_mathematical_edge'] == 'TIME_STOP'
    assert audit['extended_policy_with_mathematical_edge'] == 'TIME_STOP'
    assert not audit['mathematical_edge_changed_extended_candidate']
