from copy import deepcopy
import hashlib
import json

import pytest

from seiltanzer.execution_cost_context import COMPONENTS, MAX_BYTES, VERSION, load_execution_cost_context, validate_execution_cost_context

T0 = 1790910000.
SHA = 'a' * 40


def snapshot():
    return {'captured_ts': T0, 'trade_id': 1,
            'strategy': {'instrument': 'NAS100', 'direction': 'long'},
            'trade_identity': {'broker_id': 'executing-broker', 'account_id': 'account-1'},
            'position_execution_units': {'currency': 'USD', 'quantity_units': 2.,
                'risk_currency_per_unit': 100., 'quantity_basis': 'current_remaining_position'},
            'policy_manager': {'inputs': {'horizon_minutes': 240.}}}


def document():
    return {'version': VERSION, 'deployment_sha': SHA, 'source_verified': True,
            'source_id': 'private-broker-export', 'broker_id': 'executing-broker',
            'account_id': 'account-1', 'trade_id': 1, 'instrument': 'NAS100', 'direction': 'long',
            'observed_ts': T0 - 10., 'received_ts': T0 - 5., 'max_age_sec': 60.,
            'currency': 'USD', 'quantity_units': 2., 'risk_currency_per_unit': 100.,
            'quantity_basis': 'current_remaining_position', 'coverage_start_epoch': T0,
            'coverage_end_epoch': T0 + 240. * 60.,
            **{channel: {component: {'cost_currency_per_unit': float(index),
                 'observed_ts': T0 - 10., 'measurement_kind': 'executing_broker_quote',
                 'source_id': 'private-broker-export', 'evidence_sha256': 'b' * 64}
               for index, component in enumerate(COMPONENTS)} for channel in ('immediate', 'deferred')}}


def check(doc, snap=None):
    return validate_execution_cost_context(doc, snapshot=snap or snapshot(), expected_deployment_sha=SHA)


def test_complete_measured_costs_keep_zero_and_use_explicit_currency_risk_units():
    doc, snap = document(), snapshot()
    originals = deepcopy((doc, snap))
    result = check(doc, snap)
    assert result['complete_costs_available'] is True
    assert result['immediate_full_close_r'] == pytest.approx(.06)
    assert result['assumed'] is False
    assert result['components']['immediate']['components_r']['spread'] == 0.
    assert result['includes_rollover'] is False
    assert (doc, snap) == originals


def test_normalized_audit_retains_original_binding_coverage_and_component_evidence():
    doc = document()
    result = check(doc)
    assert result['trade_id'] == 1
    assert result['instrument'] == 'NAS100'
    assert result['direction'] == 'long'
    assert result['source_verified'] is True
    assert result['coverage_start_epoch'] == T0
    assert result['coverage_end_epoch'] == T0 + 14400.
    for channel in ('immediate', 'deferred'):
        evidence = result['components'][channel]['component_provenance']
        assert evidence == doc[channel]
        evidence['spread']['source_id'] = 'modified'
        assert doc[channel]['spread']['source_id'] == 'private-broker-export'


def test_partial_measurements_are_not_a_complete_cost_sum():
    doc = document()
    doc['immediate']['slippage'] = None
    doc['deferred'] = {}
    result = check(doc)
    assert result['available'] is True
    assert result['complete_costs_available'] is False
    assert result['immediate_full_close_r'] is None
    assert result['deferred_full_close_r'] is None
    assert result['missing_is_zero_cost'] is False
    assert result['missing_components']['immediate'] == ['slippage']


@pytest.mark.parametrize('key,value,reason', [
    ('deployment_sha', 'c' * 40, 'SHA_MISMATCH'),
    ('received_ts', T0 + 1., 'CLOCK'), ('observed_ts', T0 - 100., 'STALE'),
    ('currency', 'EUR', 'UNITS'), ('quantity_units', 3., 'UNITS'),
    ('source_verified', False, 'IDENTITY'), ('instrument', 'XAU', 'IDENTITY'),
    ('coverage_end_epoch', T0 + 10., 'COVERAGE'), ('trade_id', 2, 'IDENTITY'),
])
def test_source_identity_clocks_sha_units_and_horizon_fail_closed(key, value, reason):
    doc = document()
    doc[key] = value
    result = check(doc)
    assert result['available'] is False
    assert reason in result['reason']
    assert result['immediate_full_close_r'] is None


def test_indicative_quotes_are_not_executing_broker_measurements():
    doc = document()
    doc['immediate']['spread']['measurement_kind'] = 'public_indicative_quote'
    assert check(doc)['available'] is False
    snap = snapshot()
    snap.pop('trade_identity')
    assert check(document(), snap)['reason'] == 'EXECUTING_BROKER_ACCOUNT_IDENTITY_UNAVAILABLE_OR_MISMATCH'
    snap = snapshot()
    snap.pop('position_execution_units')
    assert check(document(), snap)['available'] is False


def test_local_loader_is_bounded_and_requires_pinned_file_sha(tmp_path):
    path = tmp_path / 'costs.json'
    raw = json.dumps(document()).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    kwargs = {'snapshot': snapshot(), 'expected_deployment_sha': SHA}
    assert load_execution_cost_context(path, **kwargs)['reason'] == 'EXECUTION_CONTEXT_DOCUMENT_SHA_UNAVAILABLE'
    result = load_execution_cost_context(path, expected_document_sha256=digest, **kwargs)
    assert result['complete_costs_available'] is True
    assert result['document_sha256'] == digest
    assert load_execution_cost_context(path, expected_document_sha256='0' * 64, **kwargs)['available'] is False
    path.write_bytes(b'x' * (MAX_BYTES + 1))
    assert load_execution_cost_context(path, expected_document_sha256=digest, **kwargs)['reason'] == 'EXECUTION_CONTEXT_EXCEEDS_BYTE_BOUND'


def test_deeply_nested_pinned_context_fails_closed_without_crashing(tmp_path):
    depth = 60_000
    raw = b'[' * depth + b'0' + b']' * depth
    path = tmp_path / 'nested.json'
    path.write_bytes(raw)
    result = load_execution_cost_context(path, snapshot=snapshot(), expected_deployment_sha=SHA,
        expected_document_sha256=hashlib.sha256(raw).hexdigest())
    assert result['available'] is False
    assert result['reason'] == 'EXECUTION_CONTEXT_JSON_INVALID'
