"""Measured broker rollover charges on explicit remaining-quantity timelines.

No quote, calendar, swap rate or broker execution is inferred. A supplied
schedule must be immutable, causal and cover the complete comparison horizon.
Positive charges are costs, negative charges credits; neither is an expert vote.
"""
from __future__ import annotations

import math

from .canonical_market_context import canonical_instrument_code

VERSION = "broker-rollover-economics-v1"


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def frozen_rollover_schedule(snapshot, horizon_minutes):
    """Return (normalized schedule, audit); reject a malformed declared quote."""
    supplied = snapshot.get("broker_rollover_schedule")
    audit = {"version": VERSION, "available": False, "applied": False,
             "reason": "BROKER_QUOTE_UNAVAILABLE", "voting_weight": 0.,
             "missing_quote_is_zero_carry": False}
    if supplied is None:
        return None, audit
    if not isinstance(supplied, dict):
        raise ValueError("BROKER_ROLLOVER_SCHEDULE_INVALID")
    cutoff = _number(snapshot.get("captured_ts"))
    horizon = _number(horizon_minutes)
    strategy = snapshot.get("strategy") or {}
    instrument = canonical_instrument_code(strategy.get("instrument") or snapshot.get("instrument"))
    observed = _number(supplied.get("observed_ts"))
    received = _number(supplied.get("received_ts"))
    quality = _number(supplied.get("quality"))
    if (supplied.get("source_verified") is not True or not supplied.get("source_id")
            or cutoff is None or horizon is None or horizon <= 0
            or observed is None or received is None or not 0 < observed <= received <= cutoff
            or quality is None or not 0 < quality <= 1
            or canonical_instrument_code(supplied.get("instrument")) != instrument):
        raise ValueError("BROKER_ROLLOVER_SOURCE_IDENTITY_OR_CLOCK_INVALID")
    max_age = _number(supplied.get("max_age_sec"))
    if max_age is None or not 0 < max_age <= 86400 or cutoff - observed > max_age:
        raise ValueError("BROKER_ROLLOVER_QUOTE_STALE_OR_EXPIRY_UNAVAILABLE")
    risk = _number(supplied.get("risk_currency_per_unit"))
    if (not supplied.get("currency") or risk is None or risk <= 0
            or supplied.get("charge_basis") != "per_unit_of_remaining_position"):
        raise ValueError("BROKER_ROLLOVER_UNITS_UNAVAILABLE")
    start = _number(supplied.get("coverage_start_epoch"))
    end = _number(supplied.get("coverage_end_epoch"))
    if start is None or end is None or start > cutoff or end < cutoff + horizon * 60:
        raise ValueError("BROKER_ROLLOVER_HORIZON_COVERAGE_INCOMPLETE")
    rule = supplied.get("same_timestamp_rule")
    if rule not in {"fills_before_rollover", "rollover_before_fills"}:
        raise ValueError("BROKER_ROLLOVER_EVENT_ORDER_UNAVAILABLE")
    raw = supplied.get("events")
    if not isinstance(raw, list) or len(raw) > 256:
        raise ValueError("BROKER_ROLLOVER_EVENTS_EXCEED_BOUND_OR_UNAVAILABLE")
    events, seen = [], set()
    for event in raw:
        if not isinstance(event, dict):
            raise ValueError("BROKER_ROLLOVER_EVENT_INVALID")
        epoch = _number(event.get("scheduled_epoch"))
        charge = _number(event.get("charge_currency_per_unit"))
        if (epoch is None or charge is None or epoch in seen
                or not start <= epoch <= end):
            raise ValueError("BROKER_ROLLOVER_EVENT_INVALID")
        seen.add(epoch)
        if cutoff < epoch <= cutoff + horizon * 60:
            events.append({"epoch": epoch, "cost_r": charge / risk})
    events.sort(key=lambda row: row["epoch"])
    included = supplied.get("included_in_base_costs")
    if not isinstance(included, bool):
        raise ValueError("BROKER_ROLLOVER_DOUBLE_COUNT_CONTRACT_UNAVAILABLE")
    audit.update(available=True, applied=not included,
                 reason="ALREADY_INCLUDED_IN_BASE_COSTS" if included else "MEASURED_REMAINING_QUANTITY_ROLLOVER",
                 source_id=supplied["source_id"], observed_ts=observed,
                 received_ts=received, currency=supplied["currency"],
                 included_in_base_costs=included, event_n=len(events),
                 same_timestamp_rule=rule)
    return {"events": [] if included else events, "same_timestamp_rule": rule}, audit


def replay_rollover_cost(result, times, schedule):
    """Cost per initial T0 remainder; callers scale immediate close separately."""
    if not schedule or not schedule["events"]:
        return 0.
    timeline = []
    for event in result.events:
        remaining = _number(event.get("remaining_after"))
        if remaining is None:
            continue
        step = event.get("step")
        fraction = _number(event.get("segment_fraction"))
        if not isinstance(step, int) or not 0 <= step < len(times) or fraction is None:
            raise ValueError("EXECUTION_QUANTITY_EVENT_TIME_UNAVAILABLE")
        epoch = (times[0] if step == 0 else
                 times[step - 1] + fraction * (times[step] - times[step - 1]))
        timeline.append((epoch, remaining))
    timeline.sort(key=lambda row: row[0])
    index, remaining, total = 0, 1., 0.
    fill_first = schedule["same_timestamp_rule"] == "fills_before_rollover"
    for rollover in schedule["events"]:
        epoch = rollover["epoch"]
        while index < len(timeline) and (timeline[index][0] < epoch or
                                        (fill_first and timeline[index][0] == epoch)):
            remaining = timeline[index][1]
            index += 1
        total += rollover["cost_r"] * remaining
    return float(total)
