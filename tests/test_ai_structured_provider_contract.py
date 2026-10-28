"""Actual request contract must constrain choices without weakening local admission."""
from copy import deepcopy
import json
import httpx
import pytest
from jsonschema import Draft202012Validator, ValidationError
from seiltanzer import ai_runtime_report_v20 as report
from seiltanzer import ai_provider_transport as transport
from seiltanzer.llm_decision_shadow import VALID_POLICIES
from test_ai_provider_explanation import _snapshot

def shadow(families=None):
    return dict(policy='HOLD',confidence=.7,
        policy_scores={p:float(p=='HOLD') for p in VALID_POLICIES},
        reason_ru='Независимая оценка.',key_evidence=[],counter_evidence=[],
        evidence_families=[],invalidation_conditions=[],
        family_assessments=[] if families is None else families)

# The wire order is a versioned contract, independently pinned here to catch
# accidental action reordering (which would silently change preferences).
WIRE_ORDER=('HOLD','CLOSE_10','CLOSE_25','CLOSE_50','EXIT','MOVE_TO_BE',
    'TRAIL_GAMMA_FLIP','TIGHTEN_STOP','EXTEND_TAKE','REDUCE_TAKE',
    'SCALE_OUT_ON_SPIKE','TIME_STOP')

def wire_shadow():
    result=shadow()
    result['policy_scores']=[.9,.8,.7,.6,.5,.4,.3,.2,.1,0.,-.1,-.2]
    return result

def explicit_shadow():
    result=shadow()
    del result['policy_scores']
    result['opinion_contract']='single-explicit-choice-v1'
    return result

@pytest.fixture
def request_body(monkeypatch):
    seen=[]
    def post(url,**kwargs):
        seen.append(kwargs['body'])
        payload={'model':'offline-fixture','choices':[{'message':{'content':
            json.dumps({'shadow_decision':explicit_shadow()},ensure_ascii=False)}}]}
        return httpx.Response(200,json=payload,request=httpx.Request('POST',url))
    monkeypatch.setenv('OPENROUTER_API_KEY','offline-fixture')
    monkeypatch.delenv('OPENROUTER_PROXY',raising=False)
    monkeypatch.setattr(transport,'post_json',post)
    snapshot=_snapshot();before=deepcopy(snapshot)
    report.request_explanation_with_shadow(snapshot,authoritative_snapshot=snapshot)
    assert snapshot==before
    assert len(seen)==1
    return seen[0]

def schema(body):
    assert 'response_format' in body, 'provider must receive an enforced action contract'
    fmt=body['response_format']
    assert fmt['type']=='json_schema' and fmt['json_schema']['strict'] is True
    result=fmt['json_schema']['schema']
    Draft202012Validator.check_schema(result)
    return result

def test_request_requires_compatible_fast_endpoint_of_same_model(request_body):
    schema(request_body)
    assert request_body['provider']=={'require_parameters':True,'sort':'latency'}
    assert request_body['max_tokens']==2400

def test_actual_provider_schema_accepts_valid_response(request_body):
    Draft202012Validator(schema(request_body)).validate({'shadow_decision':explicit_shadow()})

def test_actual_provider_schema_rejects_joined_or_unsupported_action(request_body):
    validator=Draft202012Validator(schema(request_body))
    for action in ('HOLD|CLOSE_10','UNAVAILABLE','WAIT'):
        bad=explicit_shadow();bad['policy']=action
        with pytest.raises(ValidationError):validator.validate({'shadow_decision':bad})

def test_actual_provider_schema_requires_explicit_choice_marker(request_body):
    bad=explicit_shadow();del bad['opinion_contract']
    with pytest.raises(ValidationError):
        Draft202012Validator(schema(request_body)).validate({'shadow_decision':bad})

def test_compact_scores_preserve_each_action_and_legacy_domain_contract():
    wire=wire_shadow()
    legacy=shadow()
    legacy['policy_scores']={'HOLD':.9,'CLOSE_10':.8,'CLOSE_25':.7,'CLOSE_50':.6,
        'EXIT':.5,'MOVE_TO_BE':.4,'TRAIL_GAMMA_FLIP':.3,'TIGHTEN_STOP':.2,
        'EXTEND_TAKE':.1,'REDUCE_TAKE':0.,'SCALE_OUT_ON_SPIKE':-.1,'TIME_STOP':-.2}
    _,parsed=report._provider_payload(json.dumps({'shadow_decision':wire}))
    _,old=report._provider_payload(json.dumps({'shadow_decision':legacy}))
    assert parsed==old
    assert len(json.dumps(wire)) < len(json.dumps(legacy)) - 120

@pytest.mark.parametrize('scores', [[],[0]*11,[0]*13,[True]+[0]*11,
    [1.1]+[0]*11,[float('nan')]+[0]*11,['.5']+[0]*11,[None]+[0]*11])
def test_compact_scores_never_fill_or_coerce_invalid_values(scores):
    bad=wire_shadow();bad['policy_scores']=scores
    with pytest.raises(RuntimeError,match='shadow_invalid_policy_scores'):
        report._provider_payload(json.dumps({'shadow_decision':bad}))

def test_actual_wire_request_rejects_redundant_global_vectors(request_body):
    for value in ([0]*12,[0]*13,[True]+[0]*11,[1.1]+[0]*11):
        bad=explicit_shadow();bad['policy_scores']=value
        with pytest.raises(ValidationError):
            Draft202012Validator(schema(request_body)).validate({'shadow_decision':bad})

def test_wire_family_array_preserves_legacy_interpretation_and_missingness():
    opinion={'feature_names':['intermarket.return_5m'],
        'policy_scores':{'HOLD':.2,'CLOSE_25':-.2},'reason_ru':'Проверка наблюдаемых признаков.'}
    legacy=shadow({'intermarket':opinion})
    wire=shadow([{'family_id':'intermarket','feature_names':opinion['feature_names'],
        'policy_preferences':[{'policy':p,'score':v} for p,v in opinion['policy_scores'].items()],
        'reason_ru':opinion['reason_ru']}])
    _,a=report._provider_payload(json.dumps({'shadow_decision':legacy}))
    _,b=report._provider_payload(json.dumps({'shadow_decision':wire}))
    assert a==b
    _,empty=report._provider_payload(json.dumps({'shadow_decision':shadow()}))
    assert empty['family_assessments']=={}
    assert empty['family_assessment_rejections']=={}

@pytest.mark.parametrize('bad',[['malformed'],
    [{'family_id':'invented','feature_names':['x'],'policy_preferences':[],'reason_ru':'x'}],
    [{'family_id':'macro','feature_names':['x'],'policy_preferences':
      [{'policy':'HOLD','score':.2},{'policy':'HOLD','score':.3}],'reason_ru':'x'}]])
def test_malformed_wire_preferences_are_never_silently_overwritten(bad):
    with pytest.raises(RuntimeError,match='combined_provider_invalid_family_preferences'):
        report._provider_payload(json.dumps({'shadow_decision':shadow(bad)}))

def test_schema_does_not_replace_local_risk_or_numeric_validation():
    for bad_score in (True,1.1,float('nan')):
        bad=shadow({});bad['policy_scores']['HOLD']=bad_score
        with pytest.raises(RuntimeError,match='shadow_invalid_policy_scores'):
            report._provider_payload(json.dumps({'shadow_decision':bad}))
