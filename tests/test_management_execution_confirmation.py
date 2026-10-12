from copy import deepcopy

import numpy as np
import pytest

from seiltanzer.position_state import PositionLedger
from seiltanzer.ai_policy_base import extract_policy_inputs, simulate_option_paths
from seiltanzer.ai_report_semantics_guard import execution_barrier_reached
from seiltanzer.unified_edge_ensemble import collect_candidates, rank_candidates, SCHEMES


def trade(direction='long'):
    return dict(id=1, opened_at=1000., entry=100., stop=90. if direction=='long' else 110.,
                take=125. if direction=='long' else 75., direction=direction, max_r=1.7, status='open')


def frozen(row, state, price):
    return dict(captured_ts=2000., trade_id=1, strategy={'instrument':'NAS100'},
                position_state=state, trade_geometry=dict(entry=row['entry'], original_stop=row['stop'],
                current=price, current_r=(price-row['entry'])/(row['entry']-row['stop']),
                active_risk_barrier=state['active_stop_price'], final_take=row['take']),
                policy_manager={'input_audit':{'rows':{'instrument_price':{
                    'available':True,'production_authority':True,'status':'live','source':'TradingView stream OANDA:NAS100USD'}}}})


@pytest.mark.parametrize('direction,price',[('long',98.),('short',102.)])
def test_strategy_be_plan_does_not_record_broker_execution_or_block_review(tmp_path,direction,price):
    row=trade(direction); ledger=PositionLedger(str(tmp_path/'position.db'))
    state=ledger.sync_be(row)
    assert state['active_stop_price']==row['stop']
    assert state['be_armed'] is False
    assert state['strategy_be_armed'] is True
    assert state['be_confirmation_required'] is True
    assert state['strategy_stop_price']==row['entry']
    assert execution_barrier_reached(frozen(row,state,price)) is None
    assert [e['event_type'] for e in ledger.events(1)]==['TRADE_OPEN','BE_ARM']
    ledger.close()


@pytest.mark.parametrize('direction,price',[('long',89.),('short',111.)])
def test_original_recorded_stop_still_blocks_after_unconfirmed_be(tmp_path,direction,price):
    row=trade(direction); ledger=PositionLedger(str(tmp_path/'position.db'))
    state=ledger.sync_be(row)
    barrier=execution_barrier_reached(frozen(row,state,price))
    assert barrier and barrier['kind']=='stop'
    assert barrier['barrier_r']==-1.
    ledger.close()


def test_confirmed_manual_be_becomes_actual_stop_and_blocks_crossing(tmp_path):
    row=trade(); ledger=PositionLedger(str(tmp_path/'position.db'));ledger.sync_be(row)
    ledger._event(trade=row,event_type='AI_MOVE_TO_BE',source='human_confirmed_ai_extended',
                  before=1.,closed=0.,after=1.,active_stop=100.,metadata={'broker_confirmed':True})
    state=ledger.state(row)
    assert state['active_stop_price']==100.
    assert state['be_armed'] is True
    assert state['be_confirmation_required'] is False
    assert execution_barrier_reached(frozen(row,state,98.))['kind']=='stop'
    ledger.close()


def test_policy_simulation_does_not_invent_be_floor_from_past_price_peak(tmp_path):
    row=trade();ledger=PositionLedger(str(tmp_path/'position.db'));state=ledger.sync_be(row)
    tick=dict(trade={**row,'position_state':state},prob={'r':-.2,'T':2.5,'sigma_R':.8,'available':True},
              market={'available':True},ladder={'max_r':1.7,'be_after':1.5},
              cone={'sigma_R':.8,'drift_R':-1.})
    inputs=extract_policy_inputs(tick)
    assert inputs.max_r==1.7
    assert inputs.stop_r==-1.
    assert inputs.initial_be_armed is False
    assert inputs.as_dict()['be_execution_confirmed'] is False
    sim=simulate_option_paths(inputs,n_paths=200,n_steps=40,seed=42)
    assert np.min(sim.strategy_outcome)<-.05
    ledger.close()


def specs(captured=2000.):
    return [dict(component_id=k,available=k in {'quantitative_base','current_llm'}, quality=1.,
                 observed_ts=captured,max_age_sec=900.,source_ids=[k],evidence_family_ids=[k],
                 scores={'HOLD':0.,'CLOSE_25':1.}) for k in SCHEMES['balanced']]


def candidates():
    return [dict(candidate_id=p,policy=p,parameters={},eligible=True,
                 expected_net_r=e,delta_expected_r=e-.1,delta_cvar_r=.1 if p!='HOLD' else 0.)
            for p,e in [('HOLD',.1),('CLOSE_25',.15)]]


def test_unused_expert_budget_is_shared_between_available_experts():
    _,rows,components=rank_candidates(candidates(),specs(),{'captured_ts':2000.},SCHEMES['balanced'])
    by_id={c['component_id']:c for c in components}
    assert by_id['current_llm']['effective_weight']==pytest.approx(.15/.55)
    assert by_id['quantitative_base']['effective_weight']==pytest.approx(.40/.55)
    assert sum(c['effective_weight'] for c in components)==pytest.approx(1.)
    assert all(c['effective_weight']==0. for c in components if not c['available'])


def test_conditional_actions_preserve_unconfirmed_be_execution_state(tmp_path):
    from seiltanzer.extended_policy_evaluation import _conditional_outcomes
    from dataclasses import replace
    row=trade();ledger=PositionLedger(str(tmp_path/'position.db'));state=ledger.sync_be(row)
    tick=dict(trade={**row,'position_state':state},prob={'r':.2,'T':2.5,'available':True},
              market={'available':True},ladder={'max_r':1.7,'be_after':1.5},cone={'sigma_R':.08,'drift_R':-.7})
    inputs=replace(extract_policy_inputs(tick),sigma_R=.01,horizon_minutes=10.)
    base,variant=_conditional_outcomes(inputs,{'deadline_ts':2300.},'TIME_STOP',2000.,42)
    assert np.mean(base)<-.3
    assert np.mean(variant)<-.1
    ledger.close()


def test_missing_action_opinion_is_shared_by_other_available_opinions():
    s=specs();s[1].update(available=True,scores={'HOLD':0.,'CLOSE_25':.7})
    s[-1]['scores']={'HOLD':1.}
    _,rows,_=rank_candidates(candidates(),s,{'captured_ts':2000.},SCHEMES['balanced'])
    c=next(x for x in rows if x['policy']=='CLOSE_25')['component_contributions']; by_id={x['component_id']:x for x in c}
    assert by_id['current_llm']['score'] is None
    assert by_id['mathematical_edge']['effective_weight']==pytest.approx(.15/.55)
    assert by_id['quantitative_base']['effective_weight']==pytest.approx(.40/.55)


def test_complete_manual_candidate_preflight_does_not_require_legacy_winner_confirmation():
    row=trade();state={'remaining_position_fraction':1.,'active_stop_price':90.}
    s=frozen(row,state,110.);m=s['policy_manager']
    m.update(policies={p:{'expected_final_r':e,'cvar10_r':-.2,'outcomes_include_execution_costs':True}
             for p,e in [('HOLD',.1),('CLOSE_10',.15),('CLOSE_25',.16),('CLOSE_50',.17),('EXIT',.18)]},
             risk_constraint={'net_cvar_floor_r':-.4},selection_rule={'eligible':['HOLD','CLOSE_10','CLOSE_25','CLOSE_50','EXIT']},
             gate={'working_action_confirmed':False},management_decision={'automatic_execution_allowed':False})
    assert all(c['eligible'] for c in collect_candidates(s) if c['policy'] in {'CLOSE_10','CLOSE_25','CLOSE_50','EXIT'})
    bad=deepcopy(s);bad['policy_manager']['policies']['CLOSE_25']['cvar10_r']=-.8
    assert not next(c for c in collect_candidates(bad) if c['policy']=='CLOSE_25')['eligible']


@pytest.mark.parametrize('policy,params',[('REDUCE_TAKE',{'take_price':112.}),
                                        ('SCALE_OUT_ON_SPIKE',{'trigger_price':111.,'close_fraction':.25})])
def test_new_forward_action_is_not_already_executed_by_past_price_peak(policy,params):
    from seiltanzer.extended_policy_evaluation import evaluate_extended_action
    from test_extended_policy_evaluation import _snapshot,_action
    s=_snapshot();s['captured_ts']=2000.;s['trade_geometry']['current']=105.
    s['policy_manager']['inputs'].update(r0=.5,max_r=1.7,be_execution_confirmed=False)
    result=evaluate_extended_action(s,_action(policy,**params))
    assert result['reason'] not in {'TAKE_ALREADY_CROSSED_DURING_TRADE','SPIKE_TRIGGER_ALREADY_CROSSED_OR_OUTSIDE_TAKE'}
    assert result.get('method') is not None


@pytest.mark.parametrize('policy,params',[('REDUCE_TAKE',{'take_price':112.}),
                                        ('SCALE_OUT_ON_SPIKE',{'trigger_price':111.,'close_fraction':.25})])
def test_common_forward_replay_uses_current_quote_not_past_peak(policy,params):
    from seiltanzer.unified_candidate_economics import _variant
    from seiltanzer.ai_policy_base import PolicyInputs
    from seiltanzer.execution_simulator import ExecutionSpec
    from test_extended_policy_evaluation import _snapshot
    s=_snapshot();d=s['policy_manager']['inputs'];d.update(r0=.5,max_r=1.7,be_execution_confirmed=False)
    inputs=PolicyInputs(**{**d,'rungs':tuple(d['rungs'])})
    spec=ExecutionSpec.from_values(current_r=.5,max_r=1.7,take_r=3.,rungs=inputs.rungs,
                                    rung_fraction_original=.1,be_after_r=1.5,be_execution_confirmed=False)
    result=_variant(spec,{'policy':policy,'parameters':params},s,inputs)
    assert result.take_r==pytest.approx(1.2) if policy=='REDUCE_TAKE' else result.spike_r==pytest.approx(1.1)


def test_pending_be_above_trigger_requires_new_upward_crossing_in_mc_and_replay(tmp_path):
    from dataclasses import replace
    from seiltanzer.execution_simulator import ExecutionSpec, replay_execution_path
    row = trade()
    ledger = PositionLedger(str(tmp_path / 'position.db'))
    state = ledger.sync_be(row)
    tick = dict(trade={**row, 'position_state': state},
                prob={'r': 1.7, 'T': 3., 'available': True},
                market={'available': True}, ladder={'max_r': 1.7, 'be_after': 1.5},
                cone={'sigma_R': .000001, 'drift_R': -2.})
    inputs = replace(extract_policy_inputs(tick), rungs=(), sigma_R=.000001,
                     drift_R=-2., be_execution_confirmed=False)
    sim = simulate_option_paths(inputs, n_paths=20, n_steps=40, seed=42)
    spec = ExecutionSpec.from_values(current_r=1.7, max_r=1.7, take_r=3.,
        rungs=(), rung_fraction_original=.1, be_after_r=1.5, stop_r=-1.,
        be_execution_confirmed=False)
    descent = replay_execution_path([1.7, 1.6, 1.4, -.3], spec)
    assert descent.be_armed is False
    assert descent.outcome_r == pytest.approx(-.3)
    assert np.mean(sim.strategy_outcome) == pytest.approx(-.3, abs=.0001)
    recross = replay_execution_path([1.7, 1.4, 1.6, -.3], spec)
    assert recross.be_armed is True
    assert recross.outcome_r == pytest.approx(0.)
    upward = replace(inputs, r0=1.4, drift_R=.3)
    forward = simulate_option_paths(upward, n_paths=20, n_steps=40, seed=42)
    assert np.all(np.isfinite(forward.be_arm_time))
    ledger.close()


@pytest.mark.parametrize('option_available', [False, True])
def test_new_manual_admission_requires_complete_common_economics(option_available):
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    row = trade()
    snapshot = frozen(row, {'remaining_position_fraction': 1., 'active_stop_price': 90.}, 110.)
    snapshot['policy_manager'].update(
        inputs={'option_available': option_available},
        policies={p: {'expected_final_r': e, 'cvar10_r': -.2,
                      'outcomes_include_execution_costs': True}
                  for p, e in [('HOLD', .1), ('CLOSE_10', .15), ('CLOSE_25', .16),
                               ('CLOSE_50', .17), ('EXIT', .18)]},
        risk_constraint={'net_cvar_floor_r': -.4},
        selection_rule={'eligible': ['HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT']},
        gate={'working_action_confirmed': False},
        management_decision={'automatic_execution_allowed': False})
    result = build_unified_ensemble(snapshot)
    assert result['selected_policy'] == 'HOLD'
    for candidate in result['candidates']:
        if candidate['policy'] in {'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT'}:
            assert candidate['eligible'] is False
            assert candidate['reason'].startswith('MANUAL_COMPARISON_COMMON_ECONOMICS_REQUIRED:')
