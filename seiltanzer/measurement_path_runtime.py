"""Phase F.3.2a outcome-resolution and telemetry integrity closure."""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from . import passive_learning as _pl
from .measurement_q_runtime import (
    MEASUREMENT_RUNTIME_VERSION,
    finite,
    valid_terminal_cdf,
)

_ENGINE = _pl.PassiveLearningEngine
_ORIGINAL_STATUS = _ENGINE.status
_ORIGINAL_CALIBRATION = _ENGINE.calibration_report
TERMINAL_MAX_AGE_SEC = 65.0


def _bar_authoritative(bar: dict) -> bool:
    kind = str(bar.get("kind") or "").lower()
    source = str(bar.get("source") or "").lower()
    quality = finite(bar.get("quality"))
    if kind != "direct" or (quality is not None and quality < 0.95):
        return False
    # Current F3.2 Yahoo bars are contextual return-shape/basis data, not
    # target-native first-touch truth.
    if source.startswith("yahoo_1m"):
        return False
    return source.startswith((
        "observed_1m_ohlc", "authoritative_", "broker_", "exchange_"
    ))


def _point_authoritative(point: dict) -> bool:
    quality = finite(point.get("quality"))
    return (
        str(point.get("kind") or "").lower() == "direct"
        and (quality is None or quality >= 0.70)
    )


def _expected_full_bars(instrument: str, captured: float, target: float) -> list[float]:
    cursor = math.ceil((captured - 1e-9) / 60.0) * 60.0
    starts: list[float] = []
    while cursor + 60.0 <= target + 1e-9:
        if _pl._session_state(instrument, cursor + 30.0)["is_open"]:
            starts.append(cursor)
        cursor += 60.0
    return starts


def _boundary_unobserved(instrument: str, captured: float, target: float) -> tuple[float, float]:
    first_full = math.ceil((captured - 1e-9) / 60.0) * 60.0
    last_full = math.floor((target + 1e-9) / 60.0) * 60.0
    first = _pl._trading_seconds_between(instrument, captured, min(first_full, target))
    last = _pl._trading_seconds_between(instrument, max(captured, last_full), target)
    return float(first), float(last)


def _terminal_candidate(
    bars: list[dict], points: list[dict], target: float
) -> tuple[float, float, str, bool] | None:
    candidates: list[tuple[float, float, str, bool]] = []
    for point in points:
        ts, price = finite(point.get("ts")), finite(point.get("price"))
        if ts is not None and price is not None and price > 0 and ts <= target + 1e-6:
            candidates.append((ts, price, "recorded_market_point", _point_authoritative(point)))
    for bar in bars:
        ts, price = finite(bar.get("bar_end_ts")), finite(bar.get("close"))
        if ts is not None and price is not None and price > 0 and ts <= target + 1e-6:
            candidates.append((ts, price, "recorded_ohlc_close", _bar_authoritative(bar)))
    if not candidates:
        return None
    recent_authoritative = [
        row for row in candidates if row[3] and 0 <= target - row[0] <= TERMINAL_MAX_AGE_SEC
    ]
    return max(recent_authoritative or candidates, key=lambda row: row[0])


def resolve_one_f32a(self: _ENGINE, row: dict, now: float) -> str:
    captured, target = float(row["captured_ts"]), float(row["target_ts"])
    instrument = str(row["instrument"])
    with self._lock:
        bars = [dict(x) for x in self._conn.execute(
            "SELECT * FROM passive_market_bars WHERE instrument=? "
            "AND bar_end_ts>? AND bar_start_ts<? ORDER BY bar_start_ts",
            (instrument, captured - 1e-6, target + 1e-6),
        ).fetchall()]
        points = [dict(x) for x in self._conn.execute(
            "SELECT * FROM passive_market_path WHERE instrument=? "
            "AND ts>=? AND ts<=? ORDER BY ts",
            (instrument, captured - 1e-6, target + 1e-6),
        ).fetchall()]

    candidate = _terminal_candidate(bars, points, target)
    if candidate is None:
        return "pending" if now <= target + _pl.MAX_GAP_SEC else "insufficient_future_data"
    terminal_ts, end, terminal_source, terminal_authoritative = candidate
    terminal_age = max(0.0, target - terminal_ts)
    if terminal_age > TERMINAL_MAX_AGE_SEC:
        return "pending" if now <= target + _pl.MAX_GAP_SEC else "insufficient_future_data"

    start = float(row["market_price"])
    if start <= 0 or end <= 0:
        return "insufficient_future_data"
    forecast = json.loads(row["forecast_json"])
    sigma_h = finite(forecast.get("sigma_h_return"))
    terminal_ret = math.log(end / start)

    pit = None
    cdf_payload = forecast.get("terminal_q_cdf")
    if valid_terminal_cdf(cdf_payload):
        pit = round(float(np.interp(
            terminal_ret,
            np.asarray(cdf_payload["support"], dtype=float),
            np.asarray(cdf_payload["cdf"], dtype=float),
            left=0.0, right=1.0,
        )), 6)

    terminal_class = "inside"
    if sigma_h is not None and sigma_h > 0:
        if terminal_ret >= sigma_h:
            terminal_class = "above_upper"
        elif terminal_ret <= -sigma_h:
            terminal_class = "below_lower"
    terminal_clean = bool(terminal_authoritative and terminal_age <= TERMINAL_MAX_AGE_SEC)
    terminal = {
        "terminal_price": round(end, 6),
        "terminal_price_ts": terminal_ts,
        "terminal_age_to_target_sec": round(terminal_age, 3),
        "terminal_lookahead_used": False,
        "terminal_source": terminal_source,
        "terminal_authoritative": bool(terminal_authoritative),
        "clean_label": terminal_clean,
        "terminal_log_return": round(terminal_ret, 8),
        "normalized_return": (
            round(terminal_ret / sigma_h, 8)
            if sigma_h is not None and sigma_h > 0 else None
        ),
        "normalization_denominator": "T0 reference volatility",
        "terminal_class": terminal_class,
        "terminal_pit_q": pit,
    }

    expected = _expected_full_bars(instrument, captured, target)
    expected_set = {round(x, 6) for x in expected}
    full_bars = [
        b for b in bars
        if finite(b.get("bar_start_ts")) is not None
        and finite(b.get("bar_end_ts")) is not None
        and float(b["bar_start_ts"]) >= captured - 1e-6
        and float(b["bar_end_ts"]) <= target + 1e-6
        and abs(float(b["bar_end_ts"]) - float(b["bar_start_ts"]) - 60.0) <= 1.0
    ]
    authoritative_bars = [b for b in full_bars if _bar_authoritative(b)]
    raw_starts = {round(float(b["bar_start_ts"]), 6) for b in full_bars}
    auth_starts = {round(float(b["bar_start_ts"]), 6) for b in authoritative_bars}
    expected_n = len(expected_set)
    raw_covered = len(expected_set & raw_starts)
    auth_covered = len(expected_set & auth_starts)
    raw_coverage = raw_covered / expected_n if expected_n else 0.0
    auth_coverage = auth_covered / expected_n if expected_n else 0.0
    missing_authoritative = max(0, expected_n - auth_covered)

    partial_first, partial_last = _boundary_unobserved(instrument, captured, target)
    boundaries_clean = partial_first <= 1.0 and partial_last <= 1.0
    max_open_gap = 0.0
    run = max_run = 0
    for bar_start in expected:
        if round(bar_start, 6) in auth_starts:
            max_run = max(max_run, run)
            run = 0
        else:
            run += 1
    max_open_gap = float(max(max_run, run) * 60)

    # Best-effort label can use contextual bars, but cleanliness is independent.
    event = event_ts = None
    ambiguous = False
    scan_bars = full_bars if full_bars else bars
    if scan_bars and sigma_h is not None and sigma_h > 0:
        for bar in scan_bars:
            high, low = finite(bar.get("high")), finite(bar.get("low"))
            if high is None or low is None or high <= 0 or low <= 0:
                continue
            high_r, low_r = math.log(high / start), math.log(low / start)
            ts = float(bar.get("bar_end_ts") or target)
            if high_r >= sigma_h and low_r <= -sigma_h:
                event, event_ts, ambiguous = "ambiguous_first_touch", ts, True
                break
            if high_r >= sigma_h:
                event, event_ts = "upper_hit_first", ts
                break
            if low_r <= -sigma_h:
                event, event_ts = "lower_hit_first", ts
                break
    elif points and sigma_h is not None and sigma_h > 0:
        for point in points:
            ts, price = finite(point.get("ts")), finite(point.get("price"))
            if ts is None or price is None or price <= 0 or ts <= captured:
                continue
            value = math.log(price / start)
            if value >= sigma_h:
                event, event_ts = "upper_hit_first", ts
                break
            if value <= -sigma_h:
                event, event_ts = "lower_hit_first", ts
                break

    first_touch_authoritative = bool(
        expected_n > 0
        and missing_authoritative == 0
        and auth_coverage >= 1.0 - 1e-12
        and boundaries_clean
    )
    clean_first_touch = bool(first_touch_authoritative and not ambiguous)
    if clean_first_touch:
        quality = "complete_authoritative"
    elif raw_coverage >= 1.0 - 1e-12 and auth_covered == 0:
        quality = "complete_non_authoritative"
    elif not boundaries_clean:
        quality = "partial_boundary_unobserved"
    else:
        quality = "incomplete"

    if bars:
        granularity = "1m_ohlc" if all(
            abs(float(b["bar_end_ts"]) - float(b["bar_start_ts"]) - 60.0) <= 1.0
            for b in bars
        ) else "ohlc_mixed"
        path_source = "recorded_ohlc_bars"
    else:
        granularity, path_source = "point_only", "recorded_real_market_path"

    first_touch = {
        "label": event or "no_touch",
        "clean_label": clean_first_touch,
        "authoritative_path": first_touch_authoritative,
        "ambiguous_first_touch": ambiguous,
        "first_touch_calendar_minutes": (
            round((event_ts - captured) / 60.0, 6) if event_ts is not None else None
        ),
        "first_touch_trading_minutes": (
            round(_pl._trading_seconds_between(instrument, captured, event_ts) / 60.0, 6)
            if event_ts is not None else None
        ),
        "first_touch_primary_time_basis": "trading",
    }
    trading_seconds = _pl._trading_seconds_between(instrument, captured, target)
    outcome = {
        "version": "passive-resolver-f32a-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "path_source": path_source,
        "path_granularity": granularity,
        "first_touch_resolution": granularity,
        "path_quality_status": quality,
        "path_coverage_ratio": round(raw_coverage, 6),
        "authoritative_path_coverage_ratio": round(auth_coverage, 6),
        "path_missing_authoritative_bars": missing_authoritative,
        "path_max_open_gap_seconds": round(max_open_gap, 3),
        "partial_first_bar_unobserved_sec": round(partial_first, 3),
        "partial_last_bar_unobserved_sec": round(partial_last, 3),
        "expected_open_minutes": round(trading_seconds / 60.0, 6),
        "expected_full_bar_count": expected_n,
        "observed_bar_count": len(bars),
        "resolved_from": terminal_source,
        "path_point_count": len(bars) if bars else len(points),
        "future_return": round(end / start - 1.0, 8),
        "future_log_return": round(terminal_ret, 8),
        "terminal": terminal,
        "first_touch": first_touch,
        "ambiguous_first_touch": ambiguous,
        "actual_quantile_placement": pit,
    }
    with self._lock, self._conn:
        self._conn.execute(
            "UPDATE passive_market_observations SET resolution_status='resolved',"
            "resolved_ts=?,outcome_json=?,calendar_elapsed=?,trading_elapsed=?,"
            "market_open_fraction=? WHERE observation_id=?",
            (
                now, _pl._json(outcome), target - captured, trading_seconds,
                trading_seconds / (target - captured) if target > captured else 0.0,
                row["observation_id"],
            ),
        )
        self._last_successful_resolution_ts = now
    self._resolve_virtual_states(row, points or bars, now)
    return "resolved"


def resolved_rows_f32a(self: _ENGINE) -> list[dict]:
    """Pristine = current runtime + real background origin + clean terminal truth."""
    with self._lock:
        rows = [dict(x) for x in self._conn.execute(
            "SELECT * FROM passive_market_observations "
            "WHERE resolution_status='resolved' AND evidence_eligible=1 "
            "AND retrospective_replay=0 AND feature_contract_version=? "
            "AND observation_origin='background_collector' ORDER BY captured_ts",
            (_pl.PASSIVE_SCHEMA_VERSION,),
        ).fetchall()]
    pristine = []
    for row in rows:
        try:
            forecast = json.loads(row["forecast_json"])
            outcome = json.loads(row["outcome_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if forecast.get("measurement_runtime_contract") != MEASUREMENT_RUNTIME_VERSION:
            continue
        if (outcome.get("terminal") or {}).get("clean_label") is not True:
            continue
        row["forecast"], row["outcome"] = forecast, outcome
        pristine.append(row)
    return pristine


def _runtime_counters(self: _ENGINE) -> dict:
    with self._lock:
        rows = [dict(x) for x in self._conn.execute(
            "SELECT captured_ts,target_ts,instrument,horizon_minutes,forecast_json,"
            "outcome_json,evidence_eligible,observation_origin,price_kind "
            "FROM passive_market_observations WHERE feature_contract_version=?",
            (_pl.PASSIVE_SCHEMA_VERSION,),
        ).fetchall()]
    c = {name: 0 for name in (
        "horizon_contract_error_n", "q_semantic_contract_error_n",
        "proxy_mapping_error_n", "path_contract_error_n", "time_contract_error_n",
        "terminal_pit_valid_n", "first_touch_clean_n", "inverse_proxy_observation_n",
        "runtime_current_n", "runtime_background_origin_n", "pristine_candidate_n",
        "background_collector_origin_n", "manual_origin_n", "test_origin_n", "replay_origin_n",
    )}
    for row in rows:
        origin = str(row.get("observation_origin") or "manual")
        origin_key = f"{origin}_origin_n"
        if origin_key in c:
            c[origin_key] += 1
        try:
            forecast = json.loads(row.get("forecast_json") or "{}")
        except json.JSONDecodeError:
            continue
        runtime = forecast.get("measurement_runtime_contract") == MEASUREMENT_RUNTIME_VERSION
        if not runtime:
            continue
        c["runtime_current_n"] += 1
        if origin == "background_collector":
            c["runtime_background_origin_n"] += 1
        if forecast.get("horizon_kind") == "fixed_trading_time":
            actual = _pl._trading_seconds_between(
                row["instrument"], float(row["captured_ts"]), float(row["target_ts"])
            )
            if abs(actual - float(row["horizon_minutes"]) * 60.0) > 1e-6:
                c["horizon_contract_error_n"] += 1
        if forecast.get("proxy_transform") == "inverse":
            c["inverse_proxy_observation_n"] += 1
            if forecast.get("horizon_kind") == "option_native_expiry" and not valid_terminal_cdf(
                forecast.get("terminal_q_cdf")
            ):
                c["proxy_mapping_error_n"] += 1
        if (
            forecast.get("horizon_kind") == "option_native_expiry"
            and forecast.get("q_terminal_distribution_available")
        ):
            expiry = finite(forecast.get("source_expiry_ts_utc"))
            if expiry is None or not valid_terminal_cdf(forecast.get("terminal_q_cdf")):
                c["q_semantic_contract_error_n"] += 1
            if expiry is None or abs(float(row["target_ts"]) - expiry) > 1e-6:
                c["time_contract_error_n"] += 1
            ttm = finite(forecast.get("calendar_ttm_seconds"))
            if ttm is None or abs(ttm - (float(row["target_ts"]) - float(row["captured_ts"]))) > 1.0:
                c["time_contract_error_n"] += 1
        raw_outcome = row.get("outcome_json")
        if not raw_outcome:
            continue
        try:
            outcome = json.loads(raw_outcome)
        except json.JSONDecodeError:
            c["path_contract_error_n"] += 1
            continue
        terminal = outcome.get("terminal") or {}
        first_touch = outcome.get("first_touch") or {}
        if terminal.get("clean_label") is True and terminal.get("terminal_pit_q") is not None:
            c["terminal_pit_valid_n"] += 1
        if first_touch.get("clean_label") is True:
            c["first_touch_clean_n"] += 1
            if (
                first_touch.get("authoritative_path") is not True
                or float(outcome.get("authoritative_path_coverage_ratio") or 0.0) < 1.0 - 1e-12
                or float(outcome.get("partial_first_bar_unobserved_sec") or 0.0) > 1.0
                or float(outcome.get("partial_last_bar_unobserved_sec") or 0.0) > 1.0
            ):
                c["path_contract_error_n"] += 1
        if (
            int(row.get("evidence_eligible") or 0) == 1
            and origin == "background_collector"
            and row.get("price_kind") == "direct"
            and terminal.get("clean_label") is True
        ):
            c["pristine_candidate_n"] += 1
    return c


def status_f32a(self: _ENGINE) -> dict:
    result = dict(_ORIGINAL_STATUS(self))
    c = _runtime_counters(self)
    pristine = self._resolved_rows()
    with self._lock:
        evidence_n = self._conn.execute(
            "SELECT COUNT(*) FROM passive_market_observations "
            "WHERE feature_contract_version=? AND evidence_eligible=1 "
            "AND observation_origin='background_collector' AND price_kind='direct' "
            "AND forecast_json LIKE ?",
            (_pl.PASSIVE_SCHEMA_VERSION,
             f'%\"measurement_runtime_contract\":\"{MEASUREMENT_RUNTIME_VERSION}\"%'),
        ).fetchone()[0]
    errors = dict(result.get("error_counters") or {})
    for name in (
        "horizon_contract_error_n", "q_semantic_contract_error_n",
        "proxy_mapping_error_n", "path_contract_error_n", "time_contract_error_n",
    ):
        errors[name] = c[name] + int(self._contract_error_counters[name])
    result.update({
        "measurement_runtime_version": MEASUREMENT_RUNTIME_VERSION,
        "current_f32a_runtime_n": c["runtime_current_n"],
        "pristine_f32_n": len(pristine),
        "evidence_eligible_n": int(evidence_n),
        "terminal_pit_valid_n": c["terminal_pit_valid_n"],
        "first_touch_clean_n": c["first_touch_clean_n"],
        "inverse_proxy_observation_n": c["inverse_proxy_observation_n"],
        "observation_origin_counts": {
            "background_collector": c["background_collector_origin_n"],
            "manual": c["manual_origin_n"], "test": c["test_origin_n"],
            "replay": c["replay_origin_n"],
        },
        "error_counters": errors,
        **{name: errors[name] for name in (
            "horizon_contract_error_n", "q_semantic_contract_error_n",
            "proxy_mapping_error_n", "path_contract_error_n", "time_contract_error_n",
        )},
    })
    integrity = dict(result.get("measurement_integrity") or {})
    integrity.update({
        "horizon_contract_runtime_validated": bool(
            c["runtime_background_origin_n"] > 0 and errors["horizon_contract_error_n"] == 0
        ),
        "terminal_q_contract_runtime_validated": bool(
            c["terminal_pit_valid_n"] > 0 and errors["q_semantic_contract_error_n"] == 0
        ),
        "first_touch_contract_runtime_validated": bool(
            c["first_touch_clean_n"] > 0 and errors["path_contract_error_n"] == 0
        ),
        "proxy_mapping_runtime_validated": bool(
            c["inverse_proxy_observation_n"] > 0 and errors["proxy_mapping_error_n"] == 0
        ),
        "time_contract_runtime_validated": bool(
            c["runtime_background_origin_n"] > 0
            and errors["horizon_contract_error_n"] == 0
            and errors["time_contract_error_n"] == 0
        ),
        "pristine_f32_dataset_ready": bool(len(pristine) >= 30),
    })
    result["measurement_integrity"] = integrity
    result["g1_training_allowed"] = False
    result["promotion_allowed"] = False
    return result


def calibration_report_f32a(self: _ENGINE) -> dict:
    result = dict(_ORIGINAL_CALIBRATION(self))
    rows = self._resolved_rows()
    terminal_q = [
        row for row in rows
        if row["forecast"].get("horizon_kind") == "option_native_expiry"
        and row["forecast"].get("q_terminal_distribution_available")
        and (row["outcome"].get("terminal") or {}).get("terminal_pit_q") is not None
    ]
    fixed = [row for row in rows if row["forecast"].get("horizon_kind") != "option_native_expiry"]
    effective = self._effective_n(rows)
    c = _runtime_counters(self)
    result.update({
        "version": "passive-q-calibration-f32a-v1",
        "measurement_runtime_version": MEASUREMENT_RUNTIME_VERSION,
        "raw_n": len(rows), "terminal_q_eligible_n": len(terminal_q),
        "fixed_horizon_raw_n": len(fixed), "effective_n": effective,
        "evidence_status": self._evidence_status(effective, 0.0),
        "g1_training_allowed": False, "sample_count_auto_promotion": False,
        "promotion_allowed": False,
    })
    result["measurement_integrity"] = {
        "horizon_contract_ready": c["runtime_background_origin_n"] > 0 and c["horizon_contract_error_n"] == 0,
        "terminal_q_contract_ready": len(terminal_q) > 0 and c["q_semantic_contract_error_n"] == 0,
        "first_touch_contract_ready": c["first_touch_clean_n"] > 0 and c["path_contract_error_n"] == 0,
        "proxy_mapping_ready": c["inverse_proxy_observation_n"] > 0 and c["proxy_mapping_error_n"] == 0,
        "time_contract_ready": c["runtime_background_origin_n"] > 0 and c["horizon_contract_error_n"] == 0 and c["time_contract_error_n"] == 0,
        "pristine_f32_dataset_ready": bool(len(rows) >= 30),
    }
    return result


def install_path_runtime() -> None:
    if getattr(_ENGINE, "_measurement_path_runtime", None) == MEASUREMENT_RUNTIME_VERSION:
        return
    _ENGINE._resolve_one = resolve_one_f32a
    _ENGINE._resolved_rows = resolved_rows_f32a
    _ENGINE.status = status_f32a
    _ENGINE.calibration_report = calibration_report_f32a
    _ENGINE._measurement_path_runtime = MEASUREMENT_RUNTIME_VERSION
