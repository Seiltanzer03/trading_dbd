from copy import deepcopy

import pytest

from seiltanzer.rollover_economics import frozen_rollover_schedule, replay_rollover_cost
from seiltanzer.execution_simulator import ExecutionSpec, replay_execution_path
from test_unified_candidate_economics import candidate, snapshot
from seiltanzer.unified_candidate_economics import price_unified_candidates


def quoted(value, charges=((60, 10), (180, 10)), included=False):
    cutoff = value["captured_ts"]
    value["strategy"] = {"instrument": "NAS100"}
    value["broker_rollover_schedule"] = {
        "source_id": "actual-broker-quote-v1", "source_verified": True,
        "instrument": "NAS100", "observed_ts": cutoff - 5, "received_ts": cutoff - 1,
        "quality": 1., "max_age_sec": 3600, "currency": "USD",
        "risk_currency_per_unit": 100., "charge_basis": "per_unit_of_remaining_position",
        "coverage_start_epoch": cutoff, "coverage_end_epoch": cutoff + 240 * 60,
        "same_timestamp_rule": "fills_before_rollover", "included_in_base_costs": included,
        "events": [{"scheduled_epoch": cutoff + minute * 60, "charge_currency_per_unit": charge}
                   for minute, charge in charges],
    }
    return value


def test_missing_quote_is_explicit_not_assumed_zero_broker_carry():
    value = snapshot([[1, 1, 1]], [1.])
    result = price_unified_candidates(value, [candidate("HOLD")])
    assert result["rollover_cost_audit"]["available"] is False
    assert result["rollover_cost_audit"]["missing_quote_is_zero_carry"] is False


def test_same_bank_costs_depend_on_actual_path_exit_and_remaining_close_fraction():
    value = quoted(snapshot([[1, -1, -1], [1, 1, 1]], [.5, .5]))
    result = price_unified_candidates(value, [candidate("HOLD"), candidate("CLOSE_50"), candidate("EXIT")])
    # Stop path reaches -1 at120min: one rollover; HOLD path pays two.
    assert result["candidates"]["HOLD"]["expected_rollover_cost_r"] == pytest.approx(.15)
    assert result["candidates"]["CLOSE_50"]["expected_rollover_cost_r"] == pytest.approx(.075)
    assert result["candidates"]["EXIT"]["expected_rollover_cost_r"] == 0
    assert result["candidates"]["HOLD"]["cvar10_net_r"] == pytest.approx(-1.12)


def test_time_stop_never_pays_rollover_after_its_frozen_deadline():
    value = quoted(snapshot([[1, 1, 1]], [1.]))
    result = price_unified_candidates(value, [candidate("HOLD"), candidate("TIME_STOP", deadline_ts=value["captured_ts"] + 120 * 60)])
    assert result["candidates"]["TIME_STOP"]["expected_rollover_cost_r"] == pytest.approx(.1)
    assert result["candidates"]["HOLD"]["expected_rollover_cost_r"] == pytest.approx(.2)


def test_partial_rung_and_spike_quantities_are_used_at_exact_event_times():
    spec = ExecutionSpec.from_values(current_r=0, max_r=0, take_r=5, rungs=[1],
                                     rung_fraction_original=.5, be_after_r=10)
    from dataclasses import replace
    spec = replace(spec, spike_r=2., spike_fraction=.25)
    result = replay_execution_path([0, 4], spec)
    schedule = {"same_timestamp_rule": "fills_before_rollover", "events": [
        {"epoch": 30., "cost_r": 1.}, {"epoch": 60., "cost_r": 1.}]}
    # Rung fills at25sec, spike at50sec: .5 and .25 remain atrollovers.
    assert replay_rollover_cost(result, [0., 100.], schedule) == pytest.approx(.75)


def test_credit_signed_and_already_included_schedule_never_double_counted():
    value = quoted(snapshot([[1, 1, 1]], [1.]), charges=((60, -10),))
    result = price_unified_candidates(value, [candidate("HOLD")])
    assert result["candidates"]["HOLD"]["expected_rollover_cost_r"] == pytest.approx(-.1)
    value["broker_rollover_schedule"]["included_in_base_costs"] = True
    result = price_unified_candidates(value, [candidate("HOLD")])
    assert result["candidates"]["HOLD"]["expected_rollover_cost_r"] == 0
    assert result["rollover_cost_audit"]["reason"] == "ALREADY_INCLUDED_IN_BASE_COSTS"


@pytest.mark.parametrize("field,invalid", [("instrument", "XAU"), ("source_verified", False),
    ("received_ts", 1_900_000_001), ("risk_currency_per_unit", 0),
    ("same_timestamp_rule", None), ("included_in_base_costs", None),
    ("coverage_end_epoch", 1_900_000_001), ("max_age_sec", None)])
def test_declared_invalid_cost_contract_disables_new_comparison(field, invalid):
    value = quoted(snapshot([[1, 1, 1]], [1.]))
    value["broker_rollover_schedule"][field] = invalid
    result = price_unified_candidates(value, [candidate("HOLD")])
    assert result["available"] is False
    assert "BROKER_ROLLOVER" in result["reason"]


def test_snapshot_is_not_mutated_and_event_order_is_declared():
    value = quoted(snapshot([[1, -1, -1]], [1.]), charges=((120, 10),))
    original = deepcopy(value)
    first = price_unified_candidates(value, [candidate("HOLD")])
    assert value == original
    assert first["candidates"]["HOLD"]["expected_rollover_cost_r"] == 0
    value["broker_rollover_schedule"]["same_timestamp_rule"] = "rollover_before_fills"
    second = price_unified_candidates(value, [candidate("HOLD")])
    assert second["candidates"]["HOLD"]["expected_rollover_cost_r"] == pytest.approx(.1)


def executing_quote():
    value = quoted(snapshot([[1, 1, 1]], [1.]))
    value['trade_id'] = 7
    value['strategy']['direction'] = 'long'
    value['trade_identity'] = {'broker_id': 'broker-1', 'account_id': 'account-1',
                               'trade_id': 7, 'instrument': 'NAS100', 'direction': 'long'}
    value['position_execution_units'] = {'currency': 'USD', 'quantity_units': 2.,
        'risk_currency_per_unit': 100., 'quantity_basis': 'current_remaining_position'}
    value['broker_rollover_schedule'].update(value['trade_identity'])
    value['broker_rollover_schedule'].update(value['position_execution_units'])
    return value


@pytest.mark.parametrize('field,changed,reason', [
    ('broker_id', 'broker-2', 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'),
    ('account_id', 'account-2', 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'),
    ('trade_id', 8, 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'),
    ('direction', 'short', 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'),
    ('currency', 'EUR', 'BROKER_ROLLOVER_EXECUTING_UNITS_MISMATCH'),
    ('risk_currency_per_unit', 200., 'BROKER_ROLLOVER_EXECUTING_UNITS_MISMATCH'),
    ('quantity_units', 3., 'BROKER_ROLLOVER_EXECUTING_UNITS_MISMATCH'),
    ('quantity_basis', 'original_position', 'BROKER_ROLLOVER_EXECUTING_UNITS_MISMATCH'),
])
def test_live_common_repricing_rejects_other_executing_position_or_units(field, changed, reason):
    value = executing_quote()
    value['broker_rollover_schedule'][field] = changed
    original = deepcopy(value)
    result = price_unified_candidates(value, [candidate('HOLD'), candidate('EXIT')])
    assert result['available'] is False
    assert result['reason'] == reason
    assert value == original


def test_known_strategy_direction_requires_matching_quote_even_without_account_context():
    value = quoted(snapshot([[1, 1, 1]], [1.]))
    value['strategy']['direction'] = 'short'
    result = price_unified_candidates(value, [candidate('HOLD')])
    assert result['available'] is False
    assert result['reason'] == 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'


def test_matching_executing_quote_preserves_live_carry_and_already_included_semantics():
    value = executing_quote()
    result = price_unified_candidates(value, [candidate('HOLD'), candidate('EXIT')])
    assert result['available'] is True
    assert result['candidates']['HOLD']['expected_rollover_cost_r'] == pytest.approx(.2)
    assert result['candidates']['EXIT']['expected_rollover_cost_r'] == 0.
    value['broker_rollover_schedule']['included_in_base_costs'] = True
    result = price_unified_candidates(value, [candidate('HOLD')])
    assert result['rollover_cost_audit']['reason'] == 'ALREADY_INCLUDED_IN_BASE_COSTS'
    assert result['candidates']['HOLD']['expected_rollover_cost_r'] == 0.
    value['broker_rollover_schedule']['direction'] = 'short'
    result = price_unified_candidates(value, [candidate('HOLD')])
    assert result['available'] is False
    assert result['reason'] == 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'


@pytest.mark.parametrize('context', ['trade_identity', 'position_execution_units'])
def test_malformed_executing_context_disables_live_comparison_without_crashing(context):
    value = executing_quote()
    value[context] = 'unparsed-position-context'
    result = price_unified_candidates(value, [candidate('HOLD')])
    assert result['available'] is False
    assert result['reason'] == 'BROKER_ROLLOVER_EXECUTING_CONTEXT_INVALID'


@pytest.mark.parametrize('field', ['broker_id', 'account_id', 'trade_id', 'direction',
                                 'currency', 'quantity_units', 'risk_currency_per_unit', 'quantity_basis'])
def test_known_executing_context_cannot_be_bypassed_by_omitting_quote_binding(field):
    value = executing_quote()
    del value['broker_rollover_schedule'][field]
    assert price_unified_candidates(value, [candidate('HOLD')])['available'] is False


def test_matching_malformed_direction_fails_closed_in_live_repricing():
    value = executing_quote()
    value['strategy']['direction'] = ['long']
    value['trade_identity']['direction'] = ['long']
    value['broker_rollover_schedule']['direction'] = ['long']
    result = price_unified_candidates(value, [candidate('HOLD')])
    assert result['available'] is False
    assert result['reason'] == 'BROKER_ROLLOVER_EXECUTING_IDENTITY_MISMATCH'
