from copy import deepcopy

import pytest

from seiltanzer.unified_candidate_economics import price_unified_candidates
from seiltanzer.unified_edge_ensemble import rank_candidates
from test_unified_candidate_economics import snapshot, candidate


def noisy_close_comparison():
    # Each seed: half stop at -1R, half finish at1.388R. Gross mean .194R;
    # .01R exit costs give HOLD .184R and immediate exit .195R.
    paths=[[.205,-1.]]*600+[[.205,1.388]]*600
    value=snapshot(paths,[1/1200]*1200)
    value['policy_manager']['unified_scenario_bank']['seed_ids']=[0]*300+[1]*300+[0]*300+[1]*300
    value['policy_manager']['inputs'].update(r0=.205,max_r=.205,stop_r=-1.,rungs=[],be_after=10.)
    value['trade_geometry']['current']=102.05
    value['policy_manager']['execution_cost_model'].update(immediate_full_close_r=.01,deferred_full_close_r=.01)
    value['policy_manager']['risk_constraint']['net_cvar_floor_r']=-1.01
    rows=[candidate(p) for p in ('HOLD','CLOSE_10','CLOSE_25')]
    priced=price_unified_candidates(value,rows)
    for row in rows:row.update(priced['candidates'][row['candidate_id']])
    return value,rows


def test_uncertain_point_gain_cannot_remove_the_less_intrusive_close():
    value,rows=noisy_close_comparison()
    assert rows[0]['expected_net_r']==pytest.approx(.184)
    assert rows[1]['expected_net_r']==pytest.approx(.1851)
    assert rows[2]['expected_net_r']==pytest.approx(.18675)
    assert rows[2]['cvar10_net_r']>rows[1]['cvar10_net_r']
    original=deepcopy(rows)
    specs=[dict(component_id='quantitative_base',available=True,quality=1.,scores={},
                source_ids=['bank'],evidence_family_ids=['price_path']),
           dict(component_id='historical_llm',available=True,quality=1.,
                scores={'HOLD':.2,'CLOSE_10':.2,'CLOSE_25':0.},
                observed_ts=value['captured_ts'],max_age_sec=900.,
                source_ids=['historical'],evidence_family_ids=[])]
    selected,audit,_=rank_candidates(rows,specs,value,{'quantitative_base':.8,'historical_llm':.2})
    close10=next(r for r in audit if r['policy']=='CLOSE_10')
    assert close10['ranking_eligible'] is True
    assert selected['policy']=='CLOSE_10'
    assert rows==original


def test_pair_evidence_uses_the_difference_of_matched_net_outcomes():
    _,rows=noisy_close_comparison()
    proof=rows[1]['pareto_comparisons']['CLOSE_25']
    assert proof['expected_delta_r']==pytest.approx(.00165)
    assert proof['expected_ci95_lower_r']<0
    assert proof['matched_seed_tail_gain_lower_r']==pytest.approx(.18075)
    assert proof['robust_dominance'] is False


def test_uniform_same_path_dominance_still_excludes_an_inferior_action():
    # Both paths hit the tightened .97R stop before different terminal values.
    # Net tightened result .95R vs exit .96R: an exact paired .01R improvement,
    # inside the existing sacrifice budget and with robust benefit over HOLD.
    value=snapshot([[1.,.96,-.5]]*600+[[1.,.96,2.]]*600,[1/1200]*1200)
    rows=[candidate('HOLD'),candidate('TIGHTEN_STOP',stop_price=109.7),candidate('EXIT')]
    priced=price_unified_candidates(value,rows)
    for row in rows:row.update(priced['candidates'][row['candidate_id']])
    proof=rows[1]['pareto_comparisons']['EXIT']
    assert proof['expected_delta_r']==pytest.approx(.01)
    assert proof['expected_ci95_lower_r']==pytest.approx(.01)
    assert proof['matched_seed_tail_gain_lower_r']==pytest.approx(.01)
    assert proof['robust_dominance'] is True
    selected,audit,_=rank_candidates(rows,[],value,{})
    assert selected['policy']=='EXIT'
    assert next(r for r in audit if r['policy']=='TIGHTEN_STOP')['dominated_by_candidate_ids']==['EXIT']


def test_a_positive_pooled_gain_cannot_hide_a_disagreeing_seed():
    value=snapshot([[1.,.5]]*900+[[1.,1.5]]*100,[.001]*1000)
    value['policy_manager']['unified_scenario_bank']['seed_ids']=[0]*900+[1]*100
    result=price_unified_candidates(value,[candidate('HOLD'),candidate('EXIT')])
    proof=result['candidates']['HOLD']['pareto_comparisons']['EXIT']
    assert proof['expected_delta_r']==pytest.approx(.38)
    assert proof['expected_ci95_lower_r']==pytest.approx(-.52)
    assert proof['matched_seed_tail_gain_lower_r']==pytest.approx(-.52)
    assert proof['robust_dominance'] is False


def test_zero_weight_paths_do_not_invent_pair_uncertainty():
    value=snapshot([[1.,.5],[1.,.5],[1.,3.]],[.5,.5,0.])
    value['policy_manager']['unified_scenario_bank']['seed_ids']=[0,0,1]
    result=price_unified_candidates(value,[candidate('HOLD'),candidate('EXIT')])
    proof=result['candidates']['HOLD']['pareto_comparisons']['EXIT']
    assert proof['expected_ci95_lower_r']==pytest.approx(.48)
    assert proof['robust_dominance'] is True


@pytest.mark.parametrize('weights,expected_lower',[
    ([.99,.01],-.045285600),
    ([1.,1e-20],None),
])
def test_concentrated_weights_cannot_create_false_pareto_certainty(weights,expected_lower):
    value=snapshot([[1.,.96,-.5],[1.,1.01,1.01]],weights)
    rows=[candidate('HOLD'),candidate('TIGHTEN_STOP',stop_price=109.7),candidate('EXIT')]
    priced=price_unified_candidates(value,rows)
    for row in rows:row.update(priced['candidates'][row['candidate_id']])
    proof=rows[1]['pareto_comparisons']['EXIT']
    assert proof['expected_delta_r']>0
    if expected_lower is None:
        assert proof['expected_ci95_lower_r'] is None
    else:
        assert proof['expected_ci95_lower_r']==pytest.approx(expected_lower)
    assert proof['robust_dominance'] is False
    _,audit,_=rank_candidates(rows,[],value,{})
    assert next(r for r in audit if r['policy']=='TIGHTEN_STOP')['ranking_eligible'] is True


@pytest.mark.parametrize('damage',['missing','other_bank','negative_bound','malformed'])
def test_point_values_or_invalid_pair_proof_cannot_remove_a_candidate(damage):
    value,rows=noisy_close_comparison()
    if damage=='missing':rows[1].pop('pareto_comparisons',None)
    elif damage=='other_bank':rows[2]['bank_id']='unrelated'
    elif damage=='negative_bound':
        proof=rows[1]['pareto_comparisons']['CLOSE_25']
        proof.update(robust_dominance=True,expected_ci95_lower_r=-.01)
    else:rows[1]['pareto_comparisons']=['invalid']
    _,audit,_=rank_candidates(rows,[],value,{})
    assert next(r for r in audit if r['policy']=='CLOSE_10')['ranking_eligible'] is True
