"""Model continuity reuses real fits, never creates new fitting evidence."""
from copy import deepcopy
import importlib

import pytest

from seiltanzer.mathematical_edge import CONTRACT, fingerprint, working_admission
from seiltanzer.edge_family_market import train_market_families
from test_edge_family_market import source, T0


@pytest.fixture(scope='module')
def fitted():
    return train_market_families([source()],T0,horizons=(15,))['NAS100']['session']


def previous(model,created=T0):
    return {'contract_version':CONTRACT,'created_ts':created,'production_authority':False,
        'automatic_execution':False,'family_market_models':{'NAS100':{'session':model}}}


def retain(current,reports,cutoff=T0+3600):
    module=importlib.import_module('seiltanzer.edge_family_model_retention')
    return module.retain_fitted_families({'NAS100':{'session':current}},reports,cutoff)


def unavailable():
    return {'status':'INSUFFICIENT_HISTORY','reason':'INSUFFICIENT_NONOVERLAPPING_FAMILY_OBSERVATIONS',
        'training_n':10,'forecast':None}


def test_insufficient_new_input_reuses_exact_original_fitted_model(fitted):
    original=deepcopy(fitted)
    matrix,audit=retain(unavailable(),[previous(fitted)])
    assert matrix['NAS100']['session']==original
    assert fitted==original
    assert audit['current_run_fitted']==0
    assert audit['retained_fitted']==1
    assert audit['total_fitted']==1
    assert audit['retained_models'][0]['model_sha256']==original['model_sha256']
    assert audit['retained_models'][0]['trained_at']==T0
    assert audit['new_observations'] is False


def test_current_trained_negative_result_wins_over_previous_positive_fit(fitted):
    negative=deepcopy(fitted)
    for h in negative['heads'].values():
        if 'head' not in h:continue
        h['test_gain_mbit']=-1.;h['test_block_gains_mbit']=[-1.]*3
        h['working_admission']=working_admission(h['validation_gains_mbit'],[-1.]*3,-1.)
        h['working_supported']=False
    negative['model_sha256']=fingerprint({k:v for k,v in negative.items() if k!='model_sha256'})
    matrix,audit=retain(negative,[previous(fitted)])
    assert matrix['NAS100']['session']==negative
    assert audit['retained_fitted']==0
    assert audit['current_run_fitted']==1


@pytest.mark.parametrize('change',['hash','target','future','stale','synthetic'])
def test_invalid_previous_fit_does_not_replace_missing_input(fitted,change):
    model=deepcopy(fitted)
    if change=='hash':model['model_sha256']='0'*64
    elif change=='target':model['source']['ticker']='QQQ'
    elif change=='future':model['trained_at']=T0+7200
    elif change=='stale':model['training_cutoff']=T0-100*86400
    else:model['synthetic']=True
    if change!='hash':model['model_sha256']=fingerprint({k:v for k,v in model.items() if k!='model_sha256'})
    matrix,audit=retain(unavailable(),[previous(model)])
    assert matrix['NAS100']['session']==unavailable()
    assert audit['retained_fitted']==0
    assert audit['rejected_prior_models']


def test_source_identity_failure_does_not_trigger_old_fit_fallback(fitted):
    current={'status':'SOURCE_UNAVAILABLE','reason':'TARGET_CONFIGURED_SERIES_MISMATCH'}
    matrix,audit=retain(current,[previous(fitted)])
    assert matrix['NAS100']['session']==current
    assert audit['retained_fitted']==0


def test_newest_original_fit_is_selected_without_heldout_score_selection(fitted):
    newer=deepcopy(fitted);newer['trained_at']=T0+300
    newer['model_sha256']=fingerprint({k:v for k,v in newer.items() if k!='model_sha256'})
    matrix,audit=retain(unavailable(),[previous(fitted),previous(newer,T0+300)])
    assert matrix['NAS100']['session']==newer
    assert audit['retained_fitted']==1


def test_missing_or_malformed_optional_previous_report_preserves_fresh_result(fitted):
    matrix,audit=retain(fitted,[None,{'family_market_models':[]}])
    assert matrix['NAS100']['session']==fitted
    assert audit['retained_fitted']==0


def test_report_distinguishes_old_retained_fit_from_current_training(fitted):
    from scripts.run_mathematical_edge import build_report
    report=build_report([source(n=30)],{},T0+3600,horizons=(15,),
        previous_family_reports=[previous(fitted)])
    assert report['family_market_models']['NAS100']['session']==fitted
    assert report['family_model_continuity']['retained_fitted']==1
    assert report['family_market_model_summary']['retained_fitted_models']==1
    assert report['family_market_model_summary']['current_run_fitted_models']==0
    assert report['family_market_model_summary']['trained_models']==1


def test_model_cache_roundtrip_never_changes_original_training_clocks(tmp_path,fitted):
    from seiltanzer.edge_family_model_retention import write_fitted_cache,read_fitted_cache
    path=tmp_path/'models.json'
    write_fitted_cache(previous(fitted),path)
    report=read_fitted_cache(path)
    matrix,audit=retain(unavailable(),[report])
    assert matrix['NAS100']['session']==fitted
    assert audit['retained_models'][0]['trained_at']==T0
    assert 'sources' not in report


def test_missing_malformed_or_oversize_optional_cache_is_unavailable(tmp_path):
    from seiltanzer.edge_family_model_retention import read_fitted_cache,MAX_BYTES
    path=tmp_path/'models.json'
    assert read_fitted_cache(path) is None
    path.write_text('{invalid')
    assert read_fitted_cache(path) is None
    path.write_text(' '*(MAX_BYTES+1))
    assert read_fitted_cache(path) is None


def test_deeply_nested_optional_cache_is_rejected(tmp_path):
    from seiltanzer.edge_family_model_retention import read_fitted_cache
    path = tmp_path / 'nested.json'
    path.write_text('{"ignored":' + '[' * 20000 + '0' + ']' * 20000 + '}')
    assert read_fitted_cache(path) is None


def test_deeply_nested_prior_report_preserves_fresh_fit(fitted):
    nested = 0
    for _ in range(20000):
        nested = [nested]
    report = previous(fitted)
    report['ignored'] = nested
    matrix, audit = retain(fitted, [report])
    assert matrix['NAS100']['session'] == fitted
    assert audit['retained_fitted'] == 0
    assert audit['rejected_prior_reports']


def test_deeply_nested_optional_publication_preserves_market_export(tmp_path, capsys):
    import base64, gzip, json
    from scripts.export_mathematical_edge_sources import REMOTE_EXPORT
    from test_mathematical_edge_sources import snapshot_database
    db, _ = snapshot_database(tmp_path)
    path = tmp_path / 'nested.json'
    path.write_text('{"ignored":' + '[' * 20000 + '0' + ']' * 20000 + '}')
    before = db.read_bytes()
    program = REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db', str(db)).replace(
        '/opt/seiltanzer/data/research/mathematical_edge_latest.json', str(path))
    exec(program, {})
    result = json.loads(gzip.decompress(base64.b64decode(capsys.readouterr().out.strip())))
    assert result['sources']
    assert result['previous_family_model_report'] is None
    assert result['previous_family_model_reason'] == 'PREVIOUS_MODEL_PUBLICATION_INVALID'
    assert db.read_bytes() == before


@pytest.mark.parametrize('present',[True,False])
def test_existing_publication_seed_is_read_only_and_excludes_raw_inputs(tmp_path,capsys,fitted,present):
    import base64,gzip,json
    from scripts.export_mathematical_edge_sources import REMOTE_EXPORT
    from test_mathematical_edge_sources import snapshot_database
    db,_=snapshot_database(tmp_path)
    path=tmp_path/'previous.json'
    report=previous(fitted);report['private_observations']=['excluded'];report['sources']=[{'bars':['excluded']}]
    if present:path.write_text(json.dumps(report))
    original=path.read_bytes() if present else None
    before=db.read_bytes()
    program=REMOTE_EXPORT.replace('/opt/seiltanzer/data/trades.db',str(db)).replace(
        '/opt/seiltanzer/data/research/mathematical_edge_latest.json',str(path))
    exec(program,{})
    result=json.loads(gzip.decompress(base64.b64decode(capsys.readouterr().out.strip())))
    assert db.read_bytes()==before
    assert 'previous_family_model_report' in result
    if present:
        seed=result['previous_family_model_report']
        assert seed['family_market_models']['NAS100']['session']==fitted
        assert 'private_observations' not in seed and 'sources' not in seed
        assert path.read_bytes()==original
    else:
        assert result['previous_family_model_report'] is None
        assert result['previous_family_model_reason']=='PREVIOUS_MODEL_PUBLICATION_NOT_FOUND'
        assert not path.exists()
