"""Persistent acknowledgement state for manual AI management decisions."""
from __future__ import annotations

import json
import time
from typing import Any

_VALID_STATUSES = {"executed", "not_executed"}


def ensure_schema(journal) -> None:
    """Create the acknowledgement table lazily and idempotently."""
    with journal._lock, journal._conn:  # noqa: SLF001 - Journal owns this DB
        journal._conn.execute(  # noqa: SLF001
            """
            CREATE TABLE IF NOT EXISTS ai_decision_acks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                decision_id TEXT NOT NULL,
                policy TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('executed','not_executed')),
                ts REAL NOT NULL,
                note TEXT DEFAULT '',
                UNIQUE(trade_id, decision_id),
                FOREIGN KEY(trade_id) REFERENCES trades(id)
            )
            """
        )
        journal._conn.execute(  # noqa: SLF001
            "CREATE INDEX IF NOT EXISTS ix_ai_decision_ack_trade_ts "
            "ON ai_decision_acks(trade_id, ts)"
        )


def _decision_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    decision = ((snapshot.get("policy_manager") or {}).get("management_decision"))
    return dict(decision) if isinstance(decision, dict) and decision.get("decision_id") else None


def latest_management_decision(journal, trade_id: int) -> dict[str, Any] | None:
    """Return the newest persisted management decision for one trade."""
    ensure_schema(journal)
    with journal._lock:  # noqa: SLF001
        rows = journal._conn.execute(  # noqa: SLF001
            "SELECT snapshot_json FROM ai_verdicts WHERE trade_id=? "
            "ORDER BY ts DESC LIMIT 50",
            (int(trade_id),),
        ).fetchall()
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        decision = _decision_from_snapshot(snapshot)
        if decision:
            return decision
    return None


def find_management_decision(journal, trade_id: int,
                             decision_id: str) -> dict[str, Any] | None:
    """Find an exact persisted decision, newest snapshots first."""
    ensure_schema(journal)
    with journal._lock:  # noqa: SLF001
        rows = journal._conn.execute(  # noqa: SLF001
            "SELECT snapshot_json FROM ai_verdicts WHERE trade_id=? "
            "ORDER BY ts DESC LIMIT 200",
            (int(trade_id),),
        ).fetchall()
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        decision = _decision_from_snapshot(snapshot)
        if decision and decision.get("decision_id") == decision_id:
            return decision
    return None


def latest_ack(journal, trade_id: int,
               decision_id: str) -> dict[str, Any] | None:
    ensure_schema(journal)
    with journal._lock:  # noqa: SLF001
        row = journal._conn.execute(  # noqa: SLF001
            "SELECT trade_id,decision_id,policy,status,ts,note "
            "FROM ai_decision_acks WHERE trade_id=? AND decision_id=?",
            (int(trade_id), decision_id),
        ).fetchone()
    return dict(row) if row else None


def executed_ack_count(journal, trade_id: int) -> int:
    ensure_schema(journal)
    with journal._lock:  # noqa: SLF001
        row = journal._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) AS n FROM ai_decision_acks "
            "WHERE trade_id=? AND status='executed'",
            (int(trade_id),),
        ).fetchone()
    return int(row["n"] or 0)


def record_ack(journal, trade_id: int, decision_id: str,
               status: str, *, note: str = "") -> dict[str, Any]:
    """Acknowledge the latest decision; stale modal decisions are rejected."""
    ensure_schema(journal)
    if status not in _VALID_STATUSES:
        raise ValueError("status: executed|not_executed")
    latest = latest_management_decision(journal, int(trade_id))
    if latest is None:
        raise ValueError("решение ИИ по этой сделке не найдено")
    if latest.get("decision_id") != decision_id:
        raise ValueError(
            "это решение уже заменено новым отчётом; откройте последний ИИ-разбор"
        )
    if latest.get("execution_status") != "pending_execution":
        raise ValueError("это решение не ожидает ручного исполнения")
    if not latest.get("manual_execution_required"):
        raise ValueError("для этого решения ручное подтверждение не требуется")

    policy = str(latest.get("policy") or latest.get("model_policy") or "UNKNOWN")
    now = time.time()
    with journal._lock, journal._conn:  # noqa: SLF001
        journal._conn.execute(  # noqa: SLF001
            """
            INSERT INTO ai_decision_acks(
                trade_id,decision_id,policy,status,ts,note
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(trade_id,decision_id) DO UPDATE SET
                policy=excluded.policy,
                status=excluded.status,
                ts=excluded.ts,
                note=excluded.note
            """,
            (int(trade_id), decision_id, policy, status, now, note.strip()),
        )
    return latest_ack(journal, int(trade_id), decision_id) or {
        "trade_id": int(trade_id),
        "decision_id": decision_id,
        "policy": policy,
        "status": status,
        "ts": now,
        "note": note.strip(),
    }
