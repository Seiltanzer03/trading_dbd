"""Authoritative event-sourced economic state for a real user trade."""
from __future__ import annotations
import hashlib, json, math, sqlite3, threading, time
from typing import Any

POLICY_FRACTIONS = {"HOLD": 0.0, "CLOSE_10": .10, "CLOSE_25": .25,
                    "CLOSE_50": .50, "EXIT": 1.0}
POLICY_EVENTS = {"CLOSE_10": "AI_CLOSE_10", "CLOSE_25": "AI_CLOSE_25",
                 "CLOSE_50": "AI_CLOSE_50", "EXIT": "AI_EXIT"}
EXTENDED_STOP_POLICIES = {"MOVE_TO_BE", "TRAIL_GAMMA_FLIP", "TIGHTEN_STOP"}
EXTENDED_TAKE_POLICIES = {"EXTEND_TAKE", "REDUCE_TAKE"}
EXTENDED_CONDITIONAL_POLICIES = {"SCALE_OUT_ON_SPIKE", "TIME_STOP"}
EXTENDED_POLICIES = (
    EXTENDED_STOP_POLICIES | EXTENDED_TAKE_POLICIES
    | EXTENDED_CONDITIONAL_POLICIES
)
EVENT_TYPES = {
    "TRADE_OPEN", "AI_CLOSE_10", "AI_CLOSE_25", "AI_CLOSE_50", "AI_EXIT",
    "MANUAL_REDUCTION", "LADDER_REDUCTION", "BE_ARM", "STOP_EXIT", "BE_EXIT",
    "TAKE_EXIT", "MANUAL_EXIT", "POSITION_CORRECTION", "AI_MOVE_TO_BE",
    "AI_TIGHTEN_STOP", "AI_ADJUST_TAKE", "AI_SCALE_OUT_ARM", "AI_TIME_STOP_ARM",
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


def _direction(trade: dict) -> str | None:
    value = str(trade.get("direction") or "").lower()
    if value in {"long", "buy", "лонг"}:
        return "long"
    if value in {"short", "sell", "шорт"}:
        return "short"
    entry, stop = _finite(trade.get("entry")), _finite(trade.get("stop"))
    if entry is not None and stop is not None:
        if stop < entry:
            return "long"
        if stop > entry:
            return "short"
    return None


def _is_tighter_stop(trade: dict, current_price: float, active_stop: float,
                     candidate: float) -> bool:
    direction = _direction(trade)
    if direction == "long":
        return active_stop < candidate < current_price
    if direction == "short":
        return current_price < candidate < active_stop
    return False


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
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_shadow_manual_actions (
                    action_id TEXT PRIMARY KEY, review_id TEXT NOT NULL,
                    trade_id INTEGER NOT NULL, created_ts REAL NOT NULL,
                    policy TEXT NOT NULL, status TEXT NOT NULL,
                    geometry_version TEXT NOT NULL, confidence REAL NOT NULL,
                    parameters_json TEXT NOT NULL, payload_json TEXT NOT NULL,
                    acknowledged_ts REAL, execution_price REAL, execution_r REAL
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_shadow_action_trade_ts "
                "ON llm_shadow_manual_actions(trade_id,created_ts)")

    def _event(self, *, trade: dict, event_type: str, source: str,
               before: float, closed: float, after: float,
               timestamp: float | None = None, review_id: str | None = None,
               decision_id: str | None = None, execution_price: float | None = None,
               execution_r: float | None = None, active_stop: float | None = None,
               take_price: float | None = None,
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
             float(take_price if take_price is not None else trade["take"]),
             _json(metadata or {})))
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
        stop_event = next((row for row in reversed(rows) if row["event_type"] in {
            "BE_ARM", "AI_MOVE_TO_BE", "AI_TIGHTEN_STOP",
        }), None)
        inferred_be = bool(
            trade.get("max_r") is not None and float(trade["max_r"]) >= 1.5 - 1e-12)
        be_armed = bool(
            inferred_be or (stop_event and stop_event["event_type"] in {
                "BE_ARM", "AI_MOVE_TO_BE",
            })
        )
        if stop_event is not None:
            active_stop = float(stop_event["active_stop"])
        else:
            active_stop = float(trade["entry"] if inferred_be else trade["stop"])
        entry = float(trade["entry"])
        direction = _direction(trade)
        if inferred_be and (
            (direction == "long" and active_stop < entry)
            or (direction == "short" and active_stop > entry)
        ):
            # The deterministic strategy BE rule remains authoritative even if
            # a previously confirmed shadow stop was less protective.
            active_stop = entry
        active_stop_type = (
            "BREAK_EVEN" if be_armed and abs(active_stop-float(trade["entry"])) <= 1e-12
            else "TIGHTENED" if abs(active_stop-float(trade["stop"])) > 1e-12
            else "ORIGINAL_STOP"
        )
        take_event = next((row for row in reversed(rows)
                           if row["event_type"] == "AI_ADJUST_TAKE"), None)
        active_take = float(take_event["take"] if take_event else trade["take"])
        conditional = [
            {
                "event_id": int(row["id"]),
                "event_type": row["event_type"],
                "policy": row["metadata"].get("policy"),
                "parameters": row["metadata"].get("parameters") or {},
                "armed_ts": float(row["timestamp"]),
            }
            for row in rows if row["event_type"] in {
                "AI_SCALE_OUT_ARM", "AI_TIME_STOP_ARM",
            }
        ]
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
            "active_stop_type": active_stop_type,
            "active_stop_price": active_stop,
            "original_take": float(trade["take"]), "take": active_take,
            "be_armed": be_armed, "event_count": len(rows),
            "armed_conditional_actions": conditional,
            "state_version": int(latest["id"]),
        }

    def sync_be(self, trade: dict) -> dict:
        self.ensure_trade(trade)
        if trade.get("max_r") is None or float(trade["max_r"]) < 1.5 - 1e-12:
            return self.state(trade)
        with self._lock, self._conn:
            entry = float(trade["entry"])
            direction = _direction(trade)
            stop_event = self._conn.execute(
                "SELECT active_stop FROM position_management_events "
                "WHERE trade_id=? AND event_type IN "
                "('BE_ARM','AI_MOVE_TO_BE','AI_TIGHTEN_STOP') "
                "ORDER BY id DESC LIMIT 1", (int(trade["id"]),),
            ).fetchone()
            active = float(stop_event[0] if stop_event is not None else trade["stop"])
            should_tighten = (
                (direction == "long" and active < entry - 1e-12)
                or (direction == "short" and active > entry + 1e-12)
            )
            if should_tighten:
                current = self.state(trade)
                remaining = float(current["remaining_position_fraction"])
                self._event(
                    trade=trade, event_type="BE_ARM", source="strategy_rule",
                    before=remaining, closed=0.0, after=remaining,
                    active_stop=entry,
                    take_price=float(current["take"]),
                    metadata={"trigger_r": 1.5,
                              "original_stop": trade["stop"]})
        return self.state(trade)

    @staticmethod
    def _geometry_version(trade: dict, state: dict) -> str:
        payload = {
            "trade_id": int(trade["id"]), "entry": float(trade["entry"]),
            "stop": float(trade["stop"]),
            "original_take": float(trade["take"]),
            "remaining": float(state["remaining_position_fraction"]),
            "active_stop_type": state["active_stop_type"],
            "active_stop_price": float(state["active_stop_price"]),
            "take": float(state.get("take", trade["take"])),
            "state_version": int(state.get("state_version") or 0)}
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
                 float(trade["stop"]), float(current.get("take", trade["take"])),
                 _json(decision)))
        return decision

    def register_shadow_action(self, snapshot: dict, review_id: str,
                               trade: dict, shadow: dict) -> dict | None:
        """Freeze one exact LLM extended action for optional manual execution."""
        action = dict(shadow.get("working_action") or {})
        policy = str(action.get("policy") or "")
        if policy not in EXTENDED_POLICIES:
            return None
        if action.get("status") != "READY_FOR_MANUAL_CONFIRMATION":
            return None
        confidence = _finite(action.get("confidence"))
        parameters = action.get("parameters")
        if confidence is None or not isinstance(parameters, dict):
            raise ValueError("invalid extended shadow action")
        state = self.state(trade)
        geometry_version = self._geometry_version(trade, state)
        snapshot_state = snapshot.get("position_state") or {}
        if snapshot_state and int(snapshot_state.get("state_version") or 0) != int(
            state["state_version"]
        ):
            raise StaleDecisionError("position state changed before shadow action registration")
        captured = float(snapshot["captured_ts"])
        action_payload = {
            **action,
            "review_id": str(review_id),
            "trade_id": int(trade["id"]),
            "geometry_version": geometry_version,
            "execution_status": "pending_execution",
            "manual_execution_required": True,
            "production_authority": False,
            "automatic_execution_allowed": False,
        }
        raw = _json({
            "trade_id": int(trade["id"]), "captured_ts": captured,
            "policy": policy, "parameters": parameters,
            "geometry_version": geometry_version,
        })
        action_id = "shadow-action-" + hashlib.sha256(raw.encode()).hexdigest()[:28]
        action_payload["action_id"] = action_id
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE llm_shadow_manual_actions SET status='superseded' "
                "WHERE trade_id=? AND status='pending_execution' AND action_id<>?",
                (int(trade["id"]), action_id),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO llm_shadow_manual_actions("
                "action_id,review_id,trade_id,created_ts,policy,status,"
                "geometry_version,confidence,parameters_json,payload_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (action_id, str(review_id), int(trade["id"]), captured, policy,
                 "pending_execution", geometry_version, confidence,
                 _json(parameters), _json(action_payload)),
            )
        return action_payload

    def shadow_actions(self, trade_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM llm_shadow_manual_actions WHERE trade_id=? "
                "ORDER BY created_ts,action_id", (int(trade_id),),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            output.append(item)
        return output

    def acknowledge_shadow_action(
        self, *, action_id: str, trade: dict, executed: bool,
        execution_price: float | None, execution_r: float | None,
    ) -> dict:
        """Record a broker-confirmed manual extended action, never place an order."""
        self.ensure_trade(trade)
        with self._lock, self._conn:
            raw = self._conn.execute(
                "SELECT * FROM llm_shadow_manual_actions WHERE action_id=?",
                (str(action_id),),
            ).fetchone()
            if raw is None:
                raise ValueError("shadow action not found")
            row = dict(raw)
            if int(row["trade_id"]) != int(trade["id"]):
                raise StaleDecisionError("shadow action belongs to another trade")
            if row["status"] in {"executed", "armed"}:
                return {
                    "ok": True, "idempotent": True, "action_id": action_id,
                    "execution_status": row["status"],
                    "position_state": self.state(trade),
                }
            if row["status"] != "pending_execution":
                raise StaleDecisionError(f"shadow action is {row['status']}")
            latest = self._conn.execute(
                "SELECT action_id FROM llm_shadow_manual_actions WHERE trade_id=? "
                "ORDER BY created_ts DESC,rowid DESC LIMIT 1",
                (int(trade["id"]),),
            ).fetchone()
            if latest is None or latest[0] != action_id:
                raise StaleDecisionError("newer review superseded this shadow action")
            state = self.state(trade)
            if row["geometry_version"] != self._geometry_version(trade, state):
                self._conn.execute(
                    "UPDATE llm_shadow_manual_actions SET status='superseded' "
                    "WHERE action_id=?", (action_id,),
                )
                raise StaleDecisionError("trade geometry or position state changed")
            if not executed:
                self._conn.execute(
                    "UPDATE llm_shadow_manual_actions SET status='recommended_not_executed',"
                    "acknowledged_ts=? WHERE action_id=?", (time.time(), action_id),
                )
                return {
                    "ok": True, "idempotent": False, "action_id": action_id,
                    "execution_status": "recommended_not_executed",
                    "position_state": state,
                }

            policy = str(row["policy"])
            parameters = json.loads(row["parameters_json"] or "{}")
            before = float(state["remaining_position_fraction"])
            active_stop = float(state["active_stop_price"])
            active_take = float(state["take"])
            current_price = _finite(execution_price)
            if current_price is None:
                raise StaleDecisionError("current execution price unavailable")
            event_type: str
            final_status = "executed"
            event_metadata = {
                "policy": policy, "parameters": parameters,
                "accepted_llm_shadow_action": True,
                "production_authority": False,
                "broker_confirmed": True,
            }
            if policy in EXTENDED_STOP_POLICIES:
                candidate = _finite(parameters.get("stop_price"))
                if candidate is None or not _is_tighter_stop(
                    trade, current_price, active_stop, candidate
                ):
                    raise StaleDecisionError("shadow stop is no longer a valid tighter stop")
                active_stop = candidate
                event_type = (
                    "AI_MOVE_TO_BE" if policy == "MOVE_TO_BE" else "AI_TIGHTEN_STOP"
                )
            elif policy in EXTENDED_TAKE_POLICIES:
                candidate = _finite(parameters.get("take_price"))
                direction = _direction(trade)
                if direction == "long":
                    valid = bool(
                        candidate is not None and candidate > current_price
                        and ((policy == "REDUCE_TAKE" and candidate < active_take)
                             or (policy == "EXTEND_TAKE" and candidate > active_take))
                    )
                elif direction == "short":
                    valid = bool(
                        candidate is not None and candidate < current_price
                        and ((policy == "REDUCE_TAKE" and candidate > active_take)
                             or (policy == "EXTEND_TAKE" and candidate < active_take))
                    )
                else:
                    valid = False
                if not valid:
                    raise StaleDecisionError("shadow take is no longer valid")
                active_take = float(candidate)
                event_type = "AI_ADJUST_TAKE"
            elif policy == "SCALE_OUT_ON_SPIKE":
                trigger = _finite(parameters.get("trigger_price"))
                fraction = _finite(parameters.get("close_fraction"))
                direction = _direction(trade)
                valid = bool(
                    trigger is not None and fraction is not None and 0 < fraction <= 1
                    and ((direction == "long" and current_price < trigger <= active_take)
                         or (direction == "short" and active_take <= trigger < current_price))
                )
                if not valid:
                    raise StaleDecisionError("conditional scale-out trigger is no longer valid")
                event_type, final_status = "AI_SCALE_OUT_ARM", "armed"
            elif policy == "TIME_STOP":
                deadline = _finite(parameters.get("deadline_ts"))
                if deadline is None or deadline <= time.time():
                    raise StaleDecisionError("time-stop deadline has already expired")
                event_type, final_status = "AI_TIME_STOP_ARM", "armed"
            else:
                raise ValueError("unsupported extended shadow policy")

            self._event(
                trade=trade, event_type=event_type,
                source="human_confirmed_llm_shadow", before=before, closed=0.0,
                after=before, review_id=row["review_id"], decision_id=action_id,
                execution_price=current_price, execution_r=execution_r,
                active_stop=active_stop, take_price=active_take,
                metadata=event_metadata,
            )
            self._conn.execute(
                "UPDATE llm_shadow_manual_actions SET status=?,acknowledged_ts=?,"
                "execution_price=?,execution_r=? WHERE action_id=?",
                (final_status, time.time(), current_price, _finite(execution_r), action_id),
            )
        return {
            "ok": True, "idempotent": False, "action_id": action_id,
            "execution_status": final_status,
            "position_state": self.state(trade),
        }

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
            self._conn.execute(
                "UPDATE llm_shadow_manual_actions SET status='superseded' "
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
