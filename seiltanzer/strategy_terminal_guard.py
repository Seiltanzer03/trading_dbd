"""Stateful guard for terminal strategy levels.

If a real position remains open after the configured FINAL TAKE has been reached,
strategy authority must require a manual EXIT. This is independent of AI evidence
and does not grant the AI any new execution authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _take_reached(snapshot: dict[str, Any], trade: dict[str, Any]) -> bool:
    geometry = snapshot.get("trade_geometry") or {}
    current = _finite(geometry.get("current"))
    entry = _finite(geometry.get("entry", trade.get("entry")))
    stop = _finite(geometry.get("original_stop", trade.get("stop")))
    take = _finite(geometry.get("final_take", trade.get("take")))
    if None in (current, entry, stop, take):
        return False
    if float(stop) < float(entry):
        sign = 1.0
    elif float(stop) > float(entry):
        sign = -1.0
    else:
        return False
    risk = abs(float(entry) - float(stop))
    tolerance = max(1e-9, risk * 1e-9)
    return sign * (float(current) - float(take)) >= -tolerance


def _strategy_take_decision(original: dict[str, Any], snapshot: dict[str, Any],
                            trade: dict[str, Any]) -> dict[str, Any]:
    manager = snapshot.setdefault("policy_manager", {})
    recommendation = manager.get("recommendation") or {}
    model_policy = str(recommendation.get("policy") or original.get("policy") or "HOLD")
    state = snapshot.get("position_state") or {}
    before = _finite(state.get("remaining_position_fraction"))
    before = 1.0 if before is None else max(0.0, min(1.0, before))
    captured = float(snapshot.get("captured_ts") or time.time())
    geometry_version = str(original.get("geometry_version") or "")
    raw = f"{trade['id']}|{captured:.6f}|STRATEGY_TAKE_EXIT|{geometry_version}"
    decision_id = "decision-" + hashlib.sha256(raw.encode()).hexdigest()[:28]

    arbiter = manager.setdefault("management_arbiter", {})
    arbiter.update({
        "winner": "STRATEGY",
        "effective_policy": "EXIT",
        "model_policy": model_policy,
        "reason": (
            "FINAL TAKE достигнут или пересечён при всё ещё открытом остатке; "
            "терминальное правило стратегии имеет приоритет над AI risk-overlay"
        ),
        "single_authority": True,
        "strategy_terminal_override": True,
        "strategy_terminal_event": "FINAL_TAKE_REACHED",
    })

    return {
        **original,
        "contract_version": "ai-management-decision-f2-v1",
        "trade_id": int(trade["id"]),
        "decision_id": decision_id,
        "authority": "STRATEGY",
        "policy": "EXIT",
        "model_policy": model_policy,
        "execution_status": "pending_execution",
        "manual_execution_required": True,
        "incremental_close_fraction": 1.0,
        "fraction_semantics": "fraction_of_current_remaining_position",
        "remaining_fraction_before_action": round(before, 12),
        "remaining_fraction_after_action": 0.0,
        "continuity": "strategy_terminal_final_take",
        "strategy_terminal_event": "FINAL_TAKE_REACHED",
        "instruction_ru": (
            "FINAL TAKE ДОСТИГНУТ/ПЕРЕСЕЧЁН: ЗАКРЫТЬ ВЕСЬ ТЕКУЩИЙ ОСТАТОК "
            "ПО СТРАТЕГИИ И ПОДТВЕРДИТЬ ИСПОЛНЕНИЕ В ТЕРМИНАЛЕ"
        ),
    }


def install_strategy_terminal_guard() -> None:
    """Install FINAL TAKE priority before app/Engine instances are created."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import position_state as position_module

    cls = position_module.PositionLedger
    original_preview = cls.preview_decision
    original_ack = cls.acknowledge

    def guarded_preview(self: Any, snapshot: dict[str, Any], trade: dict[str, Any]) -> dict[str, Any]:
        decision = original_preview(self, snapshot, trade)
        state = snapshot.get("position_state") or self.state(trade)
        remaining = _finite(state.get("remaining_position_fraction")) or 0.0
        if remaining <= 1e-12 or not _take_reached(snapshot, trade):
            return decision
        return _strategy_take_decision(decision, snapshot, trade)

    def guarded_acknowledge(self: Any, *, decision_id: str, trade: dict[str, Any],
                            executed: bool, execution_price: float | None,
                            execution_r: float | None) -> dict[str, Any]:
        if not executed:
            return original_ack(
                self, decision_id=decision_id, trade=trade, executed=executed,
                execution_price=execution_price, execution_r=execution_r,
            )

        self.ensure_trade(trade)
        with self._lock:
            probe = self._conn.execute(
                "SELECT payload_json FROM management_decisions WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
        if probe is None:
            return original_ack(
                self, decision_id=decision_id, trade=trade, executed=executed,
                execution_price=execution_price, execution_r=execution_r,
            )
        try:
            payload = json.loads(probe[0] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        strategy_take = bool(
            payload.get("authority") == "STRATEGY"
            and payload.get("policy") == "EXIT"
            and payload.get("strategy_terminal_event") == "FINAL_TAKE_REACHED"
        )
        if not strategy_take:
            return original_ack(
                self, decision_id=decision_id, trade=trade, executed=executed,
                execution_price=execution_price, execution_r=execution_r,
            )

        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM management_decisions WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
            if row is None:
                raise ValueError("management decision not found")
            row = dict(row)
            if int(row["trade_id"]) != int(trade["id"]):
                raise position_module.StaleDecisionError("decision belongs to another trade")
            if row["status"] == "executed":
                return {
                    "ok": True, "idempotent": True, "decision_id": decision_id,
                    "execution_status": "executed", "position_state": self.state(trade),
                }
            if row["status"] != "pending_execution":
                raise position_module.StaleDecisionError(f"decision is {row['status']}")
            latest = self._conn.execute(
                "SELECT decision_id FROM management_decisions WHERE trade_id=? "
                "ORDER BY created_ts DESC,rowid DESC LIMIT 1",
                (int(trade["id"]),),
            ).fetchone()
            if latest is None or latest[0] != decision_id:
                raise position_module.StaleDecisionError("newer review superseded this decision")
            state = self.state(trade)
            if row["geometry_version"] != self._geometry_version(trade, state):
                self._conn.execute(
                    "UPDATE management_decisions SET status='superseded' WHERE decision_id=?",
                    (decision_id,),
                )
                raise position_module.StaleDecisionError("trade geometry or position state changed")

            before = float(state["remaining_position_fraction"])
            self._event(
                trade=trade, event_type="TAKE_EXIT", source="human_confirmed_strategy",
                before=before, closed=before, after=0.0,
                review_id=row["review_id"], decision_id=decision_id,
                execution_price=execution_price, execution_r=execution_r,
                active_stop=float(state["active_stop_price"]),
                metadata={
                    "policy": "EXIT",
                    "authority": "STRATEGY",
                    "strategy_terminal_event": "FINAL_TAKE_REACHED",
                    "fraction_semantics": "fraction_of_current_remaining_position",
                },
            )
            self._conn.execute(
                "UPDATE management_decisions SET status='executed',executed_ts=?,"
                "execution_price=?,execution_r=? WHERE decision_id=?",
                (time.time(), position_module._finite(execution_price),
                 position_module._finite(execution_r), decision_id),
            )
        return {
            "ok": True, "idempotent": False, "decision_id": decision_id,
            "execution_status": "executed", "position_state": self.state(trade),
        }

    cls.preview_decision = guarded_preview
    cls.acknowledge = guarded_acknowledge
    _INSTALLED = True
