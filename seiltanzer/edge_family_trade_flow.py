"""Completed configured Binance taker-volume response, not broker book flow."""
from __future__ import annotations

import math
import re

import numpy as np

from .config import ALL_INSTRUMENTS
from .mathematical_edge import fingerprint, number
from .real_input_integrity import simulated_evidence

CONTRACT='binance-completed-taker-volume-market-response-v1'
NAMES=('flow.binance_log_quote_volume_5m','flow.binance_log_trades_5m','flow.binance_taker_buy_share_5m')


def flow_features(row):
    if not isinstance(row,dict) or simulated_evidence(row):
        return None
    volume,quote,trades,buy,buy_quote=(number(row.get(key)) for key in
        ('volume','quote_asset_volume','trades','taker_buy_base_volume','taker_buy_quote_volume'))
    if (any(value is None for value in (volume,quote,trades,buy,buy_quote))
            or min(volume,quote,trades)<=0 or min(buy,buy_quote)<0 or buy>volume or buy_quote>quote or trades!=int(trades)):
        return None
    return dict(zip(NAMES,(math.log1p(quote),math.log1p(trades),buy/volume)))


def live_flow_observation(record, *, code, cutoff, origin):
    configured=ALL_INSTRUMENTS.get(code)
    if (configured is None or configured.asset_class!='crypto' or not isinstance(record,dict)
            or simulated_evidence(record) or record.get('provider')!='Binance'
            or record.get('symbol')!=configured.binance_symbol or number(origin) is None
            or number(cutoff) is None or origin%300 or not 0<=cutoff-origin<=60):
        return None
    received=number(record.get('received_ts'))
    rows=record.get('rows')
    if received is None or not origin<=received<=cutoff or not isinstance(rows,list) or len(rows)>120:
        return None
    selected={}
    for raw in rows:
        if not isinstance(raw,list) or len(raw)!=12:
            return None
        start=number(raw[0]);closed=number(raw[6])
        if start is None or closed is None or start%60000 or closed!=start+59999:
            return None
        end=start/1000+60
        if origin-300<end<=origin:
            if end in selected:
                return None
            row=dict(zip(('volume','quote_asset_volume','trades','taker_buy_base_volume','taker_buy_quote_volume'),
                (raw[5],raw[7],raw[8],raw[9],raw[10])))
            if flow_features(row) is None:
                return None
            selected[end]=row
    if sorted(selected)!=[origin-240,origin-180,origin-120,origin-60,origin]:
        return None
    total={key:sum(float(row[key]) for row in selected.values()) for key in next(iter(selected.values()))}
    features=flow_features(total)
    if features is None:
        return None
    digest=fingerprint(record)
    proof=dict(source_verified=True,source_id='Binance:'+configured.binance_symbol+':taker-volume:'+digest,
        source_sha256=digest,source_instrument=code,measurement_instrument=configured.binance_symbol,
        observed_ts=origin,received_ts=received,available_at=received,max_age_sec=60.,quality=.75,
        dependency_group='price_path',factor_contract=CONTRACT,window_seconds=300,
        clock_basis='EXCHANGE_COMPLETED_MINUTES_AND_ACTUAL_KLINE_RECEIPT',
        observed_broker_order_book=False,target_quote_authority=False)
    return dict(captured_ts=received,features=features,feature_provenance={n:dict(proof) for n in features})


def fit_trade_flow(code,bars,captured,horizons,source,provenance):
    from .edge_family_market import market_dataset,_fit_family
    try:
        history=source.get('trade_flow_history')
        if (not isinstance(history,list) or not 0<len(history)<=20000
                or source.get('trade_flow_sha256')!=fingerprint(history)):
            raise ValueError('BINANCE_TRADE_FLOW_HASH_INVALID')
        collection=source.get('collection') or {}
        if (source.get('provider')!='Binance' or collection.get('contract_version')!='binance-public-archive-v1'
                or collection.get('historical_first_receipt_known') is not False
                or not isinstance(collection.get('files'),list) or not 1<=len(collection['files'])<=64):
            raise ValueError('BINANCE_TRADE_FLOW_ARCHIVE_PROVENANCE_INVALID')
        by_time={}
        for row in history:
            stamp=number(row.get('bar_end_ts')) if isinstance(row,dict) else None
            if stamp is None or stamp%300 or stamp in by_time or stamp>captured:
                raise ValueError('BINANCE_TRADE_FLOW_TIME_INVALID')
            by_time[stamp]=flow_features(row)
        if not set(by_time)<={row['bar_end_ts'] for row in bars}:
            raise ValueError('BINANCE_TRADE_FLOW_TARGET_ALIGNMENT_INVALID')
        datasets={}
        for horizon in horizons:
            base=market_dataset(code,'session',bars,captured,horizon)
            indices=[i for i,t0 in enumerate(base['times']) if by_time.get(float(t0)) is not None]
            datasets[horizon]={**base,'x':np.asarray([[by_time[float(base['times'][i])][n] for n in NAMES]
                for i in indices],dtype=float).reshape((-1,len(NAMES))),
                'y':base['y'][indices],'times':base['times'][indices],'ends':base['ends'][indices],
                'groups':[base['groups'][i] for i in indices],'feature_names':list(NAMES)}
        result=_fit_family(code,'order_flow',bars,captured,horizons,None,None,provenance,datasets_override=datasets)
        if result['status']=='TRAINED':
            result.pop('model_sha256',None)
            result.update(features_are_captured_receipts=False,public_trade_flow_archive=dict(
                contract_version=CONTRACT,provider='Binance',symbol=source['ticker'],interval='5m',
                trade_flow_sha256=source['trade_flow_sha256'],files=collection['files'],
                historical_first_receipt_known=False,observed_broker_order_book=False),
                historical_feature_availability='COMPLETED_EXCHANGE_KLINES_ACTUAL_ARCHIVE_RETRIEVAL_RETAINED')
            result['model_sha256']=fingerprint(result)
        return result
    except (ValueError,TypeError,KeyError) as exc:
        return dict(status='HISTORICAL_FEATURES_UNAVAILABLE',reason=str(exc)[:180],forecast=None)


def archive_provenance_valid(model,code,cutoff):
    proof=model.get('public_trade_flow_archive')
    if (not isinstance(proof,dict) or proof.get('contract_version')!=CONTRACT
            or proof.get('provider')!='Binance' or proof.get('symbol')!=ALL_INSTRUMENTS[code].binance_symbol
            or proof.get('interval')!='5m' or proof.get('historical_first_receipt_known') is not False
            or proof.get('observed_broker_order_book') is not False
            or not isinstance(proof.get('trade_flow_sha256'),str)
            or re.fullmatch('[0-9a-f]{64}',proof['trade_flow_sha256']) is None):
        return False
    files=proof.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=64:
        return False
    from .binance_public_archive import _period,BASE
    try:
        for row in files:
            filename=row['filename'];start,end=_period(filename,proof['symbol'])
            part='daily' if len(filename.removesuffix('.zip').split('-'))==5 else 'monthly'
            if (row.get('url')!=f'{BASE}/{part}/klines/{proof["symbol"]}/5m/{filename}'
                    or any(not isinstance(row.get(key),str) or re.fullmatch('[0-9a-f]{64}',row[key]) is None
                        for key in ('sha256','checksum_sha256'))
                    or number(row.get('received_ts')) is None or not 0<row['received_ts']<=cutoff):
                return False
    except (ValueError,TypeError,KeyError):
        return False
    return True
