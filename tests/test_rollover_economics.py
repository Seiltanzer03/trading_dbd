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
