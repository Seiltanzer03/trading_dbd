"""Журнал сделок (sqlite trades.db) + статистика сетапов и настройки аккаунта.

Правило калибровки (ТЗ, п.2 ядра): пока по сетапу в журнале < N закрытых сделок,
вероятностная модель калибруется по встроенной таблице; при >= N — по журналу.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass

from .config import SETUPS
from .core.risk import setup_efficiency


@dataclass
class SetupStats:
    setup: int
    n: int
    wins: int
    losses: int
    source: str            # builtin | journal
    winrate: float
    efficiency: float | None  # 2a/(a+b) по журналу (None, если журнал пуст)


class Journal:
    def __init__(self, path: str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    opened_at REAL NOT NULL,
                    closed_at REAL,
                    setup INTEGER NOT NULL,
                    instrument TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('long','short')),
                    entry REAL NOT NULL,
                    stop REAL NOT NULL,
                    take REAL NOT NULL,
                    result_r REAL,
                    notes TEXT DEFAULT '',
                    zones TEXT DEFAULT '[]',
                    max_r REAL,
                    edge_at_open REAL,
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK(status IN ('open','closed'))
                )""")
            # миграция для существующих БД без колонки edge_at_open
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(trades)")]
            if "edge_at_open" not in cols:
                self._conn.execute("ALTER TABLE trades ADD COLUMN edge_at_open REAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    name TEXT DEFAULT 'SEILTANZER',
                    phase TEXT DEFAULT 'funded',
                    acc_size REAL DEFAULT 50000,
                    balance REAL DEFAULT 50000
                )""")
            self._conn.execute(
                "INSERT OR IGNORE INTO account(id) VALUES(1)")

    # ---------------------------------------------------------------- trades

    def open_trade(self, setup: int, instrument: str, direction: str,
                   entry: float, stop: float, take: float,
                   notes: str = "", zones: list | None = None) -> dict:
        if setup not in SETUPS:
            raise ValueError(f"неизвестный сетап: {setup}")
        if direction not in ("long", "short"):
            raise ValueError("direction: long|short")
        if entry == stop:
            raise ValueError("вход и стоп совпадают")
        if (direction == "long") != (take > entry):
            raise ValueError("тейк должен быть по направлению сделки")
        if (direction == "long") != (stop < entry):
            raise ValueError("стоп должен быть с противоположной стороны от входа")
        if self.active_trade() is not None:
            raise ValueError("уже есть открытая сделка — закройте её")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO trades(opened_at, setup, instrument, direction, entry, "
                "stop, take, notes, zones) VALUES(?,?,?,?,?,?,?,?,?)",
                (time.time(), setup, instrument, direction, entry, stop, take,
                 notes, json.dumps(zones or [])))
            return self.get_trade(cur.lastrowid)

    def close_trade(self, trade_id: int, result_r: float, notes: str | None = None) -> dict:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            if row is None:
                raise ValueError(f"сделка {trade_id} не найдена")
            if row["status"] == "closed":
                raise ValueError("сделка уже закрыта")
            self._conn.execute(
                "UPDATE trades SET status='closed', closed_at=?, result_r=?, "
                "notes=COALESCE(?, notes) WHERE id=?",
                (time.time(), result_r, notes, trade_id))
        return self.get_trade(trade_id)

    def add_closed(self, setup: int, direction: str, entry: float, stop: float,
                   take: float, result_r: float, notes: str = "",
                   opened_at: float | None = None) -> dict:
        """Бэкфилл: добавить уже закрытую сделку в журнал (для истории/статистики)."""
        if setup not in SETUPS:
            raise ValueError(f"неизвестный сетап: {setup}")
        if direction not in ("long", "short"):
            raise ValueError("direction: long|short")
        if entry == stop:
            raise ValueError("вход и стоп совпадают")
        ts = opened_at or time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO trades(opened_at, closed_at, setup, instrument, "
                "direction, entry, stop, take, result_r, notes, status) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'closed')",
                (ts, ts, setup, SETUPS[setup].instrument, direction, entry, stop,
                 take, result_r, notes))
            return self.get_trade(cur.lastrowid)

    def update_max_r(self, trade_id: int, max_r: float) -> None:
        """Монотонно поднимает достигнутый максимум R (для лестницы фиксации)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE trades SET max_r = MAX(COALESCE(max_r, -1e9), ?) WHERE id=?",
                (max_r, trade_id))

    def update_edge_at_open(self, trade_id: int, edge: float | None) -> None:
        """Фиксирует край (модель−рынок) на момент входа — только если ещё не задан."""
        if edge is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE trades SET edge_at_open=? WHERE id=? AND edge_at_open IS NULL",
                (edge, trade_id))

    def edge_track(self) -> dict:
        """Сбывается ли переоценка: винрейт закрытых сделок с +краем и с −краем.

        Если сделки, где вы видели положительный край (рынок недооценивал сетап),
        закрываются в плюс чаще — край действительно предсказателен.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT edge_at_open, result_r FROM trades "
                "WHERE status='closed' AND edge_at_open IS NOT NULL "
                "AND result_r IS NOT NULL").fetchall()
        pos = [r for r in rows if r["edge_at_open"] > 0]
        neg = [r for r in rows if r["edge_at_open"] <= 0]

        def wr(rs):
            return (sum(1 for r in rs if r["result_r"] > 0) / len(rs)) if rs else None
        return {"n": len(rows),
                "pos_n": len(pos), "pos_wr": wr(pos),
                "neg_n": len(neg), "neg_wr": wr(neg)}

    def update_zones(self, trade_id: int, zones: list) -> dict:
        with self._lock, self._conn:
            self._conn.execute("UPDATE trades SET zones=? WHERE id=?",
                               (json.dumps(zones), trade_id))
        return self.get_trade(trade_id)

    def get_trade(self, trade_id: int) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"сделка {trade_id} не найдена")
        return self._row_to_dict(row)

    def active_trade(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE status='open' "
                "ORDER BY opened_at DESC LIMIT 1").fetchone()
        return self._row_to_dict(row) if row else None

    def list_trades(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["zones"] = json.loads(d.get("zones") or "[]")
        return d

    # ------------------------------------------------------------ statistics

    def setup_stats(self, setup: int, min_journal_trades: int = 20) -> SetupStats:
        """Статистика сетапа: встроенная таблица либо журнал (при достатке данных).

        Победа = закрытая сделка с result_r > 0.
        """
        builtin = SETUPS[setup]
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(CASE WHEN result_r > 0 THEN 1 ELSE 0 END) AS wins "
                "FROM trades WHERE setup=? AND status='closed'", (setup,)).fetchone()
        jn = row["n"] or 0
        jw = row["wins"] or 0
        eff = setup_efficiency(jw, jn - jw)
        if jn >= min_journal_trades:
            return SetupStats(setup=setup, n=jn, wins=jw, losses=jn - jw,
                              source="journal", winrate=jw / jn, efficiency=eff)
        return SetupStats(setup=setup, n=builtin.n, wins=builtin.wins,
                          losses=builtin.n - builtin.wins, source="builtin",
                          winrate=builtin.winrate, efficiency=eff)

    def journal_counts(self, setup: int) -> tuple[int, int]:
        """(закрытых сделок, побед) по сетапу — для бейджа калибровки."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(CASE WHEN result_r > 0 THEN 1 ELSE 0 END) AS wins "
                "FROM trades WHERE setup=? AND status='closed'", (setup,)).fetchone()
        return row["n"] or 0, row["wins"] or 0

    def export_csv(self) -> str:
        cols = ["id", "opened_at", "closed_at", "setup", "instrument", "direction",
                "entry", "stop", "take", "result_r", "status", "notes"]
        lines = [";".join(cols)]
        for t in reversed(self.list_trades(limit=100000)):
            vals = []
            for c in cols:
                v = t.get(c)
                if c in ("opened_at", "closed_at") and v:
                    v = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(v))
                s = "" if v is None else str(v)
                vals.append('"' + s.replace('"', '""') + '"' if ";" in s or '"' in s else s)
            lines.append(";".join(vals))
        return "\n".join(lines) + "\n"

    # --------------------------------------------------------------- account

    def account(self) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT * FROM account WHERE id=1").fetchone()
        return dict(row)

    def update_account(self, **kwargs) -> dict:
        allowed = {"name", "phase", "acc_size", "balance"}
        fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if fields.get("phase") not in (None, "1ph", "2ph", "funded"):
            raise ValueError("phase: 1ph|2ph|funded")
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self._lock, self._conn:
                self._conn.execute(f"UPDATE account SET {sets} WHERE id=1",
                                   tuple(fields.values()))
        return self.account()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
