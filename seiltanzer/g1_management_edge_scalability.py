"""Bound G1-M local-edge read amplification without changing report semantics.

The production G1-M endpoint used to join large frozen context JSON blobs directly
against policy-outcome rows.  That duplicated each context payload once per policy
(5x in the base report, 4x in active-edge attribution) and also asked SQLite to
sort the expanded rowset.  As the immutable research database grows, the read
amplification can dominate the strict production latency gate.

This installer changes only the report read path: read each resolved window/context
once, read the compact policy rows separately, pivot in memory, and preserve the
same chronological window order.  No evidence eligibility, statistics, maturity,
authority, promotion or execution contract is changed.
"""
from __future__ import annotations

from typing import Any

from .g1_management_local_runtime import ManagementLocalRuntime
from . import g1_management_local_edge_v2 as _local_edge
from . import g1_management_active_edge_attribution as _attribution


SCALABILITY_VERSION = "g1m-local-edge-read-scalability-v1"
_INSTALLED = False


def _pairwise_rows_bounded_io(runtime: ManagementLocalRuntime) -> list[dict[str, Any]]:
    """Load one context blob per resolved window, then attach compact policies."""
    with runtime._lock:
        window_rows = [dict(row) for row in runtime._conn.execute("""
            SELECT w.window_id,w.horizon_minutes,w.trade_id,w.observation_id,
                   w.captured_ts,w.evidence_eligible,w.origin,
                   g.production_policy,g.current_r,
                   COALESCE(c.instrument,w.instrument) AS instrument,
                   o.mfe_r,o.mae_r,v2.context_json
            FROM g1m_local_windows w
            JOIN g1m_local_outcomes o USING(window_id)
            JOIN g1m_management_observations g USING(observation_id)
            LEFT JOIN g1m_observation_context c USING(observation_id)
            LEFT JOIN g1m_t0_feature_context_v2 v2 USING(observation_id)
        """).fetchall()]
        policy_rows = [dict(row) for row in runtime._conn.execute("""
            SELECT p.window_id,p.policy_name,p.terminal_r,p.regret_r
            FROM g1m_local_policy_outcomes p
            JOIN g1m_local_outcomes o USING(window_id)
        """).fetchall()]

    pivot: dict[str, dict[str, Any]] = {}
    for row in window_rows:
        window_id = str(row["window_id"])
        pivot[window_id] = {
            "window_id": window_id,
            "horizon_minutes": int(row["horizon_minutes"]),
            "trade_id": int(row["trade_id"]),
            "observation_id": str(row["observation_id"]),
            "captured_ts": float(row["captured_ts"]),
            "evidence_eligible": bool(row["evidence_eligible"]),
            "origin": str(row["origin"]),
            "production_policy": str(row["production_policy"]),
            "current_r": row["current_r"],
            "instrument": str(row["instrument"] or "UNKNOWN"),
            "context_json": row["context_json"],
            "mfe_r": _local_edge._finite(row["mfe_r"]),
            "mae_r": _local_edge._finite(row["mae_r"]),
            "policies": {},
        }

    for row in policy_rows:
        target = pivot.get(str(row["window_id"]))
        if target is None:
            continue
        target["policies"][str(row["policy_name"])] = {
            "terminal_r": float(row["terminal_r"]),
            "regret_r": float(row["regret_r"]),
        }

    # The former SQL ORDER BY was part of the descriptive context contract because
    # the last CONTEXT_SCAN_LIMIT rows are selected.  Preserve it in memory after
    # removing the much larger joined-row SQLite sort.
    rows = [row for row in pivot.values() if row["policies"]]
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["window_id"])))
    return rows


def _window_records_bounded_io(
    runtime: ManagementLocalRuntime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load active-edge T0 JSON once per eligible resolved local window."""
    with runtime._lock:
        coverage = runtime._conn.execute("""
            SELECT COUNT(*) AS sidecar_observation_n,
                   COUNT(DISTINCT CASE WHEN available=1 THEN review_id END)
                       AS available_observation_n
            FROM g1m_active_edge_t0
        """).fetchone()
        window_rows = [dict(row) for row in runtime._conn.execute("""
            SELECT w.window_id,w.horizon_minutes,w.trade_id,w.observation_id,
                   w.captured_ts,w.evidence_eligible,w.origin,
                   ae.context_json AS active_edge_context_json
            FROM g1m_local_windows w
            JOIN g1m_local_outcomes o USING(window_id)
            JOIN g1m_active_edge_t0 ae USING(observation_id)
            WHERE w.evidence_eligible=1 AND ae.available=1
        """).fetchall()]
        policy_rows = [dict(row) for row in runtime._conn.execute("""
            SELECT p.window_id,p.policy_name,p.terminal_r
            FROM g1m_local_policy_outcomes p
            JOIN g1m_local_windows w USING(window_id)
            JOIN g1m_local_outcomes o USING(window_id)
            JOIN g1m_active_edge_t0 ae USING(observation_id)
            WHERE w.evidence_eligible=1 AND ae.available=1
              AND p.policy_name IN ('HOLD','EXIT','CLOSE_25','CLOSE_50')
        """).fetchall()]

    pivot: dict[str, dict[str, Any]] = {}
    for row in window_rows:
        window_id = str(row["window_id"])
        pivot[window_id] = {
            "window_id": window_id,
            "horizon_minutes": int(row["horizon_minutes"]),
            "trade_id": int(row["trade_id"]),
            "observation_id": str(row["observation_id"]),
            "captured_ts": float(row["captured_ts"]),
            "evidence_eligible": bool(row["evidence_eligible"]),
            "origin": str(row["origin"]),
            "active_edge_context_json": row["active_edge_context_json"],
            "policies": {},
        }

    for row in policy_rows:
        target = pivot.get(str(row["window_id"]))
        if target is None:
            continue
        target["policies"][str(row["policy_name"])] = float(row["terminal_r"])

    rows = [row for row in pivot.values() if row["policies"]]
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["window_id"])))
    windows = [_attribution._decorate_window(row) for row in rows]
    return windows, {
        "sidecar_observation_n": int(coverage["sidecar_observation_n"] or 0),
        "available_sidecar_observation_n": int(coverage["available_observation_n"] or 0),
        "resolved_prospective_window_n": len(windows),
        "resolved_unique_trade_n": len({int(row["trade_id"]) for row in windows}),
    }


def install_g1_management_edge_scalability() -> None:
    global _INSTALLED
    if _INSTALLED or getattr(
        ManagementLocalRuntime, "_edge_scalability_version", None
    ) == SCALABILITY_VERSION:
        return

    # Patch the module-level readers used by the already-installed report methods.
    # This deliberately avoids another ManagementLocalRuntime.edge wrapper.
    _local_edge._pairwise_rows = _pairwise_rows_bounded_io
    _attribution._window_records = _window_records_bounded_io
    ManagementLocalRuntime._edge_scalability_version = SCALABILITY_VERSION
    _INSTALLED = True
