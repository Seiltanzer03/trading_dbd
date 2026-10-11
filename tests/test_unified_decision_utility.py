"""Decision behavior, not forecast/profit claims, on explicit frozen paths."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from seiltanzer.unified_edge_ensemble import (
    COMPONENTS, SCHEMES, build_unified_ensemble, candidate_id, rank_candidates,
)
from seiltanzer import extended_policy_evaluation as extended
from test_extended_policy_evaluation import _snapshot, _action
from test_unified_candidate_economics import snapshot as bank_snapshot


def protective_snapshot():
    # One percent adverse paths: 0.018R Expected benefit, 0.18R tail gain.
    value = bank_snapshot([[1., .7, -1.]] * 10 + [[1., 1.2, 2.]] * 990, [.001] * 1000)
    value['strategy'] = {'instrument': 'NAS100'}
    value['policy_manager']['policies'] = {'HOLD': {
        'expected_final_r': 1.95, 'cvar10_r': 1.68,
        'outcomes_include_execution_costs': True}}
    value['active_management_candidates'] = [{
        'policy': 'TIGHTEN_STOP', 'parameters': {'stop_price': 108.},
        'status': 'eligible', 'expected_variant_net_r': 1.97,
        'expected_delta_vs_hold_r': .018, 'worst_seed_cvar10_net_r': 1.86,
        'worst_seed_hold_cvar10_net_r': 1.68, 'execution_cost_r': .02}]
    return value


def rows(delta=.002, tail=.22, policy='TIGHTEN_STOP'):
    return [{'candidate_id': 'HOLD', 'policy': 'HOLD', 'parameters': {},
             'eligible': True, 'expected_net_r': .771, 'cvar10_net_r': -.01,
             'delta_expected_r': 0., 'delta_cvar_r': 0.},
            {'candidate_id': policy, 'policy': policy, 'parameters': {},
             'eligible': True, 'expected_net_r': .771 + delta,
             'cvar10_net_r': -.01 + tail, 'delta_expected_r': delta,
             'delta_cvar_r': tail}]


def opinions(scores=None):
    return [{'component_id': identity, 'available': True, 'quality': 1.,
             'observed_ts': 100., 'max_age_sec': 900.,
             'evidence_family_ids': [identity], 'source_ids': [identity],
             'scores': (scores or {}).get(identity, {})} for identity in COMPONENTS]


def context():
    return {'captured_ts': 100., 'strategy': {'instrument': 'NAS100'},
            'policy_manager': {'selection_rule': {'indifference_band_r': .03}}}


def test_common_bank_admits_and_selects_protective_stop_with_small_expected_gain():
    value = protective_snapshot()
    before = deepcopy(value)
    audit = build_unified_ensemble(value, scheme='quant100')
    stop = next(row for row in audit['candidates'] if row['policy'] == 'TIGHTEN_STOP')
    assert stop['eligible'] is True, stop
    assert stop['benefit_assessment']['basis'] == 'BOUNDED_TAIL_PROTECTION'
    assert stop['quant_utility']['tail_credit_r'] == pytest.approx(.03)
    assert audit['selected_policy'] == 'TIGHTEN_STOP'
    assert value == before


def test_independent_extended_gate_accepts_bounded_protection(monkeypatch):
    def simulate(inputs, **kwargs):
        # Same paired samples: small economic loss, materially better tail.
        values = np.r_[np.full(120, -.5 if inputs.stop_r < 0 else -.2),
                       np.full(1080, .5 if inputs.stop_r < 0 else .45)]
        return SimpleNamespace(strategy_outcome=values)
    monkeypatch.setattr(extended, 'simulate_option_paths', simulate)
    row = extended.evaluate_extended_action(_snapshot(), _action())
    assert row['status'] == 'eligible', row
    assert row['benefit_assessment']['basis'] == 'BOUNDED_TAIL_PROTECTION'
    assert row['production_authority'] is True
    assert row['automatic_execution_allowed'] is False


@pytest.mark.parametrize('policy', ['MOVE_TO_BE', 'TIGHTEN_STOP', 'TRAIL_GAMMA_FLIP',
    'REDUCE_TAKE', 'TIME_STOP', 'SCALE_OUT_ON_SPIKE', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT'])
def test_quant_protective_utility_can_pay_small_expected_loss(policy):
    chosen, ranked, _ = rank_candidates(rows(-.015, .18, policy), opinions(), context(), SCHEMES['quant100'])
    assert chosen['policy'] == policy
    assert ranked[1]['quant_utility']['expected_delta_r'] == pytest.approx(-.015)
    assert ranked[1]['quant_utility']['tail_credit_r'] == pytest.approx(.03)
    assert ranked[1]['expected_net_r'] == pytest.approx(.756)


def test_extending_take_cannot_buy_admission_using_tail_gain():
    chosen, ranked, _ = rank_candidates(rows(.002, .22, 'EXTEND_TAKE'), opinions(), context(), SCHEMES['quant100'])
    assert chosen['policy'] == 'HOLD'
    assert ranked[1]['ranking_eligible'] is False


def test_uncertain_or_excessive_expected_loss_cannot_buy_tail_protection():
    for delta, lower in [(-.04, -.04), (-.015, -.04)]:
        candidates = rows(delta, 2.)
        candidates[1]['paired_delta_ci95_lower_r'] = lower
        chosen, ranked, _ = rank_candidates(candidates, opinions(), context(), SCHEMES['quant100'])
        assert chosen['policy'] == 'HOLD'
        assert ranked[1]['ranking_eligible'] is False


def test_quant_scale_does_not_depend_on_irrelevant_extreme_candidates():
    candidates = rows(.04, 0., 'CLOSE_10')
    _, first, _ = rank_candidates(candidates, opinions(), context(), SCHEMES['quant100'])
    irrelevant = {**rows(-10., 0., 'EXIT')[1]}
    _, second, _ = rank_candidates(candidates + [irrelevant], opinions(), context(), SCHEMES['quant100'])
    assert second[1]['score'] == first[1]['score']


@pytest.mark.parametrize('identity', COMPONENTS[1:])
def test_every_available_expert_can_change_a_close_comparison_at_its_nominal_weight(identity):
    candidates = rows(.04, 0., 'CLOSE_10') + [rows(.045, 0., 'CLOSE_25')[1]]
    specs = opinions({identity: {'HOLD': .4, 'CLOSE_10': .9, 'CLOSE_25': -.1}})
    chosen, ranked, components = rank_candidates(candidates, specs, context(), SCHEMES['balanced'])
    assert chosen['policy'] == 'CLOSE_10'
    assert [item['effective_weight'] for item in components] == pytest.approx([.4, .15, .15, .15, .15])
    without, _, _ = rank_candidates(candidates, specs, context(), SCHEMES['balanced'], identity)
    assert without['policy'] == 'CLOSE_25'
    assert sum(item['effective_weight'] for item in ranked[1]['component_contributions']) == pytest.approx(1.)
    shifted = opinions({identity: {'HOLD': 0., 'CLOSE_10': .5, 'CLOSE_25': -.5}})
    _, shifted_rows, _ = rank_candidates(candidates, shifted, context(), SCHEMES['balanced'])
    assert [row['score'] for row in ranked] == pytest.approx([row['score'] for row in shifted_rows])


def test_hold_retained_when_weighted_advantage_is_only_score_noise():
    candidates = rows(.04, 0., 'CLOSE_10')
    specs = opinions({'current_llm': {'HOLD': .94, 'CLOSE_10': 1.}})
    nominal = {identity: float(identity == 'current_llm') for identity in COMPONENTS}
    chosen, _, _ = rank_candidates(candidates, specs, context(), nominal)
    assert chosen['policy'] == 'CLOSE_10'  # 0.06 is material in score units.
    specs = opinions({'current_llm': {'HOLD': .995, 'CLOSE_10': 1.}})
    chosen, _, _ = rank_candidates(candidates, specs, context(), nominal)
    assert chosen['policy'] == 'HOLD'


def test_common_protection_never_resurrects_unavailable_authoritative_price():
    value = protective_snapshot()
    value['policy_manager']['input_audit']['rows']['instrument_price']['production_authority'] = False
    audit = build_unified_ensemble(value, scheme='quant100')
    stop = next(row for row in audit['candidates'] if row['policy'] == 'TIGHTEN_STOP')
    assert stop['eligible'] is False
    assert audit['selected_policy'] == 'HOLD'


def test_family_attribution_matches_actual_hold_relative_component_contribution():
    from seiltanzer.unified_edge_ensemble import _attach_working_family_contributions
    candidates = rows(.04, 0., 'CLOSE_10')
    specs = opinions({'current_llm': {'HOLD': .4, 'CLOSE_10': .9}})
    specs[-1].update(working_family_attributions={'HOLD': {'macro': .4}, 'CLOSE_10': {'macro': .9}},
                    working_family_weights={'HOLD': {'macro': 1.}, 'CLOSE_10': {'macro': 1.}})
    chosen, ranked, components = rank_candidates(candidates, specs, context(), SCHEMES['balanced'])
    families = {'macro': {'working_policy_scores': {'HOLD': .4, 'CLOSE_10': .9}}}
    _attach_working_family_contributions(ranked, components, families, chosen)
    actual = next(item for item in ranked[1]['component_contributions'] if item['component_id'] == 'current_llm')
    assert families['macro']['working_contribution'] == pytest.approx(actual['contribution'])
    assert ranked[1]['working_family_contributions'][0]['contribution'] == pytest.approx(.075)


def test_compact_audit_and_report_preserve_utility_and_selection_margin():
    from seiltanzer.unified_edge_audit import compact_unified_ensemble, render_unified_ensemble_lines
    audit = build_unified_ensemble(protective_snapshot(), scheme='quant100')
    compact = compact_unified_ensemble(audit)
    assert compact['decision_utility']['hold_score_margin'] == .01
    chosen = next(row for row in compact['candidates'] if row['candidate_id'] == compact['selected_candidate_id'])
    assert chosen['quant_utility']['tail_credit_r'] == pytest.approx(.03)
    assert chosen['benefit_assessment']['basis'] == 'BOUNDED_TAIL_PROTECTION'
    text = '\n'.join(render_unified_ensemble_lines(audit))
    assert 'Защитная польза' in text
    assert 'порог смены решения' in text


def test_independent_gate_does_not_confuse_worst_absolute_tail_with_matched_gain(monkeypatch):
    def simulate(inputs, *, seed, **kwargs):
        # Each absolute worst seed improves, but one matched seed deteriorates.
        values = (-.5 if seed == 0xA17E else .5) if inputs.stop_r < 0 else (-.4 if seed == 0xA17E else .4)
        return SimpleNamespace(strategy_outcome=np.full(1200, values))
    monkeypatch.setattr(extended, 'simulate_option_paths', simulate)
    row = extended.evaluate_extended_action(_snapshot(), _action())
    assert row['status'] == 'blocked'
    assert row['matched_seed_tail_gain_lower_r'] == pytest.approx(-.1)


def test_utility_cannot_override_hard_risk_even_with_strong_protection():
    value = protective_snapshot()
    value['policy_manager']['risk_constraint']['net_cvar_floor_r'] = 1.9
    audit = build_unified_ensemble(value, scheme='quant100')
    stop = next(row for row in audit['candidates'] if row['policy'] == 'TIGHTEN_STOP')
    assert stop['eligible'] is False
    assert audit['selected_policy'] != 'TIGHTEN_STOP'


def test_explicit_sparse_hold_opinion_is_not_erased_by_relative_normalization():
    candidates = rows(-.006, .05)
    without, _, _ = rank_candidates(candidates, opinions(), context(), SCHEMES['balanced'])
    assert without['policy'] == 'TIGHTEN_STOP'
    chosen, ranked, _ = rank_candidates(candidates, opinions({'current_llm': {'HOLD': 1.}}), context(), SCHEMES['balanced'])
    assert chosen['policy'] == 'HOLD'
    current = next(item for item in ranked[0]['component_contributions'] if item['component_id'] == 'current_llm')
    assert current['score'] == 1.
    assert current['hold_reference_score'] is None
    missing = next(item for item in ranked[1]['component_contributions'] if item['component_id'] == 'current_llm')
    assert missing['score'] is None


def test_independent_fallback_uses_matched_tail_and_keeps_paired_bounds():
    from test_unified_edge_ensemble import snapshot
    value = snapshot()
    manager = value['policy_manager']
    manager['selection_rule']['eligible'] = ['HOLD']
    manager['gate'] = {}
    value['active_management_candidates'] = [{
        'policy': 'TIGHTEN_STOP', 'parameters': {'stop_price': 19999.},
        'status': 'eligible', 'expected_variant_net_r': .075,
        'expected_delta_vs_hold_r': -.025, 'paired_delta_ci95_lower_r': -.029,
        'matched_seed_tail_gain_lower_r': .05,
        'worst_seed_cvar10_net_r': -.1, 'worst_seed_hold_cvar10_net_r': -.2,
        'execution_cost_r': .01}]
    audit = build_unified_ensemble(value, scheme='quant100')
    stop = next(row for row in audit['candidates'] if row['policy'] == 'TIGHTEN_STOP')
    assert stop['eligible'] is True
    assert stop['benefit_assessment']['expected_lower_r'] == pytest.approx(-.029)
    assert stop['quant_utility']['tail_delta_r'] == pytest.approx(.05)
    assert stop['quant_utility']['utility_delta_r'] == pytest.approx(-.0075)
    assert audit['selected_policy'] == 'HOLD'


def test_independent_llm_input_retains_protective_admission_basis():
    from seiltanzer.ai_runtime_report_v20 import _independent_provider_input
    value = protective_snapshot()
    row = value['active_management_candidates'][0]
    row.update(matched_seed_tail_gain_lower_r=.18,
               benefit_assessment={'basis': 'BOUNDED_TAIL_PROTECTION', 'allowed': True})
    projected = _independent_provider_input(value, value)
    kept = projected['active_management_candidates'][0]
    assert kept['matched_seed_tail_gain_lower_r'] == .18
    assert kept['benefit_assessment']['basis'] == 'BOUNDED_TAIL_PROTECTION'
