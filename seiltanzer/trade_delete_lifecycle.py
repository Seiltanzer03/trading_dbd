"""Operational trade deletion without destroying immutable research evidence.

The user-facing journal owns an operational lifecycle, while prospective T0,
decision, path and attribution rows are measurement evidence.  Deleting a trade
therefore tombstones the operational row instead of cascading through research.

This module follows the repository's existing final-runtime refinement pattern so
we can correct the lifecycle without rewriting the mature Journal implementation.
"""
from __future__ import annotations

import time

from .journal import Journal


_INSTALLED = False


def install_trade_delete_lifecycle() -> None:
    """Install the user-delete lifecycle as the final Journal refinement."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = Journal.__init__

    def lifecycle_init(self: Journal, path: str) -> None:
        original_init(self, path)
        # Additive/idempotent migration.  We deliberately preserve the original
        # economic status (open/closed); deleted_at is only the operational/UI
        # visibility boundary and must not fabricate a realized trade outcome.
        with self._lock, self._conn:
            cols = {
                row[1] for row in self._conn.execute("PRAGMA table_info(trades)")
            }
            if "deleted_at" not in cols:
                self._conn.execute("ALTER TABLE trades ADD COLUMN deleted_at REAL")

    def get_trade(self: Journal, trade_id: int) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE id=? AND deleted_at IS NULL",
                (int(trade_id),),
            ).fetchone()
        if row is None:
            raise ValueError(f"сделка {trade_id} не найдена")
        return self._row_to_dict(row)

    def active_trade(self: Journal) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE status='open' AND deleted_at IS NULL "
                "ORDER BY opened_at DESC LIMIT 1"
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_trades(self: Journal, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE deleted_at IS NULL "
                "ORDER BY opened_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def delete_trade(self: Journal, trade_id: int) -> None:
        """Hide one trade operationally and terminate actionable management.

        Research/audit rows are intentionally untouched.  This preserves frozen
        prospective evidence, prevents survivorship bias and avoids FK/orphan
        failures as new research tables acquire trade references.
        """
        trade_id = int(trade_id)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM trades WHERE id=? AND deleted_at IS NULL",
                (trade_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"сделка {trade_id} не найдена")

            # PositionLedger uses the same SQLite database but a separate
            # connection.  Updating its actionable state inside this transaction
            # makes the user delete atomic with the operational tombstone.
            management_table = self._conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='management_decisions'"
            ).fetchone()
            if management_table is not None:
                self._conn.execute(
                    "UPDATE management_decisions SET status='superseded' "
                    "WHERE trade_id=? AND status='pending_execution'",
                    (trade_id,),
                )

            changed = self._conn.execute(
                "UPDATE trades SET deleted_at=? "
                "WHERE id=? AND deleted_at IS NULL",
                (time.time(), trade_id),
            ).rowcount
            if changed != 1:
                # Keep the same controlled contract as the historical method.
                # Raising inside the context rolls back management state too.
                raise ValueError(f"сделка {trade_id} не найдена")

    Journal.__init__ = lifecycle_init
    Journal.get_trade = get_trade
    Journal.active_trade = active_trade
    Journal.list_trades = list_trades
    Journal.delete_trade = delete_trade
