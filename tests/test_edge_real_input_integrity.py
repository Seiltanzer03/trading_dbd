"""Production admission regressions, using existing causal contract fixtures."""
from copy import deepcopy
import json

import pytest

from seiltanzer.edge_family_adapters import FAMILIES, build_edge_family_evidence
from test_edge_family_adapters import family_fixture, snapshot as family_snapshot
from test_edge_family_source_runtime import bundle, write_bundle, load as load_sources
from test_mathematical_edge import runtime_fixture
from test_unified_edge_ensemble import snapshot, llm
from test_unified_edge_runtime_context import document, write_context, SHA

FLAGS = ('synthetic', 'demo', 'is_demo', 'synthetic_demo', 'contract_fixture', 'synthetic_data_used')


@pytest.mark.parametrize('flag', FLAGS)
@pytest.mark.parametrize('boundary', ['snapshot', 'source', 'model', 'nested_source'])
def test_simulated_family_evidence_never_produces_features_or_forecast(flag, boundary):
    source, feature = family_fixture('macro')
    frozen = family_snapshot('macro', source, feature)
    target = {'snapshot': frozen, 'source': source,
              'model': frozen['edge_family_models']['macro'],
              'nested_source': source.setdefault('supporting_provenance', {})}[boundary]
    target[flag] = True
    before = deepcopy(frozen)
    result = build_edge_family_evidence(frozen)
    assert result['components'] == []
    assert result['families']['macro']['forecast_available'] is False
    if boundary != 'model':
        assert result['families']['macro']['features'] == {}
    assert frozen == before


@pytest.mark.parametrize('family', FAMILIES)
def test_each_family_rejects_demo_source_and_keeps_genuine_other_family(family):
    data, feature = family_fixture(family)
    frozen = family_snapshot(family, data, feature)
    frozen['edge_family_sources'][family]['demo'] = True
    genuine, _ = family_fixture('option' if family == 'macro' else 'macro')
    other = 'option' if family == 'macro' else 'macro'
    frozen['edge_family_sources'][other] = genuine
    result = build_edge_family_evidence(frozen)
    assert result['families'][family]['features'] == {}
    assert result['families'][family]['forecast_available'] is False
    assert result['families'][other]['available'] is True


@pytest.mark.parametrize('container', ['edge_family_sources', 'edge_family_models'])
def test_parent_simulation_marker_cannot_be_stripped_before_admission(container):
    data, feature = family_fixture('macro')
    frozen = family_snapshot('macro', data, feature)
    frozen[container]['demo'] = True
    result = build_edge_family_evidence(frozen)
    assert not result['components']
    if container == 'edge_family_sources':
        assert result['families']['macro']['features'] == {}


def test_existing_option_snapshot_cannot_hide_demo_marker():
    frozen = {'captured_ts': 1_780_000_000., 'instrument': 'NAS100', 'policy_manager': {
        'evidence': {'iv_surface': {'available': True, 'snapshot_age_sec': 20.,
            'real_expiries': [{'days': 1., 'atm_iv_pct': 20., 'demo': True}]},
            'data_quality': {'chain': {'source': 'test-chain', 'age_sec': 20.}}}}}
    assert not build_edge_family_evidence(frozen)['families']['option']['features']


@pytest.mark.parametrize('boundary', ['bundle', 'source', 'nested_source', 'source_container'])
def test_exact_sha_does_not_lend_authority_to_demo_source_bundle(tmp_path, boundary):
    payload = bundle()
    source = payload['instruments']['BTCUSD']['edge_family_sources']['intermarket'][0]
    target = {'bundle': payload, 'source': source,
              'nested_source': source.setdefault('supporting_provenance', {}),
              'source_container': payload['instruments']['BTCUSD']['edge_family_sources']}[boundary]
    target['contract_fixture'] = True
    write_bundle(tmp_path, payload)
    result = load_sources(tmp_path)
    assert result['edge_family_sources'] == {}


@pytest.mark.parametrize('boundary', ['document', 'instruments', 'selected'])
def test_pinned_runtime_context_rejects_demo_marker_even_with_valid_hash(tmp_path, boundary):
    from seiltanzer.unified_edge_runtime_context import load_unified_edge_context
    frozen = snapshot()
    payload = document(frozen)
    target = {'document': payload, 'instruments': payload['instruments'],
              'selected': payload['instruments']['NAS100']}[boundary]
    target['is_demo'] = True
    path, digest = write_context(tmp_path, payload)
    loaded, audit = load_unified_edge_context(path, expected_document_sha256=digest,
                                            expected_sha=SHA, snapshot=frozen)
    assert loaded == {} and audit['available'] is False


@pytest.mark.parametrize('boundary', ['candidate_vector', 'releases'])
def test_official_macro_container_marker_cannot_be_stripped(boundary):
    from test_edge_family_adapters import T0
    feature = 'macro.cpi_value'
    frozen = family_snapshot('macro', {}, feature)
    release = {'status': 'VALID', 'release_id': 'CPI:1', 'official_source_verified': True,
               'available_at': T0-8, 'published_at': T0-10}
    numeric = {'releases': {'cpi': release}, 'candidate_vector': {feature: 3.1}}
    frozen['macro_context_v1'] = {'numeric_macro': numeric}
    genuine, _ = family_fixture('option')
    frozen['edge_family_sources']['option'] = genuine
    assert build_edge_family_evidence(frozen)['components']
    numeric[boundary]['demo'] = True
    result = build_edge_family_evidence(frozen)
    assert result['families']['macro']['features'] == {} and not result['components']
    assert result['families']['option']['available'] is True


@pytest.mark.parametrize('models', [{'demo': True}, {'unknown_family': {}}, {'macro': 3}])
def test_invalid_optional_model_matrix_preserves_genuine_registry(tmp_path, models):
    from seiltanzer.unified_edge_runtime_context import load_unified_edge_context
    frozen = snapshot()
    payload = document(frozen)
    payload['instruments']['NAS100']['edge_family_models'] = models
    path, digest = write_context(tmp_path, payload)
    loaded, audit = load_unified_edge_context(path, expected_document_sha256=digest,
                                            expected_sha=SHA, snapshot=frozen)
    assert audit['available'] is True and loaded['expert_registry']
    assert 'edge_family_models' not in loaded
    assert audit['edge_family_models']['available'] is False


@pytest.mark.parametrize('nested', [False, True])
def test_simulated_definition_cannot_vote_and_preserves_other_expert(nested):
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from test_expert_registry import registry
    frozen = snapshot()
    spec = registry(frozen)
    target = spec['definitions'][0]
    (target.setdefault('metadata', {}) if nested else target)['demo'] = True
    frozen['policy_manager']['expert_registry'] = spec
    result = build_unified_ensemble(frozen)
    rows = {row['component_id']: row for row in result['components']}
    assert rows['liquidity_model']['available'] is False
    assert rows['liquidity_model']['effective_weight'] == 0
    assert rows['liquidity_model']['reason'] == 'SIMULATED_EXPERT_NOT_ADMISSIBLE'
    assert rows['session_model']['available'] is True and rows['session_model']['effective_weight'] > 0


@pytest.mark.parametrize('flag', FLAGS)
def test_simulated_snapshot_cannot_admit_any_real_expert_vote(flag):
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from test_expert_registry import registry
    frozen = snapshot()
    frozen['policy_manager']['expert_registry'] = registry(frozen)
    frozen[flag] = True
    result = build_unified_ensemble(frozen, llm())
    assert all(row['effective_weight'] == 0 and row['available'] is False
               for row in result['components'] if row['component_id'] != 'quantitative_base')
    assert result['components'][0]['component_id'] == 'quantitative_base'
    assert result['components'][0]['effective_weight'] == 1


@pytest.mark.parametrize('boundary', ['feed', 'tick', 'report', 'model', 'training_source', 'live_authority'])
def test_mathematical_edge_never_predicts_from_simulated_inputs(tmp_path, monkeypatch, boundary):
    from seiltanzer import mathematical_edge as edge
    engine, tick, trade, report, destination = runtime_fixture(tmp_path, monkeypatch)
    if boundary == 'feed':
        engine.market.demo = True
    elif boundary == 'live_authority':
        engine.market.intraday_source_authority = {'source_verified': True, 'demo': True}
    elif boundary == 'training_source':
        report['sources'] = [{'instrument': 'NAS100', 'demo': True}]
        destination.write_text(json.dumps(report))
    else:
        target = tick if boundary == 'tick' else report if boundary == 'report' else report['instruments']['NAS100']
        target['demo'] = True
        if boundary == 'model':
            model = report['instruments']['NAS100']
            model['model_sha256'] = edge.fingerprint({k:v for k,v in model.items() if k != 'model_sha256'})
        destination.write_text(json.dumps(report))
    result = edge.runtime_profile(engine, tick, trade)
    assert result['available'] is False and result['weight_fraction'] == 0
    assert not result.get('path_predictions') and not result.get('probabilities')


@pytest.mark.parametrize('boundary', ['profile', 'override', 'current_llm', 'registered', 'registry_root'])
def test_simulated_expert_assessment_has_no_applied_vote(boundary):
    from seiltanzer.unified_edge_ensemble import build_unified_ensemble
    from test_expert_registry import registry
    frozen, opinion = snapshot(), None
    identity = 'active_edge'
    if boundary == 'profile':
        frozen['policy_manager']['active_edge_provisional_weight'] = {
            'available': True, 'quality': 1., 'direction_score': 1., 'demo': True}
    elif boundary == 'override':
        frozen['policy_manager']['unified_component_assessments'] = [{
            'component_id': identity, 'available': True, 'quality': 1.,
            'observed_ts': frozen['captured_ts'], 'max_age_sec': 900.,
            'scores': {'HOLD': 0., 'CLOSE_10': 1.}, 'demo': True}]
    elif boundary == 'current_llm':
        identity, opinion = 'current_llm', llm(demo=True)
    else:
        spec = registry(frozen)
        item = spec['assessments'][0]
        (spec if boundary == 'registry_root' else item)['demo'] = True
        identity = item['expert_id']
        frozen['policy_manager']['expert_registry'] = spec
    result = build_unified_ensemble(frozen, opinion)
    if boundary == 'registry_root':
        assert all(not row.get('registered_expert') for row in result['components'])
        assert result['expert_registry']['available'] is False
        return
    row = next(row for row in result['components'] if row['component_id'] == identity)
    assert row['available'] is False and row['effective_weight'] == 0
    assert sum(row['effective_weight'] for row in result['components']) == pytest.approx(1.)
