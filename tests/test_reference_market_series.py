"""Recorded transport fixtures test contracts, never trading evidence."""
from copy import deepcopy
import importlib
import json

import pytest

T0 = 1791504000.


def module():
    return importlib.import_module('seiltanzer.reference_market_series')


class Socket:
    def __init__(self, symbol='OANDA:XAUUSD', rows=None, error=None, exchange=None, stream='cs'):
        from seiltanzer.data.feeds import _tv_frame
        rows = rows if rows is not None else [[T0-300,100.,102.,99.,101.,10.],
                                               [T0,101.,103.,100.,102.,20.]]
        self.messages = ['handshake', _tv_frame('symbol_resolved', [stream,'sym_1',
            {'full_name':symbol,'pro_name':symbol,'exchange':exchange or symbol.split(':')[0]}])]
        self.messages += ([_tv_frame(error,['cs','s1','denied'])] if error else
            [_tv_frame('timescale_update',[stream,{'s1':{'s':[{'v':r} for r in rows]}}]),
             _tv_frame('series_completed',[stream,'s1'])])
        self.sent=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def recv(self,**kwargs):
        if not self.messages:raise TimeoutError('end of fixture')
        from seiltanzer.data.feeds import _tv_frame,_tv_payloads
        raw=self.messages.pop(0)
        if raw=='handshake':return raw
        msg=json.loads(next(_tv_payloads(raw)))
        if msg['p'][0]=='cs':msg['p'][0]=self.session
        return _tv_frame(msg['m'],msg['p'])
    def send(self,value):
        from seiltanzer.data.feeds import _tv_payloads
        self.sent.append(value)
        message=json.loads(next(_tv_payloads(value)))
        if message['m']=='chart_create_session':self.session=message['p'][0]


def fetch(socket=None, **kwargs):
    s=socket or Socket()
    return module().fetch_reference_series('XAU', connect_factory=lambda *a,**k:s,
        clock=lambda:T0+30, **kwargs), s


def test_completed_reference_retains_actual_identity_receipt_hash_and_no_execution_authority():
    source, socket=fetch()
    assert source['ticker']=='OANDA:XAUUSD'
    assert source['provider']=='TradingView chart'
    assert source['received_ts']==T0+30
    assert source['observed_ts']==T0
    assert source['bars']==[dict(bar_end_ts=T0,open=100.,high=102.,low=99.,close=101.)]
    assert source['not_broker_execution_bars'] is True
    assert source['canonical_execution_quote_replaced'] is False
    assert module().validate_reference_series(source,'XAU',T0+30,runtime=True) is None
    assert any('create_series' in x and '"5",4800' in x for x in socket.sent)


@pytest.mark.parametrize('case',['wrong_resolved','conflict','bad_ohlc','bool_ohlc','nonaligned','protocol_denial'])
def test_malformed_or_denied_reference_never_becomes_a_price_series(case):
    s=Socket()
    if case=='wrong_resolved':s=Socket(symbol='OANDA:XAGUSD')
    elif case=='conflict':s=Socket(rows=[[T0-300,100,102,99,101],[T0-300,100,103,99,102]])
    elif case=='bad_ohlc':s=Socket(rows=[[T0-300,100,99,98,101]])
    elif case=='bool_ohlc':s=Socket(rows=[[T0-300,True,True,True,True]])
    elif case=='nonaligned':s=Socket(rows=[[T0-299,100,102,99,101]])
    else:s=Socket(error='series_error')
    with pytest.raises(ValueError):fetch(s)


@pytest.mark.parametrize('case',['hash','instrument','ticker','provider','late_receipt','stale','partial'])
def test_runtime_rejects_damaged_identity_or_current_bar_without_retiming(case):
    source,_=fetch()
    if case=='hash':source['bars'][0]['close']+=.1
    elif case=='instrument':source['instrument']='XAG'
    elif case=='ticker':source['ticker']='OANDA:XAGUSD'
    elif case=='provider':source['provider']='Yahoo'
    elif case=='late_receipt':source['received_ts']=T0+31
    elif case=='stale':source['observed_ts']=T0-901
    else:
        source['bars'][0]['bar_end_ts']=T0+300
        source['source_sha256']=module().fingerprint(source['bars'])
    assert module().validate_reference_series(source,'XAU',T0+30,runtime=True) is not None


def test_historical_cutoff_excludes_unfinished_bars_and_does_not_forge_the_receipt():
    source,_=fetch(captured_ts=T0-1)
    assert source['received_ts']==T0+30
    assert source['bars']==[]
    assert module().validate_reference_series(source,'XAU',T0-1,runtime=False) is not None


def test_crypto_cannot_enter_the_new_reference_lane():
    with pytest.raises(ValueError):module().fetch_reference_series('BTCUSD')


def test_exact_fpmarkets_identity_uses_its_actual_display_exchange_label():
    socket=Socket(symbol='FPMARKETS:GER40',exchange='FP Markets')
    source=module().fetch_reference_series('GER40',connect_factory=lambda *a,**k:socket,clock=lambda:T0+30)
    assert module().validate_reference_series(source,'GER40',T0+30,runtime=True) is None


def test_collection_obeys_one_total_budget_and_does_not_retry_provider_failure():
    calls=[];now=[0.]
    def fetch(code,**kwargs):
        calls.append((code,kwargs));now[0]+=kwargs['timeout']
        raise TimeoutError('bounded')
    sources,errors=module().collect_reference_sources(T0,budget_seconds=6.,
        codes=('NAS100','XAU'),fetch=fetch,monotonic=lambda:now[0])
    assert not sources
    assert len(calls)==1 and calls[0][1]['timeout']==6.
    assert errors['XAU']=='REFERENCE_TOTAL_BUDGET_EXHAUSTED'


def test_response_from_another_chart_session_cannot_prove_the_requested_series():
    with pytest.raises((TimeoutError,ValueError)):
        fetch(Socket(stream='other_session'))


def test_boundary_crossing_response_does_not_promote_a_bar_requested_before_close():
    ticks=iter([T0-1,T0+1]);socket=Socket(rows=[[T0-600,100,102,99,101],[T0-300,100,102,99,101]])
    source=module().fetch_reference_series('XAU',connect_factory=lambda *a,**k:socket,clock=lambda:next(ticks))
    assert source['received_ts']==T0+1
    assert source['request_started_ts']==T0-1
    assert source['observed_ts']==T0-300
    assert module().validate_reference_series(source,'XAU',T0+1,runtime=True) is not None
