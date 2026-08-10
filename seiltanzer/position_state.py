"""Authoritative event-sourced economic state for a real user trade."""
from __future__ import annotations
import hashlib, json, math, sqlite3, threading, time
from typing import Any

POLICY_FRACTIONS = {"HOLD": 0.0, "CLOSE_10": .10, "CLOSE_25": .25,
                    "CLOSE_50": .50, "EXIT": 1.0}
POLICY_EVENTS = {"CLOSE_10": "AI_CLOSE_10", "CLOSE_25": "AI_CLOSE_25",
                 "CLOSE_50": "AI_CLOSE_50", "EXIT": "AI_EXIT"}
EVENT_TYPES = {
    "TRADE_OPEN", "AI_CLOSE_10", "AI_CLOSE_25", "AI_CLOSE_50", "AI_EXIT",
    "MANUAL_REDUCTION", "LADDER_REDUCTION", "BE_ARM", "STOP_EXIT", "BE_EXIT",
    "TAKE_EXIT", "MANUAL_EXIT", "POSITION_CORRECTION",
}


class StaleDecisionError(ValueError):
    """The reviewed economic state no longer matches the live trade."""


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PositionLedger:
    version = "position-ledger-f2-v1"

    def __init__(self, path: str):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS position_management_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL, timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL, source TEXT NOT NULL,
                    review_id TEXT, decision_id TEXT,
                    fraction_before REAL NOT NULL, fraction_closed REAL NOT NULL,
                    fraction_after REAL NOT NULL, execution_price REAL,
                    execution_r REAL, original_stop REAL NOT NULL,
                    active_stop REAL NOT NULL, take REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}')""")
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_position_decision_event "
                "ON position_management_events(decision_id) "
                "WHERE decision_id IS NOT NULL")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_position_trade_event "
                "ON position_management_events(trade_id,id)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS management_decisions (
                    decision_id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
                    trade_id INTEGER NOT NULL, created_ts REAL NOT NULL,
                    policy TEXT NOT NULL, status TEXT NOT NULL,
                    close_fraction_current REAL NOT NULL,
                    remaining_before REAL NOT NULL, remaining_after REAL NOT NULL,
                    geometry_version TEXT NOT NULL, entry REAL NOT NULL,
                    original_stop REAL NOT NULL, take_price REAL NOT NULL,
                    executed_ts REAL, execution_price REAL, execution_r REAL,
                    payload_json TEXT NOT NULL)""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_management_decision_trade_ts "
                "ON management_decisions(trade_id,created_ts)")

    def _event(self, *, trade: dict, event_type: str, source: str,
               before: float, closed: float, after: float,
               timestamp: float | None = None, review_id: str | None = None,
               decision_id: str | None = None, execution_price: float | None = None,
               execution_r: float | None = None, active_stop: float | None = None,
               metadata: dict | None = None) -> int:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown position event: {event_type}")
        if not all(math.isfinite(float(x)) for x in (before, closed, after)):
            raise ValueError("position fractions must be finite")
        if not (-1e-12 <= after <= before + 1e-12 <= 1.0 + 1e-12):
            raise ValueError("invalid position fraction transition")
        cur = self._conn.execute(
            "INSERT INTO position_management_events("
            "trade_id,timestamp,event_type,source,review_id,decision_id,"
            "fraction_before,fraction_closed,fraction_after,execution_price,"
            "execution_r,original_stop,active_stop,take,metadata_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(trade["id"]), float(timestamp or time.time()), event_type, source,
             review_id, decision_id, round(before, 12), round(closed, 12),
             round(after, 12), _finite(execution_price), _finite(execution_r),
             float(trade["stop"]),
             float(active_stop if active_stop is not None else trade["stop"]),
             float(trade["take"]), _json(metadata or {})))
        return int(cur.lastrowid)

    def ensure_trade(self, trade: dict) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id FROM position_management_events WHERE trade_id=? LIMIT 1",
                (int(trade["id"]),)).fetchone()
            if row is None:
                self._event(
                    trade=trade, event_type="TRADE_OPEN", source="real_user_trade",
                    before=1.0, closed=0.0, after=1.0,
                    timestamp=float(trade.get("opened_at") or time.time()),
                    execution_price=float(trade["entry"]), execution_r=0.0,
                    metadata={"position_origin": "real_user_trade",
                              "ledger_version": self.version})

    open_trade = ensure_trade

    def events(self, trade_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM position_management_events WHERE trade_id=? "
                "ORDER BY id", (int(trade_id),)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            out.append(item)
        return out

    def state(self, trade: dict) -> dict:
        self.ensure_trade(trade)
        rows = self.events(int(trade["id"]))
        latest = rows[-1]
        remaining = float(latest["fraction_after"])
        realised = sum(
            float(row["fraction_closed"]) * float(row["execution_r"])
            for row in rows if row.get("execution_r") is not None
            and float(row.get("fraction_closed") or 0) > 0)
        be_event = next((row for row in reversed(rows)
                         if row["event_type"] == "BE_ARM"), None)
        inferred_be = bool(
            trade.get("max_r") is not None and float(trade["max_r"]) >= 1.5 - 1e-12)
        be_armed = be_event is not None or inferred_be
        active_stop = float(trade["entry"] if be_armed else trade["stop"])
        return {
            "version": self.version, "position_origin": "real_user_trade",
            "trade_id": int(trade["id"]), "initial_position_fraction": 1.0,
            "remaining_position_fraction": round(remaining, 12),
            "realized_position_fraction": round(1.0 - remaining, 12),
            "realized_r_weighted": round(realised, 8),
            "future_r_semantics": "per_unit_of_current_remaining_position",
            "total_r_semantics":
                "realized_r_weighted + remaining_fraction * future_r",
            "original_stop": float(trade["stop"]),
            "active_stop_type": "BREAK_EVEN" if be_armed else "ORIGINAL_STOP",
            "active_stop_price": active_stop, "take": float(trade["take"]),
            "be_armed": be_armed, "event_count": len(rows),
            "state_version": int(latest["id"]),
        }

    def sync_be(self, trade: dict) -> dict:
        self.ensure_trade(trade)
        if trade.get("max_r") is None or float(trade["max_r"]) < 1.5 - 1e-12:
            return self.state(trade)
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM position_management_events "
                "WHERE trade_id=? AND event_type='BE_ARM' LIMIT 1",
                (int(trade["id"]),)).fetchone()
            if exists is None:
                current = self.state(trade)
                remaining = float(current["remaining_position_fraction"])
                self._event(
                    trade=trade, event_type="BE_ARM", source="strategy_rule",
                    before=remaining, closed=0.0, after=remaining,
                    active_stop=float(trade["entry"]),
                    metadata={"trigger_r": 1.5,
                              "original_stop": trade["stop"]})
        return self.state(trade)

    @staticmethod
    def _geometry_version(trade: dict, state: dict) -> str:
        payload = {
            "trade_id": int(trade["id"]), "entry": float(trade["entry"]),
            "stop": float(trade["stop"]), "take": float(trade["take"]),
            "remaining": float(state["remaining_position_fraction"]),
            "active_stop_type": state["active_stop_type"],
            "active_stop_price": float(state["active_stop_price"])}
        return hashlib.sha256(_json(payload).encode()).hexdigest()[:24]

    def preview_decision(self, snapshot: dict, trade: dict) -> dict:
        recommendation = ((snapshot.get("policy_manager") or {})
                          .get("recommendation") or {})
        policy = str(recommendation.get("policy") or "HOLD")
        if policy not in POLICY_FRACTIONS:
            raise ValueError(f"unsupported management policy: {policy}")
        state = snapshot.get("position_state") or self.state(trade)
        before = float(state["remaining_position_fraction"])
        incremental = POLICY_FRACTIONS[policy]
        after = before * (1.0 - incremental)
        captured = float(snapshot["captured_ts"])
        geometry_version = self._geometry_version(trade, state)
        raw = f"{trade['id']}|{captured:.6f}|{policy}|{geometry_version}"
        decision_id = "decision-" + hashlib.sha256(raw.encode()).hexdigest()[:28]
        manual = incremental > 0.0
        instruction = (f"Закрыть {incremental * 100:.0f}% текущего остатка позиции."
                       if manual else "Не сокращать текущий остаток позиции.")
        return {
            "contract_version": "ai-management-decision-f2-v1",
            "trade_id": int(trade["id"]), "decision_id": decision_id,
            "policy": policy,
            "execution_status": "pending_execution" if manual else "not_required",
            "manual_execution_required": manual,
            "incremental_close_fraction": incremental,
            "fraction_semantics": "fraction_of_current_remaining_position",
            "remaining_fraction_before_action": round(before, 12),
            "remaining_fraction_after_action": round(after, 12),
            "geometry_version": geometry_version, "instruction_ru": instruction}

    def register_decision(self, snapshot: dict, review_id: str, trade: dict) -> dict:
        decision = ((snapshot.get("policy_manager") or {})
                    .get("management_decision") or {})
        if not decision:
            raise ValueError("snapshot lacks management_decision")
        current = self.state(trade)
        if decision.get("geometry_version") != self._geometry_version(trade, current):
            raise StaleDecisionError("decision state changed before registration")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE management_decisions SET status='superseded' "
                "WHERE trade_id=? AND status='pending_execution' "
                "AND decision_id<>?", (int(trade["id"]), decision["decision_id"]))
            self._conn.execute(
                "INSERT OR IGNORE INTO management_decisions("
                "decision_id,review_id,trade_id,created_ts,policy,status,"
                "close_fraction_current,remaining_before,remaining_after,"
                "geometry_version,entry,original_stop,take_price,payload_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (decision["decision_id"], review_id, int(trade["id"]),
                 float(snapshot["captured_ts"]), decision["policy"],
                 decision["execution_status"],
                 float(decision["incremental_close_fraction"]),
                 float(decision["remaining_fraction_before_action"]),
                 float(decision["remaining_fraction_after_action"]),
                 decision["geometry_version"], float(trade["entry"]),
                 float(trade["stop"]), float(trade["take"]), _json(decision)))
        return decision

    def acknowledge(self, *, decision_id: str, trade: dict, executed: bool,
                    execution_price: float | None, execution_r: float | None) -> dict:
        self.ensure_trade(trade)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM management_decisions WHERE decision_id=?",
                (decision_id,)).fetchone()
            if row is None:
                raise ValueError("management decision not found")
            row = dict(row)
            if int(row["trade_id"]) != int(trade["id"]):
                raise StaleDecisionError("decision belongs to another trade")
            if row["status"] == "executed":
                return {"ok": True, "idempotent": True,
                        "decision_id": decision_id,
                        "execution_status": "executed",
                        "position_state": self.state(trade)}
            if row["status"] != "pending_execution":
                raise StaleDecisionError(f"decision is {row['status']}")
            latest = self._conn.execute(
                "SELECT decision_id FROM management_decisions WHERE trade_id=? "
                "ORDER BY created_ts DESC,rowid DESC LIMIT 1",
                (int(trade["id"]),)).fetchone()
            if latest is None or latest[0] != decision_id:
                raise StaleDecisionError("newer review superseded this decision")
            state = self.state(trade)
            if row["geometry_version"] != self._geometry_version(trade, state):
                self._conn.execute(
                    "UPDATE management_decisions SET status='superseded' "
                    "WHERE decision_id=?", (decision_id,))
                raise StaleDecisionError("trade geometry or position state changed")
            if not executed:
                self._conn.execute(
                    "UPDATE management_decisions "
                    "SET status='recommended_not_executed' WHERE decision_id=?",
                    (decision_id,))
                return {"ok": True, "idempotent": False,
                        "decision_id": decision_id,
                        "execution_status": "recommended_not_executed",
                        "position_state": state}
            policy = str(row["policy"])
            event_type = POLICY_EVENTS.get(policy)
            if event_type is None:
                raise ValueError("HOLD does not require execution")
            before = float(state["remaining_position_fraction"])
            relative = float(row["close_fraction_current"])
            closed, after = before * relative, before * (1.0 - relative)
            self._event(
                trade=trade, event_type=event_type,
                source="human_confirmed_ai", before=before, closed=closed,
                after=after, review_id=row["review_id"],
                decision_id=decision_id, execution_price=execution_price,
                execution_r=execution_r,
                active_stop=float(state["active_stop_price"]),
                metadata={"policy": policy, "accepted_ai_recommendation": True,
                          "fraction_semantics":
                              "fraction_of_current_remaining_position"})
            self._conn.execute(
                "UPDATE management_decisions SET status='executed',executed_ts=?,"
                "execution_price=?,execution_r=? WHERE decision_id=?",
                (time.time(), _finite(execution_price), _finite(execution_r),
                 decision_id))
        return {"ok": True, "idempotent": False, "decision_id": decision_id,
                "execution_status": "executed",
                "position_state": self.state(trade)}

    def supersede_trade(self, trade_id: int, reason: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE management_decisions SET status='superseded' "
                "WHERE trade_id=? AND status='pending_execution'",
                (int(trade_id),))

    def terminal_exit(self, trade: dict, *, event_type: str = "MANUAL_EXIT",
                      execution_price: float | None = None,
                      execution_r: float | None = None) -> dict:
        if event_type not in {"STOP_EXIT", "BE_EXIT", "TAKE_EXIT", "MANUAL_EXIT"}:
            raise ValueError("invalid terminal exit type")
        state = self.state(trade)
        before = float(state["remaining_position_fraction"])
        if before <= 1e-12:
            return state
        with self._lock, self._conn:
            self._event(
                trade=trade, event_type=event_type, source="real_user_trade",
                before=before, closed=before, after=0.0,
                execution_price=execution_price, execution_r=execution_r,
                active_stop=float(state["active_stop_price"]))
            self.supersede_trade(int(trade["id"]), "position_closed")
        return self.state(trade)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
