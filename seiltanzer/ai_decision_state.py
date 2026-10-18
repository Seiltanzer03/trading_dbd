"""Persistent acknowledgement state for manual AI management decisions."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

_VALID_STATUSES = {"executed", "not_executed"}
# Commit 699f6654668da0de630f0fe5429f045a4a4c3280 (2026-08-17T19:33:17Z)
# replaced physical DELETE FROM trades with deleted_at tombstones; trade id=98
# is the first tombstoned trade in production history.
PRE_TOMBSTONE_DELETE_CUTOFF_TS = 1786995197.0
PRE_TOMBSTONE_MAX_TRADE_ID = 97

_ACK_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER trg_ai_decision_acks_require_live_trade_update
BEFORE UPDATE ON ai_decision_acks
WHEN NOT EXISTS (SELECT 1 FROM trades WHERE id = OLD.trade_id)
  OR NOT EXISTS (SELECT 1 FROM trades WHERE id = NEW.trade_id)
BEGIN
    SELECT RAISE(ABORT, 'FOREIGN KEY constraint failed: ai_decision_acks.trade_id requires trades.id');
END
""".strip()


def ensure_connection_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate the acknowledgement schema and lifecycle identity table."""
    ack_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_decision_acks'"
    ).fetchone()
    ack_sql = str(ack_row[0] or "") if ack_row else ""
    ready_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE (type='table' AND name IN ('trade_lifecycle_identities', 'ai_decision_acks')) "
            "OR (type='trigger' AND name IN ("
            "'trg_trades_sync_lifecycle_identity_insert',"
            "'trg_trades_forbid_delete_with_ack',"
            "'trg_trades_sync_lifecycle_identity_delete',"
            "'trg_ai_decision_acks_require_live_trade_insert',"
            "'trg_ai_decision_acks_require_live_trade_update'"
            "))"
        ).fetchone()[0]
    )
    update_guard = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_ai_decision_acks_require_live_trade_update'"
    ).fetchone()
    if (ready_count == 7 and "trade_lifecycle_identities" in ack_sql
            and update_guard and update_guard[0].strip() == _ACK_UPDATE_TRIGGER_SQL):
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_lifecycle_identities (
            trade_id INTEGER PRIMARY KEY,
            lifecycle_origin TEXT NOT NULL CHECK(
                lifecycle_origin IN ('trades_row_present', 'pre_tombstone_hard_deleted_parent')
            ),
            recorded_ts REAL NOT NULL
        )
        """
    )
    has_trades = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchone()
    if has_trades is not None:
        conn.execute(
            """
            INSERT INTO trade_lifecycle_identities(trade_id, lifecycle_origin, recorded_ts)
            SELECT id, 'trades_row_present', COALESCE(opened_at, 0.0)
            FROM trades
            WHERE true
            ON CONFLICT(trade_id) DO UPDATE SET
                lifecycle_origin='trades_row_present',
                recorded_ts=excluded.recorded_ts
            """
        )

    if ack_row is not None:
        conn.execute(
            """
            INSERT OR IGNORE INTO trade_lifecycle_identities(
                trade_id, lifecycle_origin, recorded_ts
            )
            SELECT trade_id, 'pre_tombstone_hard_deleted_parent', MIN(ts)
            FROM ai_decision_acks
            WHERE NOT EXISTS (
                SELECT 1 FROM trades WHERE trades.id = ai_decision_acks.trade_id
            )
              AND ts < ?
              AND trade_id <= ?
            GROUP BY trade_id
            """,
            (PRE_TOMBSTONE_DELETE_CUTOFF_TS, PRE_TOMBSTONE_MAX_TRADE_ID),
        )
        if "trade_lifecycle_identities" not in ack_sql:
            unmatched = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM ai_decision_acks
                    WHERE NOT EXISTS (
                        SELECT 1 FROM trade_lifecycle_identities
                        WHERE trade_lifecycle_identities.trade_id = ai_decision_acks.trade_id
                    )
                    """
                ).fetchone()[0]
            )
            if unmatched > 0:
                raise sqlite3.IntegrityError(
                    "FOREIGN KEY constraint failed: post-tombstone orphan ai_decision_acks row"
                )
            seq_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
            ).fetchone()
            prev_seq = None
            if seq_exists is not None:
                seq_row = conn.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='ai_decision_acks'"
                ).fetchone()
                if seq_row is not None and seq_row[0] is not None:
                    prev_seq = int(seq_row[0])

            conn.execute("DROP TRIGGER IF EXISTS trg_ai_decision_acks_require_live_trade_insert")
            conn.execute("DROP TRIGGER IF EXISTS trg_ai_decision_acks_require_live_trade_update")
            conn.execute("DROP TABLE IF EXISTS ai_decision_acks__migrated")
            conn.execute(
                """
                CREATE TABLE ai_decision_acks__migrated (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    decision_id TEXT NOT NULL,
                    policy TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('executed','not_executed')),
                    ts REAL NOT NULL,
                    note TEXT DEFAULT '',
                    UNIQUE(trade_id, decision_id),
                    FOREIGN KEY(trade_id) REFERENCES trade_lifecycle_identities(trade_id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ai_decision_acks__migrated(
                    id, trade_id, decision_id, policy, status, ts, note
                )
                SELECT id, trade_id, decision_id, policy, status, ts, note
                FROM ai_decision_acks
                ORDER BY id ASC
                """
            )
            conn.execute("DROP TABLE ai_decision_acks")
            conn.execute("ALTER TABLE ai_decision_acks__migrated RENAME TO ai_decision_acks")
            if prev_seq is not None:
                has_seq_row = conn.execute(
                    "SELECT 1 FROM sqlite_sequence WHERE name='ai_decision_acks'"
                ).fetchone()
                if has_seq_row is not None:
                    conn.execute(
                        "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name='ai_decision_acks'",
                        (prev_seq,),
                    )
                else:
                    conn.execute(
                        "INSERT INTO sqlite_sequence(name, seq) VALUES('ai_decision_acks', ?)",
                        (prev_seq,),
                    )
    else:
        conn.execute(
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
                FOREIGN KEY(trade_id) REFERENCES trade_lifecycle_identities(trade_id)
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_decision_ack_trade_ts "
        "ON ai_decision_acks(trade_id, ts)"
    )
    if has_trades is not None:
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_trades_sync_lifecycle_identity_insert
            AFTER INSERT ON trades
            BEGIN
                INSERT INTO trade_lifecycle_identities(trade_id, lifecycle_origin, recorded_ts)
                VALUES(NEW.id, 'trades_row_present', COALESCE(NEW.opened_at, 0.0))
                ON CONFLICT(trade_id) DO UPDATE SET
                    lifecycle_origin='trades_row_present',
                    recorded_ts=excluded.recorded_ts;
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_trades_forbid_delete_with_ack
            BEFORE DELETE ON trades
            WHEN EXISTS (SELECT 1 FROM ai_decision_acks WHERE trade_id = OLD.id)
            BEGIN
                SELECT RAISE(ABORT, 'FOREIGN KEY constraint failed: ai_decision_acks references trades.id');
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_trades_sync_lifecycle_identity_delete
            AFTER DELETE ON trades
            BEGIN
                DELETE FROM trade_lifecycle_identities
                WHERE trade_id = OLD.id AND lifecycle_origin = 'trades_row_present';
            END
            """
        )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_ai_decision_acks_require_live_trade_insert
        BEFORE INSERT ON ai_decision_acks
        WHEN NOT EXISTS (SELECT 1 FROM trades WHERE id = NEW.trade_id)
        BEGIN
            SELECT RAISE(ABORT, 'FOREIGN KEY constraint failed: ai_decision_acks.trade_id requires trades.id');
        END
        """
    )
    # Upgrade the earlier trade_id-only guard too: retained orphan ACK bytes
    # must not be rewritten through status, timestamps, notes, or policy.
    conn.execute('DROP TRIGGER IF EXISTS trg_ai_decision_acks_require_live_trade_update')
    conn.execute(_ACK_UPDATE_TRIGGER_SQL)


def ensure_schema(journal) -> None:
    """Create the acknowledgement table lazily and idempotently."""
    with journal._lock, journal._conn:  # noqa: SLF001 - Journal owns this DB
        ensure_connection_schema(journal._conn)  # noqa: SLF001


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
