"""Bound G1-M local-edge request-path amplification without changing semantics.

The production G1-M endpoint is presentation/diagnostic research data.  It must
not wait behind long-running work that owns ``PassiveLearningEngine._lock``.
For a file-backed WAL database this layer therefore serves the existing report
from an independent read-only SQLite snapshot, following the same pattern as
the production Q-audit boundary.  In-memory runtimes keep the original locked
connection fallback used by unit tests.

The report still reads every resolved G1-M window.  Context JSON is loaded once
per window, compact policy rows are attached separately, and derived context
labels are decoded once per immutable window.  Evidence eligibility, statistics,
maturity, authority, promotion and execution contracts are unchanged.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable, TypeVar

from .g1_management_local_runtime import ManagementLocalRuntime
from . import g1_management_local_edge_v2 as _local_edge
from . import g1_management_active_edge_attribution as _attribution


SCALABILITY_VERSION = "g1m-local-edge-read-scalability-v3"
_INSTALLED = False
_ORIGINAL_INIT = ManagementLocalRuntime.__init__
_ORIGINAL_CONTEXT_LABELS = _local_edge._context_labels
_ORIGINAL_DATASET_SUMMARY = _local_edge._dataset_summary
_CONTEXT_LABELS_CACHE_KEY = "_g1m_context_labels_cached"
_T = TypeVar("_T")


def _main_database_path(connection: sqlite3.Connection) -> str | None:
    for row in connection.execute("PRAGMA database_list").fetchall():
        if str(row[1]) == "main":
            path = str(row[2] or "")
            return path or None
    return None


def _init_with_read_snapshot(self: ManagementLocalRuntime, *args: Any, **kwargs: Any) -> None:
    _ORIGINAL_INIT(self, *args, **kwargs)
    with self._lock:
        self._g1m_edge_db_path = _main_database_path(self._conn)


def _open_read_snapshot(runtime: ManagementLocalRuntime) -> sqlite3.Connection | None:
    path = str(getattr(runtime, "_g1m_edge_db_path", "") or "")
    if not path or path == ":memory:":
        return None
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=500")
    return connection


def _read_snapshot(
    runtime: ManagementLocalRuntime,
    reader: Callable[[sqlite3.Connection], _T],
) -> _T:
    """Run a read transaction without waiting for the passive runtime mutex."""
    connection = _open_read_snapshot(runtime)
    if connection is None:
        with runtime._lock:
            return reader(runtime._conn)

    try:
        connection.execute("BEGIN")
        return reader(connection)
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def _context_labels_cached(row: dict[str, Any]) -> dict[str, str]:
    """Reuse labels already derived from the same immutable frozen window."""
    cached = row.get(_CONTEXT_LABELS_CACHE_KEY)
    if isinstance(cached, dict):
        return cached
    return _ORIGINAL_CONTEXT_LABELS(row)


def _pairwise_rows_bounded_io(runtime: ManagementLocalRuntime) -> list[dict[str, Any]]:
    """Load one context blob per resolved window, then attach compact policies."""
    def read(connection: sqlite3.Connection):
        window_rows = [dict(row) for row in connection.execute("""
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
        policy_rows = [dict(row) for row in connection.execute("""
            SELECT p.window_id,p.policy_name,p.terminal_r,p.regret_r
            FROM g1m_local_policy_outcomes p
            JOIN g1m_local_outcomes o USING(window_id)
        """).fetchall()]
        return window_rows, policy_rows

    window_rows, policy_rows = _read_snapshot(runtime, read)

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

    # Every pairwise comparison shallow-copies the same window dictionary. Put the
    # derived labels on the source row once so all five copies share the immutable
    # result instead of reparsing the large context_json five times.
    rows = [row for row in pivot.values() if row["policies"]]
    for row in rows:
        row[_CONTEXT_LABELS_CACHE_KEY] = _ORIGINAL_CONTEXT_LABELS(row)

    # The former SQL ORDER BY was part of the descriptive context contract because
    # the last CONTEXT_SCAN_LIMIT rows are selected. Preserve it in memory after
    # removing the much larger joined-row SQLite sort.
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["window_id"])))
    return rows


def _dataset_summary_bounded_io(
    runtime: ManagementLocalRuntime,
    resolved_windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preserve the causal inventory while keeping presentation off worker lock."""
    def read(connection: sqlite3.Connection):
        observation_row = connection.execute("""
            SELECT COUNT(*) AS observation_n,
                   COUNT(DISTINCT trade_id) AS unique_trade_n
            FROM g1m_management_observations
        """).fetchone()
        context_row = connection.execute("""
            SELECT COUNT(*) AS context_n
            FROM g1m_management_observations g
            JOIN g1m_t0_feature_context_v2 v2 USING(observation_id)
        """).fetchone()
        horizon_rows = connection.execute("""
            SELECT w.horizon_minutes,
                   COUNT(*) AS materialized_windows,
                   COUNT(o.window_id) AS resolved_windows,
                   SUM(CASE WHEN w.evidence_eligible=1 THEN 1 ELSE 0 END)
                       AS materialized_evidence_windows,
                   SUM(CASE WHEN w.evidence_eligible=1 AND o.window_id IS NOT NULL
                            THEN 1 ELSE 0 END) AS resolved_evidence_windows
            FROM g1m_local_windows w
            LEFT JOIN g1m_local_outcomes o USING(window_id)
            GROUP BY w.horizon_minutes
            ORDER BY w.horizon_minutes
        """).fetchall()
        origin_rows = connection.execute("""
            SELECT w.origin,
                   COUNT(*) AS materialized_windows,
                   COUNT(o.window_id) AS resolved_windows,
                   SUM(CASE WHEN w.evidence_eligible=1 THEN 1 ELSE 0 END)
                       AS materialized_evidence_windows,
                   SUM(CASE WHEN w.evidence_eligible=1 AND o.window_id IS NOT NULL
                            THEN 1 ELSE 0 END) AS resolved_evidence_windows
            FROM g1m_local_windows w
            LEFT JOIN g1m_local_outcomes o USING(window_id)
            GROUP BY w.origin
            ORDER BY w.origin
        """).fetchall()
        return observation_row, context_row, horizon_rows, origin_rows

    observation_row, context_row, horizon_rows, origin_rows = _read_snapshot(runtime, read)
    by_horizon = [{
        "horizon_minutes": int(row["horizon_minutes"]),
        "materialized_windows": int(row["materialized_windows"] or 0),
        "resolved_windows": int(row["resolved_windows"] or 0),
        "materialized_evidence_windows": int(row["materialized_evidence_windows"] or 0),
        "resolved_evidence_windows": int(row["resolved_evidence_windows"] or 0),
    } for row in horizon_rows]
    by_origin = [{
        "origin": str(row["origin"]),
        "materialized_windows": int(row["materialized_windows"] or 0),
        "resolved_windows": int(row["resolved_windows"] or 0),
        "materialized_evidence_windows": int(row["materialized_evidence_windows"] or 0),
        "resolved_evidence_windows": int(row["resolved_evidence_windows"] or 0),
    } for row in origin_rows]
    return {
        "management_observations": int(observation_row["observation_n"] or 0),
        "management_unique_trades": int(observation_row["unique_trade_n"] or 0),
        "t0_feature_context_rows": int(context_row["context_n"] or 0),
        "resolved_windows": len(resolved_windows),
        "prospective_evidence_windows": sum(
            bool(row["evidence_eligible"]) for row in resolved_windows
        ),
        "by_horizon": by_horizon,
        "by_origin": by_origin,
        "descriptive_rows_never_raise_prospective_maturity": True,
        "context_scan_limit_per_comparison_horizon": _local_edge.CONTEXT_SCAN_LIMIT,
    }


def _window_records_bounded_io(
    runtime: ManagementLocalRuntime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load active-edge T0 JSON once per eligible resolved local window."""
    def read(connection: sqlite3.Connection):
        coverage = connection.execute("""
            SELECT COUNT(*) AS sidecar_observation_n,
                   COUNT(DISTINCT CASE WHEN available=1 THEN review_id END)
                       AS available_observation_n
            FROM g1m_active_edge_t0
        """).fetchone()
        window_rows = [dict(row) for row in connection.execute("""
            SELECT w.window_id,w.horizon_minutes,w.trade_id,w.observation_id,
                   w.captured_ts,w.evidence_eligible,w.origin,
                   ae.context_json AS active_edge_context_json
            FROM g1m_local_windows w
            JOIN g1m_local_outcomes o USING(window_id)
            JOIN g1m_active_edge_t0 ae USING(observation_id)
            WHERE w.evidence_eligible=1 AND ae.available=1
        """).fetchall()]
        policy_rows = [dict(row) for row in connection.execute("""
            SELECT p.window_id,p.policy_name,p.terminal_r
            FROM g1m_local_policy_outcomes p
            JOIN g1m_local_windows w USING(window_id)
            JOIN g1m_local_outcomes o USING(window_id)
            JOIN g1m_active_edge_t0 ae USING(observation_id)
            WHERE w.evidence_eligible=1 AND ae.available=1
              AND p.policy_name IN ('HOLD','EXIT','CLOSE_25','CLOSE_50')
        """).fetchall()]
        return coverage, window_rows, policy_rows

    coverage, window_rows, policy_rows = _read_snapshot(runtime, read)

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

    # Patch only runtime initialization plus read-only report helpers. The edge
    # report method itself, evidence math and authority surface are untouched.
    ManagementLocalRuntime.__init__ = _init_with_read_snapshot
    _local_edge._pairwise_rows = _pairwise_rows_bounded_io
    _local_edge._dataset_summary = _dataset_summary_bounded_io
    _local_edge._context_labels = _context_labels_cached
    _attribution._window_records = _window_records_bounded_io
    ManagementLocalRuntime._edge_scalability_version = SCALABILITY_VERSION
    _INSTALLED = True
