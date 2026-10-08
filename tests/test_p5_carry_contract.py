"""Executing carry quote facts must not invent position binding or cost inclusion."""
from copy import deepcopy
import pytest
from test_edge_family_adapters import source, snapshot, T0


def carry_snapshot():
    data = source(kind='broker_carry', charge_currency_per_rollover=4., risk_currency_per_unit=100.,
                  currency='USD', charge_basis='per_unit_of_remaining_position', next_rollover_ts=T0+100,
                  included_in_policy_economics=False, broker_id='broker', account_id='account',
                  trade_id=1, direction='long', quantity_basis='current_remaining_position', quantity_units=2.)
    value = snapshot('value_carry', data, 'carry.cost')
    value['trade_identity'] = {key: data[key] for key in ('broker_id','account_id','trade_id','instrument','direction')}
    value['position_execution_units'] = {key: data[key] for key in
        ('currency','quantity_basis','quantity_units','risk_currency_per_unit')}
    return value


@pytest.mark.parametrize('key', ['broker_id','account_id','trade_id','direction','currency','quantity_units','risk_currency_per_unit'])
def test_carry_fact_cannot_claim_execution_when_declared_identity_or_units_conflict(key):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value = carry_snapshot()
    data = value['edge_family_sources']['value_carry']
    data[key] = 99. if key in ('trade_id','quantity_units','risk_currency_per_unit') else 'wrong'
    result = build_edge_family_evidence(value)
    assert result['economics_adjustments'] == []
    assert not result['families']['value_carry']['available']
    assert result['components'] == []


@pytest.mark.parametrize('included', [None, 'false', 0])
def test_unknown_carry_inclusion_cannot_be_reported_as_not_included(included):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value = carry_snapshot()
    value['edge_family_sources']['value_carry']['included_in_policy_economics'] = included
    result = build_edge_family_evidence(value)
    assert result['economics_adjustments'] == []
    assert any(row['reason'] == 'BROKER_CARRY_DOUBLE_COUNT_STATUS_UNAVAILABLE'
               for row in result['families']['value_carry']['rejected_sources'])


@pytest.mark.parametrize('rollover', [None, T0-1, T0])
def test_carry_quote_without_future_event_is_unavailable(rollover):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value = carry_snapshot()
    value['edge_family_sources']['value_carry']['next_rollover_ts'] = rollover
    result = build_edge_family_evidence(value)
    assert result['economics_adjustments'] == []


@pytest.mark.parametrize('included', [False, True])
def test_bound_signed_carry_keeps_measured_cost_inclusion_without_vote(included):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value = carry_snapshot()
    value['edge_family_sources']['value_carry'].update(charge_currency_per_rollover=-4.,included_in_policy_economics=included)
    before = deepcopy(value)
    result = build_edge_family_evidence(value)
    adjustment = result['economics_adjustments'][0]
    assert adjustment['cost_r_per_rollover'] == -.04
    assert adjustment['included_in_policy_economics'] is included
    assert adjustment.get('declared_executing_context_matched') is True
    assert result['components'] == [] and value == before


def test_legacy_unbound_quote_is_explicitly_quote_only():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value = carry_snapshot()
    value.pop('trade_identity'); value.pop('position_execution_units')
    adjustment = build_edge_family_evidence(value)['economics_adjustments'][0]
    assert adjustment.get('declared_executing_context_matched') is False


def test_nonfinite_normalized_carry_is_not_available():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value = carry_snapshot()
    value['edge_family_sources']['value_carry'].update(charge_currency_per_rollover=1e308,risk_currency_per_unit=1e-308)
    value['position_execution_units']['risk_currency_per_unit'] = 1e-308
    assert build_edge_family_evidence(value)['economics_adjustments'] == []


def test_normalized_cutoff_clock_keeps_valid_quote():
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    value = carry_snapshot()
    value['captured_ts'] = str(T0)
    assert build_edge_family_evidence(value)['economics_adjustments'][0]['cost_r_per_rollover'] == .04
