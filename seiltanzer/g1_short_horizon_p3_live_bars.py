"""Raw Yahoo 1m capture and exact completed-5m aggregation for P3L."""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any

from .config import INSTRUMENTS
from .g1_short_horizon_historical_wf import _json, _sha
from .g1_short_horizon_p3_live_schema import (
    P3L_BAR5_VERSION,
    P3L_BAR_COMPLETION_GRACE_SEC,
    P3L_RAW_BAR_VERSION,
    ensure_p3l_tables,
)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def ingest_p3l_raw_1m(runtime, passive, *, now: float | None = None) -> int:
    """Freeze completed raw bars already fetched by passive MarketData feeds."""
    ensure_p3l_tables(runtime)
    now = float(now or time.time())
    written = 0
    feeds = getattr(passive, "_feeds", {}) or {}
    for instrument, feed in list(feeds.items()):
        if instrument not in INSTRUMENTS:
            continue
        raw = getattr(feed, "intraday_ohlcv_raw", None) or []
        fetched_ts = _finite(getattr(feed, "intraday_raw_fetched_ts", None))
        source = str(getattr(feed, "intraday_raw_source", "") or "")
        if not raw or fetched_ts is None or not source.startswith("yfinance "):
            continue
        ticker = INSTRUMENTS[instrument].yahoo
        for item in raw:
            try:
                bar_start, open_p, high, low, close, volume = item
                bar_start = float(bar_start); bar_end = bar_start+60.0
                prices = [float(open_p), float(high), float(low), float(close)]
            except (TypeError, ValueError):
                continue
            if bar_end > now-P3L_BAR_COMPLETION_GRACE_SEC:
                continue
            if not all(math.isfinite(value) and value > 0 for value in prices):
                continue
            volume_value = _finite(volume)
            payload = {
                "contract_version": P3L_RAW_BAR_VERSION,
                "instrument": instrument, "yahoo_ticker": ticker,
                "bar_start_ts": bar_start, "bar_end_ts": bar_end,
                "open": prices[0], "high": prices[1], "low": prices[2],
                "close": prices[3], "volume": volume_value,
                "source": source, "source_fetched_ts": fetched_ts,
            }
            raw_json = _json(payload)
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_volatility_raw_1m_bars("
                    "instrument,bar_start_ts,bar_end_ts,open,high,low,close,volume,"
                    "yahoo_ticker,source,source_fetched_ts,contract_version,row_sha256,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (instrument, bar_start, bar_end, *prices, volume_value, ticker,
                     source, fetched_ts, P3L_RAW_BAR_VERSION, _sha(raw_json), time.time()))
            written += int(cur.rowcount > 0)
    return written


def _aggregate_group(bucket_start: float, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    ordered = sorted(rows, key=lambda row: float(row["bar_start_ts"]))
    expected = [float(bucket_start)+60.0*i for i in range(5)]
    starts = [float(row["bar_start_ts"]) for row in ordered]
    if len(ordered) != 5 or any(abs(a-b) > 1e-6 for a,b in zip(starts, expected)):
        return None
    volume_values = [_finite(row.get("volume")) for row in ordered]
    volume = (sum(value for value in volume_values if value is not None)
              if any(value is not None for value in volume_values) else None)
    source_rows = [{
        "bar_start_ts": float(row["bar_start_ts"]),
        "row_sha256": str(row["row_sha256"]),
    } for row in ordered]
    return {
        "bar_start_ts": float(bucket_start),
        "bar_end_ts": float(bucket_start)+300.0,
        "open": float(ordered[0]["open"]),
        "high": max(float(row["high"]) for row in ordered),
        "low": min(float(row["low"]) for row in ordered),
        "close": float(ordered[-1]["close"]),
        "volume": volume,
        "source_1m_count": 5,
        "source_rows_sha256": _sha(_json(source_rows)),
    }


def aggregate_p3l_5m(runtime, *, now: float | None = None) -> int:
    """Create a 5m row only from five exact consecutive frozen raw 1m rows."""
    ensure_p3l_tables(runtime)
    now = float(now or time.time())
    cutoff = now-P3L_BAR_COMPLETION_GRACE_SEC
    written = 0
    for instrument in INSTRUMENTS:
        with runtime._lock:
            rows = [dict(row) for row in runtime._conn.execute(
                "SELECT * FROM g1s_volatility_raw_1m_bars WHERE instrument=? "
                "AND bar_end_ts>=? AND bar_end_ts<=? ORDER BY bar_start_ts",
                (instrument, now-3*86400.0, cutoff)).fetchall()]
        grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            bucket = math.floor(float(row["bar_start_ts"])/300.0)*300.0
            grouped[bucket].append(row)
        for bucket, members in grouped.items():
            if bucket+300.0 > cutoff:
                continue
            bar = _aggregate_group(bucket, members)
            if bar is None:
                continue
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_volatility_5m_bars("
                    "instrument,bar_start_ts,bar_end_ts,open,high,low,close,volume,"
                    "source_1m_count,source_rows_sha256,contract_version,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (instrument, bar["bar_start_ts"], bar["bar_end_ts"],
                     bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"],
                     5, bar["source_rows_sha256"], P3L_BAR5_VERSION, time.time()))
            written += int(cur.rowcount > 0)
    return written


def p3l_live_sources(runtime, *, now: float | None = None) -> list[dict[str, Any]]:
    """Small in-memory tail used only to construct current causal T0 context."""
    ensure_p3l_tables(runtime)
    now = float(now or time.time())
    sources = []
    for instrument in INSTRUMENTS:
        with runtime._lock:
            rows = [dict(row) for row in runtime._conn.execute(
                "SELECT bar_start_ts,bar_end_ts,open,high,low,close,volume "
                "FROM g1s_volatility_5m_bars WHERE instrument=? AND bar_end_ts>=? "
                "ORDER BY bar_start_ts",
                (instrument, now-3*86400.0)).fetchall()]
        if rows:
            sources.append({
                "instrument": instrument,
                "ticker": INSTRUMENTS[instrument].yahoo,
                "source_id": f"live-5m-{instrument}",
                "bars": rows,
            })
    return sources
