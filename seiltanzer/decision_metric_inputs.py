"""Current decision metrics using the unchanged recorded V2/V3 definitions.

Research anchors remain immutable. A position review need not wait for the next
cash-session/event capture or for fitting to release its G.1 connection lock.
This module never fetches providers, trains models or writes research/trades.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import threading
import time
import zlib

CONTRACT='decision-model-metric-inputs-v1'
MAX_RAW_BYTES=128_000
MAX_WIRE_BYTES=8_000
MAX_CONTEXT_BYTES=2_000_000
_PINNED=ContextVar('decision_model_metric_context',default=None)


def _number(value):
    return float(value) if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) else None


def _invalid_constant(value):
    raise ValueError('NONFINITE_DECISION_METRIC_JSON')


def _history(engine,code,cutoff):
    path=Path(engine.settings.data_dir)/'trades.db'
    connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA cache_size=-1024')
        deadline=time.monotonic()+.25
        connection.set_progress_handler(lambda:time.monotonic()>deadline,1000)
        rows=connection.execute('SELECT bar_start_ts,bar_end_ts,open,high,low,close,source,quality,kind '
            'FROM passive_market_bars WHERE instrument=? AND bar_end_ts<=? '
            'AND bar_end_ts>=? AND created_ts<=? ORDER BY bar_end_ts DESC LIMIT 2001',
            (code,cutoff,cutoff-30*3600,cutoff)).fetchall()
        if len(rows)>2000:raise ValueError('LIVE_METRIC_BAR_BOUND')
        return [dict(row) for row in reversed(rows)]
    finally:connection.close()


def _received_bars(market,code,cutoff):
    """Use received 1m bars even before the research writer archives them."""
    from .config import ALL_INSTRUMENTS,CRYPTO_INSTRUMENTS
    authority=getattr(market,'intraday_source_authority',None) or {}
    raw=getattr(market,'intraday_ohlcv',None) or []
    if not isinstance(authority,dict) or not isinstance(raw,(list,tuple)) or len(raw)>4096:return []
    crypto=code in CRYPTO_INSTRUMENTS
    expected=ALL_INSTRUMENTS[code]
    symbol=expected.binance_symbol if crypto else expected.yahoo
    received=_number(authority.get('available_at'));observed=_number(authority.get('observed_ts'))
    offset=getattr(market,'intraday_is_offset',False)
    if (authority.get('provider')!=('Binance' if crypto else 'Yahoo')
            or authority.get('source_symbol')!=symbol or authority.get('target_instrument')!=code
            or authority.get('source_verified') is not True or authority.get('interval_sec')!=60
            or authority.get('derived') is not offset or received is None or observed is None
            or not 0<observed<=received<=cutoff or crypto and offset):return []
    source=('binance_1m_direct:'+symbol if crypto else
            'yahoo_1m_offset_adjusted' if offset else 'yahoo_1m_direct')
    rows=[]
    for row in raw:
        if not isinstance(row,(list,tuple)) or len(row)!=6:continue
        start,open_,high,low,close=(_number(value) for value in row[:5])
        if (any(value is None for value in (start,open_,high,low,close)) or start<0
                or abs(start%60)>1e-6 or not cutoff-30*3600<=start+60<=observed
                or min(open_,high,low,close)<=0 or low>min(open_,close) or high<max(open_,close)):continue
        rows.append(dict(bar_start_ts=start,bar_end_ts=start+60,open=open_,high=high,low=low,close=close,
                         source=source,quality=1.,kind='derived' if offset else 'direct'))
    return rows


def _option_history(engine,proxy,cutoff):
    """One bounded independent read, shared by V2 and V3; never take the writer lock."""
    if not proxy:return []
    path=Path(getattr(engine.settings,'cache_db',Path(engine.settings.data_dir)/'cache.db'))
    connection=None
    try:
        connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)
        connection.execute('PRAGMA query_only=ON');connection.execute('PRAGMA cache_size=-1024')
        deadline=time.monotonic()+.25
        connection.set_progress_handler(lambda:time.monotonic()>deadline,1000)
        output=[];size=0
        cursor=connection.execute('SELECT ts,length(data),substr(data,1,2000001) FROM chain_snapshots '
            'WHERE ticker=? AND ts<=? ORDER BY ts DESC LIMIT 120',(proxy,cutoff))
        for stamp,length,raw in cursor:
            size+=length
            if length>2_000_000 or size>8_000_000 or time.monotonic()>deadline:return []
            payload=json.loads(raw,parse_constant=_invalid_constant)
            if not isinstance(payload,dict):return []
            output.append({**payload,'ts':stamp})
        return list(reversed(output))
    except (OSError,sqlite3.Error,ValueError,TypeError):return []
    finally:
        if connection is not None:connection.close()


def _native_minute_rows(market,code,cutoff):
    from .reference_market_series import validate_reference_series
    source=getattr(market,'decision_minute_reference',None)
    if validate_reference_series(source,code,cutoff,runtime=True,interval='1m') is not None:
        return None,None
    rows=[dict(bar_start_ts=row['bar_end_ts']-60,**row,quality=1.,kind='reference',
               source=source['provider']+':'+source['ticker']+':1m') for row in source['bars']
          if row['bar_end_ts']>=cutoff-30*3600]
    proof=dict(provider=source['provider'],source_symbol=source['ticker'],target_instrument=code,
        observed_ts=source['observed_ts'],available_at=source['received_ts'],derived=False,proxy=True,
        broker_execution_bars=False,authority_role='DECISION_METRIC_REFERENCE_NOT_EXECUTION_QUOTE',
        contract_version=source['contract_version'],interval='1m',source_sha256=source['source_sha256'])
    return rows,proof


def capture_decision_context(engine,market,*,captured_ts):
    """Build an as-of view of received inputs, without inserting an anchor."""
    from .canonical_market_context import price_quote_available
    from .config import ALL_INSTRUMENTS
    from .passive_learning import PassiveLearningEngine
    from .g1_short_horizon_feature_contract_v2 import _build_v2
    from .g1_broad_market_evidence_v3 import build_market_evidence_v3
    code=getattr(market,'instrument_code',None)
    quote=getattr(market,'price',{})
    cutoff=_number(captured_ts)
    if (code not in ALL_INSTRUMENTS or cutoff is None or cutoff<=0
            or getattr(market,'demo',False) or not price_quote_available(quote)):
        return {}
    stamp=_number(quote.get('ts'))
    if stamp is None or not -5<=cutoff-stamp<=30:return {}
    rows,native_proof=_native_minute_rows(market,code,cutoff)
    if rows is None:
        try:rows=_history(engine,code,cutoff)
        except (OSError,sqlite3.Error):rows=[]
        # Match the archive's INSERT OR IGNORE identity. Never rewrite an existing
        # observation using a later basis adjustment of the same provider bar.
        merged={row['bar_start_ts']:row for row in rows}
        for row in _received_bars(market,code,cutoff):merged.setdefault(row['bar_start_ts'],row)
        rows=sorted(merged.values(),key=lambda row:row['bar_end_ts'])
    if len(rows)>2000:raise ValueError('LIVE_METRIC_BAR_BOUND')
    daily=getattr(market,'daily',None) or {}
    daily_ts=_number(daily.get('ts'))
    volatility=PassiveLearningEngine._reference_volatility(SimpleNamespace(
        instrument_code=code,daily=daily if daily_ts is not None and 0<daily_ts<=cutoff else {}))
    chain=getattr(market,'chain',None) or {}
    chain_ts=_number(chain.get('ts'))
    metrics=chain.get('metrics') if chain_ts is not None and 0<chain_ts<=cutoff else None
    if isinstance(metrics,dict):
        metrics=dict(metrics)
        metrics.setdefault('ts',chain_ts)
    else:metrics=None
    correlation=getattr(market,'correlation',None) or {}
    features={'source_observation_ts':stamp,'price_state':{'price':quote['value'],'available':True},
        'volatility':volatility,
        'options':{'available':metrics is not None,'stale':chain.get('status')=='delayed'},
        'option_distribution':metrics or {'available':False},
        'option_derivatives':{'available':metrics is not None,'data':metrics},
        'cross_asset':{'available':bool(correlation.get('available')),'data':correlation},
        'gex_context':{'available':bool(metrics and metrics.get('gex')),
                       'data':metrics.get('gex') if metrics else None},
        'missing_is_not_zero':True}
    # Existing feature failure handlers may persist research counters. This
    # read-only capture has its own error state and no writable connection.
    proxy=str((metrics or {}).get('proxy') or '')
    history=_option_history(engine,proxy,cutoff)
    cache=SimpleNamespace(chain_snapshots=lambda ticker,limit=10:
        history[-limit:] if ticker==proxy else [])
    parent=SimpleNamespace(cache=cache,settings=engine.passive.settings,
                           _lock=threading.RLock())
    features['g1s_evidence_v2']=_build_v2(parent,code,cutoff,quote['value'],features,
        rows=[row for row in rows if row['bar_end_ts']>=cutoff-6*3600])
    block=build_market_evidence_v3(parent,code,cutoff,quote['value'],features,
        {'options':{'quality':PassiveLearningEngine._quality(chain)}},rows=rows)
    features['g1s_evidence_v3']=block
    authority=native_proof or getattr(market,'intraday_source_authority',None)
    if not isinstance(authority,dict):authority={}
    provenance={key:value for key,value in authority.items()
        if key in ('provider','source_symbol','target_instrument','observed_ts','available_at',
                   'derived','proxy','direct_source','broker_execution_bars','authority_role',
                   'contract_version','interval','source_sha256')
        and (isinstance(value,(bool,int,float)) or isinstance(value,str) and len(value)<=256)}
    if len(json.dumps(features,allow_nan=False,separators=(',',':')).encode())>MAX_CONTEXT_BYTES:
        raise ValueError('LIVE_METRIC_CONTEXT_BOUND')
    return {**block,'observation_t0':cutoff,
        'max_age_sec':getattr(engine.settings,'ede_context_max_age_sec',900),
        'input_provenance':provenance,
        'v2_option_context':features['g1s_evidence_v2'].get('option_context') or {},
        '_raw_frozen':features,'_context_read':{'available':True,'deferred':False,
            'cache_used':False,'reason':'LIVE_DECISION_METRICS'}}


@contextmanager
def pinned_decision_context(engine,context):
    owner=getattr(engine,'_engine',engine)
    token=_PINNED.set((id(owner),context))
    try:yield
    finally:_PINNED.reset(token)


def live_decision_context(engine,snapshot):
    code=(snapshot.get('strategy') or {}).get('instrument')
    cutoff=_number(snapshot.get('captured_ts'))
    pinned=_PINNED.get();owner=getattr(engine,'_engine',engine)
    if pinned is not None and pinned[0]==id(owner):context=pinned[1]
    else:
        runtime=getattr(owner,'decision_metric_runtime',None)
        context=runtime.context_at(code,cutoff) if runtime is not None else None
    if not isinstance(context,dict):return None
    observed=_number(context.get('observation_t0'))
    if (context.get('instrument')!=code or cutoff is None or observed is None
            or not 0<=cutoff-observed<=900):return None
    return deepcopy(context)


def pack_decision_metrics(context,instrument):
    from .edge_discovery.ai_context import canonical_current_feature_map
    values=canonical_current_feature_map(context,instrument)
    raw=json.dumps(values,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if len(raw)>MAX_RAW_BYTES:raise ValueError('DECISION_METRIC_RAW_BOUND')
    encoded=base64.b64encode(zlib.compress(raw)).decode('ascii')
    if len(encoded)>MAX_WIRE_BYTES:raise ValueError('DECISION_METRIC_WIRE_BOUND')
    return {'contract_version':CONTRACT,'instrument':instrument,
        'captured_ts':context.get('observation_t0'),'metrics_encoded':encoded,
        'max_age_sec':context.get('max_age_sec',900),'input_provenance':context.get('input_provenance',{}),
        'metrics_sha256':hashlib.sha256(raw).hexdigest(),'encoding':'json-zlib-v1',
        'metric_count':len(values),'network_calls':False,'runtime_fitting':False}


def decision_metric_values(snapshot,*,max_age_sec=None):
    """None means no pin; an invalid pin is empty, never replaced by later data."""
    if 'decision_metric_inputs' not in snapshot:return None
    try:
        packet=snapshot['decision_metric_inputs']
        frozen_age=_number(packet.get('max_age_sec',900))
        if frozen_age is None or frozen_age<=0:return {}
        max_age_sec=frozen_age if max_age_sec is None else _number(max_age_sec)
        if max_age_sec is None or max_age_sec<=0:return {}
        max_age_sec=min(frozen_age,max_age_sec)
        cutoff=_number(snapshot.get('captured_ts'))
        observed=_number(packet.get('captured_ts'))
        code=(snapshot.get('strategy') or {}).get('instrument')
        if (packet.get('contract_version')!=CONTRACT or packet.get('instrument')!=code
                or packet.get('encoding')!='json-zlib-v1' or cutoff is None or observed is None
                or not 0<observed<=cutoff or len(packet['metrics_encoded'])>MAX_WIRE_BYTES):return {}
        compressed=base64.b64decode(packet['metrics_encoded'],validate=True)
        decoder=zlib.decompressobj();raw=decoder.decompress(compressed,MAX_RAW_BYTES+1)
        if (len(raw)>MAX_RAW_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                or hashlib.sha256(raw).hexdigest()!=packet['metrics_sha256']):return {}
        values=json.loads(raw,parse_constant=_invalid_constant)
        if not isinstance(values,dict) or len(values)>128:return {}
        for key,row in values.items():
            if not isinstance(row,dict) or row.get('feature_id')!=key or row.get('instrument',code)!=code:return {}
            asof=_number(row.get('asof'))
            if (asof is None or asof>observed or not 0<=cutoff-asof<=max_age_sec
                    or row.get('stale') is True):
                row.update(available=False,availability='UNAVAILABLE',stale=True,training_eligible=False)
        return values
    except (ValueError,TypeError,KeyError,AttributeError,zlib.error):return {}


class DecisionMetricRuntime:
    """Two immutable generations for the currently selected instrument only."""
    def __init__(self,engine):
        self.engine=engine;self._contexts=();self._refresh_lock=threading.Lock()
        self._status={'state':'PENDING','network_calls':False,'runtime_fitting':False}

    def status(self):return dict(self._status)

    def generation_marker(self):
        if not self._contexts:return None
        context=self._contexts[0]
        source=(context.get('price_volatility') or {}).get('quality') or {}
        options=(context.get('option_static') or {}).get('quality') or {}
        stamp=_number(source.get('source_ts'))
        return (context.get('instrument'),None if stamp is None else int(stamp//300),options.get('source_ts'))

    def context_at(self,code,cutoff):
        if cutoff is None:return None
        return next((row for row in self._contexts if row.get('instrument')==code
                     and 0<=cutoff-row['observation_t0']<=900),None)

    def refresh(self):
        if self.engine.settings.demo:return
        if not self._refresh_lock.acquire(blocking=False):return
        try:
            from .ai_frozen_expert_inputs import capture_market_inputs
            tick=self.engine.canonical_tick_payload(copy_payload=False)
            captured=_number(tick.get('ts'));code=tick.get('instrument')
            view=capture_market_inputs(self.engine,include_decision_metrics=False)
            if view.market.instrument_code!=code or captured is None:return
            view.market.price=deepcopy((tick.get('feeds') or {}).get('price') or {})
            context=capture_decision_context(self.engine,view.market,captured_ts=captured)
            if not context or self.engine.market.instrument_code!=code:return
            values=decision_metric_values({'captured_ts':captured,'strategy':{'instrument':code},
                'decision_metric_inputs':pack_decision_metrics(context,code)})
            self._contexts=(context,*[row for row in self._contexts[:1] if row.get('instrument')==code])
            self._status={'state':'READY','instrument':code,'captured_ts':captured,
                'metric_count':len(values),'available_metrics':sum(row.get('available') is True for row in values.values()),
                'network_calls':False,'runtime_fitting':False}
        except Exception as exc:
            self._status={**self._status,'state':'DEFERRED','reason':type(exc).__name__}
        finally:self._refresh_lock.release()
