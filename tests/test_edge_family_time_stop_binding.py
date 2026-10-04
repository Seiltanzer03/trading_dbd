"""Artificial contract fixtures; no broker outcomes or source data are invented."""
from copy import deepcopy

import pytest

from test_edge_family_dataset import snapshot, record, archive, build, T0, geometry
from test_edge_family_training import dataset, train
from seiltanzer.unified_edge_ensemble import candidate_id


def concrete(captured, minutes=10.):
    params = {'deadline_ts': captured + minutes * 60., 'timeout_minutes': minutes}
    return {'policy': 'TIME_STOP', 'parameters': params,
            'candidate_id': candidate_id('TIME_STOP', params)}


def binding(minutes=10.):
    return {'version': 'edge-family-time-stop-binding-v1', 'policy': 'TIME_STOP',
            'parameters': {'deadline_offset_sec': minutes * 60., 'timeout_minutes': minutes}}


def test_true_time_stop_label_keeps_original_candidate_and_stable_binding():
    from seiltanzer.edge_family_action_binding import stable_action_id
    from scripts.run_unified_edge_comparison import observed_replay
    frozen = snapshot()
    candidate = concrete(T0)
    frozen['active_management_candidates'].append(candidate)
    source = record(frozen)
    result = build(archive(source))
    row = next(r for r in result['rows'] if r['candidate']['policy'] == 'TIME_STOP')
    assert row['candidate'] == candidate
    assert row['action_binding'] == binding()
    assert row['action'] == stable_action_id(binding())
    replay = observed_replay(frozen, source, candidate)
    hold = observed_replay(frozen, source, {'policy': 'HOLD', 'parameters': {}})
    assert row['delta_net_r'] == pytest.approx(replay['net_r_on_remaining'] - hold['net_r_on_remaining'])
    assert replay['exit_reason'] == 'time_stop'
    assert replay['endpoint_semantics'] == 'observed_terminal_resolution'


def bound_dataset():
    from seiltanzer.edge_family_action_binding import stable_action_id
    rows = dataset()['rows']
    for row in rows:
        row.update(action=stable_action_id(binding(1.)), action_binding=binding(1.),
                   candidate=concrete(row['captured_ts'], 1.))
    return dataset(rows)


def test_distinct_capture_clocks_pool_stable_action_and_artifact_retains_binding():
    result = train(bound_dataset())
    assert result['available'] and len(result['models']) == 1
    model = next(iter(result['models'][0]['action_models'].values()))
    assert model['action_binding'] == binding(1.)


@pytest.mark.parametrize('mutation', ['id', 'deadline', 'timeout', 'policy', 'extra', 'missing', 'null', 'no_binding'])
def test_trainer_rejects_mismatched_or_malformed_retained_binding(mutation):
    data = bound_dataset()
    for row in data['rows']:
        if mutation == 'id': row['candidate']['candidate_id'] = 'TIME_STOP:fake'
        elif mutation == 'deadline': row['candidate']['parameters']['deadline_ts'] += 1.
        elif mutation == 'timeout': row['candidate']['parameters']['timeout_minutes'] = 2.
        elif mutation == 'policy': row['candidate']['policy'] = 'EXIT'
        elif mutation == 'extra': row['action_binding']['extra'] = True
        elif mutation == 'missing': del row['action_binding']['parameters']['deadline_offset_sec']
        elif mutation == 'null': row['action_binding'] = None
        else: del row['action_binding']
    assert not train(dataset(data['rows']))['available']


def runtime_fixture():
    from test_edge_family_adapters import model, source, T0 as MODEL_T0
    from seiltanzer.edge_family_action_binding import stable_action_id
    frozen = snapshot()
    feature = 'macro.expected_rate_change'
    frozen['edge_family_sources'] = {'macro': source(features={feature: -.2}, observed_ts=T0 - 10., received_ts=T0 - 8.)}
    candidate = concrete(T0)
    frozen['active_management_candidates'].append(candidate)
    artifact = model('macro', feature)
    for key in ('train_end_ts', 'validation_start_ts', 'validation_end_ts', 'trained_at'):
        artifact[key] += T0 - MODEL_T0
    artifact.update(geometry_sha256=geometry(frozen), horizon_minutes=240.)
    artifact['action_models'] = {stable_action_id(binding()): {
        'validated': True, 'intercept_r': .1, 'coefficients': {feature: .02},
        'action_binding': binding()}}
    frozen['edge_family_models'] = {'macro': artifact}
    return frozen, artifact, candidate


def test_runtime_resolves_stable_key_to_exact_current_absolute_candidate_id():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    from seiltanzer.edge_family_action_binding import stable_action_id
    frozen, artifact, candidate = runtime_fixture()
    result = build_edge_family_evidence(frozen)
    assert candidate['candidate_id'] in result['components'][0]['scores']
    assert stable_action_id(binding()) not in result['components'][0]['scores']
    shifted = deepcopy(frozen)
    shifted['captured_ts'] += 100.
    shifted['active_management_candidates'][-1] = concrete(T0 + 100.)
    assert geometry(shifted) == geometry(frozen)
    scores = build_edge_family_evidence(shifted)['components'][0]['scores']
    assert concrete(T0 + 100.)['candidate_id'] in scores
    assert candidate['candidate_id'] not in scores


@pytest.mark.parametrize('mutation', ['deadline', 'timeout', 'extra', 'null', 'no_binding', 'geometry', 'horizon'])
def test_runtime_binding_mismatch_or_failed_geometry_horizon_is_unavailable(mutation):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    frozen, artifact, candidate = runtime_fixture()
    model = next(iter(artifact['action_models'].values()))
    if mutation == 'deadline': frozen['active_management_candidates'][-1]['parameters']['deadline_ts'] += 1.
    elif mutation == 'timeout': model['action_binding']['parameters']['timeout_minutes'] = 11.
    elif mutation == 'extra': model['action_binding']['extra'] = True
    elif mutation == 'null': model['action_binding'] = None
    elif mutation == 'no_binding': del model['action_binding']
    elif mutation == 'geometry': artifact['geometry_sha256'] = 'e' * 64
    else: artifact['horizon_minutes'] = 1.
    assert build_edge_family_evidence(frozen)['components'] == []


def test_pipeline_packaging_checks_optional_binding_without_changing_legacy():
    from scripts.run_edge_family_pipeline import _ridge_actions_valid
    from seiltanzer.edge_family_action_binding import stable_action_id
    action = {'validated': True, 'intercept_r': .1, 'coefficients': {'macro.x': .02}}
    assert _ridge_actions_valid({'action_models': {'CLOSE_25': action}})
    action['action_binding'] = binding()
    assert _ridge_actions_valid({'action_models': {stable_action_id(binding()): action}})
    action['action_binding']['parameters']['timeout_minutes'] = 11.
    assert not _ridge_actions_valid({'action_models': {stable_action_id(binding()): action}})


def test_dataset_at_distinct_clocks_has_identical_binding_label_and_geometry():
    frozen = snapshot()
    frozen['active_management_candidates'].append(concrete(T0))
    first = record(frozen)

    def shift(value):
        if isinstance(value, dict): return {key: shift(item) for key, item in value.items()}
        if isinstance(value, list): return [shift(item) for item in value]
        return value + 100. if isinstance(value, (int, float)) and value > T0 - 1_000_000 else value

    second = record(shift(frozen), path=shift(first['path_points']))
    second.update(captured_ts=T0 + 100., path_query_horizon_end_ts=T0 + 14500., review_id='ci-shifted')
    from test_edge_family_dataset import digest
    episodes = [first, second]
    result = build({'contract_version': 'edge-family-archive-v1', 'episodes': episodes,
                    'dataset_sha256': digest(episodes)})
    rows = [row for row in result['rows'] if row['candidate']['policy'] == 'TIME_STOP']
    assert len(rows) == 2
    assert rows[0]['candidate']['candidate_id'] != rows[1]['candidate']['candidate_id']
    assert rows[0]['action'] == rows[1]['action']
    assert rows[0]['action_binding'] == rows[1]['action_binding']
    assert rows[0]['geometry_sha256'] == rows[1]['geometry_sha256']
    assert rows[0]['delta_net_r'] == pytest.approx(rows[1]['delta_net_r'])


@pytest.mark.parametrize('params', [
    {'deadline_offset_sec': True}, {'deadline_offset_sec': 0.},
    {'deadline_offset_sec': -1.}, {'deadline_offset_sec': float('nan')},
    {'deadline_offset_sec': float('inf')}, {'deadline_offset_sec': '600'},
    {'deadline_offset_sec': 600., 'timeout_minutes': None},
    {'deadline_offset_sec': 600., 'timeout_minutes': True},
    {'deadline_offset_sec': 600., 'timeout_minutes': 11.},
    {'deadline_offset_sec': 600., 'extra': 1.},
])
def test_binding_strictly_rejects_malformed_numeric_or_unknown_parameters(params):
    from seiltanzer.edge_family_action_binding import stable_action_id
    malformed = binding()
    malformed['parameters'] = params
    with pytest.raises(ValueError): stable_action_id(malformed)


def test_deadline_only_binding_preserves_presence_and_maps_exactly():
    from seiltanzer.edge_family_action_binding import candidate_binding, stable_action_id, resolve_action
    frozen = snapshot()
    candidate = concrete(T0)
    del candidate['parameters']['timeout_minutes']
    candidate['candidate_id'] = candidate_id('TIME_STOP', candidate['parameters'])
    frozen['active_management_candidates'].append(candidate)
    bound = candidate_binding(candidate, T0)
    assert bound['parameters'] == {'deadline_offset_sec': 600.}
    assert stable_action_id(bound) != stable_action_id(binding())
    assert resolve_action(stable_action_id(bound), bound, frozen) == candidate['candidate_id']
    with pytest.raises(ValueError): resolve_action(stable_action_id(binding()), binding(), frozen)


def test_conditional_outcomes_also_resolve_concrete_current_candidate():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    frozen, artifact, candidate = runtime_fixture()
    bound = next(iter(artifact['action_models'].values()))
    bound.update(kind='conditional_net_outcomes', bins=[{
        'conditions': [{'feature': 'macro.expected_rate_change', 'lower': -1., 'upper': 1.}],
        'sample_count': 60, 'mean_delta_net_r': .08}])
    scores = build_edge_family_evidence(frozen)['components'][0]['scores']
    assert scores[candidate['candidate_id']] == pytest.approx(.4)


def test_fractional_timeout_uses_producer_formula_without_epoch_roundoff_rejection():
    from seiltanzer.edge_family_action_binding import candidate_binding, stable_action_id
    minutes = .123456789
    keys, geometries = [], []
    for captured in (1_780_000_000., 1_780_000_100., 2_147_483_648.):
        candidate = concrete(captured, minutes)
        bound = candidate_binding(candidate, captured)
        assert bound == binding(minutes)
        keys.append(stable_action_id(bound))
        frozen = snapshot()
        frozen['captured_ts'] = captured
        frozen['active_management_candidates'].append(deepcopy(candidate))
        geometries.append(geometry(frozen))
        # Even one adjacent representable deadline is a different concrete action.
        import math
        candidate['parameters']['deadline_ts'] = math.nextafter(candidate['parameters']['deadline_ts'], math.inf)
        candidate['candidate_id'] = candidate_id('TIME_STOP', candidate['parameters'])
        with pytest.raises(ValueError): candidate_binding(candidate, captured)
    assert len(set(keys)) == 1
    assert len(set(geometries)) == 1


def test_tiny_timeout_that_rounds_to_capture_is_rejected():
    from seiltanzer.edge_family_action_binding import candidate_binding
    candidate = concrete(T0, 1e-12)
    assert candidate['parameters']['deadline_ts'] == T0
    with pytest.raises(ValueError): candidate_binding(candidate, T0)


def test_ambiguous_equivalent_current_candidate_ids_are_unavailable():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    frozen, artifact, candidate = runtime_fixture()
    second = deepcopy(candidate)
    second['parameters']['timeout_minutes'] = 10
    second['candidate_id'] = candidate_id('TIME_STOP', second['parameters'])
    assert second['candidate_id'] != candidate['candidate_id']
    frozen['active_management_candidates'].append(second)
    artifact['geometry_sha256'] = geometry(frozen)
    result = build_edge_family_evidence(frozen)
    assert result['components'] == []
    assert result['families']['macro']['forecast_rejections'][0]['reason'] == 'TIME_STOP_BOUND_CANDIDATE_UNAVAILABLE'
