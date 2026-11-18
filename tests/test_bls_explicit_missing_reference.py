from copy import deepcopy

import pytest

from seiltanzer.macro_numeric_data import BLS_SERIES,build_bls_releases,_candidate_vector
from test_macro_numeric_data import _bls_payload


def missing_reference(payload,name):
    series=next(row for row in payload['Results']['series'] if row['seriesID']==BLS_SERIES[name])
    record=next(row for row in series['data'] if row['year']=='2025' and row['period']=='M07')
    record['value']='-'
    record['footnotes']=[{'code':'X','text':'Data unavailable due to a lapse in appropriations'}]
    return record


def test_official_reference_gap_keeps_current_monthly_facts_and_never_imputes_annual_values():
    payload=_bls_payload();expected=build_bls_releases(deepcopy(payload))
    for name in ('cpi_headline_nsa','cpi_core_nsa'):missing_reference(payload,name)
    actual=build_bls_releases(payload);cpi=actual['CPI']
    assert cpi['period']==expected['CPI']['period']=='2026-07'
    for name in ('headline_mom_pct','core_mom_pct','headline_mom_change_pp','core_mom_change_pp'):
        assert cpi[name]==expected['CPI'][name]
    assert cpi['headline_yoy_pct'] is None and cpi['core_yoy_pct'] is None
    assert actual['NFP']==expected['NFP']
    for field,sid in [('headline_yoy_pct',BLS_SERIES['cpi_headline_nsa']),('core_yoy_pct',BLS_SERIES['cpi_core_nsa'])]:
        gap=cpi['unavailable_fields'][field]
        assert gap['reason']=='OFFICIAL_REFERENCE_OBSERVATION_UNAVAILABLE'
        assert gap['reference_period']=='2025-07' and gap['series_id']==sid
        assert gap['official_marker']=='-' and gap['footnotes'][0]['code']=='X'
    features=_candidate_vector({'releases':{'CPI':{'status':'VALID','payload':cpi}}})
    assert features['macro.cpi_headline_mom_pct']==cpi['headline_mom_pct']
    assert 'macro.cpi_headline_yoy_pct' not in features and 'macro.cpi_core_yoy_pct' not in features


def test_only_the_documented_missing_annual_field_is_unavailable():
    payload=_bls_payload();missing_reference(payload,'cpi_headline_nsa')
    cpi=build_bls_releases(payload)['CPI']
    assert cpi['headline_yoy_pct'] is None and cpi['core_yoy_pct']==pytest.approx(2.5)
    assert set(cpi['unavailable_fields'])=={'headline_yoy_pct'}


def test_partial_official_facts_survive_verified_transport_without_changing_authority():
    from seiltanzer import macro_offhost_bundle as offhost
    from test_macro_offhost_bundle import _bundle,_rehash,NOW,SHA,RUN_ID
    payload=_bls_payload()
    for name in ('cpi_headline_nsa','cpi_core_nsa'):missing_reference(payload,name)
    cpi=build_bls_releases(payload)['CPI']
    bundle=_bundle()
    bundle['releases']['CPI']=offhost._release_record(family='CPI',payload=cpi,
        source='U.S. Bureau of Labor Statistics',
        source_url='https://api.bls.gov/publicAPI/v2/timeseries/data/',fetched_at=NOW-10)
    _rehash(bundle)
    verified=offhost.validate_bundle(bundle,expected_sha=SHA,acceptance_run_id=RUN_ID,now=NOW)
    assert verified['production_authority'] is False
    assert verified['releases']['CPI']['payload']==cpi
    cpi['unavailable_fields']['headline_yoy_pct']['footnotes'][0]['text']='edited report copy'
    original=next(row for row in payload['Results']['series'] if row['seriesID']==BLS_SERIES['cpi_headline_nsa'])
    assert original['data'][0]['footnotes'][0]['text']=='Data unavailable due to a lapse in appropriations'


@pytest.mark.parametrize('damage',['deleted','no_footnote','wrong_footnote','empty_text','nan','empty_value','monthly'])
def test_incomplete_or_unverified_history_still_fails(damage):
    payload=_bls_payload();record=missing_reference(payload,'cpi_headline_nsa')
    if damage=='deleted':
        series=next(row for row in payload['Results']['series'] if row['seriesID']==BLS_SERIES['cpi_headline_nsa'])
        series['data'].remove(record)
    elif damage=='no_footnote':record['footnotes']=[]
    elif damage=='wrong_footnote':record['footnotes'][0]['code']='P'
    elif damage=='empty_text':record['footnotes'][0]['text']=''
    elif damage=='nan':record['value']='NaN'
    elif damage=='empty_value':record['value']=''
    else:
        series=next(row for row in payload['Results']['series'] if row['seriesID']==BLS_SERIES['cpi_headline_sa'])
        next(row for row in series['data'] if row['period']=='M06')['value']='-'
    with pytest.raises(ValueError,match='BLS_CPI_HISTORY_INCOMPLETE'):build_bls_releases(payload)


@pytest.mark.parametrize('damage',['unmarked','explicit_missing','malformed','partial_series'])
def test_invalid_current_cpi_cannot_fall_back_to_an_older_partial_release(damage):
    from test_macro_numeric_data import _bls_item
    payload=_bls_payload()
    for name in ('cpi_headline_nsa','cpi_core_nsa'):missing_reference(payload,name)
    for name in ('cpi_headline_sa','cpi_core_sa','cpi_headline_nsa','cpi_core_nsa'):
        series=next(row for row in payload['Results']['series'] if row['seriesID']==BLS_SERIES[name])
        if damage=='partial_series' and name=='cpi_core_nsa':continue
        record=_bls_item(2026,8,105.)
        if name=='cpi_headline_sa' and damage!='partial_series':
            record['value']='NaN' if damage=='malformed' else '-'
            if damage=='explicit_missing':record['footnotes']=[{'code':'X','text':'Data unavailable'}]
        series['data'].append(record)
    with pytest.raises(ValueError,match='BLS_CPI_HISTORY_INCOMPLETE'):build_bls_releases(payload)
