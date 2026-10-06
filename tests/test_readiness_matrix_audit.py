import json
import subprocess
import sys

from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.edge_family_adapters import FAMILIES


def audit(tmp_path, bundle, *, training=None, reviews=None):
    path = tmp_path / 'bundle.json'
    path.write_text(json.dumps(bundle))
    command = [sys.executable, '-m', 'scripts.audit_readiness_matrix', '--input', str(path), '--json']
    if training is not None or reviews is not None:
        command += ['--code-sha', 'a'*40]
    for flag, value in (('--training-report', training), ('--reviews', reviews)):
        if value is not None:
            sidecar = tmp_path / (flag[2:] + '.json')
            sidecar.write_text(json.dumps(value))
            command += [flag, str(sidecar)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_missing_capture_is_not_ready_and_includes_all_configured_cells(tmp_path):
    report = audit(tmp_path, {'instruments': {}, 'captured_ts': 100})
    assert len(report['cells']) == len(ALL_INSTRUMENTS) * len(FAMILIES)
    assert all(c['input_available'] is False and c['forecast_available'] is False
               and c['reason'] == 'INSTRUMENT_CAPTURE_MISSING' for c in report['cells'])


def test_input_features_do_not_imply_model_samples_or_forecast(tmp_path):
    code = next(iter(ALL_INSTRUMENTS))
    report = audit(tmp_path, {'captured_ts': 100, 'instruments': {code: {
        'readiness': {'event': {'available': True, 'readiness': 'DATA_AVAILABLE_MODEL_PENDING',
            'features': {'event.test': 0}, 'observed_ts': 90, 'max_age_sec': 20,
            'feature_provenance': {'event.test': {'body_sha256': 'a' * 64}},
            'reason': 'OBSERVED_FEATURES_REQUIRE_VALIDATED_NET_ACTION_MODEL',
            'forecast_available': False, 'needs_data': ['out-of-sample calibration']}}}}})
    cell = next(c for c in report['cells'] if c['instrument'] == code and c['family'] == 'event')
    assert cell['input_available'] is True
    assert cell['freshness'] == 'FRESH_AT_CAPTURE'
    assert cell['forecast_available'] is False
    assert cell['independent_group_count'] is None
    assert cell['model_status'] == 'UNAVAILABLE'
    assert cell['source_hashes'] == ['a' * 64]


def test_missing_family_refuses_false_all_features_present(tmp_path):
    code = next(iter(ALL_INSTRUMENTS))
    report = audit(tmp_path, {'instruments': {code: {'readiness': {}}}})
    cells = [c for c in report['cells'] if c['instrument'] == code]
    assert all(c['reason'] == 'FAMILY_READINESS_MISSING' and not c['input_available'] for c in cells)


def sidecars(*, applied=True, sha='a'*40):
    import hashlib
    training = {'version': 'edge-family-pipeline-v1', 'deployment_sha': sha, 'captured_ts': 80,
        'model_matrix': {'NAS100': {'event': 1}}, 'training': {'diagnostics': {'cohorts': [
            {'instrument': 'NAS100', 'family_id': 'event', 'horizon_minutes': 60,
             'action': 'CLOSE_25', 'row_count': 55, 'group_count': 41, 'available': True,
             'reason': 'OOS_VALIDATED'}]}}}
    snapshot = {'runtime_code_sha': sha, 'trade_id': 1, 'captured_ts': 90,
        'strategy': {'instrument': 'NAS100'}, 'policy_manager': {'unified_edge_ensemble': {
            'applied': applied, 'selected_candidate_id': 'HOLD',
            'candidates': [{'candidate_id': 'HOLD', 'component_contributions': [
                {'component_id': 'active_edge', 'effective_weight': .15}]}],
            'components': [{'component_id': 'active_edge',
                'available': True, 'effective_weight': .15}],
            'edge_families': [{'family_id': 'event', 'available': True,
                'forecast_available': True, 'readiness': 'VALIDATED_FORECAST_AVAILABLE',
                'weight_pool': 'active_edge'}]}}}
    raw = json.dumps(snapshot)
    reviews = {'read_only': True, 'reviews': [{'review_id': 'r1', 'trade_id': 1,
        'captured_ts': 90, 'snapshot_json': raw,
        'snapshot_sha256': hashlib.sha256(raw.encode()).hexdigest()}]}
    return training, reviews


def test_saved_training_and_runtime_are_distinct_from_current_input_readiness(tmp_path):
    training, reviews = sidecars()
    report = audit(tmp_path, {'captured_ts': 100, 'instruments': {}}, training=training, reviews=reviews)
    cell = next(c for c in report['cells'] if c['instrument'] == 'NAS100' and c['family'] == 'event')
    assert cell['input_available'] is False and cell['forecast_available'] is False
    assert cell['independent_group_count'] == 41
    assert cell['packaged_model_count'] == 1
    assert cell['runtime_forecast_available'] is True
    assert cell['active_vote_status'] == 'APPLIED_SHARED_POOL_OBSERVED'
    assert cell['applied_pool_weight'] == .15 and cell['standalone_family_weight'] is None
    assert cell['runtime_review_id'] == 'r1' and cell['runtime_captured_ts'] == 90


def test_overlapping_training_cohorts_are_not_summed_and_unapplied_ranking_is_not_a_vote(tmp_path):
    training, reviews = sidecars(applied=False)
    training['training']['diagnostics']['cohorts'] *= 2
    report = audit(tmp_path, {'captured_ts': 100}, training=training, reviews=reviews)
    cell = next(c for c in report['cells'] if c['instrument'] == 'NAS100' and c['family'] == 'event')
    assert cell['independent_group_count'] is None and len(cell['training_cohorts']) == 2
    assert cell['active_vote_status'] == 'RANKING_NOT_APPLIED'
    assert cell['applied_pool_weight'] is None


def test_wrong_generation_and_corrupt_frozen_review_do_not_promote_models_or_votes(tmp_path):
    training, reviews = sidecars(sha='b'*40)
    reviews['reviews'][0]['snapshot_json'] += ' '
    report = audit(tmp_path, {'captured_ts': 100}, training=training, reviews=reviews)
    cell = next(c for c in report['cells'] if c['instrument'] == 'NAS100' and c['family'] == 'event')
    assert cell['independent_group_count'] is None
    assert cell['active_vote_status'] == 'NOT_REPORTED_IN_SOURCE_BUNDLE'
    assert report['sidecar_rejections']


def test_candidate_without_preference_does_not_receive_the_global_pool_budget(tmp_path):
    import hashlib
    training, reviews = sidecars()
    record = reviews['reviews'][0]
    snapshot = json.loads(record['snapshot_json'])
    snapshot['policy_manager']['unified_edge_ensemble']['candidates'][0]['component_contributions'][0]['effective_weight'] = 0
    record['snapshot_json'] = json.dumps(snapshot)
    record['snapshot_sha256'] = hashlib.sha256(record['snapshot_json'].encode()).hexdigest()
    report = audit(tmp_path, {'captured_ts': 100}, training=training, reviews=reviews)
    cell = next(c for c in report['cells'] if c['instrument'] == 'NAS100' and c['family'] == 'event')
    assert cell['applied_pool_weight'] == 0
    assert cell['active_vote_status'] == 'ADMITTED_POOL_HAS_ZERO_WEIGHT'
    assert cell['available_pool_budget'] == .15
