"""Synthetic independent broker-position fixtures; no executing account configured."""
from copy import deepcopy
import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest

from seiltanzer.unified_edge_runtime_context import attach_unified_edge_context
from test_execution_cost_context import document as cost_document

T0 = 1790910000.
SHA = 'a' * 40


def snapshot():
    return {'captured_ts': T0, 'trade_id': 1,
        'strategy': {'instrument': 'NAS100', 'direction': 'long'},
        'trade_geometry': {'entry': 100., 'original_stop': 90., 'remaining_position_fraction': .5},
        'position_state': {'remaining_position_fraction': .5},
        'policy_manager': {'inputs': {'horizon_minutes': 240.}}}


def document():
    return {'version': 'broker-position-context-v1', 'deployment_sha': SHA,
        'source_verified': True, 'measurement_kind': 'executing_broker_position',
        'source_id': 'independent-position-export', 'evidence_sha256': 'c' * 64,
        'broker_id': 'executing-broker', 'account_id': 'account-1', 'broker_position_id': 'position-1',
        'trade_id': 1, 'instrument': 'NAS100', 'direction': 'long',
        'observed_ts': T0 - 10., 'received_ts': T0 - 5., 'max_age_sec': 60.,
        'entry': 100., 'original_stop': 90., 'remaining_position_fraction': .5,
        'currency': 'USD', 'quantity_units': 2., 'risk_currency_per_unit': 100.,
        'quantity_basis': 'current_remaining_position'}


def write_context(tmp_path, value, name='position.json'):
    path = tmp_path / name
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def load(path, digest, frozen=None):
    module = importlib.import_module('seiltanzer.position_execution_context')
    return module.load_position_execution_context(path, snapshot=frozen if frozen is not None else snapshot(),
        expected_deployment_sha=SHA, expected_document_sha256=digest)


def engine(tmp_path, position=None, costs=None):
    kwargs = {}
    if position is not None:
        path, digest = write_context(tmp_path, position)
        kwargs.update(position_execution_context_path=str(path), position_execution_context_sha256=digest)
    if costs is not None:
        path, digest = write_context(tmp_path, costs, 'costs.json')
        kwargs.update(execution_cost_context_path=str(path), execution_cost_context_sha256=digest)
    return SimpleNamespace(settings=SimpleNamespace(**kwargs))


def test_independent_position_attachment_enables_complete_cost_repricing(tmp_path):
    frozen = snapshot()
    attach_unified_edge_context(engine(tmp_path, document(), cost_document()), frozen, expected_sha=SHA)
    assert frozen['execution_cost_context_audit']['complete_costs_available'] is True
    assert frozen['trade_identity']['broker_position_id'] == 'position-1'
    assert frozen['position_execution_units']['quantity_units'] == 2.
    assert frozen['policy_manager']['execution_cost_repricing_required'] is True
    assert frozen['policy_manager']['execution_cost_model']['immediate_full_close_r'] == pytest.approx(.06)
    audit = frozen['position_execution_context_audit']
    assert audit['available'] is True
    assert audit['broker_fill_verified'] is False
    assert audit['effectiveness_verified'] is False
    assert audit['automatic_execution_allowed'] is False
    assert 'account-1' not in json.dumps(audit)


def test_cost_file_cannot_supply_its_own_identity(tmp_path):
    frozen = snapshot()
    attach_unified_edge_context(engine(tmp_path, costs=cost_document()), frozen, expected_sha=SHA)
    assert frozen['position_execution_context_audit']['reason'] == 'BROKER_POSITION_CONTEXT_UNCONFIGURED'
    assert frozen['execution_cost_context_audit']['complete_costs_available'] is False
    assert 'trade_identity' not in frozen
    assert 'execution_cost_repricing_required' not in frozen['policy_manager']


def test_independent_attachment_prices_same_current_remainder_without_quantity_scaling(tmp_path):
    from seiltanzer.unified_candidate_economics import price_unified_candidates
    from test_unified_candidate_economics import snapshot as economic_snapshot, candidate
    frozen = economic_snapshot([[1., -1., -1.]], [1.])
    frozen.update(captured_ts=T0, trade_id=1,
        strategy={'instrument': 'NAS100', 'direction': 'long'},
        position_state={'remaining_position_fraction': .5})
    frozen['trade_geometry']['remaining_position_fraction'] = .5
    frozen['policy_manager']['unified_scenario_bank']['captured_ts'] = T0
    attach_unified_edge_context(engine(tmp_path, document(), cost_document()), frozen, expected_sha=SHA)
    priced = price_unified_candidates(frozen, [candidate('HOLD'), candidate('EXIT')])
    assert priced['available'] is True
    assert priced['candidates']['HOLD']['expected_net_r'] == pytest.approx(-1.06)
    assert priced['candidates']['EXIT']['expected_net_r'] == pytest.approx(.94)
    assert priced['candidates']['EXIT']['execution_cost_r'] == pytest.approx(.06)


@pytest.mark.parametrize('key', list(document()))
def test_missing_required_position_field_cannot_activate_costs(tmp_path, key):
    doc = document()
    del doc[key]
    frozen = snapshot()
    attach_unified_edge_context(engine(tmp_path, doc, cost_document()), frozen, expected_sha=SHA)
    assert frozen['position_execution_context_audit']['available'] is False
    assert frozen['execution_cost_context_audit']['complete_costs_available'] is False
    assert 'trade_identity' not in frozen


@pytest.mark.parametrize('key,value', [
    ('version', 'other'), ('deployment_sha', 'd' * 40), ('source_verified', False),
    ('measurement_kind', 'public_indicative_quote'), ('source_id', ''), ('evidence_sha256', 'bad'),
    ('broker_id', ''), ('account_id', []), ('broker_position_id', ''), ('trade_id', 2),
    ('trade_id', True), ('instrument', 'US100'), ('direction', 'short'),
    ('observed_ts', T0 + 1), ('received_ts', T0 + 1), ('observed_ts', T0 - 61),
    ('received_ts', T0 - 20), ('max_age_sec', 61), ('max_age_sec', 0),
    ('entry', 101), ('original_stop', 91), ('remaining_position_fraction', .4),
    ('quantity_basis', 'initial_position'), ('currency', ''), ('quantity_units', 0),
    ('quantity_units', True), ('risk_currency_per_unit', -1), ('entry', '100'),
])
def test_invalid_identity_geometry_units_and_clocks_never_attach(tmp_path, key, value):
    doc = document()
    doc[key] = value
    frozen = snapshot()
    original = deepcopy(frozen)
    attach_unified_edge_context(engine(tmp_path, doc, cost_document()), frozen, expected_sha=SHA)
    assert frozen['position_execution_context_audit']['available'] is False
    assert frozen['execution_cost_context_audit']['complete_costs_available'] is False
    assert 'trade_identity' not in frozen
    assert frozen['trade_geometry'] == original['trade_geometry']


@pytest.mark.parametrize('root,key,value', [
    ('trade_identity', 'broker_id', 'other'), ('trade_identity', 'account_id', 'other'),
    ('trade_identity', 'broker_position_id', 'other'),
    ('position_execution_units', 'currency', 'EUR'),
    ('position_execution_units', 'quantity_units', 3.),
    ('position_execution_units', 'risk_currency_per_unit', 50.),
    ('position_execution_units', 'quantity_basis', 'initial_position'),
])
def test_conflicting_existing_identity_or_units_not_overwritten(tmp_path, root, key, value):
    frozen = snapshot()
    frozen[root] = {key: value}
    original = deepcopy(frozen[root])
    path, digest = write_context(tmp_path, document())
    result = load(path, digest, frozen)
    assert result['available'] is False
    assert frozen[root] == original
    assert 'trade_identity' not in result


@pytest.mark.parametrize('change', ['partial_close', 'geometry_edit', 'inconsistent_fraction'])
def test_old_position_evidence_cannot_be_scaled_to_changed_position(tmp_path, change):
    frozen = snapshot()
    if change == 'partial_close':
        frozen['position_state']['remaining_position_fraction'] = .25
        frozen['trade_geometry']['remaining_position_fraction'] = .25
    elif change == 'inconsistent_fraction':
        frozen['trade_geometry']['remaining_position_fraction'] = .25
    else:
        frozen['trade_geometry']['original_stop'] = 95.
    path, digest = write_context(tmp_path, document())
    assert load(path, digest, frozen)['available'] is False


def test_pinned_loader_hash_size_and_missing_file_fail_closed(tmp_path):
    path, digest = write_context(tmp_path, document())
    assert load(path, digest)['available'] is True
    for pin in (None, '', '0' * 64):
        assert load(path, pin)['available'] is False
    path.write_bytes(b'x' * 48001)
    assert load(path, digest)['reason'] == 'POSITION_CONTEXT_EXCEEDS_BYTE_BOUND'
    assert load(tmp_path / 'absent', digest)['available'] is False


@pytest.mark.parametrize('raw', [b'{', b'\xff', b'[' * 2000 + b'0' + b']' * 2000,
    b'{"version":NaN}', b'{"padding":1e999}', b'{"version":1,"version":2}'],
    ids=['truncated', 'invalid_utf8', 'deeply_nested', 'nan', 'overflow', 'duplicate_key'])
def test_malformed_nested_duplicate_and_nonfinite_json_fail_closed(tmp_path, raw):
    path = tmp_path / 'bad.json'
    path.write_bytes(raw)
    result = load(path, hashlib.sha256(raw).hexdigest())
    assert result['available'] is False
    assert result['reason'] == 'POSITION_CONTEXT_JSON_INVALID'


def test_private_source_provenance_is_retained_for_replay_not_generic_audit(tmp_path):
    frozen = snapshot()
    doc = document()
    doc['source_id'] = 'private-account-1-readonly-export'
    path, digest = write_context(tmp_path, doc)
    attach_unified_edge_context(engine(tmp_path, doc), frozen, expected_sha=SHA)
    evidence = frozen['trade_identity']['position_evidence']
    assert evidence == {
        'version': 'broker-position-context-v1', 'source_verified': True,
        'measurement_kind': 'executing_broker_position',
        'source_id': 'private-account-1-readonly-export', 'evidence_sha256': 'c' * 64,
        'document_sha256': digest, 'deployment_sha': SHA,
        'observed_ts': T0 - 10., 'received_ts': T0 - 5., 'max_age_sec': 60.,
        'entry': 100., 'original_stop': 90., 'remaining_position_fraction': .5}
    audit = json.dumps(frozen['position_execution_context_audit'])
    assert 'account-1' not in audit
    assert 'position-1' not in audit
    assert doc['source_id'] not in audit


def test_existing_float_trade_id_cannot_alias_integer_trade_identity(tmp_path):
    frozen = snapshot()
    frozen['trade_identity'] = {'trade_id': 1.0}
    path, digest = write_context(tmp_path, document())
    result = load(path, digest, frozen)
    assert result['available'] is False
    assert result['reason'] == 'POSITION_CONTEXT_EXISTING_IDENTITY_OR_UNITS_MISMATCH'


@pytest.mark.parametrize('defect', ['stale', 'geometry', 'position_id', 'malformed_identity'])
def test_rejected_configured_position_does_not_reuse_unverified_snapshot_claims_for_costs(tmp_path, defect):
    frozen = snapshot()
    frozen['trade_identity'] = {key: document()[key] for key in (
        'broker_id', 'account_id', 'broker_position_id', 'trade_id', 'instrument', 'direction')}
    frozen['position_execution_units'] = {key: document()[key] for key in (
        'currency', 'quantity_units', 'risk_currency_per_unit', 'quantity_basis')}
    doc = document()
    if defect == 'stale':
        doc['observed_ts'] = T0 - 61.
    elif defect == 'geometry':
        doc['entry'] = 101.
    elif defect == 'position_id':
        frozen['trade_identity']['broker_position_id'] = 'other-position'
    else:
        frozen['trade_identity'] = ['private-account-1']
    original = deepcopy((frozen['trade_identity'], frozen['position_execution_units']))
    attach_unified_edge_context(engine(tmp_path, doc, cost_document()), frozen, expected_sha=SHA)
    assert frozen['position_execution_context_audit']['available'] is False
    assert frozen['execution_cost_context_audit']['complete_costs_available'] is False
    assert frozen['execution_cost_context_audit']['reason'] == 'EXECUTING_BROKER_POSITION_CONTEXT_UNAVAILABLE_OR_MISMATCH'
    assert 'execution_cost_repricing_required' not in frozen['policy_manager']
    assert (frozen['trade_identity'], frozen['position_execution_units']) == original


@pytest.mark.parametrize('root', ['trade_identity', 'position_execution_units'])
@pytest.mark.parametrize('value', [None, [], '', False])
def test_malformed_existing_operational_roots_fail_closed(tmp_path, root, value):
    frozen = snapshot()
    frozen[root] = value
    path, digest = write_context(tmp_path, document())
    result = load(path, digest, frozen)
    assert result['available'] is False
    assert result['reason'] == 'POSITION_CONTEXT_EXISTING_IDENTITY_OR_UNITS_MISMATCH'


def test_output_is_bounded_read_only_and_matches_existing_verified_fields(tmp_path):
    frozen = snapshot()
    frozen['trade_identity'] = {'broker_id': 'executing-broker', 'local_note': 'retain'}
    frozen['position_execution_units'] = {'quantity_units': 2.}
    original = deepcopy(frozen)
    doc = document()
    doc['padding'] = 'x' * 20000
    path, digest = write_context(tmp_path, doc)
    result = load(path, digest, frozen)
    assert result['available'] is True
    assert frozen == original
    assert len(json.dumps(result).encode()) < 3000
    assert result['audit']['document_sha256'] == digest
    attach_unified_edge_context(engine(tmp_path, doc), frozen, expected_sha=SHA)
    assert frozen['trade_identity']['local_note'] == 'retain'


@pytest.mark.parametrize('tier', ['v18', 'strict', 'emergency', 'bridge'])
def test_imported_identity_units_and_audits_survive_compaction(tmp_path, tier):
    from seiltanzer import ai_snapshot_budget_guard as guard, ai_verdict, ai_verdict_v18, ai_verdict_budget_bridge
    frozen = snapshot()
    attach_unified_edge_context(engine(tmp_path, document(), cost_document()), frozen, expected_sha=SHA)
    roots = ('trade_identity', 'position_execution_units', 'position_execution_context_audit',
             'execution_cost_context_audit')
    expected = {key: deepcopy(frozen[key]) for key in roots}
    if tier == 'v18':
        frozen['policy_manager']['redundant_explanation'] = 'x' * 80000
        ai_verdict_v18._enforce_snapshot_budget(frozen)
    elif tier == 'strict':
        guard._strict_authoritative_compaction(frozen)
    elif tier == 'emergency':
        guard._strict_authoritative_compaction(frozen)
        guard._emergency_authoritative_compaction(frozen, ai_verdict)
    else:
        ai_verdict_budget_bridge._drop_duplicate_integrity_views(frozen)
    assert {key: frozen.get(key) for key in roots} == expected
    assert frozen['policy_manager']['execution_cost_repricing_required'] is True


def test_position_settings_are_separate_and_disabled_by_default(monkeypatch):
    from seiltanzer.config import Settings
    monkeypatch.delenv('SEILTANZER_POSITION_EXECUTION_CONTEXT_PATH', raising=False)
    monkeypatch.delenv('SEILTANZER_POSITION_EXECUTION_CONTEXT_SHA256', raising=False)
    assert Settings().position_execution_context_path == ''
    monkeypatch.setenv('SEILTANZER_POSITION_EXECUTION_CONTEXT_PATH', '/private/position.json')
    monkeypatch.setenv('SEILTANZER_POSITION_EXECUTION_CONTEXT_SHA256', 'c' * 64)
    assert Settings().position_execution_context_path == '/private/position.json'
    assert Settings().position_execution_context_sha256 == 'c' * 64
