"""Coverage and measurement scope do not grant a flow model authority."""
import json
from copy import deepcopy
import pytest
from test_edge_family_dataset import archive, snapshot, record, T0


def flow_history():
    value = snapshot()
    value['edge_family_sources']['order_flow'] = [{
        'source_verified': True, 'source_id': 'private-book-source', 'instrument': 'NAS100',
        'observed_ts': T0-10, 'received_ts': T0-5, 'venue': 'CME',
        'kind': 'exchange_order_book',
        'previous_top': {'ts': T0-11, 'bid_price': 100., 'ask_price': 101., 'bid_size': 10., 'ask_size': 20.},
        'current_top': {'ts': T0-10, 'bid_price': 100., 'ask_price': 101., 'bid_size': 30., 'ask_size': 10.}}]
    return archive(record(value))


def reports(history, data=None, diagnostics=None):
    import scripts.p3_history_readiness as module
    assert hasattr(module, 'build_historical_readiness'), 'shared P3/P4 readiness missing'
    return module.build_historical_readiness(history, data or {'rows': []}, diagnostics or {})


def nas(result):
    return next(row for row in result['p4_readiness']['cells'] if row['instrument'] == 'NAS100')


def test_flow_history_labels_and_missing_sources_are_separate_without_replay(monkeypatch):
    from seiltanzer.edge_family_dataset import build_family_dataset
    history = flow_history()
    data = build_family_dataset(history)
    import scripts.run_unified_edge_comparison as replay
    monkeypatch.setattr(replay, 'observed_replay', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('duplicate replay')))
    before = deepcopy(history)
    result = reports(history, data)
    row = nas(result)
    assert row['causal_feature_review_count'] == row['net_label_review_count'] == 1
    assert row['status'] == 'NO_TRAINING_COHORT'
    assert row['family'] == 'order_flow'
    assert result['p4_readiness']['production_authority'] is False
    assert history == before
    assert 'private-book-source' not in json.dumps(result)


@pytest.mark.parametrize('change', ['single', 'stale', 'unmapped', 'context'])
def test_inadmissible_sequential_input_has_explicit_p4_gap(change):
    value = snapshot()
    # Rebuild record hashes after changing the frozen measurement.
    from test_edge_family_adapters import family_fixture
    source, _ = family_fixture('order_flow')
    if change == 'single': source.pop('previous_top')
    if change == 'stale': source['observed_ts'] = source['current_top']['ts'] = T0-3600
    if change == 'unmapped': source['instrument'] = 'NQ'
    if change == 'context': source['context_only'] = True
    value['edge_family_sources']['order_flow'] = [source]
    row = nas(reports(archive(record(value))))
    assert row['causal_feature_review_count'] == 0
    assert row['status'] == 'SEQUENTIAL_VENUE_OBSERVATIONS_OR_MAPPING_REQUIRED'


def test_private_summary_contains_both_matrices_with_existing_byte_bound(tmp_path):
    from scripts.run_private_edge_family_pipeline import run_job, PUBLIC_FIELDS
    from test_edge_family_private_archive import MemoryStore
    result = run_job(MemoryStore(), expected_sha='a'*40, generation=1, workspace=tmp_path/'job',
                     exporter=lambda: {'read_only': True, 'exported_ts': 100., 'reviews': []})
    assert 'p4_readiness' in result
    assert set(result) == PUBLIC_FIELDS
    assert len(result['p3_readiness']['cells']) == 26
    assert len(result['p4_readiness']['cells']) == 13
    assert len(json.dumps(result).encode()) < 16000
    assert all(row['status'] == 'HISTORY_MISSING' for row in result['p4_readiness']['cells'])


def test_admitted_flow_measurement_scope_survives_real_dataset_creation():
    from seiltanzer.edge_family_dataset import build_family_dataset
    data = build_family_dataset(flow_history())
    rows = [row for row in data['rows'] if row['family_id'] == 'order_flow']
    assert rows
    for row in rows:
        assert all(meta['venue'] == 'CME' and meta['measurement_scope'] ==
                   'SAMPLED_TWO_TOP_OBSERVATIONS_NOT_FULL_INCREMENTAL_OFI'
                   for meta in row['feature_provenance'].values())


def test_combined_projection_matches_p3_and_stays_bounded_with_maximal_counts():
    from scripts.p3_history_readiness import build_p3_readiness
    result = reports(flow_history())
    assert result['p3_readiness'] == build_p3_readiness(flow_history(), {'rows': []}, {})
    for projection in result.values():
        for row in projection['cells']:
            for key in row:
                if key.endswith('_count'): row[key] = 512
    from scripts.run_edge_family_pipeline import encode_json
    assert len(encode_json(result, max_bytes=15000)) < 15000
