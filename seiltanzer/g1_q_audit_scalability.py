"""Bounded request-time Q audit reads for production.

The G1S Q maturity audit is presentation/diagnostic data.  It must not wait for
long-running passive/research work that owns PassiveLearningEngine._lock.  On a
file-backed WAL database this layer therefore serves the *existing refined v2*
audit contract from its own read-only SQLite snapshot.  Classification,
authority and successful-capture-only maturity semantics are unchanged.
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import passive_learning as _pl
from .g1_fast_learning_integrity_refinement import Q_AUDIT_REFINEMENT_VERSION
from .g1_short_horizon_runtime import (
    G1S_Q_AUDIT_VERSION,
    ShortHorizonRuntime,
    _finite,
)

Q_AUDIT_SCALABILITY_VERSION = "g1s-q-audit-read-snapshot-v2"

_RUNTIME = ShortHorizonRuntime
_ORIGINAL_INIT = _RUNTIME.__init__
# At this final install point q_audit already is g1s-q-resolution-audit-v2.
# Keep it for in-memory tests/fail-safe fallback and use parity tests as a hard
# contract that the bounded file-backed implementation returns the same shape.
_ORIGINAL_Q_AUDIT = _RUNTIME.q_audit


def _main_database_path(connection: sqlite3.Connection) -> str | None:
    for row in connection.execute("PRAGMA database_list").fetchall():
        # PRAGMA database_list -> seq, name, file. sqlite3.Row and tuple both
        # support positional access.
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


def _direct_terminal_candidate_snapshot(
    connection: sqlite3.Connection,
    instrument: str,
    target: float,
) -> tuple[float | None, str | None]:
    """Exact read-only equivalent of the existing refined v2 candidate rule."""
    bar = connection.execute(
        """
        SELECT bar_end_ts,quality FROM passive_market_bars
        WHERE instrument=? AND bar_end_ts<=? AND kind='direct'
          AND COALESCE(quality,0)>=0.90
        ORDER BY bar_end_ts DESC LIMIT 1
        """,
        (instrument, target + 1e-6),
    ).fetchone()
    point = connection.execute(
        """
        SELECT ts,quality FROM passive_market_path
        WHERE instrument=? AND ts<=? AND kind='direct'
          AND COALESCE(quality,0)>=0.90
        ORDER BY ts DESC LIMIT 1
        """,
        (instrument, target + 1e-6),
    ).fetchone()
    candidates: list[tuple[float, str]] = []
    if bar is not None:
        candidates.append((float(bar["bar_end_ts"]), "direct_1m_bar"))
    if point is not None:
        candidates.append((float(point["ts"]), "direct_path_point"))
    return max(
        candidates,
        default=(None, None),
        key=lambda x: -float("inf") if x[0] is None else x[0],
    )


def q_audit_bounded(
    self: _RUNTIME,
    *,
    now: float | None = None,
    limit: int = 500,
) -> dict:
    """Serve g1s-q-resolution-audit-v2 without the shared worker lock."""
    now = float(now or time.time())
    bounded_limit = max(1, min(int(limit), 5000))
    connection = _open_read_snapshot(self)
    if connection is None:
        # In-memory tests cannot share state with a second SQLite connection.
        return _ORIGINAL_Q_AUDIT(self, now=now, limit=bounded_limit)

    try:
        # One explicit read transaction gives the same consistent WAL snapshot
        # for attempts, observation resolution state and terminal candidates.
        connection.execute("BEGIN")
        rows = connection.execute(
            """
            SELECT q.attempt_id,q.attempt_ts,q.target_instrument,q.observation_created,
                   q.created_observation_id,q.blocker_code,q.requested_expiry_ts,
                   p.target_ts,p.resolution_status,p.instrument
            FROM g1_q_capture_attempts q
            LEFT JOIN passive_market_observations p
              ON p.observation_id=q.created_observation_id
            ORDER BY q.attempt_ts DESC LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()

        maturity = defaultdict(int)
        capture_blockers = defaultdict(int)
        items = []
        pending_targets = []
        captured_n = 0

        for row in rows:
            created = (
                int(row["observation_created"] or 0) == 1
                and bool(row["created_observation_id"])
            )
            target = _finite(row["target_ts"])
            candidate_ts: float | None = None
            candidate_source: str | None = None

            if not created:
                state = "CAPTURE_BLOCKED"
                capture_blockers[
                    str(row["blocker_code"] or "UNKNOWN_CAPTURE_BLOCKER")
                ] += 1
            else:
                captured_n += 1
                status = str(row["resolution_status"] or "pending")
                if status == "resolved":
                    state = "RESOLVED"
                elif status != "pending":
                    state = "RESOLUTION_BLOCKED"
                elif target is None:
                    state = "CONTRACT_REJECTED"
                elif now <= target + float(_pl.MAX_GAP_SEC):
                    state = "NOT_DUE_YET"
                    pending_targets.append(target)
                else:
                    instrument = str(
                        row["instrument"] or row["target_instrument"] or ""
                    )
                    candidate_ts, candidate_source = _direct_terminal_candidate_snapshot(
                        connection, instrument, target
                    )
                    # Exact v2 rule: a later quote is not retrospective evidence;
                    # the admissible direct observation must reach the frozen target.
                    if candidate_ts is not None and candidate_ts >= target - 1e-6:
                        state = "DUE_BUT_NOT_RESOLVED"
                    else:
                        state = "RESOLUTION_BLOCKED"
                maturity[state] += 1

            item = {
                "attempt_id": row["attempt_id"],
                "attempt_ts": row["attempt_ts"],
                "instrument": row["target_instrument"],
                "observation_id": row["created_observation_id"],
                "requested_expiry_ts": _finite(row["requested_expiry_ts"]),
                "target_ts": target,
                "resolution_status": row["resolution_status"],
                "blocker_code": row["blocker_code"],
                "audit_state": state,
            }
            if (
                created
                and target is not None
                and state in {"DUE_BUT_NOT_RESOLVED", "RESOLUTION_BLOCKED"}
            ):
                if candidate_ts is None:
                    candidate_ts, candidate_source = _direct_terminal_candidate_snapshot(
                        connection,
                        str(row["instrument"] or row["target_instrument"] or ""),
                        target,
                    )
                item["latest_admissible_terminal_candidate_ts"] = candidate_ts
                item["terminal_candidate_source"] = candidate_source
                item["terminal_gap_sec"] = (
                    None if candidate_ts is None else target - candidate_ts
                )
            items.append(item)

        targets = sorted(pending_targets)
        return {
            "contract_version": G1S_Q_AUDIT_VERSION,
            "refinement_contract_version": Q_AUDIT_REFINEMENT_VERSION,
            "now": now,
            "attempt_n": len(rows),
            "captured_n": captured_n,
            "capture_blocked_n": len(rows) - captured_n,
            "capture_blockers": dict(capture_blockers),
            "counts": dict(maturity),
            "successful_capture_counts_only": True,
            "earliest_pending_target_ts": targets[0] if targets else None,
            "median_pending_target_ts": (
                statistics.median(targets) if targets else None
            ),
            "latest_pending_target_ts": targets[-1] if targets else None,
            "overdue_is_contract_failure": (
                maturity.get("DUE_BUT_NOT_RESOLVED", 0) > 0
            ),
            "items": items,
            "slow_q_semantics_unchanged": True,
        }
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def install_g1_q_audit_scalability() -> None:
    if (
        getattr(_RUNTIME, "_g1_q_audit_scalability", None)
        == Q_AUDIT_SCALABILITY_VERSION
    ):
        return
    _RUNTIME.__init__ = init_with_q_audit_snapshot
    _RUNTIME.q_audit = q_audit_bounded
    _RUNTIME._g1_q_audit_scalability = Q_AUDIT_SCALABILITY_VERSION
