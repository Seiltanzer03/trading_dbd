"""Release acceptance must exercise the actual consumer and model contracts."""
import importlib.util
import importlib
from copy import deepcopy
import pytest
from test_edge_family_market import runtime,T0


def verifier():
    assert importlib.util.find_spec('scripts.production_model_consumption') is not None
    return importlib.import_module('scripts.production_model_consumption')


def report(tmp_path,monkeypatch):
    _,_,value,_=runtime(tmp_path,monkeypatch)
    count=sum(row.get('status')=='TRAINED' for rows in value['family_market_models'].values() for row in rows.values())
    value.update(state='WORKING_MODELS_LOADED',family_market_model_summary={'trained_models':count,'working_supported_models':0})
    return value


def test_actual_loaded_model_contracts_are_verified(tmp_path,monkeypatch):
    value=report(tmp_path,monkeypatch)
    result=verifier().verify_report(value,expected_sha='a'*40,captured_ts=T0+20)
    assert result['validated_models']==value['family_market_model_summary']['trained_models']>0


@pytest.mark.parametrize('case',['unavailable','wrong_sha','damaged_model','count_mismatch','future_model'])
def test_release_cannot_accept_missing_or_rejected_models(case,tmp_path,monkeypatch):
    value=deepcopy(report(tmp_path,monkeypatch))
    if case=='unavailable':value['state']='CURRENT_WORKING_MODEL_UNAVAILABLE'
    elif case=='wrong_sha':value['published_for_sha']='b'*40
    elif case=='count_mismatch':value['family_market_model_summary']['trained_models']+=1
    else:
        model=next(row for rows in value['family_market_models'].values() for row in rows.values() if row.get('status')=='TRAINED')
        if case=='damaged_model':model['model_sha256']='0'*64
        else:
            from seiltanzer.mathematical_edge import fingerprint
            model['trained_at']=T0+100
            model['model_sha256']=fingerprint({k:v for k,v in model.items() if k!='model_sha256'})
    with pytest.raises(ValueError):verifier().verify_report(value,expected_sha='a'*40,captured_ts=T0+20)


def family_status(tmp_path,monkeypatch):
    from test_family_reference_market import reference_runtime
    from seiltanzer.edge_family_market import attach_market_family_context,family_runtime_status
    engine,snapshot=reference_runtime(tmp_path,monkeypatch)
    attach_market_family_context(engine,snapshot)
    import json
    value=json.loads((tmp_path/'research'/'mathematical_edge_latest.json').read_text())
    status={'ready':True,'current_trade_id':'test-trade','snapshot_trade_id':'test-trade','current_instrument':'NAS100',
            'family_market_forecasts':family_runtime_status(snapshot)}
    return value,status


def test_actual_frozen_family_forecast_is_verified_separately_from_archive_model_count(tmp_path,monkeypatch):
    value,status=family_status(tmp_path,monkeypatch)
    result=verifier().verify_family_runtime(status,value,expected_sha='a'*40,captured_ts=T0+20)
    assert result['state']=='LIVE_FAMILY_FORECASTS_VERIFIED'
    assert result['instrument']=='NAS100' and result['available_forecasts']==1
    assert result['families']==['session']


@pytest.mark.parametrize('case',['wrong_sha','not_ready','expired','wrong_target','different_model','fake_probability','wrong_instrument_scope'])
def test_runtime_receipt_cannot_replace_exact_fresh_model_inference(case,tmp_path,monkeypatch):
    value,status=family_status(tmp_path,monkeypatch)
    r=status['family_market_forecasts'];now=T0+20
    if case=='wrong_sha':r['published_for_sha']='b'*40
    elif case=='not_ready':status['ready']=False
    elif case=='expired':now=T0+901
    elif case=='wrong_target':r['families']['session']['target_symbol']='OANDA:XAUUSD'
    elif case=='different_model':r['families']['session']['model_sha256']='0'*64
    elif case=='fake_probability':r['families']['session']['predictions']['direction']['probability']=True
    else:r['instrument']='BTCUSD'
    with pytest.raises(ValueError):verifier().verify_family_runtime(status,value,expected_sha='a'*40,captured_ts=now)
