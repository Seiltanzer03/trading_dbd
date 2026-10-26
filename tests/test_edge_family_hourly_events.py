"""Observed hourly price opens are never represented as five-minute candles."""
from copy import deepcopy
import importlib
import importlib.util
import math
import pytest
from test_edge_family_macro_archive import archive,_bundle,NOW
from seiltanzer.mathematical_edge import fingerprint


def module():
    assert importlib.util.find_spec('seiltanzer.edge_family_hourly_events') is not None,'declared hourly event target missing'
    return importlib.import_module('seiltanzer.edge_family_hourly_events')


def hourly_source():
    published=_bundle()['bls_records'][0]['spec']['published_at']
    rows=[dict(opened_ts=published+i*3600,open=100.+i,high=201.+i,low=90.+i,close=200.+i) for i in range(-2,9)]
    return dict(event_hourly_bars=rows,event_hourly_archive=dict(contract_version=module().CONTRACT,
        instrument='NAS100',provider='Yahoo Finance via yfinance',ticker='^NDX',interval='1h',bar_seconds=3600,
        request_cutoff_ts=NOW,received_ts=NOW+1,actual_granularity='1h',requested_period='2y',
        historical_first_receipt_known=False,incomplete_final_rows_excluded=True,series_sha256=fingerprint(rows)))


def test_hourly_event_labels_use_real_open_points_and_only_hourly_horizons():
    raw=hourly_source();published=_bundle()['bls_records'][0]['spec']['published_at']
    result=module().hourly_event_dataset('NAS100',raw,NOW,60,archive())
    assert result['times'][0]==published
    assert result['ends'][0]-result['times'][0]==3600
    assert result['y'][0,2]==pytest.approx(math.log(101./100.))
    assert result['feature_names'][0]=='event.official_release_age_hours'
    assert len({tuple(value) for value in result['groups']})==1
    assert all(b-a==3600 for a,b in zip(result['times'],result['ends']))
    for horizon in (15,30):
        with pytest.raises(ValueError,match='HORIZON'):
            module().hourly_event_dataset('NAS100',raw,NOW,horizon,archive())


def test_missing_intermediate_hour_is_never_interpolated_for_two_hour_label():
    raw=hourly_source();published=_bundle()['bls_records'][0]['spec']['published_at']
    raw['event_hourly_bars']=[row for row in raw['event_hourly_bars'] if row['opened_ts']!=published+3600]
    raw['event_hourly_archive']['series_sha256']=fingerprint(raw['event_hourly_bars'])
    result=module().hourly_event_dataset('NAS100',raw,NOW,120,archive())
    assert published not in result['times']


@pytest.mark.parametrize('case',['mutation','wrong_symbol','crypto','duplicate','synthetic','incomplete','boolean'])
def test_hourly_source_rejects_wrong_or_unverifiable_observations(case):
    raw=hourly_source();code='NAS100'
    if case=='mutation':raw['event_hourly_bars'][0]['open']=111
    elif case=='wrong_symbol':raw['event_hourly_archive']['ticker']='^GSPC'
    elif case=='crypto':code='BTCUSD'
    elif case=='duplicate':raw['event_hourly_bars'].append(deepcopy(raw['event_hourly_bars'][0]))
    elif case=='synthetic':raw['event_hourly_bars'][0]['synthetic_data_used']=True
    elif case=='incomplete':raw['event_hourly_bars'][-1]['opened_ts']=NOW-1800
    else:raw['event_hourly_bars'][0]['open']=True
    if case!='mutation':raw['event_hourly_archive']['series_sha256']=fingerprint(raw['event_hourly_bars'])
    with pytest.raises(ValueError):module().hourly_event_dataset(code,raw,NOW,60,archive())


@pytest.mark.parametrize("metadata_mapping",[False,True])
def test_hourly_collector_retains_actual_retrieval_and_never_labels_current_hour_complete(monkeypatch,metadata_mapping):
    import pandas as pd
    import sys
    from types import SimpleNamespace
    from seiltanzer.mathematical_edge_sources import RequestBudget
    anchor=NOW//3600*3600
    frame=pd.DataFrame({'Open':[100.,101.,102.],'High':[102.,103.,104.],
        'Low':[99.,100.,101.],'Close':[101.,102.,103.]},
        index=pd.to_datetime([anchor-3600,anchor,anchor+3600],unit='s',utc=True))
    calls=[]
    class Provider:
        def history(self,**kwargs):calls.append(kwargs);return frame
        def get_history_metadata(self):
            from collections import UserDict
            fields={'symbol':'^NDX','dataGranularity':'1h'}
            return UserDict(fields) if metadata_mapping else fields
    monkeypatch.setitem(sys.modules,'yfinance',SimpleNamespace(Ticker=lambda ticker:Provider()))
    result=module().collect_hourly_events('NAS100',NOW,RequestBudget(30,0))
    assert len(result['event_hourly_bars'])==1
    assert result['event_hourly_bars'][-1]['opened_ts']==anchor-3600
    proof=result['event_hourly_archive']
    assert proof['received_ts']>NOW and proof['historical_first_receipt_known'] is False
    assert calls[0]['interval']=='1h' and calls[0]['auto_adjust'] is False
    assert module().provenance_valid(proof,'NAS100',NOW,proof['received_ts']+1)


def test_hourly_collector_deadline_prevents_network_call(monkeypatch):
    from seiltanzer.mathematical_edge_sources import RequestBudget
    import sys
    from types import SimpleNamespace
    called=[];monkeypatch.setitem(sys.modules,'yfinance',SimpleNamespace(Ticker=lambda ticker:called.append(ticker)))
    budget=RequestBudget(0,0)
    with pytest.raises(RuntimeError,match='BUDGET'):
        module().collect_hourly_events('NAS100',NOW,budget)
    assert not called


def test_numpy_booleans_cannot_become_hourly_price_evidence():
    import numpy as np
    raw=hourly_source()
    for key in ('open','high','low','close'):raw['event_hourly_bars'][0][key]=np.bool_(True)
    # JSON input cannot contain NumPy scalars; collector must reject them before hashing.
    assert module()._n(np.bool_(True)) is None


def training_case():
    """Controlled archive-object fixture, never published as historical evidence."""
    from dataclasses import replace
    from types import MappingProxyType
    from test_edge_family_market import source,T0
    value=archive()
    template=next(point for point in value._points['macro.cpi_core_yoy_pct'] if point.published_at is not None)
    start=NOW//1800*1800-3000*3600
    releases=[replace(template,available_at=start+i*3600,published_at=start+i*3600,
        source_id='unit-cpi-'+str(i),source_sha256=fingerprint({'unit_release':i})) for i in range(12,2900,36)]
    object.__setattr__(value,'_points',MappingProxyType({'macro.cpi_core_yoy_pct':tuple(releases)}))
    object.__setattr__(value,'_times',MappingProxyType({'macro.cpi_core_yoy_pct':tuple(p.available_at for p in releases)}))
    raw=source(n=1800)
    for row in raw['bars']:row['bar_end_ts']+=NOW//300*300-T0
    raw['source_sha256']=fingerprint(raw['bars'])
    price=100.;rows=[]
    for i in range(3000):
        opened=price;price*=math.exp(.001*math.sin(i/3.))
        rows.append(dict(opened_ts=start+i*3600,open=opened,high=max(opened,price),low=min(opened,price),close=price))
    proof={**hourly_source()['event_hourly_archive'],'series_sha256':fingerprint(rows)}
    raw.update(event_hourly_bars=rows,event_hourly_archive=proof)
    return raw,value


def test_hourly_event_fit_is_a_distinct_validated_hourly_contract_and_other_families_stay_available():
    from seiltanzer.edge_family_market import train_market_families,_model_reason
    raw,value=training_case()
    result=train_market_families([raw],NOW,horizons=(15,60,120),macro_archive=value)['NAS100']
    assert result['session']['status']=='TRAINED'
    model=result['event']
    assert model['status']=='TRAINED'
    assert model['source']['interval']=='1h'
    assert model['hourly_target_contract']==module().CONTRACT
    assert set(model['requested_horizons_minutes'])=={60,120}
    assert _model_reason(model,'NAS100','event',NOW+20) is None
    bad=deepcopy(model);bad['source']['ticker']='^GSPC';bad['model_sha256']=fingerprint({k:v for k,v in bad.items() if k!='model_sha256'})
    assert _model_reason(bad,'NAS100','event',NOW+20) is not None


def test_invalid_optional_hourly_proof_cannot_remove_other_market_families():
    from seiltanzer.edge_family_market import train_market_families
    raw,value=training_case();raw['event_hourly_archive']['synthetic_data_used']=True
    result=train_market_families([raw],NOW,horizons=(15,60,120),macro_archive=value)['NAS100']
    assert result['session']['status']=='TRAINED'
    assert result['event']['status']!='TRAINED'


def test_collector_enrichment_uses_same_budget_and_optional_failure_keeps_actual_target(monkeypatch):
    import time
    from seiltanzer import mathematical_edge_sources as sources
    base=sources._make_source('NAS100','Yahoo Finance via yfinance','^NDX',
        [sources._bar(NOW//300*300-300,100.,102.,99.,101.)],NOW,{})
    monkeypatch.setattr(sources,'fetch_yahoo',lambda *args,**kwargs:deepcopy(base))
    calls=[]
    def enrich(code,captured,budget):
        calls.append((code,captured,budget.deadline))
        return {key:value for key,value in hourly_source().items() if key.startswith('event_hourly_')}
    monkeypatch.setattr(module(),'collect_hourly_events',enrich)
    captured=NOW
    rows,errors,attempts=sources.collect_fresh_sources(captured,codes=['NAS100'],macro_archive=archive(),budget_seconds=30)
    assert len(calls)==1 and calls[0][0]=='NAS100'
    assert calls[0][2]-time.monotonic()<=30
    assert rows[0]['event_hourly_archive']['interval']=='1h'
    assert attempts['NAS100']['hourly_event_rows']==len(rows[0]['event_hourly_bars'])
    monkeypatch.setattr(module(),'collect_hourly_events',lambda *args:(_ for _ in ()).throw(TimeoutError('hourly unavailable')))
    rows,errors,attempts=sources.collect_fresh_sources(captured,codes=['NAS100'],macro_archive=archive(),budget_seconds=30)
    assert not errors and rows[0]['source_sha256']==base['source_sha256']
    assert 'TimeoutError' in attempts['NAS100']['hourly_event_error']


@pytest.mark.parametrize('failure',['HTTP_403','HTTP Error 451','YFRateLimitError: Too Many Requests'])
def test_hourly_query_does_not_repeat_a_yahoo_host_denial(failure,monkeypatch):
    import time
    from seiltanzer import mathematical_edge_sources as sources
    seed=sources._make_source('NAS100','Yahoo Finance via yfinance','^NDX',
        [sources._bar(NOW//300*300-300,100.,102.,99.,101.)],NOW,{})
    monkeypatch.setattr(sources,'fetch_yahoo',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError(failure)))
    calls=[];monkeypatch.setattr(module(),'collect_hourly_events',lambda *args:calls.append(args))
    rows,errors,attempts=sources.collect_fresh_sources(NOW,codes=['NAS100'],seed_sources=[seed],macro_archive=archive(),budget_seconds=30)
    assert rows and not calls
    assert attempts['NAS100'].get('hourly_event_error')=='YAHOO_HOST_DENIED_DURING_PRIMARY_COLLECTION'


def test_pretest_eligible_five_minute_contract_does_not_switch_after_final_labels_change():
    from dataclasses import replace
    from types import MappingProxyType
    from seiltanzer.edge_family_market import train_market_families
    raw,value=training_case()
    template=value._points['macro.cpi_core_yoy_pct'][0]
    start=raw['bars'][0]['bar_end_ts']-300
    releases=[replace(template,available_at=start+i*3600,published_at=start+i*3600,
        source_id='unit-dense-cpi-'+str(i)) for i in range(0,150,4)]
    object.__setattr__(value,'_points',MappingProxyType({'macro.cpi_core_yoy_pct':tuple(releases)}))
    object.__setattr__(value,'_times',MappingProxyType({'macro.cpi_core_yoy_pct':tuple(p.available_at for p in releases)}))
    for flatten in (False,True):
        candidate=deepcopy(raw)
        if flatten:
            price=candidate['bars'][1440]['close']
            for row in candidate['bars'][1441:]:row.update(open=price,high=price,low=price,close=price)
        candidate['source_sha256']=fingerprint(candidate['bars'])
        result=train_market_families([candidate],NOW,horizons=(15,60,120),macro_archive=value)['NAS100']['event']
        assert 'official_event_archive' in result
        assert 'hourly_target_contract' not in result


def test_frozen_hourly_event_forecast_matches_direct_engine_and_cannot_claim_fifteen_minute_head(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from seiltanzer import mathematical_edge
    from seiltanzer.edge_family_market import train_market_families,attach_market_family_context
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    raw,value=training_case()
    models=train_market_families([raw],NOW,horizons=(15,60),macro_archive=value)
    assert models['NAS100']['event']['status']=='TRAINED'
    monkeypatch.setattr(mathematical_edge,'load_artifact',lambda *args:dict(family_market_models=models))
    minute=[[bar['bar_end_ts']-300+i*60,bar['open'],bar['high'],bar['low'],bar['close']]
        for bar in raw['bars'][-3:] for i in range(5)]
    feed=SimpleNamespace(instrument_code='NAS100',demo=False,intraday_ohlcv=minute,
        intraday_source_authority=dict(provider='Yahoo',source_symbol='^NDX',target_instrument='NAS100',
            source_verified=True,observed_ts=raw['bars'][-1]['bar_end_ts'],available_at=NOW+10))
    engine=SimpleNamespace(market=feed,settings=SimpleNamespace(data_dir=tmp_path))
    payload=_bundle()['bls_records'][0]['payload']
    snapshot=dict(captured_ts=NOW+20,strategy=dict(instrument='NAS100',direction='long'),
        policy_manager={'inputs':{'horizon_minutes':60}},macro_context_v1={'numeric_macro':{
            'releases':{'CPI':dict(status='VALID',release_id='unit-current-cpi',official_source_verified=True,
                published_at=NOW-1800,available_at=NOW-1700,fetched_at=NOW-1700,payload=payload)},
            'candidate_vector':{'macro.cpi_core_yoy_pct':payload['core_yoy_pct']}}})
    direct=deepcopy(snapshot);attach_market_family_context(engine,direct)
    attach_market_family_context(capture_market_inputs(engine),snapshot)
    assert snapshot['edge_family_market_context']['families']['event']['features']==direct['edge_family_market_context']['families']['event']['features']
    event=build_edge_family_evidence(snapshot)['families']['event']
    assert event['forecast_available']
    assert all(head['horizon_minutes']==60 for head in event['market_predictions'].values())
    snapshot['policy_manager']['inputs']['horizon_minutes']=15
    assert build_edge_family_evidence(snapshot)['families']['event']['forecast_available'] is False


def test_hourly_marker_cannot_bypass_archive_validation_by_dropping_parent_proof():
    from seiltanzer.edge_family_market import train_market_families,_model_reason
    raw,value=training_case()
    model=train_market_families([raw],NOW,horizons=(15,60),macro_archive=value)['NAS100']['event']
    model.pop('official_event_archive');model['features_are_captured_receipts']=True
    model['model_sha256']=fingerprint({key:value for key,value in model.items() if key!='model_sha256'})
    assert _model_reason(model,'NAS100','event',NOW+20) is not None


def test_hourly_label_declaration_also_requires_its_target_proof():
    from seiltanzer.edge_family_market import train_market_families,_model_reason
    raw,value=training_case()
    model=train_market_families([raw],NOW,horizons=(15,60),macro_archive=value)['NAS100']['event']
    model.pop('event_hourly_target_archive');model.pop('hourly_target_contract');model['source']['interval']='5m'
    model['model_sha256']=fingerprint({key:value for key,value in model.items() if key!='model_sha256'})
    assert _model_reason(model,'NAS100','event',NOW+20) is not None


def test_retained_actual_price_history_ignores_large_optional_hourly_sidecar(monkeypatch):
    from seiltanzer import mathematical_edge_sources as sources
    raw,value=training_case()
    seed=sources._make_source('NAS100','Yahoo Finance via yfinance','^NDX',raw['bars'],NOW,{})
    seed.update({key:raw[key] for key in ('event_hourly_bars','event_hourly_archive')})
    monkeypatch.setattr(sources,'fetch_yahoo',lambda *a,**kw:(_ for _ in ()).throw(TimeoutError('primary unavailable')))
    monkeypatch.setattr(module(),'collect_hourly_events',lambda *args:(_ for _ in ()).throw(TimeoutError('hourly unavailable')))
    rows,errors,attempts=sources.collect_fresh_sources(NOW,codes=['NAS100'],seed_sources=[seed],macro_archive=value,budget_seconds=30)
    assert not errors and rows[0]['source_sha256']==seed['source_sha256']
    assert attempts['NAS100']['fallback']=='READ_ONLY_EXPORTED_SOURCE'


def test_hourly_model_availability_declaration_does_not_claim_five_minute_driver_inputs():
    from seiltanzer.edge_family_market import train_market_families
    raw,value=training_case()
    model=train_market_families([raw],NOW,horizons=(15,60),macro_archive=value)['NAS100']['event']
    assert model['historical_bar_availability']=='COMPLETED_HOURLY_PROVIDER_RECORDS_OPEN_TO_OPEN_NOT_RECORDED_RECEIPT'


def test_retention_preserves_older_actual_bars_even_when_archive_has_hourly_sidecar():
    from seiltanzer import mathematical_edge_sources as sources
    raw,value=training_case()
    older=sources._make_source('NAS100','Yahoo Finance via yfinance','^NDX',raw['bars'],NOW-1,{})
    older.update(fetched_ts=NOW-1,receipt_observed_ts=NOW-1)
    older.update({key:raw[key] for key in ('event_hourly_bars','event_hourly_archive')})
    current=sources._make_source('NAS100','Yahoo Finance via yfinance','^NDX',raw['bars'][-3:],NOW,{})
    result=sources.retain_yahoo_history(current,[older],NOW)
    assert len(result['bars'])==len(older['bars'])
