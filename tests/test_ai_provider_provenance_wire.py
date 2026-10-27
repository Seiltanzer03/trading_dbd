from copy import deepcopy
import json


def _restore(wire, family, feature):
    table = wire.get('feature_provenance_table', {})
    def resolve(value):
        if isinstance(value, dict):
            if set(value) == {'provenance_ref'}:
                return resolve(table[value['provenance_ref']])
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value
    defaults = resolve(wire.get('feature_provenance_defaults', {}).get(family, {}))
    return {**defaults, **resolve(wire['edge_family_facts'][family]['feature_provenance'][feature])}


def test_full_provenance_codec_shares_only_identical_provenance():
    from test_edge_family_working import frozen_all
    from seiltanzer.ai_provider_provenance_wire import share_provenance
    from seiltanzer.llm_decision_shadow import _shadow_projection
    snapshot = frozen_all()
    record = snapshot['edge_family_sources']['macro']
    record = record[0] if isinstance(record, list) else record
    record['features'].update({f'macro.observed_{i}': i*.01 for i in range(12)})
    before = deepcopy(snapshot)
    expected = _shadow_projection(snapshot)['edge_family_facts']
    wire = share_provenance(_shadow_projection(snapshot))
    table = wire.get('feature_provenance_table')
    assert table or wire.get('feature_provenance_defaults'), 'same source metadata is repeated for every numeric feature'
    for family, row in expected.items():
        encoded = wire['edge_family_facts'][family]
        assert encoded.get('features') == row.get('features')
        for name, meta in (row.get('feature_provenance') or {}).items():
            value = encoded['feature_provenance'][name]
            restored = _restore(wire, family, name)
            assert restored == meta
    assert snapshot == before
    plain_bytes = len(json.dumps(expected, separators=(',',':')))
    wire_bytes = len(json.dumps({'facts':wire['edge_family_facts'],'table':table,'defaults':wire.get('feature_provenance_defaults',{})}, separators=(',',':')))
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
    assert _restore(wire, 'macro', 'c') == {**meta,'received_ts':cutoff}
    with pytest.raises(ValueError, match='post-capture'):
        validate_no_future_timestamps(wire,cutoff)
    assert data == before
    assert share_provenance(wire) == wire


def test_nested_overlapping_source_proofs_are_lossless_and_small():
    from seiltanzer.ai_provider_provenance_wire import share_provenance
    cutoff = 1793100000.
    peers = [dict(source_id='provider:'+str(i)+':'+str(i)*64,
                  body_sha256=str(i)*64, observed_ts=cutoff-300,
                  received_ts=cutoff-1, source_verified=True,
                  provider='observed-reference', symbol=symbol,
                  quote_currency='USD', start_ts=cutoff-3600, end_ts=cutoff-300)
             for i, symbol in enumerate(('XAUUSD','EURUSD','SPX500','NAS100'))]
    shared = dict(source_id='derived:related-market-observed-window',
                  source_instrument='XAU', dependency_group='shared_related_price_path',
                  source_verified=True, quality=.85, max_age_sec=900.,
                  observed_ts=cutoff-300, received_ts=cutoff-1,
                  history_contract_version='verified-original-source-history-v1',
                  supporting_body_sha256={p['source_id']:p['body_sha256'] for p in peers},
                  supporting_source_ids=[p['source_id'] for p in peers])
    meta = {}
    for i in range(12):
        meta['intermarket.feature_'+str(i)] = {
            **deepcopy(shared), 'constituent_provenance':deepcopy([peers[0],peers[1+i%3]]),
            'measurement_kind':'CORRELATION' if i%2 else 'RETURN',
            'lag_seconds':i*300}
    data = dict(captured_ts=cutoff, edge_family_facts={
        'intermarket':dict(features={key:i*.01 for i,key in enumerate(meta)},
                           feature_provenance=meta, reason='ALIGNED_WINDOW_UNAVAILABLE')})
    before = deepcopy(data)
    wire = share_provenance(data)
    assert data == before
    for name, original in meta.items():
        assert _restore(wire,'intermarket',name) == original
    assert wire['edge_family_facts']['intermarket']['features'] == data['edge_family_facts']['intermarket']['features']
    encode = lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    assert len(encode(wire)) < len(encode(data))*.45
    assert share_provenance(wire) == wire


def test_defaults_preserve_absent_null_and_json_scalar_types():
    from seiltanzer.ai_provider_provenance_wire import share_provenance
    common = dict(source_id='observed-reference-'+('a'*100), received_ts=1793100000.,
                  manual_preliminary=True, measurement_kind='REALIZED_RANGE_VOLATILITY_NOT_IMPLIED')
    rows = {'a':{**common,'quality':None,'value':1},
            'b':{**common,'value':1.0},
            'c':{**common,'quality':False,'value':True}}
    wire = share_provenance({'edge_family_facts':{'option':{'feature_provenance':rows}}})
    for name, original in rows.items():
        assert json.dumps(_restore(wire,'option',name),sort_keys=True) == json.dumps(original,sort_keys=True)
    small = {'edge_family_facts':{'event':{'feature_provenance':{'x':{'quality':None}}}}}
    assert share_provenance(small) == small


def test_nested_shared_future_clock_stays_visible():
    from seiltanzer.ai_provider_provenance_wire import share_provenance
    from seiltanzer.decision_research import validate_no_future_timestamps
    import pytest
    cutoff = 1793100000.
    proof = dict(source_id='x'*150, observed_ts=cutoff-300, received_ts=cutoff+2)
    rows = {str(i):dict(source_id='shared-reference-source',constituent_provenance=[dict(proof)],
                       lag_seconds=i*300) for i in range(6)}
    wire = share_provenance({'captured_ts':cutoff,'edge_family_facts':{'intermarket':{'feature_provenance':rows}}})
    with pytest.raises(ValueError,match='post-capture'):
        validate_no_future_timestamps(wire,cutoff)
