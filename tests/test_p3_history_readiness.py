"""P3 readiness is evidence coverage, never model activation or profit."""
from copy import deepcopy
import importlib
import importlib.util
import json

from test_edge_family_dataset import archive, snapshot, record


def report(history, data=None, diagnostics=None):
    assert importlib.util.find_spec('scripts.p3_history_readiness'), 'P3 historical readiness missing'
    return importlib.import_module('scripts.p3_history_readiness').build_p3_readiness(
        history, data or {'rows': []}, diagnostics or {})


def cell(result, family='intermarket', code='NAS100'):
    return next(row for row in result['cells'] if row['instrument'] == code and row['family'] == family)


def test_existing_received_features_without_costs_are_distinct_from_missing_history():
    value = snapshot()
    value.pop('execution_cost_context_audit')
    value['policy_manager'].pop('execution_cost_model')
    result = report(archive(record(value)))
    row = cell(result)
    assert row['retained_review_count'] == row['causal_feature_review_count'] == 1
    assert row['complete_cost_review_count'] == row['net_label_review_count'] == 0
    assert row['status'] == 'COST_OR_POSITION_EVIDENCE_REQUIRED'
    assert cell(result, code='SP500')['status'] == 'HISTORY_MISSING'
    assert cell(result, family='session')['status'] == 'CALENDAR_OR_MAPPING_REQUIRED'


def test_real_dataset_rows_count_reviews_not_actions_and_do_not_repeat_replay(monkeypatch):
    from seiltanzer.edge_family_dataset import build_family_dataset
    history = archive()
    data = build_family_dataset(history)
    assert len(data['rows']) > 1
    import scripts.run_unified_edge_comparison as replay
    monkeypatch.setattr(replay, 'observed_replay', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('repeat replay')))
    result = report(history, data)
    row = cell(result)
    assert row['complete_cost_review_count'] == row['net_label_review_count'] == 1
    assert row['status'] == 'NO_TRAINING_COHORT'
    assert result['production_authority'] is False


def test_per_cohort_counts_are_not_added_and_private_fields_never_projected():
    history = archive()
    data = {'rows': [{'instrument': 'NAS100', 'family_id': 'intermarket', 'review_id': 'ci-review'}] * 12}
    diagnostics = {'model_matrix': {}, 'training': {'diagnostics': {'cohorts': [
        {'instrument': 'NAS100', 'family_id': 'intermarket', 'group_count': n,
         'reason': 'INSUFFICIENT_INDEPENDENT_GROUPS', 'trade_id': 'secret-account'} for n in (12, 18)]}}}
    original = deepcopy((history, data, diagnostics))
    result = report(history, data, diagnostics)
    row = cell(result)
    assert row['training_cohort_count'] == 2 and row['max_cohort_independent_group_count'] == 18
    assert row['status'] == 'INSUFFICIENT_INDEPENDENT_GROUPS'
    assert 'secret-account' not in json.dumps(result) and 'ci-review' not in json.dumps(result)
    assert (history, data, diagnostics) == original


def test_unknown_training_reason_is_not_published_and_packaged_is_not_active():
    data = {'rows': [{'instrument': 'NAS100', 'family_id': 'intermarket', 'review_id': 'ci-review'}]}
    diagnostics = {'training': {'diagnostics': {'cohorts': [
        {'instrument': 'NAS100', 'family_id': 'intermarket', 'group_count': 40,
         'reason': 'private-provider-response'}]}}}
    result = report(archive(), data, diagnostics)
    assert cell(result)['status'] == 'OOS_NOT_VALIDATED'
    assert 'private-provider-response' not in json.dumps(result)
    diagnostics['model_matrix'] = {'NAS100': {'intermarket': 1}}
    assert cell(report(archive(), data, diagnostics))['status'] == 'MODEL_PACKAGED_NOT_ACTIVE'


def test_private_pipeline_exposes_only_safe_p3_matrix(tmp_path):
    from scripts.run_private_edge_family_pipeline import run_job, PUBLIC_FIELDS
    from test_edge_family_private_archive import MemoryStore
    result = run_job(MemoryStore(), expected_sha='a' * 40, generation=1,
        workspace=tmp_path/'job', exporter=lambda: {'read_only': True, 'exported_ts': 100., 'reviews': []})
    assert 'p3_readiness' in result
    assert set(result) == PUBLIC_FIELDS
    assert result['p3_readiness']['production_authority'] is False
    assert all(row['status'] == 'HISTORY_MISSING' for row in result['p3_readiness']['cells'])


def test_unusable_frozen_geometry_is_not_reported_as_missing_market_source():
    value = snapshot()
    value['policy_manager']['inputs']['horizon_minutes'] = None
    row = cell(report(archive(record(value))))
    assert row['retained_review_count'] == 1
    assert row['usable_frozen_review_count'] == 0
    assert row['status'] == 'FROZEN_HISTORY_UNUSABLE'


def test_future_or_context_only_sources_do_not_become_historical_features():
    for change in ({'context_only': True}, {'received_ts': 1e12}):
        value = snapshot()
        value['edge_family_sources']['intermarket'][0].update(change)
        result = report(archive(record(value)))
        assert cell(result)['causal_feature_review_count'] == 0
        assert result['production_authority'] is False


def test_matrix_is_bounded_by_configured_instruments_and_public_byte_limit():
    from seiltanzer.config import ALL_INSTRUMENTS
    result = report(archive())
    assert len(result['cells']) == len(ALL_INSTRUMENTS) * 2
    assert len(json.dumps({'p3_readiness': result}).encode()) < 14000


def test_missing_execution_geometry_is_unusable_even_with_complete_costs():
    from seiltanzer.edge_family_dataset import build_family_dataset
    value = snapshot()
    value['policy_manager']['inputs'].pop('r0')
    history = archive(record(value))
    data = build_family_dataset(history)
    assert any(row['reason'] == 'EXECUTION_GEOMETRY_INCOMPLETE' for row in data['exclusions'])
    row = cell(report(history, data))
    assert row['usable_frozen_review_count'] == 0
    assert row['status'] == 'FROZEN_HISTORY_UNUSABLE'
