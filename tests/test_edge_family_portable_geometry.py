"""Artificial contract fixtures only; these are not broker observations."""
from copy import deepcopy
import importlib
import importlib.util

import pytest

from test_edge_family_dataset import snapshot, record, archive, build, geometry, digest
from test_edge_family_training import dataset, train
from test_edge_family_time_stop_binding import runtime_fixture, concrete
from seiltanzer.unified_edge_ensemble import candidate_id

VERSION = 'edge-family-affine-r-geometry-v1'


def contract():
    assert importlib.util.find_spec('seiltanzer.edge_family_geometry') is not None, 'portable geometry contract missing'
    return importlib.import_module('seiltanzer.edge_family_geometry')


def affine(value, scale=3., offset=500.):
    result = deepcopy(value)
    for key in ('entry', 'original_stop', 'current', 'active_risk_barrier', 'final_take'):
        result['trade_geometry'][key] = value['trade_geometry'][key] * scale + offset
    for key in ('active_stop_price', 'original_stop', 'original_take', 'take'):
        if key in result['position_state']:
            result['position_state'][key] = value['position_state'][key] * scale + offset
    for candidate in result['active_management_candidates']:
        for key in ('stop_price', 'take_price', 'trigger_price'):
            if key in candidate['parameters']:
                candidate['parameters'][key] = candidate['parameters'][key] * scale + offset
        if 'candidate_id' in candidate:
            candidate['candidate_id'] = candidate_id(candidate['policy'], candidate['parameters'])
    return result


def extension(value):
    c = contract()
    descriptor = c.portable_geometry(value)
    return dict(geometry_contract=VERSION, geometry_descriptor=descriptor,
                geometry_sha256=c.geometry_descriptor_sha256(descriptor))


def portable_dataset():
    rows = dataset()['rows']
    for i, row in enumerate(rows):
        value = affine(snapshot(), 1. + i, 100. * i)
        value.update(captured_ts=row['captured_ts'], trade_id=row['trade_id'])
        value['policy_manager']['inputs']['horizon_minutes'] = 1.
        value['position_state']['remaining_position_fraction'] = 1.
        row['cost_provenance']['position_evidence'].update(
            entry=value['trade_geometry']['entry'], original_stop=value['trade_geometry']['original_stop'])
        c = contract()
        row.update(extension(value), geometry_evidence=c.geometry_evidence(value),
                   exact_geometry_sha256=geometry(value),
                   candidate={'candidate_id': 'CLOSE_25', 'policy': 'CLOSE_25', 'parameters': {}})
    return dataset(rows)


@pytest.mark.parametrize('direction', ['long', 'short'])
def test_affine_price_equivalence_keeps_signed_r_and_original_snapshot(direction):
    value = snapshot()
    if direction == 'short':
        value = affine(value, -1., 500.)
        value['strategy']['direction'] = 'short'
    before = deepcopy(value)
    descriptor = contract().portable_geometry(value)
    transformed = affine(value, 2.3, 777.)
    assert descriptor == contract().portable_geometry(transformed)
    assert descriptor['prices'] == {'entry': 0., 'original_stop': -1., 'active_risk_barrier': -1., 'current': 1., 'final_take': 3.}
    assert value == before
    assert geometry(value) != geometry(transformed)


def test_dataset_defaults_supported_rows_to_portable_without_rewriting_labels_candidates():
    source = archive()
    before = deepcopy(source)
    rows = build(source)['rows']
    assert source == before
    for row in rows:
        if row['action'] in ('CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT'):
            assert row['geometry_contract'] == VERSION
            assert contract().portable_row_reason(row) is None
            assert row['exact_geometry_sha256'] == geometry(snapshot())
        else:
            assert 'geometry_contract' not in row
    deltas = {row['action']: row['delta_net_r'] for row in rows}
    assert deltas['CLOSE_25'] == pytest.approx(.2)
    assert deltas['EXIT'] == pytest.approx(.8)


def test_affine_rows_pool_one_cohort_and_artifact_omits_original_proof():
    data = portable_dataset()
    assert len({row['exact_geometry_sha256'] for row in data['rows']}) == 60
    result = train(data)
    assert result['available'] and len(result['models']) == 1
    model = result['models'][0]
    assert model['geometry_contract'] == VERSION
    assert model['geometry_descriptor'] == data['rows'][0]['geometry_descriptor']
    assert 'geometry_evidence' not in model and 'exact_geometry_sha256' not in model
    assert contract().portable_artifact_reason(model) is None
    assert model['validation']['sample_count'] == 20


@pytest.mark.parametrize('change', ['version', 'hash', 'proof', 'candidate', 'clock', 'instrument', 'descriptor', 'nonportable'])
def test_forged_extensions_are_rejected_before_training(change):
    data = portable_dataset()
    for row in data['rows']:
        if change == 'version': row['geometry_contract'] = 'future-v2'
        elif change == 'hash': row['geometry_sha256'] = 'f' * 64
        elif change == 'proof': row['geometry_evidence']['trade_geometry']['current'] += 1.
        elif change == 'candidate':
            row['candidate'] = {'policy': 'TIME_STOP', 'parameters': {'deadline_ts': row['captured_ts'] + 60.}, 'candidate_id': 'TIME_STOP:fake'}
        elif change == 'clock': row['geometry_evidence']['captured_ts'] += 1.
        elif change == 'instrument': row['geometry_evidence']['strategy']['instrument'] = 'SP500'
        elif change == 'descriptor': row['geometry_descriptor']['execution_inputs']['max_r'] += .1
        else:
            params = {'stop_price': row['geometry_evidence']['trade_geometry']['current']}
            row.update(action=candidate_id('TIGHTEN_STOP', params), candidate={'policy': 'TIGHTEN_STOP', 'parameters': params, 'candidate_id': candidate_id('TIGHTEN_STOP', params)})
    result = train(dataset(data['rows']))
    assert not result['available']
    assert result['diagnostics']['rejected_row_count'] == 60


def test_valid_hash_and_proof_do_not_admit_a_candidate_absent_from_original_matrix():
    row = portable_dataset()['rows'][0]
    from seiltanzer.edge_family_action_binding import candidate_binding, stable_action_id
    candidate = concrete(row['captured_ts'], 1.)
    bound = candidate_binding(candidate, row['captured_ts'])
    row.update(action=stable_action_id(bound), candidate=candidate, action_binding=bound)
    assert contract().portable_row_reason(row) is not None


@pytest.mark.parametrize('change', ['exposure', 'max_r', 'topology', 'horizon', 'instrument', 'direction', 'current_r', 'rungs', 'barrier_type', 'parameter_presence'])
def test_changed_r_exposure_or_topology_cannot_match(change):
    value = snapshot()
    artifact = dict(extension(value), instrument='NAS100', horizon_minutes=240., action_models={'CLOSE_25': {}})
    changed = affine(value)
    if change == 'exposure': changed['position_state']['remaining_position_fraction'] = .7
    elif change == 'max_r': changed['policy_manager']['inputs']['max_r'] += .1
    elif change == 'topology': changed['active_management_candidates'] = []
    elif change == 'horizon': changed['policy_manager']['inputs']['horizon_minutes'] = 1.
    elif change == 'instrument': changed['strategy']['instrument'] = 'SP500'
    elif change == 'current_r':
        changed['policy_manager']['inputs'].update(r0=1.1, max_r=1.1)
        changed['trade_geometry']['current'] += 3.
    elif change == 'rungs': changed['policy_manager']['inputs']['rungs'] = [1.6, 2.]
    elif change == 'barrier_type': changed['trade_geometry']['active_risk_barrier_type'] = 'break_even'
    elif change == 'parameter_presence': changed['active_management_candidates'][0]['parameters']['anchor'] = None
    else:
        changed = affine(value, -1., 500.)
        changed['strategy']['direction'] = 'short'
    assert not contract().portable_geometry_matches(artifact, changed)


def test_runtime_admits_affine_current_prices_and_resolves_current_time_stop():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value, artifact, candidate = runtime_fixture()
    artifact.update(extension(value))
    current = affine(value)
    current['captured_ts'] += 100.
    current['active_management_candidates'][-1] = concrete(current['captured_ts'])
    scores = build_edge_family_evidence(current)['components'][0]['scores']
    assert concrete(current['captured_ts'])['candidate_id'] in scores
    assert candidate['candidate_id'] not in scores


@pytest.mark.parametrize('change', ['unknown', 'missing_contract', 'extended', 'malformed', 'bound'])
def test_runtime_and_packaging_reject_explicit_invalid_extensions(change):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    from scripts.run_edge_family_pipeline import package_runtime_context
    value, artifact, _ = runtime_fixture()
    artifact.update(extension(value))
    if change == 'unknown': artifact['geometry_contract'] = 'future-v2'
    elif change == 'missing_contract': del artifact['geometry_contract']
    elif change == 'extended': artifact['action_models'] = {'TIGHTEN_STOP:123': {}}
    elif change == 'malformed': artifact['geometry_descriptor']['extra'] = True
    else: artifact['geometry_descriptor']['extra'] = 'x' * (32 * 1024)
    assert build_edge_family_evidence(value)['components'] == []
    with pytest.raises(ValueError):
        package_runtime_context([artifact], expected_sha='a' * 40, captured_ts=value['captured_ts'], dataset={})


@pytest.mark.parametrize('change', ['unknown', 'nonfinite', 'overflow_risk', 'tiny_risk', 'inconsistent_r'])
def test_normalization_preserves_exact_validation_and_rejects_unsafe_arithmetic(change):
    value = snapshot()
    if change == 'unknown': value['trade_geometry']['future_rule'] = True
    elif change == 'nonfinite': value['trade_geometry']['entry'] = float('inf')
    elif change == 'overflow_risk':
        value['trade_geometry'].update(entry=1e308, original_stop=-1e308, current=1e308, active_risk_barrier=-1e308, final_take=1e308)
    elif change == 'tiny_risk':
        value['trade_geometry'].update(entry=0., original_stop=-5e-324, current=1., active_risk_barrier=-5e-324, final_take=2.)
    else: value['policy_manager']['inputs']['r0'] += .01
    with pytest.raises(ValueError): contract().portable_geometry(value)


def test_legacy_exact_models_keep_their_original_matching_behavior():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value, _, candidate = runtime_fixture()
    assert candidate['candidate_id'] in build_edge_family_evidence(value)['components'][0]['scores']
    assert build_edge_family_evidence(affine(value))['components'] == []


@pytest.mark.parametrize('change', ['entry', 'original_stop', 'remaining_position_fraction'])
def test_portable_proof_must_agree_with_independent_position_evidence(change):
    data = portable_dataset()
    for row in data['rows']:
        row['cost_provenance']['position_evidence'][change] += -.1 if change == 'remaining_position_fraction' else 1.
    result = train(dataset(data['rows']))
    assert not result['available']
    assert result['diagnostics']['rejected_row_count'] == 60


def test_artifact_time_stop_binding_must_be_in_normalized_original_matrix():
    from seiltanzer.edge_family_action_binding import stable_action_id
    from test_edge_family_time_stop_binding import binding
    value, artifact, _ = runtime_fixture()
    artifact.update(extension(value))
    model = next(iter(artifact['action_models'].values()))
    model['action_binding'] = binding(11.)
    artifact['action_models'] = {stable_action_id(binding(11.)): model}
    assert contract().portable_artifact_reason(artifact) is not None


@pytest.mark.parametrize('change', ['partial', 'null', 'evidence_bound', 'evidence_nonfinite', 'evidence_unknown', 'descriptor_schema', 'action_type'])
def test_row_extension_validators_fail_closed_on_malformed_or_partial_input(change):
    row = portable_dataset()['rows'][0]
    if change == 'partial': del row['geometry_contract']
    elif change == 'null': row['geometry_contract'] = None
    elif change == 'evidence_bound': row['geometry_evidence']['position_state']['extra'] = 'x' * (48 * 1024)
    elif change == 'evidence_nonfinite': row['geometry_evidence']['position_state']['realized_r_weighted'] = float('nan')
    elif change == 'evidence_unknown': row['geometry_evidence']['policy_manager']['inputs']['future_rule'] = 1.
    elif change == 'descriptor_schema':
        row['geometry_descriptor']['execution_inputs']['future_rule'] = 1.
        row['geometry_sha256'] = contract().geometry_descriptor_sha256(row['geometry_descriptor'])
    else: row['action'] = []
    assert contract().portable_row_reason(row) is not None


@pytest.mark.parametrize('field', ['realized_position_fraction', 'realized_r_weighted'])
def test_nonfinite_numeric_exposure_string_cannot_define_portable_identity(field):
    value = snapshot()
    value['position_state'][field] = 'nan'
    with pytest.raises(ValueError): contract().portable_geometry(value)


def test_geometry_proof_projection_is_frozen_and_omits_broker_account_context():
    value = snapshot()
    proof = contract().geometry_evidence(value)
    assert 'execution_cost_model' not in proof['policy_manager']
    assert 'trade_identity' not in proof and 'broker_rollover_schedule' not in proof
    proof['trade_geometry']['entry'] = 0.
    assert value['trade_geometry']['entry'] == 100.


def test_legacy_and_portable_hash_namespaces_do_not_pool_or_crash():
    rows = portable_dataset()['rows']
    legacy = deepcopy(rows)
    for row in legacy:
        row['review_id'] += 100
        for key in ('geometry_contract', 'geometry_descriptor', 'geometry_evidence', 'exact_geometry_sha256'):
            del row[key]
    result = train(dataset(rows + legacy))
    assert result['available'] and len(result['models']) == 2
    assert sum('geometry_contract' in model for model in result['models']) == 1
    assert all(model['validation']['group_count'] == 20 for model in result['models'])


def test_oversized_portable_proof_excludes_supported_actions_without_exact_fallback():
    value = snapshot()
    value['position_state']['extra'] = 'x' * (48 * 1024)
    result = build(archive(record(value)))
    assert {row['candidate']['policy'] for row in result['rows']} == {'HOLD', 'TIGHTEN_STOP'}
    exclusions = [item for item in result['exclusions'] if item.get('action') in ('CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT')]
    assert len(exclusions) == 4
    assert all(item['reason'] == 'PORTABLE_GEOMETRY_BOUND_EXCEEDED' for item in exclusions)


def test_equal_numeric_exposure_has_one_canonical_descriptor_hash():
    value = snapshot()
    value['position_state']['remaining_position_fraction'] = 1
    other = affine(value)
    other['position_state']['remaining_position_fraction'] = 1.
    first, second = contract().portable_geometry(value), contract().portable_geometry(other)
    assert contract().geometry_descriptor_sha256(first) == contract().geometry_descriptor_sha256(second)


@pytest.mark.parametrize('change', ['integer_execution', 'negative_zero'])
def test_artifact_canonical_numeric_schema_is_checked_even_with_valid_hash(change):
    value, artifact, _ = runtime_fixture()
    artifact.update(extension(value))
    if change == 'integer_execution': artifact['geometry_descriptor']['execution_inputs']['r0'] = 1
    else: artifact['geometry_descriptor']['prices']['entry'] = -0.
    artifact['geometry_sha256'] = contract().geometry_descriptor_sha256(artifact['geometry_descriptor'])
    assert contract().portable_artifact_reason(artifact) is not None


@pytest.mark.parametrize('artifact', [None, [], 'future', True])
def test_portable_match_interface_returns_false_for_malformed_artifacts(artifact):
    assert contract().portable_geometry_matches(artifact, snapshot()) is False
