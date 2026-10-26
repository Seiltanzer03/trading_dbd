"""Independent family-forecast references; never execution quotes or fills."""
from __future__ import annotations

import json
import os
import time
import uuid

from .config import ALL_INSTRUMENTS, CRYPTO_INSTRUMENTS
from .mathematical_edge import fingerprint, number as _finite_number
from .real_input_integrity import simulated_evidence

PROVIDER = 'TradingView chart'
CONTRACT = 'configured-family-reference-5m-v1'
SYMBOLS = {code: item.tradingview_symbol for code,item in ALL_INSTRUMENTS.items()
           if code not in CRYPTO_INSTRUMENTS and item.tradingview_symbol}
SYMBOLS.update(XAU='OANDA:XAUUSD',XAG='OANDA:XAGUSD',EURUSD='OANDA:EURUSD',USDCAD='OANDA:USDCAD')
MAX_BARS = 4800


def number(value):
    return (_finite_number(value) if isinstance(value,(int,float)) and not isinstance(value,bool) else None)


def _bar(row):
    if not isinstance(row,list) or len(row)<5:
        raise ValueError('REFERENCE_BAR_SCHEMA_INVALID')
    start,open_,high,low,close = map(number,row[:5])
    if (any(v is None for v in (start,open_,high,low,close)) or start<=0 or start%300
            or min(open_,high,low,close)<=0 or low>min(open_,close) or high<max(open_,close)):
        raise ValueError('REFERENCE_BAR_VALUE_INVALID')
    return dict(bar_end_ts=start+300,open=open_,high=high,low=low,close=close)


def validate_reference_series(source,code,cutoff,*,runtime=False):
    try:
        if (code not in SYMBOLS or number(cutoff) is None or not isinstance(source,dict)
                or simulated_evidence({k:v for k,v in source.items() if k!='bars'})
                or source.get('contract_version')!=CONTRACT or source.get('instrument')!=code
                or source.get('provider')!=PROVIDER or source.get('ticker')!=SYMBOLS[code]
                or source.get('resolved_symbol')!=SYMBOLS[code] or source.get('interval')!='5m'
                or source.get('not_broker_execution_bars') is not True
                or source.get('canonical_execution_quote_replaced') is not False):
            return 'REFERENCE_IDENTITY_INVALID'
        bars=source.get('bars');receipt=number(source.get('received_ts'));observed=number(source.get('observed_ts'))
        started=number(source.get('request_started_ts'))
        if (not isinstance(bars,list) or not 1<=len(bars)<=MAX_BARS
                or source.get('source_sha256')!=fingerprint(bars) or receipt is None or observed is None
                or started is None or not 0<started<=receipt):
            return 'REFERENCE_HASH_OR_CLOCK_INVALID'
        ends=[]
        for row in bars:
            if not isinstance(row,dict) or simulated_evidence(row):return 'REFERENCE_BAR_INVALID'
            end=number(row.get('bar_end_ts'))
            if end is None:return 'REFERENCE_BAR_INVALID'
            checked=_bar([end-300,*[row.get(k) for k in ('open','high','low','close')]])
            if checked!=row or end>min(cutoff,started):return 'REFERENCE_BAR_INVALID'
            ends.append(end)
        if ends!=sorted(set(ends)) or observed!=ends[-1] or not 0<observed<=receipt:
            return 'REFERENCE_CLOCK_INVALID'
        if runtime and (receipt>cutoff or cutoff-observed>=900 or observed!=(cutoff//300)*300):
            return 'REFERENCE_LATEST_COMPLETED_BAR_UNAVAILABLE'
    except (ValueError,TypeError,KeyError,OverflowError):
        return 'REFERENCE_INVALID'
    return None


def fetch_reference_series(code,*,limit=MAX_BARS,timeout=8.,captured_ts=None,
                           connect_factory=None,clock=time.time):
    if (code not in SYMBOLS or type(limit) is not int or not 1<=limit<=MAX_BARS
            or number(timeout) is None or not 0<timeout<=20):
        raise ValueError('REFERENCE_REQUEST_INVALID')
    if captured_ts is not None and (number(captured_ts) is None or captured_ts<=0):
        raise ValueError('REFERENCE_CUTOFF_INVALID')
    from .data.feeds import _tv_frame,_tv_payloads
    if connect_factory is None:
        from websockets.sync.client import connect
        connect_factory=connect
    session='cs_'+uuid.uuid4().hex[:12];symbol=SYMBOLS[code]
    started=clock()
    if number(started) is None or started<=0:raise ValueError('REFERENCE_REQUEST_CLOCK_INVALID')
    deadline=time.monotonic()+timeout;rows={};resolved=None;complete=False;size=0
    with connect_factory('wss://data.tradingview.com/socket.io/websocket',
            origin='https://data.tradingview.com',open_timeout=timeout,close_timeout=1,max_size=4_000_000) as ws:
        ws.recv(timeout=max(.1,deadline-time.monotonic()))
        commands=[('set_auth_token',[os.environ.get('TRADINGVIEW_AUTH_TOKEN','unauthorized_user_token')]),
            ('chart_create_session',[session,'']),
            ('resolve_symbol',[session,'sym_1','='+json.dumps(dict(symbol=symbol,adjustment='splits',session='regular'),separators=(',',':'))]),
            ('create_series',[session,'s1','s1','sym_1','5',limit])]
        for method,params in commands:ws.send(_tv_frame(method,params))
        while time.monotonic()<deadline:
            raw=ws.recv(timeout=max(.1,deadline-time.monotonic()));size+=len(raw.encode() if isinstance(raw,str) else raw)
            if size>4_000_000:raise ValueError('REFERENCE_BODY_BOUND')
            for payload in _tv_payloads(raw):
                if payload.startswith('~h~'):
                    ws.send(f'~m~{len(payload.encode())}~m~{payload}');continue
                try:message=json.loads(payload)
                except (ValueError,TypeError):continue
                if not isinstance(message,dict):raise ValueError('REFERENCE_MESSAGE_INVALID')
                kind=message.get('m');params=message.get('p') or []
                if not isinstance(params,list):raise ValueError('REFERENCE_MESSAGE_INVALID')
                if kind in {'critical_error','series_error','symbol_error','protocol_error'}:
                    raise ValueError('REFERENCE_PROVIDER_REFUSED')
                if kind in {'symbol_resolved','timescale_update','du','series_completed'} and (not params or params[0]!=session):
                    continue
                if kind=='symbol_resolved' and len(params)>2 and isinstance(params[2],dict):
                    if params[1]!='sym_1':continue
                    meta=params[2]
                    if (meta.get('full_name')!=symbol or meta.get('pro_name')!=symbol
                            or meta.get('exchange')!={'FPMARKETS':'FP Markets','OANDA':'OANDA'}[symbol.split(':')[0]]):
                        raise ValueError('REFERENCE_RESOLVED_IDENTITY_INVALID')
                    resolved=symbol
                if kind in {'timescale_update','du'} and len(params)>1 and isinstance(params[1],dict):
                    batch=(params[1].get('s1') or {}).get('s') or []
                    if not isinstance(batch,list) or len(batch)>limit:raise ValueError('REFERENCE_ROW_BOUND')
                    for item in batch:
                        bar=_bar(item.get('v') if isinstance(item,dict) else None);end=bar['bar_end_ts']
                        if end in rows and rows[end]!=bar:raise ValueError('REFERENCE_CONFLICTING_BAR')
                        rows[end]=bar
                        if len(rows)>limit:raise ValueError('REFERENCE_ROW_BOUND')
                if kind=='series_completed' and len(params)>1 and params[1]=='s1':complete=True
            if complete:break
    if not complete or resolved is None:raise ValueError('REFERENCE_RESPONSE_INCOMPLETE')
    receipt=clock()
    if number(receipt) is None or receipt<started:raise ValueError('REFERENCE_RECEIPT_CLOCK_INVALID')
    cutoff=min(started,captured_ts) if captured_ts is not None else started
    bars=[rows[end] for end in sorted(rows) if end<=cutoff]
    return dict(contract_version=CONTRACT,provider=PROVIDER,instrument=code,ticker=symbol,
        interval='5m',resolved_symbol=resolved,bars=bars,source_sha256=fingerprint(bars),
        received_ts=receipt,request_started_ts=started,observed_ts=bars[-1]['bar_end_ts'] if bars else None,
        not_broker_execution_bars=True,canonical_execution_quote_replaced=False,
        reference_scope='INDEPENDENT_FORECAST_REFERENCE_NOT_EXECUTION_QUOTE',
        historical_bar_availability='COMPLETED_PROVIDER_BAR_NOT_RECORDED_HISTORICAL_RECEIPT')


def model_source_valid(source,code):
    return (isinstance(source,dict) and code in SYMBOLS and source.get('provider')==PROVIDER
        and source.get('ticker')==SYMBOLS[code] and source.get('reference_contract')==CONTRACT
        and source.get('resolved_symbol')==SYMBOLS[code] and source.get('interval')=='5m'
        and source.get('not_broker_execution_bars') is True
        and isinstance(source.get('bars_sha256'),str) and len(source['bars_sha256'])==64
        and all(c in '0123456789abcdef' for c in source['bars_sha256']))


def runtime_proof(source):
    proof={k:source[k] for k in ('contract_version','provider','instrument','ticker','resolved_symbol',
        'interval','received_ts','request_started_ts','observed_ts','source_sha256','not_broker_execution_bars',
        'canonical_execution_quote_replaced')}
    proof['last_bar']=dict(source['bars'][-1])
    return {**proof,'proof_sha256':fingerprint(proof)}


def validate_runtime_proof(proof,code,cutoff):
    try:
        if not isinstance(proof,dict) or simulated_evidence(proof):return 'REFERENCE_PROOF_INVALID'
        if proof.get('proof_sha256')!=fingerprint({k:v for k,v in proof.items() if k!='proof_sha256'}):
            return 'REFERENCE_PROOF_INVALID'
        original=proof.get('source_sha256')
        if (not isinstance(original,str) or len(original)!=64
                or any(c not in '0123456789abcdef' for c in original)):return 'REFERENCE_PROOF_INVALID'
        bar=proof['last_bar'];single={**proof,'bars':[bar],'source_sha256':fingerprint([bar])}
        return validate_reference_series(single,code,cutoff,runtime=True)
    except (ValueError,TypeError,KeyError,OverflowError):return 'REFERENCE_PROOF_INVALID'


def collect_reference_sources(captured,*,budget_seconds=45.,codes=None,fetch=None,monotonic=time.monotonic):
    if number(budget_seconds) is None or not 0<budget_seconds<=60:
        raise ValueError('REFERENCE_TOTAL_BUDGET_INVALID')
    codes=tuple(SYMBOLS if codes is None else codes)
    if len(codes)>10 or len(set(codes))!=len(codes) or any(c not in SYMBOLS for c in codes):
        raise ValueError('REFERENCE_CODES_INVALID')
    fetch=fetch or fetch_reference_series
    deadline=monotonic()+budget_seconds;sources=[];errors={}
    for code in codes:
        remaining=deadline-monotonic()
        if remaining<=0:
            errors[code]='REFERENCE_TOTAL_BUDGET_EXHAUSTED';continue
        try:
            source=fetch(code,limit=MAX_BARS,timeout=min(8.,remaining),captured_ts=captured)
            reason=validate_reference_series(source,code,captured)
            if reason:raise ValueError(reason)
            sources.append(source)
        except Exception as exc:
            errors[code]=type(exc).__name__
    return sources,errors
