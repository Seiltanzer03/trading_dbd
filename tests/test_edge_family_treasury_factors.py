"""Recorded interest-rate factors are not broker rollover charges."""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json

import pytest

T0 = 1791504000.


def treasury():
    fields = {'rates.us02y_yield':4.2, 'rates.us10y_yield':4.5, 'rates.curve_2s10s':.3}
    return {'contract_version':'treasury-live-t0-context-v1', 'available':True,
        'official_source_verified':True, 'complete_base_curve':True,
        'source_url':'https://home.treasury.gov/resource-center-data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value=202610',
        'source':'US_TREASURY_OFFICIAL_DAILY_PAR_YIELD_CURVE',
        'captured_ts':T0-100, 'published_at':T0-7200, 'fetched_at':T0-200,
        'asof':T0-200, 'stale_after':T0+1000, 'stale':False, 'future_points_used':False,
        'research_only':True, 'production_authority':False, 'historical_backfill_allowed':False,
        'features':{name:{'value':value, 'available':True, 'asof':T0-200,
            'published_at':T0-7200, 'fetched_at':T0-200, 'stale_after':T0+1000,
            'stale':False, 'future_points_used':False} for name,value in fields.items()}}


def helper():
    assert importlib.util.find_spec('seiltanzer.edge_family_treasury_factors') is not None, 'recorded Treasury factor derivation missing'
    return importlib.import_module('seiltanzer.edge_family_treasury_factors').treasury_observation


def test_recorded_factor_keeps_original_source_clocks_units_and_shared_dependency():
    block = treasury()
    original = deepcopy(block)
    result = helper()(block, code='NAS100', cutoff=T0)
    assert result['features'] == {'carry.us02y_yield_pct':4.2, 'carry.us10y_yield_pct':4.5, 'carry.curve_2s10s_pp':.3}
    proof = result['feature_provenance']['carry.us02y_yield_pct']
    assert proof['observed_ts'] == T0-7200
    assert proof['received_ts'] == T0-200
    assert proof['available_at'] == T0-200
    assert proof['dependency_group'] == 'official_us_treasury_daily_curve'
    assert proof['broker_carry_available'] is False
    assert proof['parent_research_only'] is True
    assert proof['measurement_instrument'] == 'US_TREASURY_PAR_YIELD_CURVE'
    assert block == original


@pytest.mark.parametrize('field,value', [('stale',True),('future_points_used',True),
    ('official_source_verified',False),('complete_base_curve',False),('fetched_at',T0+1),
    ('source_url','https://treasury.example/fake'),('published_at',T0-86401),('simulated',True)])
def test_factor_rejects_invalid_parent_and_future_or_stale_clocks(field,value):
    block = treasury(); block[field] = value
    assert helper()(block, code='NAS100', cutoff=T0) is None


def test_projection_and_native_history_preserve_real_factor_without_promoting_parent():
    from seiltanzer.native_family_capture_projection import project_capture
    from seiltanzer.edge_family_native_history import native_history
    block = treasury()
    raw = json.dumps({'treasury_context_v1':block})
    digest = hashlib.sha256(raw.encode()).hexdigest()
    projected = project_capture(raw,digest)
    assert 'treasury_context_v1' in json.loads(projected['frozen_features_json']), 'Treasury history dropped before training'
    rows = [dict(instrument='NAS100', captured_ts=T0-100, original_created_ts=T0-90,
        source_verified=True, **projected)]
    observation = native_history(rows,captured_ts=T0)['NAS100']['value_carry'][0]
    assert observation['feature_provenance']['carry.us02y_yield_pct']['received_ts'] == T0-200
    assert observation['captured_ts'] == T0-90


def test_runtime_adapter_derives_identical_factors_and_requires_trained_model_for_voice():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    result = build_edge_family_evidence({'instrument':'NAS100','captured_ts':T0,'treasury_context_v1':treasury()})
    family = result['families']['value_carry']
    assert family['features'] == helper()(treasury(),code='NAS100',cutoff=T0)['features']
    assert family['forecast_available'] is False
    assert result['components'] == []
