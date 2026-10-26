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
