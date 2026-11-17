from copy import deepcopy
import json
import math

import pytest

from seiltanzer.decision_metric_inputs import pinned_decision_context
from seiltanzer.edge_family_market import attach_market_family_context
from seiltanzer.edge_family_native_history import option_observation
from seiltanzer.edge_family_option_interest import interest_observation
from seiltanzer.mathematical_edge import fingerprint
from test_edge_family_market import option_runtime,source,market,T0


def chain(stamp,iv=.28):
    return {'ts':stamp,'source':'yfinance QQQ options','status':'delayed',
        'metrics':{'proxy':'QQQ','demo':False,'implied_move':{'sigma_annual':iv},
            'oi_profile':{'strikes':[100,110],'call_oi':[200,250],'put_oi':[100,150]}}}


def native_input(family,value):
    build=option_observation if family=='option' else interest_observation
    return build(code='NAS100',metrics=value['metrics'],source_id=value['source'],
        observed_ts=value['ts'],received_ts=value['ts'],quality=.72,
        source_sha256=fingerprint(value['metrics']))


@pytest.mark.parametrize('family',['option','positioning'])
def test_post_origin_chain_cannot_erase_genuine_pinned_pre_origin_option_input(tmp_path,monkeypatch,family):
    engine,snapshot,report,path=option_runtime(tmp_path,monkeypatch)
    engine.market.chain=chain(T0-60)
    if family=='positioning':
        history=[]
        for bar in source()['bars']:
            value=chain(bar['bar_end_ts']-17)
            value['metrics']['oi_profile']['put_oi']=[150+100*math.sin(bar['bar_end_ts']/86400),100]
            history.append(native_input(family,value))
        model=market().train_market_families([source()],T0,horizons=(15,),
            feature_history={'NAS100':{'positioning':history}})['NAS100']['positioning']
        assert model['status']=='TRAINED'
        report['family_market_models']['NAS100']['positioning']=model
        path.write_text(json.dumps(report))
    original_chain=deepcopy(engine.market.chain)
    prior=native_input(family,original_chain)
    snapshot['captured_ts']=T0+30
    engine.market.chain['ts']=T0+15
    engine.market.chain['metrics']['implied_move']['sigma_annual']=.99
    context={'instrument':'NAS100','observation_t0':T0+30,
             'option_family_inputs':{family:prior}}
    with pinned_decision_context(engine,context):attach_market_family_context(engine,snapshot)
    actual=snapshot['edge_family_market_context']['families'].get(family)
    assert actual is not None, 'a later chain must not erase an actual pre-origin observation'
    assert actual['features']==prior['features']
    assert all(row['received_ts']==T0-60 and row['quality']==.72
               for row in actual['feature_provenance'].values())


def test_background_capture_retains_original_pre_origin_facts_across_windows_and_mutation():
    from seiltanzer.option_family_inputs import freeze_option_family_inputs
    cache={};old=chain(T0-60)
    assert freeze_option_family_inputs(cache,'NAS100',old,T0-30)=={}
    current=chain(T0+15,.99)
    actual=freeze_option_family_inputs(cache,'NAS100',current,T0+30)
    assert actual=={family:native_input(family,old) for family in ('option','positioning')}
    actual['option']['features']['option.iv']=.01
    current['metrics']['implied_move']['sigma_annual']=.4
    again=freeze_option_family_inputs(cache,'NAS100',current,T0+45)
    assert again['option']['features']['option.iv']==.28
    newer=chain(T0+315,.7)
    next_window=freeze_option_family_inputs(cache,'NAS100',newer,T0+330)
    assert next_window['option']['features']['option.iv']==.99
    assert next_window['option']['captured_ts']==T0+15


@pytest.mark.parametrize('damage',['instrument','proxy','source','future_chain','earlier_cutoff','demo','malformed','expired'])
def test_background_history_never_crosses_identity_clock_or_source_expiry(damage):
    from seiltanzer.option_family_inputs import freeze_option_family_inputs
    cache={};freeze_option_family_inputs(cache,'NAS100',chain(T0-60),T0-30)
    current=chain(T0+15,.99);cutoff=T0+30;code='NAS100'
    if damage=='instrument':code='GER40';current['metrics']['proxy']='EWG'
    elif damage=='proxy':current['metrics']['proxy']='SPY'
    elif damage=='source':current['source']='another actual provider'
    elif damage=='future_chain':current['ts']=T0+31
    elif damage=='earlier_cutoff':cutoff=T0-100;current['ts']=cutoff-1
    elif damage=='demo':current['metrics']['demo']=True
    elif damage=='malformed':current['metrics']['implied_move']['sigma_annual']=float('nan');current['metrics']['oi_profile']={}
    else:cutoff=T0+2000;current['ts']=cutoff-1
    assert freeze_option_family_inputs(cache,code,current,cutoff)=={}


@pytest.mark.parametrize('damage',['future_received','future_observed','future_available','wrong_source',
    'wrong_proxy','wrong_instrument','unverified','bad_hash','nonfinite','expired','future_context'])
def test_pinned_origin_facts_are_revalidated_before_consumption(damage):
    from types import SimpleNamespace
    from seiltanzer.option_family_inputs import option_family_input
    current=chain(T0+15);prior=native_input('option',chain(T0-60))
    proof=prior['feature_provenance']['option.iv'];captured=T0+30
    if damage=='future_received':proof['received_ts']=T0+1
    elif damage=='future_observed':proof['observed_ts']=T0+1
    elif damage=='future_available':proof['available_at']=T0+1
    elif damage=='wrong_source':proof['source_id']='another provider'
    elif damage=='wrong_proxy':proof['measurement_instrument']='SPY'
    elif damage=='wrong_instrument':proof['source_instrument']='GER40'
    elif damage=='unverified':proof['source_verified']=False
    elif damage=='bad_hash':proof['source_sha256']='invalid'
    elif damage=='nonfinite':prior['features']['option.iv']=float('nan')
    elif damage=='expired':prior=native_input('option',chain(T0-1801))
    else:captured=T0+31
    engine=SimpleNamespace(market=SimpleNamespace(chain=current))
    snapshot={'captured_ts':T0+30,'strategy':{'instrument':'NAS100'}}
    context={'instrument':'NAS100','observation_t0':captured,'option_family_inputs':{'option':prior}}
    with pinned_decision_context(engine,context):
        assert option_family_input(engine,snapshot,'NAS100','option',T0,T0+30) is None
