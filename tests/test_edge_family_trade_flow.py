"""Real reported taker aggregates; fixture outcomes are not market evidence."""
import importlib
import importlib.util
from copy import deepcopy

import pytest

from test_edge_family_market import source, T0
from seiltanzer.mathematical_edge import fingerprint


def module():
    assert importlib.util.find_spec('seiltanzer.edge_family_trade_flow') is not None, 'historical and live taker flow path missing'
    return importlib.import_module('seiltanzer.edge_family_trade_flow')


def record():
    rows=[]
    for index in range(5):
        start=int((T0-300+60*index)*1000)
        rows.append([start,'100','102','99','101','10',start+59999,'1000',5,'6','600','0'])
    return dict(provider='Binance',symbol='BTCUSDT',received_ts=T0+10,rows=rows)


def test_live_flow_aggregates_exact_five_completed_minutes_and_actual_receipt():
    result=module().live_flow_observation(record(),code='BTCUSD',cutoff=T0+20,origin=T0)
    assert result['features']['flow.binance_taker_buy_share_5m']==.6
    proof=result['feature_provenance']['flow.binance_taker_buy_share_5m']
    assert proof['observed_ts']==T0
    assert proof['received_ts']==T0+10
    assert proof['measurement_instrument']=='BTCUSDT'
    assert proof['observed_broker_order_book'] is False


@pytest.mark.parametrize('case',['wrong_symbol','missing_minute','duplicate','future_receipt','stale','taker_over_total','synthetic'])
def test_live_flow_rejects_invalid_or_stale_real_input(case):
    raw=record();cutoff=T0+20
    if case=='wrong_symbol':raw['symbol']='ETHUSDT'
    elif case=='missing_minute':raw['rows'].pop(2)
    elif case=='duplicate':raw['rows'].append(raw['rows'][0])
    elif case=='future_receipt':raw['received_ts']=cutoff+1
    elif case=='stale':cutoff=T0+61
    elif case=='taker_over_total':raw['rows'][0][9]='11'
    else:raw['synthetic_data_used']=True
    assert module().live_flow_observation(raw,code='BTCUSD',cutoff=cutoff,origin=T0) is None


def training_source(n=1800):
    raw=source('BTCUSD',n=n);raw.update(provider='Binance',ticker='BTCUSDT')
    rows=[dict(bar_end_ts=bar['bar_end_ts'],volume=50.,quote_asset_volume=5000.+index,
        trades=25,taker_buy_base_volume=20.+index%10,taker_buy_quote_volume=2000.) for index,bar in enumerate(raw['bars'])]
    raw.update(trade_flow_history=rows,trade_flow_sha256=fingerprint(rows),collection={
        'contract_version':'binance-public-archive-v1','historical_first_receipt_known':False,
        'files':[dict(url='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-2026-10.zip',
            filename='BTCUSDT-5m-2026-10.zip',sha256='a'*64,checksum_sha256='b'*64,received_ts=T0+1,rows=n)]})
    return raw


@pytest.mark.parametrize('n',[1800,4000])
def test_archive_flow_training_matches_live_features_and_rejects_mutated_sidecar(n):
    from seiltanzer.edge_family_market import train_market_families, _model_reason
    raw=training_source(n)
    result=train_market_families([raw],T0,horizons=(15,))['BTCUSD']['order_flow']
    assert result['status']=='TRAINED'
    assert result['features_are_captured_receipts'] is False
    assert result['public_trade_flow_archive']['historical_first_receipt_known'] is False
    assert _model_reason(result,'BTCUSD','order_flow',T0+20) is None
    bad=deepcopy(raw);bad['trade_flow_history'][0]['volume']=51
    rejected=train_market_families([bad],T0,horizons=(15,))['BTCUSD']['order_flow']
    assert rejected['status']!='TRAINED'
    assert rejected['reason']=='BINANCE_TRADE_FLOW_HASH_INVALID'


def test_normal_archive_depth_does_not_reject_all_crypto_families_as_simulated():
    from seiltanzer.edge_family_market import train_market_families
    raw=source('BTCUSD',n=4000);raw.update(provider='Binance',ticker='BTCUSDT')
    # Even an unavailable/malformed optional flow sidecar cannot remove the
    # independently valid exact market bars from session and other families.
    raw['trade_flow_history']=[dict(bar_end_ts=bar['bar_end_ts'],volume=10.,quote_asset_volume=1000.,
        trades=10,taker_buy_base_volume=5.,taker_buy_quote_volume=500.) for bar in raw['bars']]
    result=train_market_families([raw],T0,horizons=(15,))['BTCUSD']
    assert result['session']['status']=='TRAINED', result['session']


def test_frozen_market_inputs_preserve_independent_flow_receipt_and_bytes():
    from types import SimpleNamespace
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    raw=record();engine=SimpleNamespace(market=SimpleNamespace(binance_trade_flow=raw,instrument_code='BTCUSD'))
    frozen=capture_market_inputs(engine)
    original=module().live_flow_observation(raw,code='BTCUSD',cutoff=T0+20,origin=T0)
    captured=module().live_flow_observation(getattr(frozen.market,'binance_trade_flow',None),code='BTCUSD',cutoff=T0+20,origin=T0)
    assert captured==original and captured is not None
    raw['rows'][0][9]='9'
    assert module().live_flow_observation(frozen.market.binance_trade_flow,code='BTCUSD',cutoff=T0+20,origin=T0)==captured


def test_fitted_flow_model_attaches_through_the_real_frozen_engine_view(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from seiltanzer import mathematical_edge
    from seiltanzer.edge_family_market import train_market_families,attach_market_family_context
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    models=train_market_families([training_source()],T0,horizons=(15,))
    monkeypatch.setattr(mathematical_edge,'load_artifact',lambda *args:dict(family_market_models=models))
    raw=record()
    feed=SimpleNamespace(instrument_code='BTCUSD',demo=False,binance_trade_flow=raw,
        intraday_ohlcv=[[row[0]/1000,*map(float,row[1:6])] for row in raw['rows']],
        intraday_source_authority=dict(provider='Binance',source_symbol='BTCUSDT',target_instrument='BTCUSD',
            source_verified=True,observed_ts=T0,available_at=T0+10,derived=False))
    engine=SimpleNamespace(market=feed,settings=SimpleNamespace(data_dir=tmp_path))
    snapshot=dict(captured_ts=T0+20,strategy=dict(instrument='BTCUSD',direction='long'),
        policy_manager={'inputs':{'horizon_minutes':15}})
    frozen=capture_market_inputs(engine)
    attach_market_family_context(frozen,snapshot)
    context=snapshot['edge_family_market_context']
    assert 'order_flow' in context['families'], context
    assert context['families']['order_flow']['features']['flow.binance_taker_buy_share_5m']==.6
