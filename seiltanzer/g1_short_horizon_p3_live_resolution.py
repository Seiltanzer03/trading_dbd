"""Prospective target resolution for P3L volatility evidence."""
from __future__ import annotations

import math
import statistics
import time
from typing import Any

from .g1_short_horizon_historical_wf import _json, _sha
from . import g1_short_horizon_p3_path_geometry as _p3
from .g1_short_horizon_p3_live_schema import (
    P3L_RESOLUTION_GRACE_SEC,
    P3L_TARGET_VERSION,
    ensure_p3l_tables,
)


def _exact_future_5m(runtime, instrument: str, captured_ts: float,
                     target_ts: float) -> list[dict[str, Any]] | None:
    count = int(round((target_ts-captured_ts)/300.0))
    expected = [captured_ts+300.0*i for i in range(1, count+1)]
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute(
            "SELECT * FROM g1s_volatility_5m_bars WHERE instrument=? "
            "AND bar_end_ts>? AND bar_end_ts<=? ORDER BY bar_end_ts",
            (instrument, captured_ts+1e-6, target_ts+1e-6)).fetchall()]
    by_end = {float(row["bar_end_ts"]): row for row in rows}
    if any(ts not in by_end for ts in expected):
        return None
    return [by_end[ts] for ts in expected]


def _secondary_1m(runtime, instrument: str, captured_ts: float,
                  target_ts: float, t0_close: float) -> tuple[float | None, int]:
    expected_n = int(round((target_ts-captured_ts)/60.0))
    expected_starts = [captured_ts+60.0*i for i in range(expected_n)]
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute(
            "SELECT bar_start_ts,bar_end_ts,close FROM g1s_volatility_raw_1m_bars "
            "WHERE instrument=? AND bar_start_ts>=? AND bar_end_ts<=? ORDER BY bar_start_ts",
            (instrument, captured_ts-1e-6, target_ts+1e-6)).fetchall()]
    by_start = {float(row["bar_start_ts"]): row for row in rows}
    if any(ts not in by_start for ts in expected_starts):
        return None, len(rows)
    closes = [float(t0_close)] + [float(by_start[ts]["close"]) for ts in expected_starts]
    if any(price <= 0 for price in closes):
        return None, 0
    returns = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
    if len(returns) < 2:
        return None, len(returns)
    return float(statistics.pstdev(returns)), len(returns)


def resolve_p3l_due(runtime, *, now: float | None = None,
                    limit: int = 500) -> int:
    ensure_p3l_tables(runtime)
    now = float(now or time.time())
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute("""
            SELECT o.* FROM g1s_volatility_observations o
            LEFT JOIN g1s_volatility_resolutions r USING(observation_id)
            WHERE r.observation_id IS NULL AND o.target_ts<=?
            ORDER BY o.target_ts LIMIT ?
        """, (now, max(1, min(int(limit), 2000)))).fetchall()]
    written = 0
    for row in rows:
        captured = float(row["captured_ts"]); target = float(row["target_ts"])
        future = _exact_future_5m(runtime, str(row["instrument"]), captured, target)
        if future is None:
            if now <= target+P3L_RESOLUTION_GRACE_SEC:
                continue
            payload = {
                "contract_version": P3L_TARGET_VERSION,
                "observation_id": str(row["observation_id"]),
                "resolution_status": "INSUFFICIENT_FUTURE_5M",
                "target": _p3.TARGET_FUTURE_RV,
                "future_data_used_after_t0_only": True,
                "primary_evidence_frequency": "5m",
                "production_authority": False,
            }
            target_raw = _json(payload)
            values = (
                str(row["observation_id"]), "INSUFFICIENT_FUTURE_5M",
                None, None, None, None, target_raw, _sha(target_raw),
                P3L_TARGET_VERSION, now, time.time())
        else:
            closes = [float(row["t0_close"])] + [float(bar["close"]) for bar in future]
            if any(price <= 0 for price in closes):
                continue
            returns = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
            if len(returns) < 2:
                continue
            future_rv5 = float(statistics.pstdev(returns))
            secondary, one_minute_n = _secondary_1m(
                runtime, str(row["instrument"]), captured, target,
                float(row["t0_close"]))
            payload = {
                "contract_version": P3L_TARGET_VERSION,
                "observation_id": str(row["observation_id"]),
                "resolution_status": "RESOLVED",
                "target": _p3.TARGET_FUTURE_RV,
                "future_realized_volatility_5m": future_rv5,
                "future_realized_volatility_1m_secondary": secondary,
                "future_5m_steps": len(returns),
                "future_1m_steps": one_minute_n,
                "source": "frozen_raw_yahoo_1m_aggregated_to_exact_5m",
                "future_data_used_after_t0_only": True,
                "primary_evidence_frequency": "5m",
                "secondary_1m_not_used_for_primary_edge": True,
                "production_authority": False,
            }
            target_raw = _json(payload)
            values = (
                str(row["observation_id"]), "RESOLVED", future_rv5,
                secondary, len(returns), one_minute_n, target_raw,
                _sha(target_raw), P3L_TARGET_VERSION, now, time.time())
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_volatility_resolutions("
                "observation_id,resolution_status,future_realized_volatility_5m,"
                "future_realized_volatility_1m_secondary,future_5m_steps,future_1m_steps,"
                "target_json,target_sha256,contract_version,resolved_ts,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)", values)
        written += int(cur.rowcount > 0)
    return written
