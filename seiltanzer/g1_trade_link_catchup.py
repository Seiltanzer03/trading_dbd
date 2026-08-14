"""Bounded catch-up for immutable G1S trade relevance links.

This preserves the existing link payload/eligibility semantics while selecting only
a small batch of trades that still have at least one eligible horizon without a
materialized link. It is scheduling/infrastructure only; no model or edge math.
"""
from __future__ import annotations

import time

from .g1_short_horizon_runtime import (
    G1S_TRADE_RELEVANCE_VERSION,
    HORIZONS,
    TRADE_LINK_MAX_AGE_SEC,
    _json,
    _sha_text,
)


def materialize_trade_links_bounded(runtime, *, limit: int = 50) -> dict:
    batch_limit = max(1, int(limit))
    placeholders = ",".join("?" for _ in HORIZONS)
    sql = f"""
        SELECT t.id,t.opened_at,t.instrument,t.direction,t.setup,t.result_r,t.status
        FROM trades t
        WHERE EXISTS (
            SELECT 1
            FROM g1s_observations o
            WHERE o.instrument=t.instrument
              AND o.horizon_minutes IN ({placeholders})
              AND o.measurement_eligible=1
              AND o.captured_ts<=t.opened_at
              AND o.captured_ts>=t.opened_at-?
              AND NOT EXISTS (
                  SELECT 1 FROM g1s_trade_links l
                  WHERE l.trade_id=t.id
                    AND l.horizon_minutes=o.horizon_minutes
              )
        )
        ORDER BY t.opened_at,t.id
        LIMIT ?
    """
    with runtime._lock:
        trades = runtime._conn.execute(
            sql, (*HORIZONS, float(TRADE_LINK_MAX_AGE_SEC), batch_limit)
        ).fetchall()

    created = 0
    for trade in trades:
        for horizon in HORIZONS:
            with runtime._lock:
                already = runtime._conn.execute(
                    "SELECT 1 FROM g1s_trade_links WHERE trade_id=? AND horizon_minutes=? LIMIT 1",
                    (int(trade["id"]), horizon),
                ).fetchone()
                if already is not None:
                    continue
                obs = runtime._conn.execute(
                    """
                    SELECT observation_id,captured_ts FROM g1s_observations
                    WHERE instrument=? AND horizon_minutes=? AND captured_ts<=?
                      AND measurement_eligible=1
                    ORDER BY captured_ts DESC LIMIT 1
                    """,
                    (trade["instrument"], horizon, float(trade["opened_at"])),
                ).fetchone()
            if obs is None:
                continue
            age = float(trade["opened_at"]) - float(obs["captured_ts"])
            if age < -1e-9 or age > TRADE_LINK_MAX_AGE_SEC:
                continue
            payload = {
                "contract_version": G1S_TRADE_RELEVANCE_VERSION,
                "trade_id": int(trade["id"]),
                "observation_id": str(obs["observation_id"]),
                "horizon_minutes": horizon,
                "forecast_age_sec": age,
                "forecast_precedes_entry": True,
                "instrument": trade["instrument"],
                "direction": trade["direction"],
                "setup": trade["setup"],
                "trade_status": trade["status"],
                "trade_result_r": trade["result_r"],
            }
            raw = _json(payload)
            link_id = "g1s-trade-" + _sha_text(raw)[:28]
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_trade_links(link_id,trade_id,observation_id,"
                    "horizon_minutes,forecast_age_sec,link_json,link_sha256,created_ts)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (
                        link_id,
                        int(trade["id"]),
                        obs["observation_id"],
                        horizon,
                        age,
                        raw,
                        _sha_text(raw),
                        time.time(),
                    ),
                )
                created += int(cur.rowcount > 0)
    return {
        "trades_scanned": len(trades),
        "links_created": created,
        "batch_limit": batch_limit,
    }
