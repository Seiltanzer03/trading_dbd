"""Bounded request-time Q audit reads for production.

The G1S Q maturity audit is presentation/diagnostic data. It must not wait for
long-running passive/research work that owns PassiveLearningEngine._lock, and it
must not issue O(rows) Python->SQLite round trips. On a file-backed WAL database
this layer serves the existing refined v2 audit contract from an independent
read-only snapshot, resolves terminal candidates in bounded SQL batches, and
indexes only the admissible direct-evidence subset used by those predecessor
queries. Classification, authority and successful-capture-only maturity
semantics are unchanged.
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from . import passive_learning as _pl
from .g1_fast_learning_integrity_refinement import Q_AUDIT_REFINEMENT_VERSION
from .g1_short_horizon_runtime import (
    G1S_Q_AUDIT_VERSION,
    ShortHorizonRuntime,
    _finite,
)

Q_AUDIT_SCALABILITY_VERSION = "g1s-q-audit-read-snapshot-v6"
Q_AUDIT_CANDIDATE_BATCH_SIZE = 500

_RUNTIME = ShortHorizonRuntime
_ORIGINAL_INIT = _RUNTIME.__init__
# At this final install point q_audit already is g1s-q-resolution-audit-v2.
# Keep it for in-memory tests/fail-safe fallback and use parity tests as a hard
# contract that the bounded file-backed implementation returns the same shape.
_ORIGINAL_Q_AUDIT = _RUNTIME.q_audit


def _main_database_path(connection: sqlite3.Connection) -> str | None:
    for row in connection.execute("PRAGMA database_list").fetchall():
        if str(row[1]) == "main":
            path = str(row[2] or "")
            return path or None
    return None


def _install_indexes(runtime: _RUNTIME) -> None:
    """Install indexes for the exact bounded audit predicates."""
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1_q_attempt_ts "
            "ON g1_q_capture_attempts(attempt_ts)"
        )
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_passive_bar_instrument_end "
            "ON passive_market_bars(instrument,bar_end_ts)"
        )
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_passive_direct_path_terminal "
            "ON passive_market_path(instrument,ts) "
            "WHERE kind='direct' AND COALESCE(quality,0)>=0.90"
        )
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_passive_direct_bar_terminal "
            "ON passive_market_bars(instrument,bar_end_ts) "
            "WHERE kind='direct' AND COALESCE(quality,0)>=0.90"
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


def _chunks(values: list[tuple[int, str, float]], size: int) -> Iterable[list[tuple[int, str, float]]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _choose_terminal_candidate(
    bar_ts: float | None,
    path_ts: float | None,
) -> tuple[float | None, str | None]:
    """Preserve v2 tie semantics: equal timestamps prefer the bar candidate."""
    if bar_ts is None and path_ts is None:
        return None, None
    if bar_ts is not None and (path_ts is None or bar_ts >= path_ts):
        return bar_ts, "direct_1m_bar"
    return path_ts, "direct_path_point"


def _deduplicate_candidate_requests(
    requests: list[tuple[int, str, float]],
) -> tuple[list[tuple[int, str, float]], dict[int, list[int]]]:
    """Collapse identical instrument/target predecessor seeks.

    Q capture can retry the same option expiry/target many times. The terminal
    evidence predecessor is a pure function of (instrument,target), so doing the
    same two B-tree seeks once per capture attempt wastes latency without adding
    information. A representative row key is queried once and fanned back out to
    every original row key. Float targets are frozen persisted timestamps and are
    therefore safe exact dictionary keys here.
    """
    representative_for: dict[tuple[str, float], int] = {}
    fanout: dict[int, list[int]] = defaultdict(list)
    unique: list[tuple[int, str, float]] = []
    for row_key, instrument, target in requests:
        identity = (str(instrument), float(target))
        representative = representative_for.get(identity)
        if representative is None:
            representative = int(row_key)
            representative_for[identity] = representative
            unique.append((representative, identity[0], identity[1]))
        fanout[representative].append(int(row_key))
    return unique, dict(fanout)


def _terminal_candidate_batch_snapshot(
    connection: sqlite3.Connection,
    requests: list[tuple[int, str, float]],
) -> dict[int, tuple[float | None, str | None]]:
    """Resolve many predecessor candidates with bounded, deduplicated SQL seeks."""
    if not requests:
        return {}

    unique_requests, fanout = _deduplicate_candidate_requests(requests)
    representative_results: dict[int, tuple[float | None, str | None]] = {}
    for batch in _chunks(unique_requests, Q_AUDIT_CANDIDATE_BATCH_SIZE):
        values_sql = ",".join("(?,?,?)" for _ in batch)
        params: list[Any] = []
        for row_key, instrument, target in batch:
            params.extend((int(row_key), str(instrument), float(target) + 1e-6))
        rows = connection.execute(
            f"""
            WITH requested(row_key,instrument,cutoff_ts) AS (
                VALUES {values_sql}
            )
            SELECT
                r.row_key AS row_key,
                (
                    SELECT b.bar_end_ts
                    FROM passive_market_bars b
                    WHERE b.instrument=r.instrument
                      AND b.bar_end_ts<=r.cutoff_ts
                      AND b.kind='direct'
                      AND COALESCE(b.quality,0)>=0.90
                    ORDER BY b.bar_end_ts DESC
                    LIMIT 1
                ) AS bar_ts,
                (
                    SELECT p.ts
                    FROM passive_market_path p
                    WHERE p.instrument=r.instrument
                      AND p.ts<=r.cutoff_ts
                      AND p.kind='direct'
                      AND COALESCE(p.quality,0)>=0.90
                    ORDER BY p.ts DESC
                    LIMIT 1
                ) AS path_ts
            FROM requested r
            """,
            params,
        ).fetchall()
        for row in rows:
            bar_ts = _finite(row["bar_ts"])
            path_ts = _finite(row["path_ts"])
            representative_results[int(row["row_key"])] = _choose_terminal_candidate(
                bar_ts, path_ts
            )

    resolved: dict[int, tuple[float | None, str | None]] = {}
    for representative, row_keys in fanout.items():
        value = representative_results.get(representative, (None, None))
        for row_key in row_keys:
            resolved[row_key] = value
    return resolved


def _candidate_requests(
    rows: list[sqlite3.Row],
    *,
    now: float,
    include_diagnostics: bool = True,
) -> list[tuple[int, str, float]]:
    """Return rows whose maturity state or requested diagnostics need evidence.

    An already RESOLUTION_BLOCKED observation is already classified and needs a
    predecessor seek only to populate per-item diagnostics. Summary-only health
    probes intentionally skip those diagnostic-only seeks while still resolving
    every overdue *pending* row needed to compute the exact maturity counts.
    """
    requests: list[tuple[int, str, float]] = []
    for row_key, row in enumerate(rows):
        created = (
            int(row["observation_created"] or 0) == 1
            and bool(row["created_observation_id"])
        )
        target = _finite(row["target_ts"])
        if not created or target is None:
            continue
        status = str(row["resolution_status"] or "pending")
        overdue_pending = status == "pending" and now > target + float(_pl.MAX_GAP_SEC)
        diagnostic_only = status not in {"pending", "resolved"}
        if overdue_pending or (include_diagnostics and diagnostic_only):
            requests.append((
                row_key,
                str(row["instrument"] or row["target_instrument"] or ""),
                target,
            ))
    return requests


def q_audit_bounded(
    self: _RUNTIME,
    *,
    now: float | None = None,
    limit: int = 500,
    include_items: bool = True,
) -> dict:
    """Serve g1s-q-resolution-audit-v2 without worker-lock/N+1 SQL latency.

    ``include_items=False`` is a presentation optimization for health/readiness
    callers that consume only aggregate maturity counts. It evaluates the same
    bounded attempt cohort and the same overdue-pending evidence rule; it merely
    omits per-attempt payloads and predecessor lookups used *only* to decorate
    rows whose resolution state is already final/blocked.
    """
    now = float(now or time.time())
    bounded_limit = max(1, min(int(limit), 5000))
    include_items = bool(include_items)
    connection = _open_read_snapshot(self)
    if connection is None:
        result = _ORIGINAL_Q_AUDIT(self, now=now, limit=bounded_limit)
        if include_items:
            return result
        result = dict(result)
        result["items"] = []
        result["items_included"] = False
        result["item_count_total"] = int(result.get("attempt_n") or 0)
        result["summary_semantics_unchanged"] = True
        return result

    try:
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
        candidates = _terminal_candidate_batch_snapshot(
            connection,
            _candidate_requests(
                rows,
                now=now,
                include_diagnostics=include_items,
            ),
        )

        maturity = defaultdict(int)
        capture_blockers = defaultdict(int)
        items = []
        pending_targets = []
        captured_n = 0

        for row_key, row in enumerate(rows):
            created = (
                int(row["observation_created"] or 0) == 1
                and bool(row["created_observation_id"])
            )
            target = _finite(row["target_ts"])
            candidate_ts, candidate_source = candidates.get(
                row_key, (None, None)
            )

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
                    if candidate_ts is not None and candidate_ts >= target - 1e-6:
                        state = "DUE_BUT_NOT_RESOLVED"
                    else:
                        state = "RESOLUTION_BLOCKED"
                maturity[state] += 1

            if include_items:
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
            "scalability_contract_version": Q_AUDIT_SCALABILITY_VERSION,
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
            "items_included": include_items,
            "item_count_total": len(rows),
            "summary_semantics_unchanged": True,
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