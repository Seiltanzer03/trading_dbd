from copy import deepcopy
import json


def test_actual_independent_projection_shares_only_identical_provenance():
    from test_edge_family_working import frozen_all
    from seiltanzer.ai_runtime_report_v20 import _independent_provider_input
    from seiltanzer.llm_decision_shadow import _shadow_projection
    snapshot = frozen_all()
    record = snapshot['edge_family_sources']['macro']
    record = record[0] if isinstance(record, list) else record
    record['features'].update({f'macro.observed_{i}': i*.01 for i in range(12)})
    before = deepcopy(snapshot)
    expected = _shadow_projection(snapshot)['edge_family_facts']
    wire = _independent_provider_input(snapshot, snapshot)
    table = wire.get('feature_provenance_table')
    assert table, 'same source metadata is repeated for every numeric feature'
    for family, row in expected.items():
        encoded = wire['edge_family_facts'][family]
        assert encoded.get('features') == row.get('features')
        for name, meta in (row.get('feature_provenance') or {}).items():
            value = encoded['feature_provenance'][name]
            restored = table[value['provenance_ref']] if set(value) == {'provenance_ref'} else value
            assert restored == meta
    assert snapshot == before
    plain_bytes = len(json.dumps(expected, separators=(',',':')))
    wire_bytes = len(json.dumps({'facts':wire['edge_family_facts'],'table':table}, separators=(',',':')))
    assert wire_bytes < plain_bytes*.8


def test_shared_metadata_keeps_future_clock_validation_and_different_receipts():
    from seiltanzer.ai_provider_provenance_wire import share_provenance
    from seiltanzer.decision_research import validate_no_future_timestamps
    import pytest
    cutoff = 1793100000.
    meta = dict(source_id='observed-provider-source', received_ts=cutoff+2, observed_ts=cutoff-1,
                source_verified=True, source_instrument='XAU', dependency_group='price_path',
                source_symbol='OANDA:XAUUSD', max_age_sec=900.)
    data = {'captured_ts':cutoff,'edge_family_facts':{'macro':{
        'feature_provenance':{'a':meta,'b':dict(meta),'c':{**meta,'received_ts':cutoff}}}}}
    before = deepcopy(data)
    wire = share_provenance(data)
    assert wire['edge_family_facts']['macro']['feature_provenance']['c'] == {**meta,'received_ts':cutoff}
    with pytest.raises(ValueError, match='post-capture'):
        validate_no_future_timestamps(wire,cutoff)
    assert data == before
    assert share_provenance(wire) == wire
