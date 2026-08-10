"""Refine F.3.2a path coverage semantics for resolved first-touch events.

A confirmed touch only needs authoritative coverage from T0 through the touch;
NO_TOUCH still requires authoritative coverage through the entire horizon.
"""
from __future__ import annotations

import json

from . import passive_learning as _pl
from .measurement_path_runtime import _bar_authoritative
from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION, finite

_ENGINE = _pl.PassiveLearningEngine
_CURRENT_RESOLVE = _ENGINE._resolve_one


def _merged_coverage(intervals: list[tuple[float, float]], start: float, end: float) -> float:
    clipped = sorted(
        (max(start, a), min(end, b)) for a, b in intervals
        if b > start and a < end and min(end, b) > max(start, a)
    )
    if not clipped:
        return 0.0
    total = 0.0
    left, right = clipped[0]
    for a, b in clipped[1:]:
        if a <= right + 1e-6:
            right = max(right, b)
        else:
            total += right - left
            left, right = a, b
    return total + right - left


def resolve_with_event_scope(self: _ENGINE, row: dict, now: float) -> str:
    status = _CURRENT_RESOLVE(self, row, now)
    if status != "resolved":
        return status

    captured, target = float(row["captured_ts"]), float(row["target_ts"])
    instrument = str(row["instrument"])
    with self._lock:
        bars = [dict(x) for x in self._conn.execute(
            "SELECT * FROM passive_market_bars WHERE instrument=? "
            "AND bar_end_ts>? AND bar_start_ts<? ORDER BY bar_start_ts",
            (instrument, captured - 1e-6, target + 1e-6),
        ).fetchall()]
        stored = self._conn.execute(
            "SELECT outcome_json FROM passive_market_observations WHERE observation_id=?",
            (row["observation_id"],),
        ).fetchone()
    if not stored or not stored[0]:
        return status
    outcome = json.loads(stored[0])

    raw_intervals = []
    auth_intervals = []
    for bar in bars:
        a, b = finite(bar.get("bar_start_ts")), finite(bar.get("bar_end_ts"))
        if a is None or b is None or b <= a:
            continue
        raw_intervals.append((a, b))
        if _bar_authoritative(bar):
            auth_intervals.append((a, b))

    full_open = _pl._trading_seconds_between(instrument, captured, target)
    raw_seconds = _merged_coverage(raw_intervals, captured, target)
    if full_open > 0:
        outcome["path_coverage_ratio"] = round(min(1.0, raw_seconds / full_open), 6)

    first = outcome.get("first_touch") or {}
    label = first.get("label")
    event_ts = finite(
        captured + 60.0 * float(first["first_touch_calendar_minutes"])
        if first.get("first_touch_calendar_minutes") is not None else None
    )
    if label in {"upper_hit_first", "lower_hit_first"} and event_ts is not None:
        required = _pl._trading_seconds_between(instrument, captured, event_ts)
        covered = _merged_coverage(auth_intervals, captured, event_ts)
        clean_to_event = required > 0 and covered >= required - 1.0
        if clean_to_event and first.get("ambiguous_first_touch") is not True:
            first["clean_label"] = True
            first["authoritative_path"] = True
            outcome["authoritative_path_coverage_ratio"] = 1.0
            outcome["path_missing_authoritative_bars"] = 0
            outcome["path_max_open_gap_seconds"] = 0.0
            outcome["partial_first_bar_unobserved_sec"] = 0.0
            outcome["partial_last_bar_unobserved_sec"] = 0.0
            outcome["path_quality_status"] = "complete_to_first_touch_authoritative"
            outcome["authoritative_path_coverage_scope"] = "t0_to_first_touch"
            outcome["first_touch"] = first
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE passive_market_observations SET outcome_json=? WHERE observation_id=?",
                    (_pl._json(outcome), row["observation_id"]),
                )
    return status


def install_path_refinement() -> None:
    if getattr(_ENGINE, "_measurement_path_refinement", None) == MEASUREMENT_RUNTIME_VERSION:
        return
    _ENGINE._resolve_one = resolve_with_event_scope
    _ENGINE._measurement_path_refinement = MEASUREMENT_RUNTIME_VERSION
