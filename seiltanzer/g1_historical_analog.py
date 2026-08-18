"""Causal nearest-neighbour analogs for frozen G.1S observations.

The engine is deliberately read-only and research-only.  It reuses the current
G.1S V2 feature contract, compares only same-instrument/same-horizon observations,
and only exposes outcomes that were already resolved by the simulated T0.
It has no production decision authority and performs no network or LLM work.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import median
from typing import Any

import numpy as np

from .g1_short_horizon_feature_contract_v2 import (
    FEATURE_CONTRACT_V2,
    V2_FEATURE_SETS,
    _has_v2,
)


ANALOG_CONTRACT_VERSION = "g1s-historical-analog-v1"
DEFAULT_FEATURE_SET = "FULL_V2"
DEFAULT_K = 20
MIN_ANALOGS = 8
MIN_FEATURE_OVERLAP = 0.70
MAX_CANDIDATES = 1500
MAX_K = 50


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _row_values(runtime, row: dict[str, Any], feature_set: str) -> dict[str, float | None]:
    """Use the exact runtime adapter; never reinterpret frozen T0 bytes here."""
    _vector, values = runtime._feature_vector(row, feature_set)
    return {name: _finite(values.get(name)) for name in V2_FEATURE_SETS[feature_set]}


def _robust_scales(rows: list[dict[str, float | None]], names: tuple[str, ...]) -> dict[str, float]:
    scales: dict[str, float] = {}
    for name in names:
        vals = np.asarray([row[name] for row in rows if row.get(name) is not None], dtype=float)
        if len(vals) < 2:
            scales[name] = 1.0
            continue
        centre = float(np.median(vals))
        mad = float(np.median(np.abs(vals-centre))) * 1.4826
        if mad > 1e-12:
            scales[name] = mad
            continue
        q75, q25 = np.percentile(vals, [75.0, 25.0])
        iqr_scale = float(q75-q25) / 1.349
        if iqr_scale > 1e-12:
            scales[name] = iqr_scale
            continue
        std = float(np.std(vals))
        scales[name] = std if std > 1e-12 else 1.0
    return scales


def _distance(current: dict[str, float | None], candidate: dict[str, float | None],
              names: tuple[str, ...], scales: dict[str, float]) -> tuple[float | None, float]:
    common = [name for name in names
              if current.get(name) is not None and candidate.get(name) is not None]
    overlap = len(common) / max(1, len(names))
    if overlap < MIN_FEATURE_OVERLAP or not common:
        return None, overlap
    squared = []
    for name in common:
        scale = max(float(scales.get(name, 1.0)), 1e-12)
        z = (float(current[name])-float(candidate[name]))/scale
        # A single pathological field must not dominate the whole similarity score.
        z = max(-8.0, min(8.0, z))
        squared.append(z*z)
    return math.sqrt(sum(squared)/len(squared)), overlap


def _summary_number(values: list[float], fn) -> float | None:
    return float(fn(values)) if values else None


def _top_differences(current: dict[str, float | None], analog_values: list[dict[str, float | None]],
                     names: tuple[str, ...], scales: dict[str, float], limit: int = 8) -> list[dict]:
    items = []
    for name in names:
        cur = current.get(name)
        vals = [float(row[name]) for row in analog_values if row.get(name) is not None]
        if cur is None or not vals:
            continue
        med = float(median(vals))
        scale = max(float(scales.get(name, 1.0)), 1e-12)
        z = abs(float(cur)-med)/scale
        items.append({
            "feature_id": name,
            "current": float(cur),
            "analog_median": med,
            "robust_abs_z": float(z),
            "analog_available_n": len(vals),
        })
    items.sort(key=lambda item: (-item["robust_abs_z"], item["feature_id"]))
    return items[:max(0, int(limit))]


def _unavailable(reason: str, **extra) -> dict[str, Any]:
    return {
        "contract_version": ANALOG_CONTRACT_VERSION,
        "status": "UNAVAILABLE" if reason not in {"INSUFFICIENT_ANALOGS", "INSUFFICIENT_FEATURES"}
        else reason,
        "reason": reason,
        "research_only": True,
        "production_authority": False,
        "may_change_position_manager": False,
        **extra,
    }


def historical_analogs(runtime, observation_id: str, *, k: int = DEFAULT_K,
                       feature_set: str = DEFAULT_FEATURE_SET) -> dict[str, Any]:
    """Return causal nearest historical analogs for one already-frozen T0.

    Historical simulation integrity is stricter than simply requiring an older
    capture: an analog's resolution must have existed by the current T0.  This
    prevents future outcomes leaking into backdated analog reports.
    """
    if feature_set not in V2_FEATURE_SETS:
        return _unavailable("UNKNOWN_FEATURE_SET", feature_set=feature_set)
    k = max(1, min(int(k), MAX_K))
    try:
        with runtime._lock:
            current_row = runtime._conn.execute(
                "SELECT * FROM g1s_observations WHERE observation_id=? LIMIT 1",
                (str(observation_id),),
            ).fetchone()
    except Exception as exc:  # fail soft: this route must never affect trading core
        return _unavailable("STORAGE_ERROR", detail=type(exc).__name__)
    if current_row is None:
        return _unavailable("OBSERVATION_NOT_FOUND", observation_id=str(observation_id))
    current = dict(current_row)
    if not _has_v2(current):
        return _unavailable(
            "FEATURE_CONTRACT_UNAVAILABLE",
            observation_id=str(observation_id),
            required_feature_contract=FEATURE_CONTRACT_V2,
        )

    captured_ts = float(current["captured_ts"])
    instrument = str(current["instrument"])
    horizon = int(current["horizon_minutes"])
    names = tuple(V2_FEATURE_SETS[feature_set])
    current_values = _row_values(runtime, current, feature_set)
    current_coverage = sum(value is not None for value in current_values.values()) / max(1, len(names))
    if current_coverage < MIN_FEATURE_OVERLAP:
        return _unavailable(
            "INSUFFICIENT_FEATURES", observation_id=str(observation_id), instrument=instrument,
            horizon_minutes=horizon, feature_set=feature_set,
            feature_coverage=float(current_coverage), minimum_feature_overlap=MIN_FEATURE_OVERLAP,
        )

    try:
        with runtime._lock:
            source_rows = runtime._conn.execute("""
                SELECT g.*,r.terminal_log_return,r.direction_label,r.mfe_log_return,
                       r.mae_log_return,r.resolved_ts
                FROM g1s_observations g
                JOIN g1s_resolutions r USING(observation_id)
                WHERE g.instrument=? AND g.horizon_minutes=?
                  AND g.training_eligible=1
                  AND g.captured_ts<?
                  AND r.resolved_ts<=?
                ORDER BY g.captured_ts DESC,g.observation_id DESC
                LIMIT ?
            """, (instrument, horizon, captured_ts, captured_ts, MAX_CANDIDATES)).fetchall()
    except Exception as exc:
        return _unavailable("STORAGE_ERROR", detail=type(exc).__name__)

    candidates: list[dict[str, Any]] = []
    candidate_values: list[dict[str, float | None]] = []
    for source in source_rows:
        row = dict(source)
        if not _has_v2(row):
            continue
        values = _row_values(runtime, row, feature_set)
        candidates.append(row)
        candidate_values.append(values)

    if len(candidates) < MIN_ANALOGS:
        return _unavailable(
            "INSUFFICIENT_ANALOGS", observation_id=str(observation_id), instrument=instrument,
            horizon_minutes=horizon, feature_set=feature_set,
            admissible_candidate_n=len(candidates), required_analog_n=MIN_ANALOGS,
            causal_resolution_cutoff_ts=captured_ts,
        )

    scales = _robust_scales(candidate_values, names)
    ranked = []
    for row, values in zip(candidates, candidate_values):
        distance, overlap = _distance(current_values, values, names, scales)
        if distance is None:
            continue
        ranked.append((float(distance), -float(overlap), float(row["captured_ts"]),
                       str(row["observation_id"]), row, values, float(overlap)))
    ranked.sort(key=lambda item: item[:4])
    selected = ranked[:k]
    if len(selected) < MIN_ANALOGS:
        return _unavailable(
            "INSUFFICIENT_ANALOGS", observation_id=str(observation_id), instrument=instrument,
            horizon_minutes=horizon, feature_set=feature_set,
            admissible_candidate_n=len(candidates), distance_eligible_n=len(ranked),
            required_analog_n=MIN_ANALOGS, minimum_feature_overlap=MIN_FEATURE_OVERLAP,
            causal_resolution_cutoff_ts=captured_ts,
        )

    analog_rows = [item[4] for item in selected]
    analog_values = [item[5] for item in selected]
    distances = [item[0] for item in selected]
    overlaps = [item[6] for item in selected]
    returns = [float(row["terminal_log_return"]) for row in analog_rows
               if _finite(row.get("terminal_log_return")) is not None]
    mfes = [float(row["mfe_log_return"]) for row in analog_rows
            if _finite(row.get("mfe_log_return")) is not None]
    maes = [float(row["mae_log_return"]) for row in analog_rows
            if _finite(row.get("mae_log_return")) is not None]
    up_n = sum(str(row.get("direction_label")) == "UP" for row in analog_rows)
    down_n = sum(str(row.get("direction_label")) == "DOWN" for row in analog_rows)
    flat_n = sum(str(row.get("direction_label")) == "FLAT" for row in analog_rows)
    nonflat = up_n+down_n

    compact = []
    for item in selected:
        row = item[4]
        compact.append({
            "observation_id": str(row["observation_id"]),
            "captured_ts": float(row["captured_ts"]),
            "resolved_ts": float(row["resolved_ts"]),
            "distance": item[0],
            "feature_overlap": item[6],
            "direction_label": str(row["direction_label"]),
            "terminal_log_return": _finite(row.get("terminal_log_return")),
            "mfe_log_return": _finite(row.get("mfe_log_return")),
            "mae_log_return": _finite(row.get("mae_log_return")),
        })
    analog_set_sha256 = hashlib.sha256(
        json.dumps([item["observation_id"] for item in compact], separators=(",", ":")).encode()
    ).hexdigest()

    return {
        "contract_version": ANALOG_CONTRACT_VERSION,
        "status": "OK",
        "observation_id": str(observation_id),
        "instrument": instrument,
        "horizon_minutes": horizon,
        "captured_ts": captured_ts,
        "feature_contract_version": FEATURE_CONTRACT_V2,
        "feature_set": feature_set,
        "expected_feature_n": len(names),
        "current_feature_coverage": float(current_coverage),
        "minimum_feature_overlap": MIN_FEATURE_OVERLAP,
        "candidate_scan_limit": MAX_CANDIDATES,
        "admissible_candidate_n": len(candidates),
        "analog_n": len(compact),
        "requested_k": k,
        "median_distance": _summary_number(distances, median),
        "median_feature_overlap": _summary_number(overlaps, median),
        "up_n": up_n,
        "down_n": down_n,
        "flat_n": flat_n,
        "positive_rate_nonflat": (up_n/nonflat) if nonflat else None,
        "mean_terminal_log_return": _summary_number(returns, np.mean),
        "median_terminal_log_return": _summary_number(returns, median),
        "terminal_log_return_std": _summary_number(returns, np.std),
        "median_mfe_log_return": _summary_number(mfes, median),
        "median_mae_log_return": _summary_number(maes, median),
        "top_feature_differences": _top_differences(
            current_values, analog_values, names, scales),
        "analogs": compact,
        "analog_set_sha256": analog_set_sha256,
        "causal_rules": {
            "same_instrument_only": True,
            "same_horizon_only": True,
            "candidate_capture_strictly_before_current_t0": True,
            "candidate_outcome_resolved_by_current_t0": True,
            "future_feature_backfill": False,
            "llm_selects_analogs": False,
        },
        "research_only": True,
        "production_authority": False,
        "may_change_position_manager": False,
        "may_change_cvar_stop_or_size": False,
    }
