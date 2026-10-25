"""Positive reported proxy option interest, never a complete dealer inventory."""
from copy import deepcopy
import importlib
import importlib.util

import pytest

from test_edge_family_native_history import row, T0


def module():
    assert importlib.util.find_spec('seiltanzer.edge_family_option_interest') is not None, 'observed option-interest history missing'
    return importlib.import_module('seiltanzer.edge_family_option_interest')


def metrics():
    return {'proxy':'QQQ','demo':False,'oi_profile':{'strikes':[100.,101.,102.],
        'call_oi':[10,0,20], 'put_oi':[30,0,0]}}


def observation(**changes):
    values=dict(code='NAS100',metrics=metrics(),source_id='yfinance QQQ options',
        observed_ts=T0-300,received_ts=T0-100,quality=.5,source_sha256='a'*64)
    values.update(changes)
    return module().interest_observation(**values)


def test_interest_uses_positive_reported_oi_and_preserves_partial_proxy_meaning():
    result=observation()
    assert result['features']['positioning.option_reported_put_share']==.5
    proof=result['feature_provenance']['positioning.option_reported_put_share']
    assert proof['partial_reported_chain'] is True
    assert proof['measurement_instrument']=='QQQ'
    assert proof['dependency_group']=='NAS100:option_distribution'
    assert proof['observed_broker_positions'] is False
    assert proof['zero_open_interest_imputed'] is False


@pytest.mark.parametrize('changes', [dict(code='JPY100'), dict(metrics={'proxy':'SPY','demo':False,'oi_profile':metrics()['oi_profile']}),
    dict(observed_ts=T0+1),dict(received_ts=T0-400),dict(quality=0),
    dict(metrics={**metrics(),'synthetic_data_used':True}),
    dict(metrics={**metrics(),'oi_profile':{'strikes':[100], 'call_oi':[0], 'put_oi':[0]}})])
def test_interest_rejects_missing_wrong_proxy_simulated_or_clock_invalid(changes):
    assert observation(**changes) is None


def test_native_projection_uses_raw_chain_capture_age_not_unrelated_v3_clock():
    import hashlib,json
    from seiltanzer.native_family_capture_projection import project_capture
    from seiltanzer.edge_family_native_history import native_history
    record=row();frozen=json.loads(record['frozen_features_json'])
    frozen['option_distribution']=metrics()
    # Different earlier V3 option block is not the raw OI value's timestamp.
    frozen['g1s_evidence_v3']['option_static']['quality']['source_ts']=T0-900
    raw=json.dumps(frozen);record.update(frozen_features_json=raw,features_sha256=hashlib.sha256(raw.encode()).hexdigest(),option_age_sec=40.)
    record.update(project_capture(raw,record['features_sha256']))
    output=native_history([record],captured_ts=T0)
    interest=output['NAS100']['positioning'][0]
    assert interest['feature_provenance']['positioning.option_reported_put_share']['observed_ts']==record['captured_ts']-40
    assert interest['captured_ts']==record['original_created_ts']
