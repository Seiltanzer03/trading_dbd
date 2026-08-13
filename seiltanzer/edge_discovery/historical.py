"""Immutable P1B adapter for the first EDE discovery audit."""
from __future__ import annotations

import json
import math
import bisect
from collections import defaultdict
from typing import Any

from seiltanzer.config import INSTRUMENTS
from seiltanzer.g1_short_horizon_historical_wf import (
    _build_horizon_rows,
    _load_source_bars,
)
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import (
    ASSET_FAMILY_BY_INSTRUMENT,
    _source_context,
    session_utc,
)


PEER_BY_INSTRUMENT = {
    "NAS100": "SP500", "SP500": "NAS100", "US30": "SP500",
    "GER40": "UK100", "UK100": "GER40", "JPY100": "NAS100",
    "XAU": "XAG", "XAG": "XAU", "EURUSD": "USDCAD", "USDCAD": "EURUSD",
}
CROSS_MAX_STALENESS_SECONDS = 20 * 60.0
CROSS_CORRELATION_WINDOW = 20
CROSS_CORRELATION_MIN_POINTS = 8


def load_p1b_sources(runtime: Any) -> tuple[list[dict[str, Any]], str]:
    """Read the current COMPLETE immutable source set without writing the DB."""
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT state,source_set_sha256 FROM g1s_historical_wf_state WHERE id=1"
        ).fetchone()
    if state is None or str(state["state"]) != "COMPLETE" or not state["source_set_sha256"]:
        raise RuntimeError("P1B historical source set is not COMPLETE")
    source_set = str(state["source_set_sha256"])
    with runtime._lock:
        run = runtime._conn.execute(
            "SELECT artifact_json FROM g1s_historical_wf_runs WHERE source_set_sha256=? "
            "ORDER BY created_ts LIMIT 1", (source_set,)
        ).fetchone()
    if run is None:
        raise RuntimeError("P1B current run artifact unavailable")
    artifact = json.loads(str(run["artifact_json"]))
    source_ids = [str(item["source_id"]) for item in artifact.get("source_summary") or []]
    if len(source_ids) != len(INSTRUMENTS):
        raise RuntimeError(f"expected {len(INSTRUMENTS)} source ids, got {len(source_ids)}")
    placeholders = ",".join("?" for _ in source_ids)
    with runtime._lock:
        rows = runtime._conn.execute(
            f"SELECT * FROM g1s_historical_sources WHERE source_id IN ({placeholders})",
            tuple(source_ids),
        ).fetchall()
    by_id = {str(row["source_id"]): dict(row) for row in rows}
    sources: list[dict[str, Any]] = []
    for source_id in source_ids:
        item = by_id.get(source_id)
        if item is None:
            raise RuntimeError(f"missing immutable source {source_id}")
        item["bars"] = _load_source_bars(item)
        sources.append(item)
    return sources, source_set


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < CROSS_CORRELATION_MIN_POINTS:
        return None
    xs = [item[0] for item in pairs[-CROSS_CORRELATION_WINDOW:]]
    ys = [item[1] for item in pairs[-CROSS_CORRELATION_WINDOW:]]
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    xx = sum((value-mx)**2 for value in xs)
    yy = sum((value-my)**2 for value in ys)
    if xx <= 1e-18 or yy <= 1e-18:
        return None
    return max(-1.0, min(1.0, sum(
        (x-mx)*(y-my) for x, y in zip(xs, ys))/math.sqrt(xx*yy)))


def aligned_cross_asset_context(
        rows: list[dict[str, Any]], *,
        max_staleness_seconds: float = CROSS_MAX_STALENESS_SECONDS) -> None:
    """Attach strictly external, nearest-causal peer context.

    Breadth is leave-one-out: an instrument can never confirm itself through a
    market or family aggregate.  A sequential snapshot is admitted only when
    ``peer_asof <= T0`` and its age is within the predeclared staleness limit.
    Metadata is frozen alongside the value so missing evidence cannot later be
    mistaken for neutral market evidence.
    """
    ordered = sorted(rows, key=lambda row: (
        float(row["captured_ts"]), str(row["instrument"])))
    by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    times: dict[str, list[float]] = defaultdict(list)
    for row in ordered:
        instrument = str(row["instrument"])
        by_instrument[instrument].append(row)
        times[instrument].append(float(row["captured_ts"]))

    def causal(other: str, t0: float) -> tuple[dict[str, Any] | None, float | None]:
        index = bisect.bisect_right(times.get(other, []), t0+1e-6)-1
        if index < 0:
            return None, None
        candidate = by_instrument[other][index]
        asof = float(candidate["captured_ts"])
        age = max(0.0, t0-asof)
        if asof > t0+1e-6 or age > float(max_staleness_seconds):
            return None, age
        try:
            value = float((candidate.get("features") or {}).get("ret_5m"))
        except (TypeError, ValueError):
            return None, age
        return (candidate, age) if math.isfinite(value) else (None, age)

    correlation_history: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    previous_correlation: dict[tuple[str, str], float] = {}
    instruments = sorted(by_instrument)
    for row in ordered:
        instrument = str(row["instrument"])
        t0 = float(row["captured_ts"])
        try:
            own_return = float((row.get("features") or {}).get("ret_5m"))
        except (TypeError, ValueError):
            own_return = float("nan")
        own = _sign(own_return) if math.isfinite(own_return) else 0
        peer_instrument = PEER_BY_INSTRUMENT.get(instrument)
        peer_row, peer_age = causal(peer_instrument, t0) if peer_instrument else (None, None)
        peer_return = None
        peer_asof = None
        if peer_row is not None:
            peer_return = float((peer_row.get("features") or {}).get("ret_5m"))
            peer_asof = float(peer_row["captured_ts"])
        peer_sign = _sign(peer_return) if peer_return is not None else None
        if not own or peer_sign is None or not peer_sign:
                confirmation = "NEUTRAL"
        else:
            confirmation = "SAME" if own == peer_sign else "OPPOSITE"

        external: list[tuple[str, float, float, float]] = []
        for other in instruments:
            if other == instrument:
                continue
            other_row, age = causal(other, t0)
            if other_row is None or age is None:
                continue
            other_return = float((other_row.get("features") or {}).get("ret_5m"))
            external.append((other, other_return, float(other_row["captured_ts"]), age))
        family = ASSET_FAMILY_BY_INSTRUMENT.get(instrument, "UNKNOWN")
        family_values = [value for other, value, _asof, _age in external
                         if ASSET_FAMILY_BY_INSTRUMENT.get(other, "UNKNOWN") == family]
        market_values = [value for _other, value, _asof, _age in external]
        family_breadth = (sum(value > 0 for value in family_values)/len(family_values)
                          if family_values else None)
        market_breadth = (sum(value > 0 for value in market_values)/len(market_values)
                          if market_values else None)

        correlation = None
        correlation_change = None
        if peer_instrument and peer_return is not None and math.isfinite(own_return):
            pair_key = (instrument, peer_instrument)
            correlation_history[pair_key].append((own_return, peer_return))
            correlation = _pearson(correlation_history[pair_key])
            if correlation is not None and pair_key in previous_correlation:
                correlation_change = correlation-previous_correlation[pair_key]
            if correlation is not None:
                previous_correlation[pair_key] = correlation

        eligible_family = sum(
            other != instrument
            and ASSET_FAMILY_BY_INSTRUMENT.get(other, "UNKNOWN") == family
            for other in instruments)
        metadata = {
            "peer_instrument": peer_instrument,
            "peer_asof": peer_asof,
            "peer_age_sec": peer_age,
            "peer_count": int(peer_row is not None),
            "coverage": len(external)/max(1, len(instruments)-1),
            "stale": bool(peer_row is None and peer_age is not None
                          and peer_age > float(max_staleness_seconds)),
            "max_staleness_sec": float(max_staleness_seconds),
            "future_peer_used": False,
            "external_instruments": [item[0] for item in external],
            "external_asof": {item[0]: item[2] for item in external},
            "external_age_sec": {item[0]: item[3] for item in external},
        }
        row["ede_features"].update({
            "cross_confirmation": confirmation,
            "family_breadth": family_breadth,
            "market_breadth": market_breadth,
            "cross_correlation": correlation,
            "cross_correlation_change": correlation_change,
            "cross_peer_count": int(peer_row is not None),
            "family_breadth_peer_count": len(family_values),
            "market_breadth_peer_count": len(market_values),
            "family_breadth_coverage": len(family_values)/max(1, eligible_family),
            "market_breadth_coverage": len(market_values)/max(1, len(instruments)-1),
            "cross_asof": max((item[2] for item in external), default=None),
            "cross_stale": metadata["stale"],
            "cross_join_metadata": metadata,
        })


def build_discovery_rows(sources: list[dict[str, Any]], horizon: int) -> list[dict[str, Any]]:
    contexts = {str(source["instrument"]): _source_context(source) for source in sources}
    rows: list[dict[str, Any]] = []
    for source in sources:
        instrument = str(source["instrument"])
        context_by_ts = contexts[instrument]
        for row in _build_horizon_rows(source, horizon):
            if row["direction_label"] == "FLAT":
                continue
            context = context_by_ts.get(float(row["captured_ts"]))
            if context is None:
                continue
            item = dict(row)
            current = float(context.get("path_high_60") or 0.0)
            low = float(context.get("path_low_60") or 0.0)
            item["ede_features"] = {
                "asset": instrument,
                "asset_family": ASSET_FAMILY_BY_INSTRUMENT[instrument],
                "session_utc": session_utc(float(row["captured_ts"])),
                "rv15_over_rv60": float(context["rv15_over_rv60"]),
                "trend_efficiency_60": float(context["trend_efficiency_60"]),
                "range_60": (math.log(current/low) if current > 0 and low > 0 else None),
                "cross_confirmation": "NEUTRAL",
                "family_breadth": None,
                "market_breadth": None,
            }
            rows.append(item)
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))
    aligned_cross_asset_context(rows)
    return rows


def option_t0_coverage(runtime: Any) -> dict[str, Any]:
    """Inventory actual immutable T0 option coverage without retrofitting it."""
    with runtime._lock:
        tables = {str(row[0]) for row in runtime._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    if "g1s_observations" not in tables:
        return {"observation_count": 0, "v2_count": 0, "v3_count": 0,
                "option_static_available": 0, "option_dynamics_available": 0,
                "eligible_for_current_p1b_discovery": False,
                "reason": "g1s_observations table unavailable"}
    with runtime._lock:
        rows = runtime._conn.execute(
            "SELECT frozen_features_json FROM g1s_observations ORDER BY captured_ts"
        ).fetchall()
    counts = {"observation_count": len(rows), "v2_count": 0, "v3_count": 0,
              "option_static_available": 0, "option_dynamics_available": 0}
    for row in rows:
        try:
            frozen = json.loads(str(row[0] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        v2 = frozen.get("g1s_evidence_v2") or {}
        v3 = frozen.get("g1s_evidence_v3") or {}
        counts["v2_count"] += int(bool(v2))
        counts["v3_count"] += int(bool(v3))
        static = (v3.get("option_static") or v2.get("option_context") or {})
        dynamics = v3.get("option_dynamics") or {}
        counts["option_static_available"] += int(bool(static.get("available")))
        counts["option_dynamics_available"] += int(bool(dynamics.get("available")))
    counts.update({
        "eligible_for_current_p1b_discovery": False,
        "reason": (
            "future-only immutable T0 captures are inventoried separately; they are not "
            "retrofitted onto the 60d P1B bar source set"
        ),
        "synthetic_option_history_used": False,
    })
    return counts
