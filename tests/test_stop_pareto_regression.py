from copy import deepcopy
import pytest
from seiltanzer.unified_edge_ensemble import rank_candidates
from seiltanzer.llm_shadow_working_action import build_working_action


def rows():
    return [dict(candidate_id=p, policy=p, eligible=True, expected_net_r=e,
                 cvar10_net_r=c, delta_expected_r=e-1.403, delta_cvar_r=c+.995,
                 economics_basis='common_frozen_weighted_option_driver_paths', bank_id='same')
            for p,e,c in [('HOLD',1.403,-.995),('EXIT',1.595,1.595),('TIGHTEN_STOP',1.592,1.095)]]


def test_common_bank_dominated_stop_cannot_win_from_saturated_scores():
    candidates=rows()
    # A sparse outside preference must not beat a better common-bank alternative.
    specs=[dict(component_id='quantitative_base', available=True, quality=1., scores={},
                source_ids=['bank'], evidence_family_ids=['price_path']),
           dict(component_id='current_llm',available=True,quality=1.,scores={'TIGHTEN_STOP':1.},
                source_ids=['llm'], evidence_family_ids=['option_distribution'])]
    selected, audit, _=rank_candidates(candidates,specs,{'captured_ts':100.,'policy_manager':{}},
                                      {'quantitative_base':.996,'current_llm':.004})
    assert selected['policy']=='EXIT'
    stop=next(x for x in audit if x['policy']=='TIGHTEN_STOP')
    assert stop['ranking_reason']=='COMMON_BANK_PARETO_DOMINATED'
    assert stop['dominated_by_candidate_ids']==['EXIT']


@pytest.mark.parametrize('direction,current,previous,expected',[
    ('long',4054.135,4040.41,4040.41),('short',3957.,3970.,3970.)])
def test_unconfirmed_stop_reference_is_ratchet_not_executed_geometry(direction,current,previous,expected):
    s={'strategy':{'direction':direction},'trade_geometry':{'entry':4006.,
       'original_stop':3976. if direction=='long' else 4036.,
       'active_risk_barrier':3976. if direction=='long' else 4036.,
       'current':current,'final_take':4085. if direction=='long' else 3890.},
       'pending_stop_reference':{'stop_price':previous,'broker_execution_confirmed':False}}
    frozen=deepcopy(s)
    action=build_working_action(s,{'status':'ok','policy':'TIGHTEN_STOP','confidence':.75})
    assert action['parameters']['stop_price']==expected
    assert s==frozen


def test_crossed_unconfirmed_stop_requires_reconciliation_not_looser_replacement():
    s={'strategy':{'direction':'long'},'trade_geometry':{'entry':4006.,'original_stop':3976.,
       'active_risk_barrier':3976.,'current':4039.,'final_take':4085.},
       'pending_stop_reference':{'stop_price':4040.41,'broker_execution_confirmed':False}}
    action=build_working_action(s,{'status':'ok','policy':'TIGHTEN_STOP','confidence':.75})
    assert action['status']=='NOT_ACTIONABLE'
    assert action['reason']=='PENDING_STOP_EXECUTION_RECONCILIATION_REQUIRED'


@pytest.mark.parametrize('change', ['different_bank','ineligible_exit','tradeoff','equal'])
def test_pareto_filter_does_not_invent_comparability_or_prefer_infeasible_action(change):
    candidates=rows()
    stop=candidates[-1]; exit=candidates[1]
    if change=='different_bank': exit['bank_id']='other'
    if change=='ineligible_exit': exit['eligible']=False
    if change=='tradeoff': exit['expected_net_r']=1.590
    if change=='equal': exit.update(expected_net_r=stop['expected_net_r'],cvar10_net_r=stop['cvar10_net_r'])
    selected,audit,_=rank_candidates(candidates,[],{'policy_manager':{}},{})
    assert next(x for x in audit if x['policy']=='TIGHTEN_STOP')['ranking_eligible'] is True


def test_rejected_llm_preference_does_not_advertise_ready_standalone_action():
    from test_unified_edge_ensemble import snapshot,llm
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    opinion=llm(policy='HOLD',policy_scores={'HOLD':-1.,'CLOSE_10':1.},
                working_action={'status':'READY_FOR_MANUAL_CONFIRMATION'})
    audit=build_unified_ensemble(snapshot(),opinion)
    component=next(c for c in audit['components'] if c['component_id']=='current_llm')
    assert component['effective_weight']==0.
    assert component['standalone_action_status']=='NOT_ACTIONABLE'
    assert component['standalone_action_reason']=='CURRENT_LLM_POLICY_SCORE_CONTRADICTION'


def test_final_llm_section_reports_rejected_synthesis_as_unavailable():
    from seiltanzer.ai_verdict_v19 import _final_llm_proposal_lines
    s={'llm_shadow_decision':{'status':'ok','policy':'TIGHTEN_STOP','confidence':.75,
       'working_action':{'status':'READY_FOR_MANUAL_CONFIRMATION','parameters':{'stop_price':105.}}},
       'policy_manager':{'unified_edge_ensemble':{'components':[{'component_id':'current_llm',
          'reason':'CURRENT_LLM_POLICY_SCORE_CONTRADICTION','available':False}]}}}
    text='\n'.join(_final_llm_proposal_lines(s))
    assert 'READY_FOR_MANUAL_CONFIRMATION' not in text
    assert 'CURRENT_LLM_POLICY_SCORE_CONTRADICTION' in text
