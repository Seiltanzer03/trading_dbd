"""Дисковый кэш (sqlite): последние значения фидов и история снапшотов цепочек."""

from __future__ import annotations

import json
import sqlite3
import threading
import time


class DiskCache:
    """Потокобезопасный kv-кэш + история снапшотов опционных цепочек."""

    def __init__(self, path: str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, ts REAL, data TEXT)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS chain_snapshots ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, ts REAL, data TEXT)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_chain_ticker_ts "
                "ON chain_snapshots(ticker, ts)")

    def put(self, key: str, value: dict, ts: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv(key, ts, data) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET ts=excluded.ts, data=excluded.data",
                (key, ts or time.time(), json.dumps(value)))

    def get(self, key: str, max_age: float | None = None) -> tuple[dict, float] | None:
        """Возвращает (значение, ts) или None, если нет/протухло."""
        with self._lock:
            row = self._conn.execute(
                "SELECT ts, data FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        ts, data = row
        if max_age is not None and time.time() - ts > max_age:
            return None
        return json.loads(data), ts

    def add_chain_snapshot(self, ticker: str, snapshot: dict,
                           ts: float | None = None, keep: int = 60) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO chain_snapshots(ticker, ts, data) VALUES(?,?,?)",
                (ticker, ts or time.time(), json.dumps(snapshot)))
            self._conn.execute(
                "DELETE FROM chain_snapshots WHERE ticker=? AND id NOT IN "
                "(SELECT id FROM chain_snapshots WHERE ticker=? ORDER BY ts DESC LIMIT ?)",
                (ticker, ticker, keep))

    def chain_snapshots(self, ticker: str, limit: int = 10) -> list[dict]:
        """Последние снапшоты (старые -> новые)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, data FROM chain_snapshots WHERE ticker=? "
                "ORDER BY ts DESC LIMIT ?", (ticker, limit)).fetchall()
        out = []
        for ts, data in reversed(rows):
            d = json.loads(data)
            d["ts"] = ts
            out.append(d)
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
