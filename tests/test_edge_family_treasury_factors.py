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


def captured_curve(code='NAS100',recorded=T0-90,block=None):
    block=treasury() if block is None else block
    raw=json.dumps({'treasury_context_v1':block})
    return dict(instrument=code,captured_ts=T0-100,original_created_ts=recorded,
        frozen_features_json=raw,features_sha256=hashlib.sha256(raw.encode()).hexdigest(),source_verified=True)


def test_global_curve_recorded_under_one_target_supplies_all_targets_with_original_receipt():
    from seiltanzer.edge_family_native_history import native_history
    record=captured_curve()
    result=native_history([record],captured_ts=T0)
    for code in ('NAS100','JPY100','BTCUSD','ETHUSD','SOLUSD'):
        assert code in result, 'the official US curve is global, not an NAS100 price mapping'
        point=result[code]['value_carry'][0]
        proof=point['feature_provenance']['carry.us02y_yield_pct']
        assert point['captured_ts']==T0-90
        assert proof['received_ts']==T0-200
        assert proof['origin_capture_instrument']=='NAS100'
        assert proof['origin_capture_sha256']==record['features_sha256']
        assert proof['global_factor'] is True
        assert proof['measurement_instrument']=='US_TREASURY_PAR_YIELD_CURVE'
        assert proof['dependency_group']=='official_us_treasury_daily_curve'
        assert proof['target_quote_authority'] is False


def test_duplicate_global_curves_use_earliest_actual_capture_without_new_receipts():
    from seiltanzer.edge_family_native_history import native_history
    later=captured_curve('SP500',recorded=T0-50)
    first=captured_curve('NAS100',recorded=T0-90)
    result=native_history([later,first,first],captured_ts=T0)
    point=result['NAS100']['value_carry']
    assert len(point)==1
    assert point[0]['captured_ts']==T0-90
    assert point[0]['feature_provenance']['carry.us02y_yield_pct']['origin_capture_instrument']=='NAS100'


def test_conflicting_global_curve_values_at_same_source_clocks_are_not_selected():
    from seiltanzer.edge_family_native_history import native_history
    revised=treasury()
    revised['features']['rates.us02y_yield']['value']=4.3
    revised['features']['rates.curve_2s10s']['value']=.2
    result=native_history([captured_curve(),captured_curve('SP500',block=revised)],captured_ts=T0)
    assert all(not values.get('value_carry') for values in result.values())


def before_boundary_fetch_context():
    from test_treasury_live_t0 import _states,_ts
    from seiltanzer.treasury_t0_context import build_treasury_t0_context
    fetched=_ts(2026,8,21,22);captured=_ts(2026,8,22,1)
    return build_treasury_t0_context(_states(),fetched_at=fetched,captured_ts=captured)


def test_actual_parent_accepts_fetch_before_conservative_boundary_without_fabricating_publication_clock():
    block=before_boundary_fetch_context();original=deepcopy(block)
    assert block['available'] is True and block['fetched_at']<block['published_at']<=block['captured_ts']
    result=helper()(block,code='JPY100',cutoff=block['captured_ts'])
    assert result is not None
    proof=result['feature_provenance']['carry.us02y_yield_pct']
    assert proof['observed_ts']==proof['received_ts']==block['fetched_at']
    assert proof['available_at']==block['published_at']
    assert proof['conservative_publication_bound_ts']==block['published_at']
    assert proof.get('published_at') is None
    assert block==original


def test_two_real_curve_dates_in_same_http_response_are_not_conflicting_duplicates():
    from test_treasury_live_t0 import _states,_ts
    from seiltanzer.treasury_t0_context import build_treasury_t0_context
    from seiltanzer.edge_family_native_history import native_history
    fetched=_ts(2026,8,21,22);rows=[]
    for captured in (_ts(2026,8,21,23),_ts(2026,8,22,1)):
        block=build_treasury_t0_context(_states(),fetched_at=fetched,captured_ts=captured)
        raw=json.dumps({'treasury_context_v1':block})
        rows.append(dict(instrument='JPY100',captured_ts=captured,original_created_ts=captured+1,
            frozen_features_json=raw,features_sha256=hashlib.sha256(raw.encode()).hexdigest(),source_verified=True))
    result=native_history(rows,captured_ts=rows[-1]['original_created_ts']+1)
    facts=result['JPY100']['value_carry']
    assert len(facts)==2
    assert len({point['features']['carry.us02y_yield_pct'] for point in facts})==2
    assert {point['feature_provenance']['carry.us02y_yield_pct']['received_ts'] for point in facts}=={fetched}


def test_repeated_fetches_of_same_curve_keep_a_shared_purge_dependency():
    from test_treasury_live_t0 import _states,_ts
    from seiltanzer.treasury_t0_context import build_treasury_t0_context
    from seiltanzer.edge_family_market import _history_rows
    points=[]
    for fetched,captured in ((_ts(2026,8,21,22),_ts(2026,8,22,1)),(_ts(2026,8,22,2),_ts(2026,8,22,3))):
        block=build_treasury_t0_context(_states(),fetched_at=fetched,captured_ts=captured)
        point=helper()(block,code='JPY100',cutoff=captured)
        assert point is not None
        points.append(point)
    rows=list(_history_rows('JPY100','value_carry',points,_ts(2026,8,22,4)).values())
    assert len(rows)==2
    assert set(rows[0]['group']) & set(rows[1]['group']), 'same daily curve must not leak across purged folds'


@pytest.mark.parametrize('case',['missing_kind','wrong_date','before_boundary','old_source_fresh_fetch'])
def test_conservative_curve_still_rejects_unknown_future_or_stale_evidence(case):
    block=before_boundary_fetch_context();cutoff=block['captured_ts']
    if case=='missing_kind':block.pop('publication_timestamp_kind')
    elif case=='wrong_date':block['observation_date']='08/20/2026'
    elif case=='before_boundary':cutoff=block['published_at']-1
    else:
        cutoff=block['published_at']+86401
        block.update(captured_ts=cutoff,fetched_at=cutoff-10,asof=cutoff-10,stale_after=cutoff+1000)
        for row in block['features'].values():
            row.update(fetched_at=cutoff-10,asof=cutoff-10,stale_after=cutoff+1000)
    assert helper()(block,code='JPY100',cutoff=cutoff) is None


def test_actual_receipt_clock_does_not_extend_old_curve_freshness_in_historical_reuse():
    from test_treasury_live_t0 import _states,_ts
    from seiltanzer.treasury_t0_context import build_treasury_t0_context
    from seiltanzer.edge_family_training import _feature_reason
    boundary=_ts(2026,8,22)
    block=build_treasury_t0_context(_states(),fetched_at=boundary+23*3600,captured_ts=boundary+23*3600+60)
    point=helper()(block,code='JPY100',cutoff=block['captured_ts'])
    assert point is not None
    proof=point['feature_provenance']['carry.us02y_yield_pct']
    assert _feature_reason(proof,'JPY100','value_carry',boundary+86401) is not None


def _same_curve_clock_points():
    from test_treasury_live_t0 import _states,_ts
    from seiltanzer.treasury_t0_context import build_treasury_t0_context
    points=[]
    for fetched,captured in ((_ts(2026,8,22,1),_ts(2026,8,22,2)),(_ts(2026,8,22,3),_ts(2026,8,22,4))):
        point=helper()(build_treasury_t0_context(_states(),fetched_at=fetched,captured_ts=captured),code='JPY100',cutoff=captured)
        assert point is not None
        points.append(point)
    return points,_ts(2026,8,22,5)


def test_legacy_and_receipt_clock_versions_of_same_curve_are_purged_together():
    from seiltanzer.edge_family_market import _history_rows
    points,cutoff=_same_curve_clock_points()
    for proof in points[0]['feature_provenance'].values():
        bound=proof['conservative_publication_bound_ts']
        for key in ('conservative_publication_bound_ts','source_observation_date','publication_timestamp_kind','purge_dependency_id'):proof.pop(key)
        proof.update(observed_ts=bound,published_at=bound,factor_contract='recorded-treasury-market-response-factors-v1')
    rows=list(_history_rows('JPY100','value_carry',points,cutoff).values())
    assert len(rows)==2 and set(rows[0]['group']) & set(rows[1]['group'])


def test_same_curve_dependency_is_derived_even_if_optional_purge_id_was_not_serialized():
    from seiltanzer.edge_family_market import _history_rows
    points,cutoff=_same_curve_clock_points()
    for proof in points[0]['feature_provenance'].values():proof.pop('purge_dependency_id')
    rows=list(_history_rows('JPY100','value_carry',points,cutoff).values())
    assert len(rows)==2 and set(rows[0]['group']) & set(rows[1]['group'])
