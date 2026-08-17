"""Operational trade deletion without destroying immutable research evidence.

The user-facing journal owns an operational lifecycle, while prospective T0,
decision, path and attribution rows are measurement evidence. Deleting a trade
therefore tombstones the operational row instead of cascading through research.

This module follows the repository's existing final-runtime refinement pattern so
we can correct the lifecycle without rewriting the mature Journal implementation.
"""
from __future__ import annotations

import time

from .config import SETUPS
from .core.risk import setup_efficiency
from .journal import Journal, SetupStats


_INSTALLED = False


def install_trade_delete_lifecycle() -> None:
    """Install the user-delete lifecycle as the final Journal refinement."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = Journal.__init__
    original_close_trade = Journal.close_trade
    original_edit_trade = Journal.edit_trade
    original_update_zones = Journal.update_zones

    def lifecycle_init(self: Journal, path: str) -> None:
        original_init(self, path)
        # Additive/idempotent migration. We deliberately preserve the original
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

    def close_trade(self: Journal, trade_id: int, result_r: float,
                    notes: str | None = None) -> dict:
        # Keep the visibility check and the historical mutation under the same
        # re-entrant Journal lock. A stale UI/runtime request must not resolve a
        # trade after the user has deleted it operationally.
        with self._lock:
            self.get_trade(trade_id)
            return original_close_trade(self, trade_id, result_r, notes)

    def edit_trade(self: Journal, trade_id: int, **fields) -> dict:
        # Scenario edits can delete/rebuild decision evidence in the historical
        # implementation, so they are especially forbidden after tombstoning.
        with self._lock:
            self.get_trade(trade_id)
            return original_edit_trade(self, trade_id, **fields)

    def update_zones(self: Journal, trade_id: int, zones: list) -> dict:
        with self._lock:
            self.get_trade(trade_id)
            return original_update_zones(self, trade_id, zones)

    def update_max_r(self: Journal, trade_id: int, max_r: float) -> None:
        # Internal market loops may carry one stale trade object across the exact
        # delete boundary. Treat that race as a no-op rather than mutating the
        # retained audit row after it left live management.
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE trades SET max_r = MAX(COALESCE(max_r, -1e9), ?) "
                "WHERE id=? AND deleted_at IS NULL",
                (max_r, int(trade_id)),
            )

    def update_edge_at_open(self: Journal, trade_id: int,
                            edge: float | None) -> None:
        if edge is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE trades SET edge_at_open=? "
                "WHERE id=? AND edge_at_open IS NULL AND deleted_at IS NULL",
                (edge, int(trade_id)),
            )

    def setup_stats(self: Journal, setup: int,
                    min_journal_trades: int = 20) -> SetupStats:
        # Operational/user calibration keeps the historical hard-delete behavior:
        # a user-deleted journal row no longer contributes to visible setup stats.
        # Research-specific tables/metrics remain untouched and can still retain
        # the immutable evidence tied to the tombstoned trade id.
        builtin = SETUPS[setup]
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(CASE WHEN result_r > 0 THEN 1 ELSE 0 END) AS wins "
                "FROM trades WHERE setup=? AND status='closed' "
                "AND deleted_at IS NULL",
                (setup,),
            ).fetchone()
        journal_n = row["n"] or 0
        journal_wins = row["wins"] or 0
        efficiency = setup_efficiency(journal_wins, journal_n - journal_wins)
        if journal_n >= min_journal_trades:
            return SetupStats(
                setup=setup,
                n=journal_n,
                wins=journal_wins,
                losses=journal_n - journal_wins,
                source="journal",
                winrate=journal_wins / journal_n,
                efficiency=efficiency,
            )
        return SetupStats(
            setup=setup,
            n=builtin.n,
            wins=builtin.wins,
            losses=builtin.n - builtin.wins,
            source="builtin",
            winrate=builtin.winrate,
            efficiency=efficiency,
        )

    def journal_counts(self: Journal, setup: int) -> tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(CASE WHEN result_r > 0 THEN 1 ELSE 0 END) AS wins "
                "FROM trades WHERE setup=? AND status='closed' "
                "AND deleted_at IS NULL",
                (setup,),
            ).fetchone()
        return row["n"] or 0, row["wins"] or 0

    def delete_trade(self: Journal, trade_id: int) -> None:
        """Hide one trade operationally and terminate actionable management.

        Research/audit rows are intentionally untouched. This preserves frozen
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
            # connection. Updating its actionable state inside this transaction
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
    Journal.close_trade = close_trade
    Journal.edit_trade = edit_trade
    Journal.update_zones = update_zones
    Journal.update_max_r = update_max_r
    Journal.update_edge_at_open = update_edge_at_open
    Journal.setup_stats = setup_stats
    Journal.journal_counts = journal_counts
    Journal.delete_trade = delete_trade
