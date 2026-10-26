"""Exact hourly-open event responses; no reconstructed five-minute prices."""
from __future__ import annotations
from collections.abc import Mapping
import math
import re
import time
import numpy as np
from .config import ALL_INSTRUMENTS,CRYPTO_INSTRUMENTS
from .mathematical_edge import fingerprint,number
from .real_input_integrity import simulated_evidence

CONTRACT='official-event-hourly-open-target-v1'
MAX_ROWS=20000
HORIZONS=(60,120)


def _n(value):
    return None if isinstance(value,(bool,np.bool_)) else number(value)


def provenance_valid(proof,code,trained,cutoff):
    try:
        if (code not in ALL_INSTRUMENTS or code in CRYPTO_INSTRUMENTS or not isinstance(proof,dict)
                or simulated_evidence(proof) or proof.get('contract_version')!=CONTRACT
                or proof.get('instrument')!=code or proof.get('provider')!='Yahoo Finance via yfinance'
                or proof.get('ticker')!=ALL_INSTRUMENTS[code].yahoo or proof.get('interval')!='1h'
                or proof.get('actual_granularity') not in ('1h','60m') or proof.get('bar_seconds')!=3600
                or proof.get('requested_period')!='2y' or proof.get('request_cutoff_ts')!=trained
                or proof.get('historical_first_receipt_known') is not False
                or proof.get('incomplete_final_rows_excluded') is not True
                or not isinstance(proof.get('series_sha256'),str)
                or re.fullmatch('[a-f0-9]{64}',proof['series_sha256']) is None):return False
        received=_n(proof.get('received_ts'))
        return received is not None and 0<trained<=received<=cutoff
    except (ValueError,TypeError,KeyError):return False


def hourly_points(code,source,captured):
    proof=source.get('event_hourly_archive');rows=source.get('event_hourly_bars')
    if (not provenance_valid(proof,code,captured,time.time()) or not isinstance(rows,list)
            or not 0<len(rows)<=MAX_ROWS or fingerprint(rows)!=proof['series_sha256']):
        raise ValueError('HOURLY_TARGET_SOURCE_OR_HASH_INVALID')
    points={}
    for row in rows:
        if not isinstance(row,dict) or simulated_evidence(row):raise ValueError('HOURLY_TARGET_ROW_INVALID')
        at,o,h,l,c=(_n(row.get(k)) for k in ('opened_ts','open','high','low','close'))
        if (any(value is None for value in (at,o,h,l,c)) or not 0<at<=captured-3600 or at%1800
                or min(o,h,l,c)<=0 or l>min(o,c) or h<max(o,c) or at in points):
            raise ValueError('HOURLY_TARGET_POINT_INVALID')
        points[at]=o
    return points


def hourly_event_dataset(code,source,captured,horizon,archive):
    from .edge_family_event_archive import archive_event_state,NAMES
    if horizon not in HORIZONS:raise ValueError('HOURLY_TARGET_HORIZON_INVALID')
    points=hourly_points(code,source,captured)
    xs=[];ys=[];times=[];ends=[];groups=[];last_end=-1
    for start in sorted(points):
        end=start+horizon*60
        if start<last_end or any(start+step not in points for step in range(0,horizon*60+1,3600)):continue
        state=archive_event_state(archive,start)
        if state is None:continue
        ret=math.log(points[end]/points[start])
        xs.append([state['features'][name] for name in NAMES]);ys.append([float(ret>0),float(abs(ret)>.0002),ret])
        times.append(start);ends.append(end);groups.append((state['source']['dependency_group'],));last_end=end
    return dict(x=np.asarray(xs,dtype=float).reshape((-1,len(NAMES))),y=np.asarray(ys,dtype=float).reshape((-1,3)),
        times=np.asarray(times,dtype=float),ends=np.asarray(ends,dtype=float),groups=groups,feature_names=list(NAMES))


def collect_hourly_events(code,captured,budget):
    """One bounded exact-symbol query; actual retrieval is later than history."""
    if code not in ALL_INSTRUMENTS or code in CRYPTO_INSTRUMENTS:
        raise ValueError('HOURLY_TARGET_CONFIGURED_NONCRYPTO_REQUIRED')
    budget.acquire()
    if budget.remaining()<=0:raise RuntimeError('COLLECTION_TIME_BUDGET_EXHAUSTED')
    import yfinance as yf
    from .real_input_integrity import FLAGS
    ticker=ALL_INSTRUMENTS[code].yahoo
    provider=yf.Ticker(ticker)
    frame=provider.history(period='2y',interval='1h',auto_adjust=False,actions=False,
        repair=False,timeout=min(12.,budget.remaining()),raise_errors=True)
    received=time.time();metadata=provider.get_history_metadata()
    if (not isinstance(metadata,Mapping) or metadata.get('symbol')!=ticker
            or metadata.get('dataGranularity') not in ('1h','60m')
            or simulated_evidence({key:metadata.get(key) for key in FLAGS})
            or simulated_evidence(getattr(frame,'attrs',{}))
            or not 0<len(frame)<=MAX_ROWS or getattr(frame.index,'tz',None) is None):
        raise ValueError('HOURLY_PROVIDER_SCHEMA_OR_IDENTITY_INVALID')
    rows=[]
    for timestamp,row in frame.iterrows():
        at=float(timestamp.timestamp())
        if at+3600>captured:continue
        values={key:_n(row.get(column)) for key,column in
            (('open','Open'),('high','High'),('low','Low'),('close','Close'))}
        rows.append(dict(opened_ts=at,**values))
    proof=dict(contract_version=CONTRACT,instrument=code,provider='Yahoo Finance via yfinance',ticker=ticker,
        interval='1h',bar_seconds=3600,request_cutoff_ts=captured,received_ts=received,
        actual_granularity=metadata['dataGranularity'],requested_period='2y',historical_first_receipt_known=False,
        incomplete_final_rows_excluded=True,series_sha256=fingerprint(rows))
    result=dict(event_hourly_bars=rows,event_hourly_archive=proof)
    hourly_points(code,result,captured)
    return result


def model_provenance_valid(model,trained,cutoff):
    try:
        proof=model.get('event_hourly_target_archive');source=model.get('source') or {}
        horizons=model.get('requested_horizons_minutes')
        return (cutoff is not None and provenance_valid(proof,model.get('instrument'),trained,cutoff)
            and model.get('family_id')=='event' and model.get('hourly_target_contract')==CONTRACT
            and model.get('historical_target_prices')=='COMPLETED_PROVIDER_HOURLY_OPEN_TO_OPEN'
            and model.get('historical_bar_availability')=='COMPLETED_HOURLY_PROVIDER_RECORDS_OPEN_TO_OPEN_NOT_RECORDED_RECEIPT'
            and source.get('interval')=='1h' and source.get('bars_sha256')==proof['series_sha256']
            and source.get('provider')==proof['provider'] and source.get('ticker')==proof['ticker']
            and isinstance(horizons,list) and bool(horizons) and all(h in HORIZONS for h in horizons)
            and all(row.get('horizon_minutes') in HORIZONS for row in model['heads'].values() if 'head' in row))
    except (ValueError,TypeError,KeyError):return False
