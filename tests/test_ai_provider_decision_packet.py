from copy import deepcopy
import hashlib
import json

import pytest


def _rows(value):
    if not isinstance(value, dict) or 'column_groups' not in value:
        return value
    rows = {name: dict(zip(group['columns'], cells, strict=True))
            for group in value['column_groups'] for name, cells in group['rows'].items()}
    return ([rows[str(i)] for i in range(len(rows))]
            if value['row_kind'] == 'sequence' else rows)


def _meta(packet, family):
    table = packet.get('feature_provenance_table', {})
    def resolve(value):
        if isinstance(value, dict):
            if set(value) == {'provenance_ref'}:
                return resolve(table[value['provenance_ref']])
            return {key: resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child) for child in value]
        return value
    defaults = resolve(packet.get('feature_provenance_defaults', {}).get(family, {}))
    return {name: {**defaults, **resolve(value)} for name, value in
            _rows(packet['edge_family_facts'][family]['feature_provenance']).items()}


def _frozen():
    from test_ai_provider_explanation import _snapshot
    from test_edge_family_working import frozen_all
    value = _snapshot()
    facts = frozen_all()
    value.update({key: item for key, item in facts.items() if key != 'policy_manager'})
    for index, row in enumerate(value['edge_family_sources'].values()):
        row['source_id'] = 'observed:provider:' + format(index + 1, '064x')
    value['edge_family_source_bundle_audit'] = {
        'available': True, 'delivery_note': 'LOCAL_ONLY_DELIVERY_AUDIT',
        'received_ts': value['captured_ts'] - 1}
    value['policy_manager']['input_audit']['delivery_note'] = 'LOCAL_INPUT_AUDIT'
    value['policy_manager']['llm_edge_exploratory_weight'] = {'effective_weight': .15}
    return value


def test_actual_independent_input_uses_labels_and_keeps_decision_facts():
    from seiltanzer.ai_runtime_report_v20 import _independent_provider_input
    from seiltanzer.llm_decision_shadow import _shadow_projection
    snapshot = _frozen()
    before = deepcopy(snapshot)
    full = _shadow_projection(snapshot)
    packet = _independent_provider_input(snapshot, snapshot)
    wire = json.dumps(packet, sort_keys=True)
    assert format(1, '064x') not in wire
    assert 'LOCAL_ONLY_DELIVERY_AUDIT' not in wire
    assert 'input_audit' not in packet['policy_manager']
    assert packet['policy_manager']['instrument_quote_audit'] == snapshot[
        'policy_manager']['input_audit']['rows']['instrument_price']
    assert 'llm_edge_exploratory_weight' not in packet['policy_manager']
    for family, row in full['edge_family_facts'].items():
        assert packet['edge_family_facts'][family]['features'] == row['features']
        for key in ('available', 'reason', 'forecast_kind', 'forecast_origin_ts', 'market_predictions'):
            if key in row:
                assert packet['edge_family_facts'][family][key] == row[key]
    for key in ('policies', 'selection_rule'):
        assert (key in packet['policy_manager']) == (key in full['policy_manager'])
        assert _rows(packet['policy_manager'].get(key)) == full['policy_manager'].get(key)
    assert snapshot == before


def test_actual_transport_archives_full_audit_and_exact_label_mapping(monkeypatch):
    from test_ai_provider_telemetry import transport
    from seiltanzer import ai_runtime_report_v20 as report
    calls, _, _ = transport(monkeypatch)
    snapshot = _frozen()
    before = deepcopy(snapshot)
    result = report.request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    contract = result['llm_shadow_decision']['input_contract']
    sent = calls[0]['messages'][1]['content'].split('\n', 1)[1]
    assert sent == contract['frozen_input_json']
    assert contract['frozen_input_sha256'] == hashlib.sha256(sent.encode()).hexdigest()
    full = contract['full_input_json']
    assert 'LOCAL_ONLY_DELIVERY_AUDIT' in full
    assert format(1, '064x') in full
    assert contract['full_input_sha256'] == hashlib.sha256(full.encode()).hexdigest()
    aliases = contract['evidence_identity_aliases']
    assert any(format(1, '064x') in original for original in aliases.values())
    assert len(aliases) == len(set(aliases.values()))
    assert all(label in sent for label in aliases)
    assert 'LOCAL_ONLY_DELIVERY_AUDIT' not in sent
    assert snapshot == before


def test_omitted_delivery_clock_cannot_hide_future_input(monkeypatch):
    from test_ai_provider_telemetry import transport
    from seiltanzer import ai_runtime_report_v20 as report
    calls, _, _ = transport(monkeypatch)
    snapshot = _frozen()
    snapshot['edge_family_source_bundle_audit']['received_ts'] = snapshot['captured_ts'] + 2
    with pytest.raises(ValueError, match='post-capture'):
        report.request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    assert calls == []


def test_model_evidence_labels_restore_original_source_identity(monkeypatch):
    from test_ai_provider_telemetry import transport
    from seiltanzer import ai_runtime_report_v20 as report
    transport(monkeypatch)
    base = report.httpx.Client
    class EchoEvidenceClient(base):
        def post(self, url, **kwargs):
            response = super().post(url, **kwargs)
            packet = json.loads(kwargs['json']['messages'][1]['content'].split('\n', 1)[1])
            evidence = next(name for name in packet['shadow_contract']['available_evidence_family_ids']
                            if name.startswith('macro:'))
            payload = response.json()
            opinion = json.loads(payload['choices'][0]['message']['content'])
            opinion['shadow_decision']['evidence_families'] = [evidence]
            payload['choices'][0]['message']['content'] = json.dumps(opinion)
            response.json = lambda: payload
            return response
    monkeypatch.setattr(report.httpx, 'Client', EchoEvidenceClient)
    snapshot = _frozen()
    result = report.request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    assert result['llm_shadow_decision']['evidence_families'] == [
        'macro:observed:provider:' + format(1, '064x')]
    assert 'macro:observed:provider:' + format(1, '064x') in result[
        'llm_shadow_decision']['input_contract']['available_evidence_family_ids']


def test_packet_preserves_preliminary_roles_clocks_and_distinct_hash_identities():
    from seiltanzer.ai_provider_decision_packet import build_decision_packet
    digest_a, digest_b = 'a' * 64, 'b' * 64
    raw = {
        'edge_family_facts': {'option': {'features': {'option.not_iv': .001},
            'feature_provenance': {'option.not_iv': {
                'source_id': 'source:' + digest_a, 'manual_preliminary': True,
                'measurement_kind': 'REALIZED_RANGE_VOLATILITY_NOT_IMPLIED',
                'context_only': True, 'implied_volatility_observed': False,
                'source_instrument': 'XAU', 'symbol': 'OANDA:XAUUSD',
                'observed_ts': 1793100000., 'received_ts': 1793100001.,
                'quality': None, 'dependency_group': 'price_path:' + digest_a,
                'constituent_provenance': [{'source_id': 'source:' + digest_b,
                    'received_ts': 1793099999., 'broker_order_book_observed': False}]}}}},
        'position_state': {'literal_label': '@sha:0;'},
        'trade_geometry': {'entry': 4006., 'current': 4015.5}}
    before = deepcopy(raw)
    packet, aliases = build_decision_packet(raw)
    # Labels never collide with literal input text and shared digests stay shared.
    assert packet['position_state']['literal_label'] == '@sha:0;'
    assert len(aliases) == len(set(aliases.values()))
    assert any(digest_a in value for value in aliases.values())
    assert all(digest_b not in value for value in aliases.values())
    encoded = json.dumps(packet)
    for label, original in sorted(aliases.items(), key=lambda item: -len(item[0])):
        encoded = encoded.replace(label, original)
    decoded = json.loads(encoded)
    decoded.pop('decision_packet_version')
    for row in decoded['edge_family_facts'].values():
        row['feature_provenance'] = _meta(decoded, 'option')
    decoded.pop('feature_provenance_table', None)
    decoded.pop('feature_provenance_defaults', None)
    expected = deepcopy(raw)
    expected['edge_family_facts']['option']['feature_provenance']['option.not_iv'].pop('constituent_provenance')
    assert decoded == expected
    assert raw == before


def test_large_technical_proofs_stay_local_without_losing_numeric_features():
    from seiltanzer.ai_provider_decision_packet import build_decision_packet
    rows = {}
    for i in range(12):
        digest = format(i + 1, '064x')
        rows[str(i)] = dict(source_id='observed:reference:' + digest,
            observed_ts=1793100000., received_ts=1793100001., quality=.8,
            measurement_kind='OBSERVED_COMPLETED_BAR', body_sha256=digest,
            supporting_body_sha256={'source:' + digest: digest})
    raw = {'edge_family_facts': {'intermarket': {
        'features': {str(i): i*.01 for i in range(12)}, 'feature_provenance': rows}},
        'research_working_context': {'source_provenance': {
            'provider': 'TradingView chart', 'source_symbol': 'OANDA:XAUUSD',
            'observed_ts': 1793100000., 'reference_proof': {'body_sha256': 'c'*64,
                'transport_receipts': [{'body_sha256': format(i+1, '064x')} for i in range(30)]}}}}
    before = deepcopy(raw)
    packet, aliases = build_decision_packet(raw)
    assert packet['edge_family_facts']['intermarket']['features'] == raw['edge_family_facts']['intermarket']['features']
    meta = _meta(packet, 'intermarket')
    for key, row in meta.items():
        assert row['observed_ts'] == 1793100000.
        assert row['received_ts'] == 1793100001.
        assert row['quality'] == .8
        assert row['measurement_kind'] == 'OBSERVED_COMPLETED_BAR'
    assert len(json.dumps(packet)) < len(json.dumps(raw))*.6
    assert all(any(format(i+1, '064x') in original for original in aliases.values()) for i in range(12))
    assert raw == before


def test_tables_preserve_every_policy_parameter_missing_null_and_original_order():
    from seiltanzer.ai_provider_decision_packet import build_decision_packet
    candidates = [dict(policy='HOLD', parameters={'stop': 3976.},
                       expected_variant_net_r=.398+i*.01, hard_net_floor_r=-1.,
                       execution_cost_r=.01, risk_quality='OBSERVED_COSTS_ONLY',
                       production_authority=False, automatic_execution_allowed=False)
                  for i in range(7)]
    candidates[2]['paired_delta_ci95_lower_r'] = None
    candidates[3]['paired_delta_ci95_lower_r'] = -.03
    raw = {'active_management_candidates': candidates,
           'policy_manager': {'policies': {str(i): deepcopy(row) for i, row in enumerate(candidates)}}}
    packet, aliases = build_decision_packet(raw)
    assert 'column_groups' in packet['active_management_candidates']
    assert _rows(packet['active_management_candidates']) == candidates
    assert _rows(packet['policy_manager']['policies']) == raw['policy_manager']['policies']
    assert aliases == {}


def test_digest_named_numeric_feature_and_its_provenance_key_are_never_renamed():
    from seiltanzer.ai_provider_decision_packet import build_decision_packet
    name = 'f'*64
    raw = {'edge_family_facts': {'macro': {'features': {name: .02},
           'feature_provenance': {name: {'source_id': 'provider:' + 'e'*64}}}}}
    packet, aliases = build_decision_packet(raw)
    assert packet['edge_family_facts']['macro']['features'] == {name: .02}
    assert name in _rows(packet['edge_family_facts']['macro']['feature_provenance'])


def test_valid_future_time_stop_deadline_survives_actual_tabular_transport(monkeypatch):
    from test_ai_provider_telemetry import transport
    from seiltanzer import ai_runtime_report_v20 as report
    calls, _, _ = transport(monkeypatch)
    snapshot = _frozen()
    snapshot['active_management_candidates'] = [
        {'policy': 'TIME_STOP', 'status': 'eligible',
         'parameters': {'deadline_ts': snapshot['captured_ts'] + 900 + i*60},
         'expected_variant_net_r': .1, 'hard_net_floor_r': -1.,
         'production_authority': False, 'automatic_execution_allowed': False}
        for i in range(7)]
    result = report.request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    wire = json.loads(calls[0]['messages'][1]['content'].split('\n', 1)[1])
    assert _rows(wire['active_management_candidates']) == snapshot['active_management_candidates']
    assert result['llm_shadow_decision']['input_contract']['full_input_json']


@pytest.mark.parametrize('tamper', ['omitted_future_clock', 'omitted_constituent_clock', 'changed_policy_number'])
def test_replay_validates_full_audit_and_its_exact_packet_binding(monkeypatch, tamper):
    from test_ai_provider_telemetry import transport
    from seiltanzer import ai_runtime_report_v20 as report
    from seiltanzer.decision_research import validate_no_future_timestamps
    transport(monkeypatch)
    snapshot = _frozen()
    result = report.request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    contract = result['llm_shadow_decision']['input_contract']
    replay = json.loads(contract['full_input_json'])
    replay['llm_shadow_decision'] = result['llm_shadow_decision']
    if tamper in ('omitted_future_clock', 'omitted_constituent_clock'):
        full = json.loads(contract['full_input_json'])
        if tamper == 'omitted_future_clock':
            full['edge_family_source_bundle_audit']['received_ts'] = snapshot['captured_ts'] + 2
        else:
            full['edge_family_facts']['macro']['feature_provenance']['macro.expected_rate_change'][
                'constituent_provenance'] = [{'source_id': 'original-window', 'received_ts': snapshot['captured_ts'] + 2}]
        contract['full_input_json'] = json.dumps(full, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        contract['full_input_sha256'] = hashlib.sha256(contract['full_input_json'].encode()).hexdigest()
        reason = 'post-capture'
    else:
        packet = json.loads(contract['frozen_input_json'])
        table = packet['policy_manager']['policies']['column_groups'][0]
        index = table['columns'].index('expected_final_r')
        next(iter(table['rows'].values()))[index] = 999.
        contract['frozen_input_json'] = json.dumps(packet, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        contract['frozen_input_sha256'] = hashlib.sha256(contract['frozen_input_json'].encode()).hexdigest()
        reason = 'audit binding'
    with pytest.raises(ValueError, match=reason):
        validate_no_future_timestamps(replay, snapshot['captured_ts'])


def test_malformed_row_table_is_rejected_by_existing_clock_entrypoint():
    from seiltanzer.decision_research import validate_no_future_timestamps
    raw = {'decision_packet_version': 'independent-decision-packet-v1',
           'captured_ts': 1793100000., 'active_management_candidates': {
               'row_kind': 'sequence', 'column_groups': [
                   {'columns': ['policy', 'policy'], 'rows': {'0': ['HOLD', 'EXIT']}}]}}
    with pytest.raises(ValueError, match='row columns'):
        validate_no_future_timestamps(raw, raw['captured_ts'])


def test_packet_rebuild_is_independent_of_original_dictionary_order():
    from seiltanzer.ai_provider_decision_packet import build_decision_packet
    from seiltanzer.ai_provider_provenance_wire import share_provenance
    raw = {'edge_family_facts': {'option': {'feature_provenance': {
        'option.z': {'source_id': 'x'*80, 'received_ts': 1793100000., 'observed_ts': 1793099990.},
        'option.a': {'source_id': 'y'*80, 'received_ts': 1793100000., 'observed_ts': 1793099990.},
        'option.c': {'source_id': 'x'*80, 'received_ts': 1793100000., 'observed_ts': 1793099990.}}},
        'macro': {'feature_provenance': {
            'macro.z': {'source_id': 'x'*80, 'received_ts': 1793100000., 'observed_ts': 1793099990.},
            'macro.a': {'source_id': 'y'*80, 'received_ts': 1793100000., 'observed_ts': 1793099990.}}}}}
    for family in raw['edge_family_facts'].values():
        for row in family['feature_provenance'].values():
            row['applicability_provenance'] = {'source_id': row['source_id'],
                'provider': 'verified-related-window-' + 'p'*50, 'observed_ts': 1793099990.}
    # The real full audit is saved with sorted keys; rebuilding must yield the
    # identical wire and alias map, including shared provenance identities.
    other = json.loads(json.dumps(raw, sort_keys=True))
    encode = lambda x: json.dumps(share_provenance(x), sort_keys=True, separators=(',', ':'))
    assert encode(raw) == encode(other)
    packet, aliases = build_decision_packet(raw)
    rebuilt, rebuilt_aliases = build_decision_packet(other)
    assert json.dumps(packet, sort_keys=True) == json.dumps(rebuilt, sort_keys=True)
    assert aliases == rebuilt_aliases
