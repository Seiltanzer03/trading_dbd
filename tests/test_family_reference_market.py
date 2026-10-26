"""Transport/target fixtures do not establish market profitability."""
from copy import deepcopy

import pytest

from test_edge_family_market import T0, source, runtime, market
from seiltanzer.mathematical_edge import fingerprint


def reference_source():
    s=source()
    return {**s,'provider':'TradingView chart','ticker':'OANDA:NAS100USD',
        'contract_version':'configured-family-reference-5m-v1','resolved_symbol':'OANDA:NAS100USD',
        'received_ts':T0,'request_started_ts':T0,'observed_ts':T0,'not_broker_execution_bars':True,
        'canonical_execution_quote_replaced':False}


def test_family_training_declares_its_actual_reference_target_and_keeps_price_source_separate():
    raw=source();before=deepcopy(raw)
    result=market().train_market_families([raw],T0,horizons=(15,),family_target_sources=[reference_source()])
    row=result['NAS100']['session']
    assert row['status']=='TRAINED'
    assert row['source']['provider']=='TradingView chart'
    assert row['source']['ticker']=='OANDA:NAS100USD'
    assert row['source']['reference_contract']=='configured-family-reference-5m-v1'
    assert market()._model_reason(row,'NAS100','session',T0) is None
    assert raw==before


def test_damaged_optional_reference_does_not_relabel_or_remove_the_legacy_target():
    reference=reference_source();reference['ticker']='OANDA:US30USD'
    result=market().train_market_families([source()],T0,horizons=(15,),family_target_sources=[reference])
    assert result['NAS100']['session']['status']=='TRAINED'
    assert result['NAS100']['session']['source']['ticker']=='^NDX'


def reference_runtime(tmp_path,monkeypatch):
    import json
    engine,snapshot,report,path=runtime(tmp_path,monkeypatch)
    models=market().train_market_families([source()],T0,horizons=(15,),family_target_sources=[reference_source()])
    report['family_market_models']=models;path.write_text(json.dumps(report))
    engine.market.family_reference_series=reference_source()
    # The old Yahoo lane is unavailable: it must not gate an independent model.
    engine.market.intraday_ohlcv=[];engine.market.intraday_source_authority={}
    return engine,snapshot


def test_current_reference_unblocks_its_fitted_forecast_without_borrowing_yahoo_or_quote_authority(tmp_path,monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    market().attach_market_family_context(engine,snapshot)
    row=build_edge_family_evidence(snapshot)['families']['session']
    assert row['forecast_available'] is True
    assert row['forecast_origin_ts']==T0
    assert snapshot['edge_family_market_context']['families']['session']['model']['source']['ticker']=='OANDA:NAS100USD'
    assert row['market_predictions']['direction']['probability'] is not None
    assert engine.market.intraday_ohlcv==[]


@pytest.mark.parametrize('case',['wrong_symbol','late','hash','partial','model_profile'])
def test_reference_model_never_borrows_a_different_or_later_series(tmp_path,monkeypatch,case):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    r=engine.market.family_reference_series
    if case=='wrong_symbol':r['ticker']='OANDA:XAUUSD'
    elif case=='late':r['received_ts']=T0+1
    elif case=='hash':r['bars'][-1]['close']+=.01
    elif case=='partial':
        r['bars'][-1]['bar_end_ts']=T0+300;r['source_sha256']=fingerprint(r['bars'])
    else:
        import json
        path=tmp_path/'research'/'mathematical_edge_latest.json';data=json.loads(path.read_text())
        model=data['family_market_models']['NAS100']['session'];model['source']['reference_contract']='unknown'
        model['model_sha256']=fingerprint({k:v for k,v in model.items() if k!='model_sha256'})
        path.write_text(json.dumps(data))
    market().attach_market_family_context(engine,snapshot)
    assert not build_edge_family_evidence(snapshot)['components']


def test_public_report_keeps_price_target_and_publishes_only_reference_metadata():
    from scripts.run_mathematical_edge import build_report
    report=build_report([source()],{},T0,horizons=(15,),family_target_sources=[reference_source()])
    assert report['sources'][0]['ticker']=='^NDX'
    assert report['family_market_models']['NAS100']['session']['source']['ticker']=='OANDA:NAS100USD'
    assert report['family_reference_sources'][0]['bar_count']==1800
    assert 'bars' not in report['family_reference_sources'][0]


def test_trained_model_waiting_for_current_inputs_keeps_a_concrete_readiness_reason(tmp_path,monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    engine.market.family_reference_series['ticker']='wrong'
    market().attach_market_family_context(engine,snapshot)
    row=build_edge_family_evidence(snapshot)['families']['session']
    assert row['market_model_trained'] is True
    assert row['forecast_available'] is False
    assert row['readiness']=='TRAINED_MODEL_WAITING_CURRENT_INPUTS'
    assert 'REFERENCE_' in row['reason']


def test_background_receipt_proves_inference_without_raw_inputs_or_a_trading_decision(tmp_path,monkeypatch):
    import json
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    market().attach_market_family_context(engine,snapshot)
    receipt=market().family_runtime_status(snapshot)
    assert receipt['published_for_sha']=='a'*40
    assert receipt['available_forecasts_at_capture']==1
    assert receipt['qualified_preferences_at_capture']==1
    assert receipt['families']['session']['target_symbol']=='OANDA:NAS100USD'
    assert receipt['families']['session']['predictions']['direction']['probability'] is not None
    assert receipt['network_calls'] is False and receipt['runtime_fitting'] is False
    assert receipt['final_action_selected'] is False
    assert '"beta"' not in json.dumps(receipt) and '"bars"' not in json.dumps(receipt)


def test_full_family_context_fits_existing_budget_without_dropping_models_or_provenance():
    import json
    from seiltanzer.operational_edge_compaction import MARKET_CONTEXT_MAX_BYTES
    # Representative full-family envelope: repeated validation/lineage fields
    # are retained exactly, including every coefficient and source clock.
    model={'heads':{'direction':{'beta':[.1,.2,.3],'validation':['recorded-contract-lineage']*180}},
           'source':{'ticker':'OANDA:XAUUSD','receipt':T0},'model_sha256':'f'*64}
    context={'families':{f:{'model':deepcopy(model),'features':{'x':1.},
                'feature_provenance':{'x':{'source_id':'official-source','received_ts':T0}}}
              for f in ('macro','intermarket','positioning','value_carry','option','session')}}
    original=deepcopy(context)
    assert len(json.dumps(context,separators=(',',':')).encode())>MARKET_CONTEXT_MAX_BYTES
    market().freeze_context_models(context)
    assert len(json.dumps(context,separators=(',',':')).encode())<=MARKET_CONTEXT_MAX_BYTES
    for family,item in context['families'].items():
        assert market().frozen_model(item)==original['families'][family]['model']
        assert item['feature_provenance']==original['families'][family]['feature_provenance']


@pytest.mark.parametrize('case',['bad_base64','truncated','oversized','ambiguous'])
def test_damaged_frozen_model_encoding_cannot_become_a_model(case):
    import base64,zlib,json
    raw=json.dumps({'data':'x'*25000}).encode() if case=='oversized' else b'{"model_sha256":"abc"}'
    compressed=zlib.compress(raw)
    if case=='truncated':compressed=compressed[:-2]
    item={'model_encoding':'json-zlib-v1','model_encoded':base64.b64encode(compressed).decode()}
    if case=='bad_base64':item['model_encoded']='not base64!'
    if case=='ambiguous':item['model']={'other':'model'}
    assert market().frozen_model(item) is None


@pytest.mark.parametrize('marked',[False,True])
def test_encoded_model_is_still_fully_validated_before_inference(tmp_path,monkeypatch,marked):
    import base64,zlib,json
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    market().attach_market_family_context(engine,snapshot)
    item=snapshot['edge_family_market_context']['families']['session']
    model=item.pop('model')
    if marked:
        model['contract_fixture']=True
        model['model_sha256']=fingerprint({k:v for k,v in model.items() if k!='model_sha256'})
    item.update(model_encoding='json-zlib-v1',model_encoded=base64.b64encode(zlib.compress(json.dumps(model).encode())).decode())
    evidence=build_edge_family_evidence(snapshot)
    assert bool(evidence['components']) is (not marked)


def test_enclosing_snapshot_pressure_encodes_intact_model_before_excluding_forecasts(tmp_path,monkeypatch):
    import json
    from seiltanzer.operational_edge_compaction import compact_operational_edges
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    market().attach_market_family_context(engine,snapshot)
    item=snapshot['edge_family_market_context']['families']['session']
    original=deepcopy(item['model'])
    expected=build_edge_family_evidence(snapshot)['families']['session']['market_predictions']
    snapshot['policy_manager']['bounded_existing_decision_facts']='x'*54000
    assert len(json.dumps(snapshot).encode())>60000
    compact_operational_edges(snapshot)
    item=snapshot['edge_family_market_context']['families']['session']
    assert 'model_encoded' in item
    assert market().frozen_model(item)==original
    assert build_edge_family_evidence(snapshot)['families']['session']['market_predictions']==expected
    assert len(json.dumps(snapshot,separators=(',',':')).encode())<60000


def test_malformed_optional_model_under_byte_pressure_is_excluded_without_breaking_review():
    from seiltanzer.operational_edge_compaction import compact_operational_edges,EXCLUDED_REASON
    snapshot={'edge_family_market_context':{'families':{'session':{'model':{'invalid':float('nan')}}}},
              'policy_manager':{'management_decision':'HOLD','facts':'x'*55000}}
    compact_operational_edges(snapshot)
    assert snapshot['policy_manager']['management_decision']=='HOLD'
    assert 'edge_family_market_context' not in snapshot
    assert snapshot['edge_family_budget_status']['reason']==EXCLUDED_REASON


def test_heavy_builder_cannot_move_frozen_reference_into_the_next_bar_window(tmp_path,monkeypatch):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    engine=capture_market_inputs(engine)
    snapshot['captured_ts']=T0+301
    snapshot['policy_manager']['input_audit']={'snapshot_ts':T0+301}
    market().attach_market_family_context(engine,snapshot)
    row=build_edge_family_evidence(snapshot)['families']['session']
    assert row['forecast_available'] is True
    assert row['forecast_origin_ts']==T0
    assert snapshot['edge_family_market_context']['market_observation_ts']==T0
