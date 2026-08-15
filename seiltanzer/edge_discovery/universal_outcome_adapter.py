"""Adapters from existing immutable G1S evidence to universal market outcomes.

No new market data is invented here. Historical outcomes use the immutable bar
source already consumed by the historical walk-forward. Prospective outcomes use
only bars recorded in SQLite no later than the existing resolution timestamp.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Any

from .universal_outcomes import resolve_universal_market_outcome


UNIVERSAL_OUTCOME_ADAPTER_VERSION = "g1s-universal-outcome-adapter-v1"


def resolve_historical_universal_outcome(
    source: dict[str, Any], row: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one historical WF row from its immutable OHLC source."""
    t0 = float(row["captured_ts"])
    target = float(row["target_ts"])
    bars = [
        bar for bar in source.get("bars") or []
        if float(bar["bar_end_ts"]) > t0 + 1e-9
        and float(bar["bar_end_ts"]) <= target + 1e-6
    ]
    result = resolve_universal_market_outcome(
        start_price=_historical_t0_close(source, t0),
        captured_ts=t0,
        target_ts=target,
        horizon_minutes=int(row["horizon_minutes"]),
        t0_realized_vol_60m=(row.get("features") or {}).get("realized_vol_60m"),
        bars=bars,
        path_complete=bool(bars and float(bars[-1]["bar_end_ts"]) >= target - 1e-6),
    )
    result["adapter_version"] = UNIVERSAL_OUTCOME_ADAPTER_VERSION
    result["evidence_source"] = "IMMUTABLE_HISTORICAL_WF_OHLC"
    return result


def _historical_t0_close(source: dict[str, Any], t0: float) -> float | None:
    bars = source.get("bars") or []
    ends = [float(bar["bar_end_ts"]) for bar in bars]
    index = bisect.bisect_right(ends, t0 + 1e-6) - 1
    if index < 0 or abs(ends[index]-t0) > 5*60.0 + 1e-6:
        return None
    return float(bars[index]["close"])


class ProspectiveUniversalOutcomeAdapter:
    """Attach clean strategy-agnostic outcomes to prospective EDE rows."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._bars = self._load_bars()

    def _load_bars(self) -> dict[str, list[dict[str, Any]]]:
        with self.runtime._lock:
            tables = {str(row[0]) for row in self.runtime._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "passive_market_bars" not in tables:
                return {}
            columns = {str(row[1]) for row in self.runtime._conn.execute(
                "PRAGMA table_info(passive_market_bars)").fetchall()}
            required = {
                "instrument", "bar_start_ts", "bar_end_ts",
                "high", "low", "close",
            }
            if not required <= columns:
                return {}
            created = "created_ts" if "created_ts" in columns else "bar_end_ts AS created_ts"
            rows = self.runtime._conn.execute(
                "SELECT instrument,bar_start_ts,bar_end_ts,high,low,close," + created + " "
                "FROM passive_market_bars ORDER BY instrument,bar_start_ts"
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["instrument"])].append(dict(row))
        return dict(grouped)

    def _resolution_context(self, observation_id: str) -> dict[str, Any]:
        with self.runtime._lock:
            row = self.runtime._conn.execute(
                "SELECT g.market_price,r.resolved_ts,r.path_quality_status "
                "FROM g1s_observations g LEFT JOIN g1s_resolutions r USING(observation_id) "
                "WHERE g.observation_id=?",
                (str(observation_id),),
            ).fetchone()
        if row is None:
            return {}
        return {
            "market_price": float(row["market_price"]),
            "resolved_ts": (float(row["resolved_ts"]) if row["resolved_ts"] is not None else None),
            "path_quality_status": row["path_quality_status"],
        }

    def _bars_for(self, row: dict[str, Any], resolved_ts: float | None) -> list[dict[str, Any]]:
        t0 = float(row["captured_ts"])
        target = float(row["target_ts"])
        if resolved_ts is None:
            return []
        return [
            bar for bar in self._bars.get(str(row["instrument"]), [])
            if float(bar["bar_start_ts"]) >= t0 - 1e-6
            and float(bar["bar_end_ts"]) <= target + 1e-6
            and float(bar.get("created_ts") or bar["bar_end_ts"]) <= resolved_ts + 1e-6
        ]

    def attach(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for source in rows:
            row = dict(source)
            if not bool(row.get("outcome_available")):
                row["universal_outcome"] = None
                row["universal_outcome_reason"] = "OUTCOME_NOT_RESOLVED"
                output.append(row)
                continue
            context = self._resolution_context(str(row["observation_id"]))
            rv60 = (row.get("ede_features") or {}).get("vol.rv_60m")
            bars = self._bars_for(row, context.get("resolved_ts"))
            path_quality = str(context.get("path_quality_status") or "").lower()
            reaches_target = bool(
                bars and float(bars[-1]["bar_end_ts"]) >= float(row["target_ts"]) - 1e-6
            )
            path_complete = path_quality == "complete" and reaches_target
            result = resolve_universal_market_outcome(
                start_price=context.get("market_price"),
                captured_ts=float(row["captured_ts"]),
                target_ts=float(row["target_ts"]),
                horizon_minutes=int(row["horizon_minutes"]),
                t0_realized_vol_60m=rv60,
                bars=bars,
                path_complete=path_complete,
            )
            result["adapter_version"] = UNIVERSAL_OUTCOME_ADAPTER_VERSION
            result["evidence_source"] = "RECORDED_PROSPECTIVE_OHLC_AT_RESOLUTION"
            result["source_path_quality_status"] = context.get("path_quality_status")
            result["retained_path_reaches_target"] = reaches_target
            result["bars_created_no_later_than_resolution"] = True
            row["universal_outcome"] = result
            row["universal_outcome_reason"] = None if result.get("available") else result.get("reason")
            output.append(row)
        return output
