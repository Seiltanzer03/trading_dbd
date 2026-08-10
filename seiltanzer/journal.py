"""Журнал сделок (sqlite trades.db) + статистика сетапов и настройки аккаунта.

Правило калибровки (ТЗ, п.2 ядра): пока по сетапу в журнале < N закрытых сделок,
вероятностная модель калибруется по встроенной таблице; при >= N — по журналу.
"""

from __future__ import annotations

import json
import math
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
                    quote_offset REAL DEFAULT 0,
                    raw_price_at_open REAL,
                    quote_source TEXT,
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK(status IN ('open','closed'))
                )""")
            # малые идемпотентные миграции существующей локальной БД
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(trades)")]
            if "edge_at_open" not in cols:
                self._conn.execute("ALTER TABLE trades ADD COLUMN edge_at_open REAL")
            if "quote_offset" not in cols:
                self._conn.execute(
                    "ALTER TABLE trades ADD COLUMN quote_offset REAL DEFAULT 0")
            if "raw_price_at_open" not in cols:
                self._conn.execute(
                    "ALTER TABLE trades ADD COLUMN raw_price_at_open REAL")
            if "quote_source" not in cols:
                self._conn.execute(
                    "ALTER TABLE trades ADD COLUMN quote_source TEXT")
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
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS option_forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    ts REAL NOT NULL,
                    price REAL NOT NULL,
                    r REAL NOT NULL,
                    p_take REAL NOT NULL,
                    p_stop REAL NOT NULL,
                    p_unresolved REAL NOT NULL,
                    option_edge REAL,
                    option_ev REAL,
                    chain_ts REAL,
                    chain_age_sec REAL,
                    source TEXT NOT NULL,
                    FOREIGN KEY(trade_id) REFERENCES trades(id)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_forecast_trade_ts "
                "ON option_forecasts(trade_id, ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_verdicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    ts REAL NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    model TEXT,
                    FOREIGN KEY(trade_id) REFERENCES trades(id)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_ai_verdict_trade_ts "
                "ON ai_verdicts(trade_id, ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS policy_shadow_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    ts REAL NOT NULL,
                    old_policy TEXT NOT NULL,
                    candidate_policy TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    review_r REAL NOT NULL,
                    expected_delta_r REAL,
                    cvar_delta_r REAL,
                    execution_cost_delta_r REAL,
                    source_quality REAL,
                    final_result_r REAL,
                    resolved_at REAL,
                    FOREIGN KEY(trade_id) REFERENCES trades(id)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_policy_shadow_trade_ts "
                "ON policy_shadow_reviews(trade_id, ts)")

    # ---------------------------------------------------------------- trades

    @staticmethod
    def _validate_levels(direction: str, entry: float,
                         stop: float, take: float) -> None:
        if direction not in ("long", "short"):
            raise ValueError("direction: long|short")
        vals = (entry, stop, take)
        if not all(math.isfinite(float(x)) and float(x) > 0 for x in vals):
            raise ValueError("вход, стоп и тейк должны быть положительными числами")
        if entry == stop:
            raise ValueError("вход и стоп совпадают")
        if (direction == "long") != (take > entry):
            raise ValueError("тейк должен быть по направлению сделки")
        if (direction == "long") != (stop < entry):
            raise ValueError("стоп должен быть с противоположной стороны от входа")

    def open_trade(self, setup: int, instrument: str, direction: str,
                   entry: float, stop: float, take: float,
                   notes: str = "", zones: list | None = None,
                   quote_offset: float = 0.0,
                   raw_price_at_open: float | None = None,
                   quote_source: str | None = None) -> dict:
        if setup not in SETUPS:
            raise ValueError(f"неизвестный сетап: {setup}")
        self._validate_levels(direction, entry, stop, take)
        if not math.isfinite(float(quote_offset)):
            raise ValueError("basis котировки должен быть конечным числом")
        if self.active_trade() is not None:
            raise ValueError("уже есть открытая сделка — закройте её")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO trades(opened_at, setup, instrument, direction, entry, "
                "stop, take, notes, zones, quote_offset, raw_price_at_open, "
                "quote_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), setup, instrument, direction, entry, stop, take,
                 notes, json.dumps(zones or []), quote_offset,
                 raw_price_at_open, quote_source))
            return self.get_trade(cur.lastrowid)

    def close_trade(self, trade_id: int, result_r: float, notes: str | None = None) -> dict:
        if not math.isfinite(float(result_r)):
            raise ValueError("результат R должен быть конечным числом")
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
            self._conn.execute(
                "UPDATE policy_shadow_reviews SET final_result_r=?, resolved_at=? "
                "WHERE trade_id=? AND final_result_r IS NULL",
                (result_r, time.time(), trade_id))
        return self.get_trade(trade_id)

    def add_closed(self, setup: int, direction: str, entry: float, stop: float,
                   take: float, result_r: float, notes: str = "",
                   opened_at: float | None = None) -> dict:
        """Бэкфилл: добавить уже закрытую сделку в журнал (для истории/статистики)."""
        if setup not in SETUPS:
            raise ValueError(f"неизвестный сетап: {setup}")
        self._validate_levels(direction, entry, stop, take)
        if not math.isfinite(float(result_r)):
            raise ValueError("результат R должен быть конечным числом")
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
        """Фиксирует option P − breakeven P на первом валидном снимке."""
        if edge is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE trades SET edge_at_open=? WHERE id=? AND edge_at_open IS NULL",
                (edge, trade_id))

    def edge_track(self) -> dict:
        """Сбывается ли option edge: исходы сделок с запасом над EV=0 и без него."""
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

    # ------------------------------------------------------ option forecasts

    def record_option_forecast(self, trade_id: int, *, price: float, r: float,
                               p_take: float, p_stop: float,
                               p_unresolved: float, option_edge: float | None,
                               option_ev: float | None, chain_ts: float | None,
                               chain_age_sec: float | None, source: str,
                               min_interval_sec: float = 60.0) -> None:
        """Редкий снимок живого опционного прогноза для честной валидации.

        Тиковый UI продолжает обновляться каждую секунду; в БД пишем не чаще
        раза в минуту, чтобы не раздувать журнал.
        """
        if not all(math.isfinite(float(x)) for x in
                   (price, r, p_take, p_stop, p_unresolved)):
            return
        now = time.time()
        with self._lock, self._conn:
            last = self._conn.execute(
                "SELECT ts FROM option_forecasts WHERE trade_id=? "
                "ORDER BY ts DESC LIMIT 1", (trade_id,)).fetchone()
            if last is not None and now - float(last[0]) < min_interval_sec:
                return
            self._conn.execute(
                "INSERT INTO option_forecasts("
                "trade_id,ts,price,r,p_take,p_stop,p_unresolved,option_edge,"
                "option_ev,chain_ts,chain_age_sec,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade_id, now, price, r, p_take, p_stop, p_unresolved,
                 option_edge, option_ev, chain_ts, chain_age_sec, source))

    def option_forecast_history(self, trade_id: int, limit: int = 120) -> list[dict]:
        """Хронология option-метрик активной сделки для динамического разбора."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts,price,r,p_take,p_stop,p_unresolved,option_edge,"
                "option_ev,chain_ts,chain_age_sec,source FROM option_forecasts "
                "WHERE trade_id=? ORDER BY ts DESC LIMIT ?",
                (trade_id, max(1, int(limit)))).fetchall()
        return [dict(row) for row in reversed(rows)]

    def record_ai_verdict(self, trade_id: int, snapshot: dict,
                          verdict: str, model: str | None = None) -> None:
        """Сохраняет наблюдение ИИ только в контексте конкретной сделки."""
        if not verdict.strip():
            return
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO ai_verdicts(trade_id,ts,snapshot_json,verdict,model) "
                "VALUES(?,?,?,?,?)",
                (trade_id, time.time(), payload, verdict.strip(), model))

    def recent_ai_verdicts(self, trade_id: int, limit: int = 3) -> list[dict]:
        """Последние разборы текущей сделки, от старого к новому."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts,verdict,model FROM ai_verdicts WHERE trade_id=? "
                "ORDER BY ts DESC LIMIT ?", (trade_id, max(1, int(limit)))).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_ai_contexts(self, trade_id: int, limit: int = 3) -> list[dict]:
        """Последние разборы вместе с минимальным машинным контекстом."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts,snapshot_json,verdict,model FROM ai_verdicts WHERE trade_id=? "
                "ORDER BY ts DESC LIMIT ?", (trade_id, max(1, int(limit)))).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            try:
                snapshot = json.loads(item.pop("snapshot_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot = {}
            obs = snapshot.get("observation") or {}
            cone = obs.get("probability_cone") or {}
            tape = (obs.get("position") or {}).get("price_tape") or {}
            item["metrics"] = {
                "r": ((obs.get("position") or {}).get("r")),
                "p_take": ((obs.get("option_probability") or {}).get("p_take_first")),
                "p_stop": ((obs.get("option_probability") or {}).get("touch_stop_horizon")),
                "no_touch": ((obs.get("option_probability") or {}).get("no_touch_horizon")),
                "barrier_ev_r": ((obs.get("option_probability") or {}).get("barrier_ev_r")
                                 if (obs.get("option_probability") or {}).get("barrier_ev_r") is not None
                                 else (obs.get("option_probability") or {}).get("option_ev")),
                "mode_r": cone.get("mode_r"),
                "median_r": cone.get("median_r"),
                "live_short_r": tape.get("directional_short_r"),
            }
            result.append(item)
        return result

    # ------------------------------------------------------- policy shadow OOS

    def record_policy_shadow(
        self, trade_id: int, *, old_policy: str, candidate_policy: str,
        reason: str, review_r: float, expected_delta_r: float | None,
        cvar_delta_r: float | None, execution_cost_delta_r: float | None,
        source_quality: float | None, min_interval_sec: float = 60.0,
    ) -> None:
        """Persist old/candidate decisions before their future outcome is known."""
        if not math.isfinite(float(review_r)):
            return
        now = time.time()
        with self._lock, self._conn:
            last = self._conn.execute(
                "SELECT ts,old_policy,candidate_policy FROM policy_shadow_reviews "
                "WHERE trade_id=? ORDER BY ts DESC LIMIT 1", (trade_id,)).fetchone()
            if (last is not None and now - float(last["ts"]) < min_interval_sec
                    and last["old_policy"] == old_policy
                    and last["candidate_policy"] == candidate_policy):
                return
            self._conn.execute(
                "INSERT INTO policy_shadow_reviews("
                "trade_id,ts,old_policy,candidate_policy,reason,review_r,"
                "expected_delta_r,cvar_delta_r,execution_cost_delta_r,source_quality) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (trade_id, now, old_policy, candidate_policy, reason, review_r,
                 expected_delta_r, cvar_delta_r, execution_cost_delta_r,
                 source_quality))

    def policy_shadow_report(self) -> dict:
        """Observed agreement and conservative outcome proxies; never auto-promotes."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM policy_shadow_reviews ORDER BY ts").fetchall()
        if not rows:
            return {
                "observations": 0, "resolved_observations": 0,
                "resolved_trades": 0, "policy_agreement": None,
                "policy_changes": 0, "expected_improvement_r": None,
                "tail_loss_improvement_r": None, "false_early_exit_proxy": None,
                "false_hold_proxy": None, "turnover_increase": None,
                "execution_cost_increase_r": None,
                "promotion_allowed": False,
                "promotion_reason": "no resolved out-of-sample shadow observations",
            }
        fractions = {
            "HOLD": 0.0, "CLOSE_10": 0.10, "CLOSE_25": 0.25,
            "CLOSE_50": 0.50, "EXIT": 1.0,
        }
        changed = [row for row in rows if row["old_policy"] != row["candidate_policy"]]
        resolved = [row for row in rows if row["final_result_r"] is not None]
        resolved_changed = [row for row in resolved
                            if row["old_policy"] != row["candidate_policy"]]

        def average(key: str, collection) -> float | None:
            values = [float(row[key]) for row in collection if row[key] is not None]
            return sum(values) / len(values) if values else None

        false_early = [
            row for row in resolved_changed
            if fractions.get(row["candidate_policy"], 0.0)
            > fractions.get(row["old_policy"], 0.0)
            and float(row["final_result_r"]) > float(row["review_r"])
        ]
        false_hold = [
            row for row in resolved_changed
            if fractions.get(row["candidate_policy"], 0.0)
            < fractions.get(row["old_policy"], 0.0)
            and float(row["final_result_r"]) < float(row["review_r"])
        ]
        turnover = [
            max(0.0, fractions.get(row["candidate_policy"], 0.0)
                - fractions.get(row["old_policy"], 0.0))
            for row in rows
        ]
        return {
            "observations": len(rows),
            "resolved_observations": len(resolved),
            "resolved_trades": len({int(row["trade_id"]) for row in resolved}),
            "policy_agreement": 1.0 - len(changed) / len(rows),
            "policy_changes": len(changed),
            "expected_improvement_r": average("expected_delta_r", rows),
            "tail_loss_improvement_r": average("cvar_delta_r", rows),
            "false_early_exit_proxy": (
                len(false_early) / len(resolved_changed) if resolved_changed else None),
            "false_hold_proxy": (
                len(false_hold) / len(resolved_changed) if resolved_changed else None),
            "turnover_increase": sum(turnover) / len(turnover),
            "execution_cost_increase_r": average("execution_cost_delta_r", rows),
            "outcome_proxy_warning": (
                "final trade R is not a causal counterfactual for an unexecuted policy; "
                "false-exit/hold fields are diagnostics only"
            ),
            "promotion_allowed": False,
            "promotion_reason": (
                "manual reviewed calibration is required; observation count alone "
                "cannot prove causal policy improvement"
            ),
        }

    def validation_report(self) -> dict:
        """Out-of-sample отчёт по ПЕРВОМУ прогнозу каждой закрытой сделки."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT t.id, t.result_r, t.max_r, t.entry, t.stop, t.take,
                       f.p_take, f.option_edge, f.option_ev, f.source, f.ts
                FROM trades t
                JOIN option_forecasts f ON f.id = (
                    SELECT f2.id FROM option_forecasts f2
                    WHERE f2.trade_id=t.id ORDER BY f2.ts ASC LIMIT 1
                )
                WHERE t.status='closed' AND t.result_r IS NOT NULL
                ORDER BY t.closed_at
            """).fetchall()
        if not rows:
            return {
                "n": 0, "brier": None, "log_loss": None,
                "calibration": [], "censored_n": 0,
                "message": "нет закрытых сделок с прогнозом",
                "policy_shadow": self.policy_shadow_report(),
            }
        resolved: list[tuple[sqlite3.Row, float]] = []
        censored = 0
        for row in rows:
            risk = abs(float(row["entry"]) - float(row["stop"]))
            target_r = abs(float(row["take"]) - float(row["entry"])) / risk
            max_r = row["max_r"]
            if max_r is not None and float(max_r) >= target_r - 1e-6:
                resolved.append((row, 1.0))
            elif float(row["result_r"]) <= -0.95:
                resolved.append((row, 0.0))
            else:
                # Ручной/частичный выход не отвечает на прогноз
                # «тейк раньше стопа» и не должен портить Brier как ложный loss.
                censored += 1
        if not resolved:
            return {
                "n": 0, "brier": None, "log_loss": None,
                "calibration": [], "censored_n": censored,
                "message": "есть прогнозы, но нет закрытых barrier-исходов",
                "policy_shadow": self.policy_shadow_report(),
            }
        ps = [
            min(max(float(row["p_take"]), 1e-6), 1 - 1e-6)
            for row, _ in resolved
        ]
        ys = [outcome for _, outcome in resolved]
        brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(resolved)
        log_loss = -sum(
            y * math.log(p) + (1 - y) * math.log(1 - p)
            for p, y in zip(ps, ys)) / len(resolved)
        bins = []
        for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
            pairs = [(p, y) for p, y in zip(ps, ys) if lo <= p < lo + 0.2]
            if pairs:
                bins.append({
                    "lo": lo, "hi": lo + 0.2, "n": len(pairs),
                    "forecast": sum(p for p, _ in pairs) / len(pairs),
                    "actual": sum(y for _, y in pairs) / len(pairs),
                })
        return {
            "n": len(resolved), "brier": brier, "log_loss": log_loss,
            "calibration": bins,
            "censored_n": censored,
            "positive_edge_n": sum(
                1 for r, _ in resolved
                if r["option_edge"] is not None and r["option_edge"] > 0),
            "warning": ("исследовательская статистика; малые выборки не доказывают "
                        "устойчивое преимущество"),
            "policy_shadow": self.policy_shadow_report(),
        }

    def update_zones(self, trade_id: int, zones: list) -> dict:
        with self._lock, self._conn:
            self._conn.execute("UPDATE trades SET zones=? WHERE id=?",
                               (json.dumps(zones), trade_id))
        return self.get_trade(trade_id)

    def edit_trade(self, trade_id: int, **fields) -> dict:
        """Редактирование сделки: setup/direction/entry/stop/take/result_r/notes.

        Проверяет геометрию уровней; для закрытой сделки допускает правку result_r.
        """
        cur = self.get_trade(trade_id)
        allowed = {"setup", "direction", "entry", "stop", "take", "result_r", "notes"}
        upd = {k: v for k, v in fields.items() if k in allowed and v is not None}
        # UI отправляет всю форму целиком. Не считаем неизменённые уровни новой
        # first-passage задачей, иначе обычная правка заметки стирала прогноз.
        upd = {k: v for k, v in upd.items() if cur.get(k) != v}
        if not upd:
            return cur
        merged = {**cur, **upd}
        if merged["setup"] not in SETUPS:
            raise ValueError(f"неизвестный сетап: {merged['setup']}")
        e, s, tk, d = merged["entry"], merged["stop"], merged["take"], merged["direction"]
        self._validate_levels(d, e, s, tk)
        if "result_r" in upd and not math.isfinite(float(upd["result_r"])):
            raise ValueError("результат R должен быть конечным числом")
        # setup и instrument — одна сущность. Раньше смена сетапа оставляла старый
        # инструмент и могла смешать NAS100-уровни с XAU-котировкой.
        instrument_changed = False
        if "setup" in upd:
            new_instrument = SETUPS[merged["setup"]].instrument
            instrument_changed = new_instrument != cur["instrument"]
            upd["instrument"] = new_instrument

        scenario_changed = bool(
            {"direction", "entry", "stop", "take"} & set(upd)
            or "setup" in upd or instrument_changed
        )
        if scenario_changed:
            # Старый максимум R и первый option forecast относятся к другой
            # задаче first-passage; оставлять их означало бы загрязнить валидацию.
            upd["max_r"] = None
            upd["edge_at_open"] = None
        if instrument_changed:
            # Additive basis был измерен против другого бесплатного ряда.
            upd["quote_offset"] = 0.0
            upd["raw_price_at_open"] = None
            upd["quote_source"] = None

        sets = ", ".join(f"{k}=?" for k in upd)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE trades SET {sets} WHERE id=?",
                               (*upd.values(), trade_id))
            if scenario_changed:
                self._conn.execute(
                    "DELETE FROM option_forecasts WHERE trade_id=?", (trade_id,))
                self._conn.execute(
                    "DELETE FROM ai_verdicts WHERE trade_id=?", (trade_id,))
                self._conn.execute(
                    "DELETE FROM policy_shadow_reviews WHERE trade_id=?", (trade_id,))
            elif "result_r" in upd and cur.get("status") == "closed":
                self._conn.execute(
                    "UPDATE policy_shadow_reviews SET final_result_r=?, resolved_at=? "
                    "WHERE trade_id=?",
                    (upd["result_r"], time.time(), trade_id))
        return self.get_trade(trade_id)

    def delete_trade(self, trade_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM option_forecasts WHERE trade_id=?",
                               (trade_id,))
            self._conn.execute("DELETE FROM ai_verdicts WHERE trade_id=?",
                               (trade_id,))
            self._conn.execute("DELETE FROM policy_shadow_reviews WHERE trade_id=?",
                               (trade_id,))
            n = self._conn.execute("DELETE FROM trades WHERE id=?",
                                   (trade_id,)).rowcount
        if n == 0:
            raise ValueError(f"сделка {trade_id} не найдена")

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
