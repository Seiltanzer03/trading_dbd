"""Official release timing is available without fabricating a consensus."""
import importlib
import importlib.util
import math
from copy import deepcopy

import pytest

from test_edge_family_macro_archive import archive, _bundle, NOW


def module():
    assert importlib.util.find_spec('seiltanzer.edge_family_event_archive') is not None, 'official event timing training path missing'
    return importlib.import_module('seiltanzer.edge_family_event_archive')


def test_official_archive_event_state_has_known_release_age_but_no_surprise():
    bundle=_bundle();published=bundle['bls_records'][0]['spec']['published_at']
    result=module().archive_event_state(archive(bundle),published+1800)
    assert result['features']['event.official_release_age_hours']==.5
    assert result['features']['event.official_release_is_CPI']==1.
    assert result['consensus_available'] is False
    assert result['surprise_computed'] is False
    assert result['source']['fetched_at']>published
    assert 'received_ts' not in result['source']
    assert module().archive_event_state(archive(bundle),published-1) is None


def test_current_event_facts_match_archived_release_timing_with_actual_receipt():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    bundle=_bundle();record=bundle['bls_records'][0]
    published=record['spec']['published_at'];cutoff=published+1800
    snapshot=dict(instrument='NAS100',captured_ts=cutoff,macro_context_v1={'numeric_macro':{
        'releases':{'CPI':{'status':'VALID','release_id':'official-cpi',
            'official_source_verified':True,'published_at':published,'available_at':published+60,
            'fetched_at':published+60,'payload':record['payload']}},
        'candidate_vector':{'macro.cpi_core_yoy_pct':record['payload']['core_yoy_pct']}}})
    family=build_edge_family_evidence(snapshot)['families']['event']
    assert family['features']==module().archive_event_state(archive(bundle),cutoff)['features']
    assert family['feature_provenance']['event.official_release_age_hours']['received_ts']==published+60
    snapshot['captured_ts']=published+14401
    assert not build_edge_family_evidence(snapshot)['families']['event']['features']


def test_archive_event_labels_remain_nonoverlapping_and_purge_shared_release():
    from test_edge_family_market import source
    bundle=_bundle();published=bundle['bls_records'][0]['spec']['published_at']
    bars=source(n=100)['bars']
    offset=published+300-bars[0]['bar_end_ts']
    bars=[{**bar,'bar_end_ts':bar['bar_end_ts']+offset} for bar in bars]
    result=module().archive_event_dataset('NAS100',bars,NOW,15,archive(bundle))
    assert 0<len(result['times'])<=16
    assert result['feature_names'][0]=='event.official_release_age_hours'
    assert len({tuple(group) for group in result['groups']})==1
    assert all(end-start==900 for start,end in zip(result['times'],result['ends']))


@pytest.mark.parametrize('family',['event','macro'])
def test_native_archive_contract_choice_does_not_use_final_test_labels(family):
    from test_edge_family_market import source,T0
    from seiltanzer.edge_family_market import train_market_families
    from seiltanzer.mathematical_edge import fingerprint
    raw=source(n=1800);price=100.;history=[];name=family+'.fixed_native_fact'
    for i,bar in enumerate(raw['bars']):
        close=price*math.exp(.001 if (i//9)%2==0 else -.001)
        bar.update(bar_end_ts=bar['bar_end_ts']+math.floor(NOW/300)*300-T0,
            open=price,close=close,high=max(price,close),low=min(price,close))
        price=close;t=bar['bar_end_ts']
        proof=dict(source_verified=True,source_id='actual-capture',source_instrument='NAS100',
            observed_ts=t,received_ts=t,available_at=t,max_age_sec=14400.,quality=1.,dependency_group='release:'+str(i))
        history.append(dict(captured_ts=t,features={name:math.sin(i)},feature_provenance={name:proof}))
    choices=[]
    for flatten in (False,True):
        candidate=deepcopy(raw)
        if flatten:
            price=candidate['bars'][1440]['close']
            for bar in candidate['bars'][1441:]:bar.update(open=price,close=price,high=price,low=price)
        candidate['source_sha256']=fingerprint(candidate['bars'])
        result=train_market_families([candidate],NOW,horizons=(15,),
            feature_history={'NAS100':{family:history}},macro_archive=archive())['NAS100'][family]
        choices.append('official_event_archive' in result or 'official_macro_archive' in result)
    assert choices==[False,False], 'held-out outcome cannot select a different feature source'
