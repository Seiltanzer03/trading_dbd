from copy import deepcopy
import json

from seiltanzer import ai_snapshot_budget_guard as guard,ai_verdict
from seiltanzer.ai_verdict_v19 import _metric_audit_lines
from seiltanzer.g1_management_feature_context_v2 import _option_state
from seiltanzer.llm_decision_shadow import _shadow_projection
from seiltanzer.llm_shadow_working_action import _distance
from seiltanzer.ai_provider_guard import compact_provider_snapshot
from seiltanzer.unified_edge_ensemble import build_unified_ensemble
from test_operational_edge_compaction import operational_snapshot,signatures
from test_unified_edge_ensemble import llm


def option_snapshot():
    value=operational_snapshot()
    names=('p_take','p_stop','p_no_touch','barrier_ev','bop','q10','q50','q90',
           'width','tail_log_ratio','skew','term_slope','iv','rv','vrp','h_take',
           'h_stop','hazard_log_ratio','gex_force','gex_stiffness','distance_to_zero_gamma')
    stamp=value['captured_ts']-60
    state={'version':'option-derivative-state-v1','available':True,'observed_ts':stamp,
        'source_quality':{'quality':.85,'source':'yfinance QQQ options 2027-01-15'},
        'metrics':{name:{'value':index/100,'slope':.0001,'acceleration':-.00001,
            'noise':.01,'normalization_noise':.01,'numerical_effect_floor':1e-6,
            'normalization_horizon_minutes':5.,'sample_count':360,'time_span_minutes':90.,
            'confidence':.2,'source_quality':.85,'available':True,'value_units':'R',
            'slope_units':'R/min','acceleration_units':'R/min2','units':'R',
            'timestamp':stamp,'observed_ts':stamp,'source':'yfinance QQQ options 2027-01-15',
            'reason':'observed metric; derivative confidence is not market probability'}
            for index,name in enumerate(names)}}
    value['policy_manager']['option_derivative_state']=state
    guard._strict_authoritative_compaction(value)
    return value


def test_emergency_transport_reduces_bytes_without_changing_inputs_reports_or_ranking():
    original=option_snapshot();compacted=deepcopy(original)
    before=json.dumps(original['policy_manager']['option_derivative_state'],ensure_ascii=False)
    expected=signatures(build_unified_ensemble(original,llm(captured_ts=original['captured_ts'])))
    guard._emergency_authoritative_compaction(compacted,ai_verdict)
    after=json.dumps(compacted['policy_manager']['option_derivative_state'],ensure_ascii=False)
    assert len(after.encode())<len(before.encode())*.6
    assert _option_state(compacted)==_option_state(original)
    assert _metric_audit_lines(compacted)==_metric_audit_lines(original)
    assert _distance(compacted,'distance_to_zero_gamma')==_distance(original,'distance_to_zero_gamma')
    assert (_shadow_projection(compacted)['policy_manager']['option_derivative_state']
            ==_shadow_projection(original)['policy_manager']['option_derivative_state'])
    assert (compact_provider_snapshot(compacted)['policy_manager']['option_derivative_state']
            ==compact_provider_snapshot(original)['policy_manager']['option_derivative_state'])
    assert signatures(build_unified_ensemble(compacted,llm(captured_ts=original['captured_ts'])))==expected
    assert ai_verdict._impl.SNAPSHOT_LIMIT_BYTES==60000
    assert json.loads(before)==original['policy_manager']['option_derivative_state']


def test_repeated_budget_passes_keep_the_complete_decoded_metric_audit():
    original=option_snapshot();value=deepcopy(original)
    for _ in range(3):
        guard._emergency_authoritative_compaction(value,ai_verdict)
        ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18(value)
        assert _option_state(value)==_option_state(original)
        assert _metric_audit_lines(value)==_metric_audit_lines(original)


def test_damaged_or_oversized_option_transport_never_becomes_a_metric():
    import base64,hashlib,zlib
    from seiltanzer.option_metric_transport import pack_option_state
    good=pack_option_state(option_snapshot()['policy_manager']['option_derivative_state'])
    variants=[]
    for field,damage in [('raw_sha256','0'*64),('option_metrics_encoded','?'),
                         ('option_metrics_encoded','a'*45001),('transport_contract','unknown')]:
        variants.append({**good,field:damage})
    missing=dict(good);missing.pop('option_metrics_encoded');variants.append(missing)
    for raw,trailing in [(b'[]',b''),(b'{"metrics":{"iv":{"value":NaN}}}',b''),
                         (b'{"available":true,"metrics":{"iv":{"value":1e400,"available":true}}}',b''),
                         (b'{"available":true,"metrics":{"iv":{"value":-1e400,"available":true}}}',b''),
                         (b'{"metrics":{},"padding":"'+b'x'*32001+b'"}',b''),
                         (b'{"metrics":{}}',b'extra')]:
        variants.append({**good,'raw_sha256':hashlib.sha256(raw).hexdigest(),
            'option_metrics_encoded':base64.b64encode(zlib.compress(raw)+trailing).decode()})
    for packet in variants:
        value={'policy_manager':{'option_derivative_state':packet}}
        state=_option_state(value)
        assert state.get('available') is False
        assert state.get('metrics')=={}
        assert state.get('reason')=='INVALID_OPTION_METRIC_TRANSPORT'
        assert _distance(value,'distance_to_zero_gamma') is None


def test_packed_option_metrics_cannot_hide_post_capture_source_timestamps():
    import pytest
    from seiltanzer.option_metric_transport import pack_option_state
    from seiltanzer.decision_research import validate_no_future_timestamps
    value=option_snapshot();cutoff=value['captured_ts']
    value['policy_manager']['option_derivative_state']['metrics']['iv']['observed_ts']=cutoff+10
    with pytest.raises(ValueError):validate_no_future_timestamps(value,cutoff)
    value['policy_manager']['option_derivative_state']=pack_option_state(value['policy_manager']['option_derivative_state'])
    with pytest.raises(ValueError):validate_no_future_timestamps(value,cutoff)
