"""Calculation identity must not overwrite immutable historical evidence."""
import copy
import importlib
import importlib.util
from pathlib import Path

import pytest

from seiltanzer.edge_discovery.candidate_registry import CandidateRegistry
from seiltanzer.edge_discovery.dataset_fingerprint import research_dataset_fingerprint
from seiltanzer.edge_discovery.selective import run_selective_search
_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'production_ede_v13_audit.py'
_SPEC = importlib.util.spec_from_file_location('production_ede_measurement_test', _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_AUDIT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AUDIT)
_register_evaluations = _AUDIT._register_evaluations
EVALUATION_MEASUREMENT_CONTRACT = _AUDIT.EVALUATION_MEASUREMENT_CONTRACT


def candidate():
    return {'candidate_id':'ede-candidate-measurement','template_id':'measured-template',
            'template':[{'feature_id':'option.iv','kind':'train_relative','state':'ABOVE_MEDIAN'}],
            'signal':'ret5_persistence','horizon_minutes':15,'status':'REJECTED',
            'model':{'brier':.25},'q_value':.4}


def test_actual_implementation_identity_is_stable_and_tracks_calculation_change(monkeypatch):
    module=importlib.import_module('seiltanzer.edge_discovery.measurement_identity')
    first=module.research_measurement_identity()
    assert first==module.research_measurement_identity()
    assert len(first['implementation_sha256'])==64
    original=Path.read_bytes
    def changed(path):
        raw=original(path)
        return raw+b'\n# changed measurement\n' if path.name=='scoring.py' else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert module.research_measurement_identity()['implementation_sha256']!=first['implementation_sha256']


def test_numerical_runtime_change_creates_distinct_calculation(monkeypatch):
    module=importlib.import_module('seiltanzer.edge_discovery.measurement_identity')
    first=module.research_measurement_identity()
    monkeypatch.setattr(module.np,'__version__','different-numerical-runtime')
    second=module.research_measurement_identity()
    assert second['implementation_sha256']!=first['implementation_sha256']
    assert second['source_sha256']==first['source_sha256']
    assert second['new_observations'] is False


def test_unreadable_measurement_source_does_not_reuse_legacy_identity(monkeypatch):
    module=importlib.import_module('seiltanzer.edge_discovery.measurement_identity')
    original=Path.read_bytes
    def unavailable(path):
        if path.name=='scoring.py':raise OSError('measurement source unavailable')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',unavailable)
    with pytest.raises(OSError,match='unavailable'):
        module.research_measurement_identity()


def test_old_contract_is_preserved_new_implementation_is_recorded_and_rerun_deduplicates(tmp_path):
    path=tmp_path/'registry.jsonl';reg=CandidateRegistry(path);old=candidate()
    legacy='g1s-ede-selective-edge-v1.3.1+g1s-ede-dataset-fingerprint-v2'
    old_id=reg.register_evaluation(old,dataset_sha256='same-data',research_run='old',measurement_contract=legacy)
    old_record=reg.evaluation(old_id);new=copy.deepcopy(old);new['model']['brier']=.24
    report={'horizons':[{'hypothesis_evaluations':[new]}]}
    _register_evaluations(path,report,dataset_sha256='same-data',run_id='current')
    _register_evaluations(path,report,dataset_sha256='same-data',run_id='rerun')
    restored=CandidateRegistry(path)
    recorded=[e for e in restored.events() if e['event']=='EVALUATION_RECORDED']
    assert len(recorded)==2
    assert restored.evaluation(old_id)==old_record
    assert all(e['evaluation']['dataset_source_sha256']=='same-data' for e in recorded)
    assert all(e['production_authority'] is False for e in recorded)
    assert recorded[-1]['evaluation']['measurement_contract']==EVALUATION_MEASUREMENT_CONTRACT
    new['model']['brier']=.23
    with pytest.raises(ValueError,match='immutable'):
        _register_evaluations(path,report,dataset_sha256='same-data',run_id='corrupt-same-measurement')


def test_research_rerun_with_tied_observation_order_has_identical_evaluations(tmp_path):
    rows=[]
    for i in range(480):
        ts=1700000000.+i*900
        for j in range(3):
            rows.append({'observation_id':f'obs-{i:04d}-{j}', 'instrument':'NAS100',
                'captured_ts':ts,'target_ts':ts+900,'resolved_ts':ts+901,
                'horizon_minutes':15,'outcome_available':True,
                'direction_label':'UP' if (i+j)%5<3 else 'DOWN',
                'terminal_log_return':(.0001 if (i+j)%5<3 else -.0003)*(1+j*.01),
                'features':{'ret_5m':.0005 if i%3 else -.0008,'ret_15m':.001},
                'ede_features':{'option.iv':.1+((i*7+j*11)%113)/1000,'session_utc':'US'}})
    left=run_selective_search(prospective_rows=rows,source_set_sha256='same-data',eligible_feature_ids={'option.iv'})
    right=run_selective_search(prospective_rows=list(reversed(rows)),source_set_sha256='same-data',eligible_feature_ids={'option.iv'})
    a=left['horizons'][0]['hypothesis_evaluations'];b=right['horizons'][0]['hypothesis_evaluations']
    assert a and b
    assert research_dataset_fingerprint(rows,eligible_feature_ids={'option.iv'})==research_dataset_fingerprint(reversed(rows),eligible_feature_ids={'option.iv'})
    assert a==b
    path=tmp_path/'real-rerun.jsonl'
    _register_evaluations(path,left,dataset_sha256='same-data',run_id='first')
    _register_evaluations(path,right,dataset_sha256='same-data',run_id='second')
    expected=sum(len(h['hypothesis_evaluations']) for h in left['horizons'])
    assert len([e for e in CandidateRegistry(path).events() if e['event']=='EVALUATION_RECORDED'])==expected
