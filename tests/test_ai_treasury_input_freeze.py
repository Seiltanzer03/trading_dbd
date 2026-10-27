"""Source-free wiring fixtures establish no market or net-profit advantage."""
from copy import deepcopy
import json
import math
import threading
from types import SimpleNamespace

import pytest

from test_edge_family_market import T0, market, source
from test_edge_family_treasury_factors import treasury
from test_family_reference_market import reference_source


@pytest.fixture(scope='module')
def fitted_models():
    observations = []
    for bar in source()['bars']:
        stamp = bar['bar_end_ts']
        phase = 2*math.pi*stamp/86400
        short, long = 4.2+.2*math.sin(phase), 4.5+.1*math.cos(phase)
        features = {'carry.us02y_yield_pct': short, 'carry.us10y_yield_pct': long,
                    'carry.curve_2s10s_pp': long-short}
        proof = dict(source_verified=True, source_id='unit-factor:'+str(stamp),
                     source_instrument='NAS100', observed_ts=stamp-1,
                     received_ts=stamp-1, max_age_sec=86400., quality=1.,
                     dependency_group='unit-factor:'+str(stamp))
        observations.append(dict(captured_ts=stamp, features=features,
                                 feature_provenance={name:dict(proof) for name in features}))
    models = market().train_market_families([source()], T0, horizons=(15,),
        feature_history={'NAS100':{'value_carry':observations}},
        family_target_sources=[reference_source()])
    assert models['NAS100']['value_carry']['status'] == 'TRAINED'
    return models


def inputs(tmp_path, monkeypatch, fitted_models, block=None, installed=True):
    from seiltanzer import mathematical_edge, runtime_git_identity
    from seiltanzer.ai_frozen_expert_inputs import capture_market_inputs
    parent = treasury() if block is None else block
    seen = []

    def context_at(captured):
        assert not engine.market._intraday_lock.locked(), 'optional cache read holds the price lock'
        seen.append(captured)
        if isinstance(parent, Exception):
            raise parent
        return parent

    runtime = SimpleNamespace(context_at=context_at)
    engine = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path),
        passive=SimpleNamespace(**({'_treasury_live_runtime':runtime} if installed else {})),
        market=SimpleNamespace(instrument_code='NAS100', demo=False,
            intraday_ohlcv=[], intraday_source_authority={},
            family_reference_series=reference_source(), _intraday_lock=threading.Lock()))
    directory = tmp_path/'research';directory.mkdir()
    (directory/'mathematical_edge_latest.json').write_text(json.dumps(dict(
        contract_version=mathematical_edge.CONTRACT, created_ts=T0,
        published_for_sha='a'*40, production_authority=False,
        automatic_execution=False, instruments={}, family_market_models=fitted_models)))
    monkeypatch.setattr(runtime_git_identity, 'runtime_git_sha', lambda:'a'*40)
    monkeypatch.setattr('seiltanzer.ai_frozen_expert_inputs.time.time',lambda:T0)
    view = capture_market_inputs(engine)
    snapshot = dict(captured_ts=T0, strategy=dict(instrument='NAS100', direction='long'),
                    policy_manager={'inputs':{'horizon_minutes':15}})
    return engine, view, snapshot, parent, seen


def test_existing_treasury_cache_reaches_actual_frozen_carry_model(tmp_path,monkeypatch,fitted_models):
    from seiltanzer.ai_frozen_expert_inputs import freeze_expert_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,view,snapshot,parent,seen=inputs(tmp_path,monkeypatch,fitted_models)
    before=deepcopy(parent)
    freeze_expert_inputs(engine,snapshot,market_inputs=view)
    assert snapshot.get('treasury_context_v1') == before, 'existing Treasury cache never reaches the AI snapshot'
    row=build_edge_family_evidence(snapshot)['families']['value_carry']
    assert row['forecast_available'] is True
    assert row['features']['carry.us02y_yield_pct'] == 4.2
    proof=row['feature_provenance']['carry.us02y_yield_pct']
    assert proof['received_ts'] == T0-200
    assert proof['observed_ts'] == T0-7200
    assert proof['broker_carry_available'] is False
    assert snapshot['treasury_context_v1']['production_authority'] is False
    assert parent == before
    assert seen == [T0]


def test_builder_and_cached_reuse_cannot_replace_original_treasury_input(tmp_path,monkeypatch,fitted_models):
    from seiltanzer.ai_frozen_expert_inputs import freeze_expert_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,view,snapshot,parent,seen=inputs(tmp_path,monkeypatch,fitted_models)
    original=deepcopy(parent)
    parent['features']['rates.us02y_yield']['value']=99.
    snapshot['captured_ts']=T0+301
    snapshot['treasury_context_v1']=deepcopy(parent)

    def later(captured):
        raise AssertionError('completion/request must not reread a later Treasury state')

    engine.passive._treasury_live_runtime.context_at=later
    freeze_expert_inputs(engine,snapshot,market_inputs=view)
    assert snapshot['treasury_context_v1']==original
    assert build_edge_family_evidence(snapshot)['families']['value_carry']['forecast_available'] is True
    freeze_expert_inputs(engine,snapshot)
    assert snapshot['treasury_context_v1']==original
    assert seen==[T0]


@pytest.mark.parametrize('case',['missing','raises','malformed','nonfinite','oversized','stale','future',
                                 'url_dict','url_list','url_number'])
def test_bad_optional_treasury_cannot_break_or_fabricate_other_model_inputs(tmp_path,monkeypatch,fitted_models,case):
    from seiltanzer.ai_frozen_expert_inputs import freeze_expert_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    block=treasury()
    if case=='raises':block=RuntimeError('cache unavailable')
    elif case=='malformed':block=[]
    elif case=='nonfinite':block['features']['rates.us02y_yield']['value']=float('nan')
    elif case=='oversized':block['padding']='x'*20000
    elif case=='stale':block['stale']=True
    elif case=='future':block['fetched_at']=T0+1
    elif case=='url_dict':block['source_url']={'unexpected':'object'}
    elif case=='url_list':block['source_url']=['https://home.treasury.gov']
    elif case=='url_number':block['source_url']=123
    engine,view,snapshot,_,_=inputs(tmp_path,monkeypatch,fitted_models,block,installed=case!='missing')
    freeze_expert_inputs(engine,snapshot,market_inputs=view)
    rows=build_edge_family_evidence(snapshot)['families']
    assert rows['value_carry']['forecast_available'] is False
    assert rows['session']['forecast_available'] is True
    json.dumps(snapshot,allow_nan=False)


def test_preexisting_frozen_parent_survives_when_runtime_is_absent(tmp_path,monkeypatch,fitted_models):
    from seiltanzer.ai_frozen_expert_inputs import freeze_expert_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,view,snapshot,_,seen=inputs(tmp_path,monkeypatch,fitted_models,installed=False)
    snapshot['treasury_context_v1']=treasury()
    original=deepcopy(snapshot['treasury_context_v1'])
    freeze_expert_inputs(engine,snapshot,market_inputs=view)
    assert snapshot['treasury_context_v1']==original
    assert build_edge_family_evidence(snapshot)['families']['value_carry']['forecast_available'] is True
    assert seen==[]


def test_frozen_carry_features_survive_existing_enclosing_byte_pressure(tmp_path,monkeypatch,fitted_models):
    from seiltanzer.ai_frozen_expert_inputs import freeze_expert_inputs
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    engine,view,snapshot,_,_=inputs(tmp_path,monkeypatch,fitted_models)
    snapshot['policy_manager']['facts']='x'*47000
    freeze_expert_inputs(engine,snapshot,market_inputs=view)
    assert len(json.dumps(snapshot,allow_nan=False,separators=(',',':')).encode())<=60000
    row=build_edge_family_evidence(snapshot)['families']['value_carry']
    assert row['forecast_available'] is True
    assert row['feature_provenance']['carry.us02y_yield_pct']['received_ts']==T0-200
