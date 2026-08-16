"""Bounded request-time Q audit reads for production.

The G1S Q maturity audit is presentation/diagnostic data.  It must not wait for
long-running passive/research work that owns PassiveLearningEngine._lock.  On a
file-backed WAL database this layer therefore serves the audit from its own
read-only SQLite snapshot.  Audit classification semantics and authority are
unchanged.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .g1_short_horizon_runtime import (
    G1S_Q_AUDIT_VERSION,
    ShortHorizonRuntime,
    _finite,
)

Q_AUDIT_SCALABILITY_VERSION = "g1s-q-audit-read-snapshot-v1"

_RUNTIME = ShortHorizonRuntime
_ORIGINAL_INIT = _RUNTIME.__init__
_ORIGINAL_Q_AUDIT = _RUNTIME.q_audit


def _main_database_path(connection: sqlite3.Connection) -> str | None:
    for row in connection.execute("PRAGMA database_list").fetchall():
        # PRAGMA database_list -> seq, name, file.  sqlite3.Row and tuple both
        # support positional access, which keeps this wrapper compatible with
        # the shared production connection and small unit-test connections.
        if str(row[1]) == "main":
            path = str(row[2] or "")
            return path or None
    return None


def _install_indexes(runtime: _RUNTIME) -> None:
    """Install only indexes required by the bounded audit read path."""
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_q_attempt_ts "
            "ON g1_q_capture_attempts(attempt_ts)"
        )
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_passive_bar_instrument_end "
            "ON passive_market_bars(instrument,bar_end_ts)"
        )


def init_with_q_audit_snapshot(self: _RUNTIME, *args: Any, **kwargs: Any) -> None:
    _ORIGINAL_INIT(self, *args, **kwargs)
    with self._lock:
        self._q_audit_db_path = _main_database_path(self._conn)
    _install_indexes(self)


def _open_read_snapshot(self: _RUNTIME) -> sqlite3.Connection | None:
    path = str(getattr(self, "_q_audit_db_path", "") or "")
    if not path or path == ":memory:":
        return None
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=500")
    return connection


def _audit_snapshot(
    connection: sqlite3.Connection,
    *,
    limit: int,
) -> tuple[list[sqlite3.Row], dict[str, float], dict[str, float]]:
    """Read one consistent WAL snapshot without scanning full path/bar tables."""
    connection.execute("BEGIN")
    rows = connection.execute(
        """
        SELECT q.attempt_id,q.attempt_ts,q.target_instrument,q.observation_created,
               q.created_observation_id,q.blocker_code,p.target_ts,p.resolution_status,
               p.instrument
        FROM g1_q_capture_attempts q
        LEFT JOIN passive_market_observations p
          ON p.observation_id=q.created_observation_id
        ORDER BY q.attempt_ts DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    # The production instrument universe is small.  Indexed MAX per instrument
    # is substantially cheaper than GROUP BY over the entire continuously-grown
    # path/bar ledgers and preserves the exact value used by the old audit.
    instruments = sorted({
        str(row["instrument"] or row["target_instrument"] or "")
        for row in rows
        if (row["instrument"] or row["target_instrument"])
    })
    latest_path: dict[str, float] = {}
    latest_bar: dict[str, float] = {}
    for instrument in instruments:
        path_row = connection.execute(
            "SELECT MAX(ts) FROM passive_market_path WHERE instrument=?",
            (instrument,),
        ).fetchone()
        bar_row = connection.execute(
            "SELECT MAX(bar_end_ts) FROM passive_market_bars WHERE instrument=?",
            (instrument,),
        ).fetchone()
        latest_path[instrument] = float((path_row[0] if path_row else 0) or 0)
        latest_bar[instrument] = float((bar_row[0] if bar_row else 0) or 0)
    return rows, latest_path, latest_bar


def q_audit_bounded(
    self: _RUNTIME,
    *,
    now: float | None = None,
    limit: int = 500,
) -> dict:
    now = float(now or time.time())
    bounded_limit = max(1, min(int(limit), 5000))
    connection = _open_read_snapshot(self)
    if connection is None:
        # In-memory tests cannot share state with a second SQLite connection.
        # Keep the original implementation there; production is file-backed.
        return _ORIGINAL_Q_AUDIT(self, now=now, limit=bounded_limit)
    try:
        rows, latest_path, latest_bar = _audit_snapshot(
            connection, limit=bounded_limit
        )
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()

    counts = defaultdict(int)
    items = []
    targets = []
    for row in rows:
        target = _finite(row["target_ts"])
        if int(row["observation_created"] or 0) != 1 or not row["created_observation_id"]:
            state = "CONTRACT_REJECTED" if row["blocker_code"] else "RESOLUTION_BLOCKED"
        elif row["resolution_status"] == "resolved":
            state = "RESOLVED"
        elif target is not None and target > now:
            state = "NOT_DUE_YET"
            targets.append(target)
        else:
            instrument = str(row["instrument"] or row["target_instrument"] or "")
            latest = max(latest_path.get(instrument, 0), latest_bar.get(instrument, 0))
            if target is not None and latest >= target - 1e-6:
                state = "DUE_BUT_NOT_RESOLVED"
            else:
                state = "RESOLUTION_BLOCKED"
        counts[state] += 1
        items.append({
            "attempt_id": row["attempt_id"],
            "attempt_ts": row["attempt_ts"],
            "instrument": row["target_instrument"],
            "observation_id": row["created_observation_id"],
            "target_ts": target,
            "resolution_status": row["resolution_status"],
            "blocker_code": row["blocker_code"],
            "audit_state": state,
        })

    return {
        "contract_version": G1S_Q_AUDIT_VERSION,
        "now": now,
        "counts": dict(counts),
        "earliest_pending_target_ts": min(targets) if targets else None,
        "median_pending_target_ts": (
            sorted(targets)[len(targets) // 2] if targets else None
        ),
        "latest_pending_target_ts": max(targets) if targets else None,
        "overdue_is_contract_failure": counts.get("DUE_BUT_NOT_RESOLVED", 0) > 0,
        "items": items,
        "slow_q_semantics_unchanged": True,
    }


def install_g1_q_audit_scalability() -> None:
    if getattr(_RUNTIME, "_g1_q_audit_scalability", None) == Q_AUDIT_SCALABILITY_VERSION:
        return
    _RUNTIME.__init__ = init_with_q_audit_snapshot
    _RUNTIME.q_audit = q_audit_bounded
    _RUNTIME._g1_q_audit_scalability = Q_AUDIT_SCALABILITY_VERSION
