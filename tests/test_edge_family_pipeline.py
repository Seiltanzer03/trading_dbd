"""Filesystem packaging and runtime geometry regression contracts."""
import hashlib
import json
from copy import deepcopy

import pytest

from seiltanzer.edge_family_adapters import build_edge_family_evidence
from seiltanzer.unified_edge_runtime_context import load_unified_edge_context
from test_edge_family_adapters import T0, family_fixture, model, snapshot, source

SHA = 'a' * 40


def packaging():
    import scripts.run_edge_family_pipeline as pipeline
    return pipeline


def measured_model():
    artifact = model('macro', 'macro.expected_rate_change')
    artifact['geometry_sha256'] = 'c' * 64
    return artifact


@pytest.mark.parametrize('global_claim,expected', [(True, True), (False, False)])
def test_admitted_source_provenance_preserves_only_validated_global_context(global_claim, expected):
    data = source(features={'macro.expected_rate_change': -.2}, global_context=global_claim)
    frozen = snapshot('macro', data, 'macro.expected_rate_change')
    result = build_edge_family_evidence(frozen)
    meta = result['families']['macro']['feature_provenance']['macro.expected_rate_change']
    assert meta['global_context'] is expected
    assert meta['source_verified'] is True


def test_event_provenance_retains_independently_recheckable_prepublication_consensus():
    data, feature = family_fixture('event')
    result = build_edge_family_evidence(snapshot('event', data, feature))
    meta = result['families']['event']['feature_provenance'][feature]
    consensus = meta['consensus_provenance']
    assert consensus['observed_ts'] == T0 - 900
    assert consensus['received_ts'] == T0 - 600
    assert consensus['source_instrument'] == 'NAS100'
    assert consensus['source_verified'] is True
    assert consensus['global_context'] is False
    assert consensus['period'] == meta['period'] == '2026-04'
    assert consensus['unit'] == meta['unit'] == 'percent'
    assert consensus['release_id'] == meta['release_id'] == 'CPI:1'


def test_missing_instrument_is_not_inferred_to_be_global_context():
    data = source(features={'macro.expected_rate_change': -.2})
    data.pop('instrument')
    frozen = snapshot('macro', data, 'macro.expected_rate_change')
    assert build_edge_family_evidence(frozen)['families']['macro']['features'] == {}


@pytest.mark.parametrize('where', ['snapshot', 'source', 'consensus', 'official_release', 'macro_root', 'numeric_root'])
def test_runtime_sources_explicitly_marked_synthetic_cannot_vote(where):
    data, feature = family_fixture('event' if where == 'consensus' else 'macro')
    family = 'event' if where == 'consensus' else 'macro'
    frozen = snapshot(family, data, feature)
    if where == 'snapshot':
        frozen['synthetic'] = True
    elif where == 'source':
        data['synthetic'] = True
    elif where == 'consensus':
        data['consensus']['synthetic'] = True
    else:
        frozen.pop('edge_family_sources')
        frozen['macro_context_v1'] = {'numeric_macro': {
            'candidate_vector': {'macro.cpi_value': 3.1}, 'releases': {'cpi': {
                'status': 'VALID', 'release_id': 'ci-official-release',
                'official_source_verified': True, 'available_at': T0 - 5,
                'published_at': T0 - 10}}}}
        root = frozen['macro_context_v1']
        target = root if where == 'macro_root' else root['numeric_macro'] if where == 'numeric_root' else root['numeric_macro']['releases']['cpi']
        target['synthetic'] = True
    result = build_edge_family_evidence(frozen)
    assert result['components'] == []
    assert result['families'][family]['features'] == {}


def test_runtime_package_round_trips_pinned_consumer(tmp_path):
    pipeline = packaging()
    artifact = measured_model()
    context = pipeline.package_runtime_context([artifact], expected_sha=SHA,
                                              captured_ts=T0-1, dataset={'rows': []})
    raw = pipeline.encode_json(context, max_bytes=48_000)
    path = tmp_path / 'runtime_context.json'
    path.write_bytes(raw)
    frozen = snapshot('macro', source(features={'macro.expected_rate_change': -.2}),
                      'macro.expected_rate_change')
    loaded, audit = load_unified_edge_context(path, expected_document_sha256=hashlib.sha256(raw).hexdigest(),
                                             expected_sha=SHA, snapshot=frozen)
    assert audit['available'] is True
    assert loaded['edge_family_models']['macro'][0] == artifact
    assert context['deployment_sha'] == SHA


def test_synthetic_and_empty_models_produce_no_runtime_context():
    pipeline = packaging()
    assert pipeline.package_runtime_context([], expected_sha=SHA, captured_ts=T0,
                                            dataset={'rows': []}) is None
    assert pipeline.package_runtime_context([measured_model()], expected_sha=SHA, captured_ts=T0,
                                            dataset={'rows': [{'synthetic': True}]}) is None


@pytest.mark.parametrize('change', ['actions', 'coefficient', 'pool', 'count'])
def test_packager_rejects_models_the_runtime_consumer_cannot_admit(change):
    artifact = measured_model()
    if change == 'actions':
        artifact['action_models'] = {}
    elif change == 'coefficient':
        artifact['action_models']['CLOSE_25']['coefficients'] = {'macro.expected_rate_change': 'invalid'}
    elif change == 'pool':
        artifact['component_id'] = 'unknown'
    else:
        artifact['validation']['sample_count'] = 20.5
    with pytest.raises(ValueError, match='UNVALIDATED_MODEL'):
        packaging().package_runtime_context([artifact], expected_sha=SHA,
                                            captured_ts=T0, dataset={'rows': []})


def test_oversized_context_refused_without_truncating_models():
    pipeline = packaging()
    artifact = measured_model()
    artifact['lineage'] = 'x' * 48_000
    with pytest.raises(ValueError, match='BYTE_BOUND'):
        pipeline.package_runtime_context([artifact], expected_sha=SHA,
                                         captured_ts=T0, dataset={'rows': []})


@pytest.mark.parametrize('raw', [b'{"read_only":true,"read_only":false}', b'{"x":NaN}',
                                  b'{"x":1e999}', b'[]', b'{"x":' + b'[' * 70 + b'0' + b']' * 70 + b'}'])
def test_strict_json_rejects_ambiguous_nonfinite_and_deep_inputs(tmp_path, raw):
    path = tmp_path / 'input.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        packaging().read_json(path, max_bytes=96_000_000)


def test_json_reader_enforces_bound_before_decoding(tmp_path):
    path = tmp_path / 'input.json'
    path.write_bytes(b'{"padding":"' + b'x' * 100 + b'"}')
    with pytest.raises(ValueError, match='BYTE_BOUND'):
        packaging().read_json(path, max_bytes=32)


@pytest.mark.parametrize('geometry', ['0'*64, 'malformed', None, 42, True])
def test_wrong_or_malformed_optional_geometry_excludes_model(geometry):
    frozen = snapshot('macro', source(features={'macro.expected_rate_change': -.2}),
                      'macro.expected_rate_change')
    frozen['edge_family_models']['macro']['geometry_sha256'] = geometry
    result = build_edge_family_evidence(frozen)
    assert result['components'] == []
    assert result['families']['macro']['forecast_rejections'][0]['reason'].startswith('MODEL_GEOMETRY_')


def test_external_legacy_artifact_retains_existing_causal_contract():
    frozen = snapshot('macro', source(features={'macro.expected_rate_change': -.2}),
                      'macro.expected_rate_change')
    assert build_edge_family_evidence(frozen)['components']
    frozen['edge_family_models']['macro']['validation']['point_in_time'] = False
    assert build_edge_family_evidence(frozen)['components'] == []


def test_matching_optional_geometry_admits_only_with_existing_horizon_and_features():
    from test_edge_family_dataset import snapshot as frozen_fixture
    from seiltanzer.edge_family_dataset import family_geometry_sha256
    frozen = frozen_fixture()
    artifact = model('intermarket', 'intermarket.SP500.return')
    for clock in ('train_end_ts', 'validation_start_ts', 'validation_end_ts', 'trained_at'):
        artifact[clock] += frozen['captured_ts'] - T0
    artifact.update(geometry_sha256=family_geometry_sha256(frozen), horizon_minutes=240,
                    feature_windows_sec={'intermarket.SP500.return': 600.})
    frozen['edge_family_models'] = {'intermarket': artifact}
    assert build_edge_family_evidence(frozen)['components']
    frozen['policy_manager']['inputs'].pop('stop_r')
    result = build_edge_family_evidence(frozen)
    assert result['components'] == []
    assert result['families']['intermarket']['forecast_rejections'][0]['reason'] == 'MODEL_GEOMETRY_INVALID_OR_UNAVAILABLE'


def test_empty_real_export_has_explicit_diagnostics_and_no_active_models(tmp_path):
    pipeline = packaging()
    result = pipeline.run_pipeline({'read_only': True, 'reviews': [], 'exported_ts': T0},
                                   expected_sha=SHA, trained_at=T0)
    assert result['diagnostics']['available'] is False
    assert result['diagnostics']['status'] == 'UNAVAILABLE'
    assert result['diagnostics']['archive_state'] == 'INITIAL_EMPTY'
    assert result['diagnostics']['active_model_count'] == 0
    assert result['diagnostics']['document_sha256'] is None
    assert result['runtime_context'] is None
    path = tmp_path / 'runtime_context.json'
    path.write_text('{"stale_model":true}')
    pipeline.write_outputs(result, tmp_path)
    assert not path.exists()
    assert json.loads((tmp_path / 'diagnostics.json').read_bytes())['model_matrix'] == {}
    assert json.loads((tmp_path / 'archive.json').read_bytes())['episodes'] == []


def test_explicit_archive_restore_state_is_not_reported_as_initial():
    pipeline = packaging()
    previous = pipeline.run_pipeline({'read_only': True, 'reviews': [], 'exported_ts': T0},
                                     expected_sha=SHA, trained_at=T0)['archive']
    result = pipeline.run_pipeline({'read_only': True, 'reviews': [], 'exported_ts': T0},
                                   expected_sha=SHA, previous_archive=previous, trained_at=T0)
    assert result['diagnostics']['archive_state'] == 'RESTORED'


def _ci_longitudinal_records():
    """Admission-shaped CI fixtures only; never exported as broker evidence."""
    from test_edge_family_dataset import snapshot as frozen_fixture, record, T0 as dataset_clock
    records = []
    for index in range(60):
        offset = (index - 60) * 20_000.
        captured = dataset_clock + offset

        def shift(value):
            if isinstance(value, dict):
                return {key: index + 1 if key == 'trade_id' else shift(item)
                        for key, item in value.items()}
            if isinstance(value, list):
                return [shift(item) for item in value]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1_000_000_000:
                return value + offset
            return value

        frozen = shift(frozen_fixture())
        frozen['edge_family_sources']['intermarket'][0]['linked_returns'][0]['end_price'] = 105. + index % 10
        endpoint = .15 + .01 * (index % 10)
        retained = record(frozen, path=[
            {'ts': captured, 'r': 1., 'price': 110.},
            {'ts': captured + 300., 'r': .4, 'price': 104.},
            {'ts': captured + 14_400., 'r': endpoint, 'price': 100. + 10. * endpoint}])
        retained.update(review_id='ci-pipeline-' + str(index), trade_id=index + 1,
                        captured_ts=captured,
                        path_query_horizon_end_ts=captured + 14_400.)
        records.append(retained)
    return records


def test_ci_full_archive_dataset_trainer_runtime_consumer_path(tmp_path):
    pipeline = packaging()
    records = _ci_longitudinal_records()
    from test_edge_family_dataset import T0 as dataset_clock
    trained = dataset_clock
    first = pipeline.run_pipeline({'read_only': True, 'reviews': records[:32], 'exported_ts': trained},
                                  expected_sha=SHA, trained_at=trained)
    assert first['diagnostics']['status'] == 'UNAVAILABLE'
    assert first['runtime_context'] is None
    result = pipeline.run_pipeline({'read_only': True, 'reviews': records[32:], 'exported_ts': trained},
                                   previous_archive=first['archive'], expected_sha=SHA, trained_at=trained)
    assert len(result['archive']['episodes']) == 60
    assert result['diagnostics']['active_model_count'] > 0
    pipeline.write_outputs(result, tmp_path)
    # Apply the retained geometry to a fresh causal source observation. Never
    # backdate a newly trained artifact into one of its historical snapshots.
    from test_edge_family_dataset import snapshot as frozen_fixture
    from seiltanzer.edge_family_dataset import family_geometry_sha256
    frozen = frozen_fixture()
    offset = trained - frozen['captured_ts']
    frozen['captured_ts'] = trained
    current_source = frozen['edge_family_sources']['intermarket'][0]
    current_source['observed_ts'] += offset
    current_source['received_ts'] += offset
    current_source['linked_returns'][0]['start_ts'] += offset
    current_source['linked_returns'][0]['end_ts'] += offset
    loaded, audit = load_unified_edge_context(tmp_path / 'runtime_context.json',
        expected_document_sha256=result['diagnostics']['document_sha256'], expected_sha=SHA, snapshot=frozen)
    assert audit['available'] is True
    frozen.update(loaded)
    models = frozen['edge_family_models']['intermarket']
    from seiltanzer.edge_family_geometry import portable_geometry_matches
    portable = [value for value in models if 'geometry_contract' in value]
    exact = [value for value in models if 'geometry_contract' not in value]
    assert portable and exact
    assert all(portable_geometry_matches(value, frozen) for value in portable)
    assert all(value['geometry_sha256'] == family_geometry_sha256(frozen) for value in exact)
    assert build_edge_family_evidence(frozen)['components']
    frozen['position_state']['remaining_position_fraction'] = .7
    assert build_edge_family_evidence(frozen)['components'] == []
    synthetic = pipeline.run_pipeline({'read_only': True, 'synthetic': True,
        'reviews': [], 'exported_ts': trained}, previous_archive=result['archive'],
        expected_sha=SHA, trained_at=trained)
    assert synthetic['runtime_context'] is None
    assert synthetic['diagnostics']['active_model_count'] == 0


def test_output_serialization_failure_preserves_existing_artifacts(tmp_path):
    pipeline = packaging()
    path = tmp_path / 'archive.json'
    path.write_text('existing')
    result = {'archive': {}, 'dataset': {}, 'diagnostics': {},
              'runtime_context': {'padding': 'x' * 48_000}}
    with pytest.raises(ValueError, match='BYTE_BOUND'):
        pipeline.write_outputs(result, tmp_path)
    assert path.read_text() == 'existing'
    assert list(tmp_path.iterdir()) == [path]


def test_synthetic_ci_envelope_never_writes_runtime_artifact(tmp_path):
    pipeline = packaging()
    result = pipeline.run_pipeline({'read_only': True, 'synthetic': True,
                                   'reviews': [], 'exported_ts': T0},
                                   expected_sha=SHA, trained_at=T0)
    pipeline.write_outputs(result, tmp_path)
    assert result['diagnostics']['active_model_count'] == 0
    assert result['diagnostics']['production_activation_performed'] is False
    assert not (tmp_path / 'runtime_context.json').exists()


def test_cli_empty_export_writes_diagnostics_without_runtime_or_activation(tmp_path, capsys):
    reviews = tmp_path / 'reviews.json'
    reviews.write_text(json.dumps({'read_only': True, 'reviews': [], 'exported_ts': T0}))
    output = tmp_path / 'output'
    assert packaging().main(['--reviews', str(reviews), '--expected-sha', SHA,
                              '--output-dir', str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['available'] is False
    assert summary['active_model_count'] == 0
    diagnostics = json.loads((output / 'diagnostics.json').read_bytes())
    assert diagnostics['status'] == 'UNAVAILABLE'
    assert diagnostics['network_calls'] == diagnostics['production_or_database_writes'] == 0
    assert not (output / 'runtime_context.json').exists()


def test_cli_invalid_sha_cannot_create_output_directory(tmp_path):
    reviews = tmp_path / 'reviews.json'
    reviews.write_text(json.dumps({'read_only': True, 'reviews': [], 'exported_ts': T0}))
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='IMMUTABLE_DEPLOYMENT_SHA_REQUIRED'):
        packaging().main(['--reviews', str(reviews), '--expected-sha', 'main',
                          '--output-dir', str(output)])
    assert not output.exists()


@pytest.mark.parametrize('change', ['validation', 'future', 'geometry', 'synthetic', 'sha'])
def test_packager_refuses_unvalidated_models_and_invalid_deployment(change):
    pipeline = packaging()
    artifact = measured_model()
    expected_sha = SHA
    if change == 'validation':
        artifact['validation']['status'] = 'UNAVAILABLE'
    elif change == 'future':
        artifact['trained_at'] = T0 + 1
    elif change == 'geometry':
        artifact['geometry_sha256'] = None
    elif change == 'synthetic':
        artifact['synthetic'] = True
    else:
        expected_sha = 'main'
    with pytest.raises(ValueError):
        pipeline.package_runtime_context([artifact], expected_sha=expected_sha,
                                         captured_ts=T0, dataset={'rows': []})


def test_staging_io_failure_preserves_all_existing_outputs(tmp_path, monkeypatch):
    pipeline = packaging()
    for name in ('archive', 'runtime_context'):
        (tmp_path / (name + '.json')).write_text('existing-' + name)
    calls = 0
    original = pipeline.os.fsync

    def disk_failure(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('simulated disk failure')
        return original(fd)

    monkeypatch.setattr(pipeline.os, 'fsync', disk_failure)
    with pytest.raises(OSError, match='disk failure'):
        pipeline.write_outputs({'archive': {}, 'dataset': {}, 'diagnostics': {},
                                'runtime_context': None}, tmp_path)
    assert (tmp_path / 'archive.json').read_text() == 'existing-archive'
    assert (tmp_path / 'runtime_context.json').read_text() == 'existing-runtime_context'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['archive.json', 'runtime_context.json']


def test_runtime_package_cannot_substitute_a_different_training_dataset():
    with pytest.raises(ValueError, match='UNVALIDATED_MODEL'):
        packaging().package_runtime_context([measured_model()], expected_sha=SHA,
            captured_ts=T0, dataset={'rows': [], 'dataset_sha256': 'f' * 64})


def test_runtime_synthetic_model_cannot_vote_with_real_sources():
    frozen = snapshot('macro', source(features={'macro.expected_rate_change': -.2}),
                      'macro.expected_rate_change')
    frozen['edge_family_models']['macro']['synthetic'] = True
    result = build_edge_family_evidence(frozen)
    assert result['components'] == []
    assert result['families']['macro']['forecast_rejections'][0]['reason'] == 'SYNTHETIC_MODEL_NOT_ADMISSIBLE'


@pytest.mark.parametrize('global_claim', [True, False])
def test_event_global_context_remains_an_explicit_source_declaration(global_claim):
    data, feature = family_fixture('event')
    data['global_context'] = global_claim
    data['consensus']['global_context'] = global_claim
    if global_claim:
        data.pop('instrument')
        data['consensus'].pop('instrument')
    result = build_edge_family_evidence(snapshot('event', data, feature))
    meta = result['families']['event']['feature_provenance'][feature]
    assert meta['global_context'] is global_claim
    assert meta['consensus_provenance']['global_context'] is global_claim
    assert result['components']
