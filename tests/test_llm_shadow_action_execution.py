from __future__ import annotations

import time

import pytest

from seiltanzer.position_state import PositionLedger, StaleDecisionError


def _trade(trade_id=1):
    return {
        "id": trade_id,
        "opened_at": 1_000.0,
        "entry": 100.0,
        "stop": 90.0,
        "take": 130.0,
        "max_r": 0.0,
        "direction": "long",
        "status": "open",
    }


def _shadow(policy, parameters, instruction="Выполнить действие"):
    return {
        "status": "ok",
        "policy": policy,
        "confidence": 0.8,
        "production_authority": False,
        "automatic_execution_allowed": False,
        "working_action": {
            "contract_version": "llm-shadow-manual-action-v1",
            "status": "READY_FOR_MANUAL_CONFIRMATION",
            "policy": policy,
            "confidence": 0.8,
            "instruction_ru": instruction,
            "parameters": parameters,
            "manual_confirmation_required": True,
            "automatic_execution_allowed": False,
            "may_widen_stop": False,
            "may_increase_position": False,
        },
    }


def _register(ledger, trade, shadow, *, captured=2_000.0, review="review-1"):
    snapshot = {
        "captured_ts": captured,
        "position_state": ledger.state(trade),
    }
    return ledger.register_shadow_action(snapshot, review, trade, shadow)


def test_confirmed_tighter_stop_is_durable_and_idempotent(tmp_path):
    path = str(tmp_path / "trades.db")
    trade = _trade()
    ledger = PositionLedger(path)
    action = _register(
        ledger, trade,
        _shadow("TIGHTEN_STOP", {"stop_price": 105.0, "anchor": "STRUCTURE"}),
    )
    assert action["execution_status"] == "pending_execution"
    assert action["production_authority"] is False
    result = ledger.acknowledge_shadow_action(
        action_id=action["action_id"], trade=trade, executed=True,
        execution_price=110.0, execution_r=1.0,
    )
    assert result["execution_status"] == "executed"
    assert result["position_state"]["active_stop_price"] == 105.0
    assert result["position_state"]["active_stop_type"] == "TIGHTENED"
    again = ledger.acknowledge_shadow_action(
        action_id=action["action_id"], trade=trade, executed=True,
        execution_price=111.0, execution_r=1.1,
    )
    assert again["idempotent"] is True
    ledger.close()

    reopened = PositionLedger(path)
    assert reopened.state(trade)["active_stop_price"] == 105.0
    assert reopened.events(trade["id"])[-1]["source"] == "human_confirmed_llm_shadow"
    reopened.close()


def test_take_adjustment_updates_authoritative_position_state(tmp_path):
    ledger = PositionLedger(str(tmp_path / "trades.db"))
    trade = _trade()
    action = _register(
        ledger, trade,
        _shadow("EXTEND_TAKE", {"take_price": 140.0, "anchor": "CALL_WALL"}),
    )
    result = ledger.acknowledge_shadow_action(
        action_id=action["action_id"], trade=trade, executed=True,
        execution_price=110.0, execution_r=1.0,
    )
    assert result["position_state"]["original_take"] == 130.0
    assert result["position_state"]["take"] == 140.0
    assert ledger.events(trade["id"])[-1]["event_type"] == "AI_ADJUST_TAKE"
    ledger.close()


def test_conditional_action_is_armed_without_fake_position_reduction(tmp_path):
    ledger = PositionLedger(str(tmp_path / "trades.db"))
    trade = _trade()
    action = _register(
        ledger, trade,
        _shadow("TIME_STOP", {
            "deadline_ts": time.time() + 3_600.0,
            "horizon_minutes": 60.0,
        }),
    )
    result = ledger.acknowledge_shadow_action(
        action_id=action["action_id"], trade=trade, executed=True,
        execution_price=110.0, execution_r=1.0,
    )
    assert result["execution_status"] == "armed"
    state = result["position_state"]
    assert state["remaining_position_fraction"] == 1.0
    assert state["armed_conditional_actions"][-1]["policy"] == "TIME_STOP"
    ledger.close()


def test_stale_or_widening_shadow_action_fails_closed(tmp_path):
    ledger = PositionLedger(str(tmp_path / "trades.db"))
    trade = _trade()
    widening = _register(
        ledger, trade,
        _shadow("TIGHTEN_STOP", {"stop_price": 85.0}),
    )
    with pytest.raises(StaleDecisionError, match="valid tighter stop"):
        ledger.acknowledge_shadow_action(
            action_id=widening["action_id"], trade=trade, executed=True,
            execution_price=110.0, execution_r=1.0,
        )

    old = _register(
        ledger, trade,
        _shadow("MOVE_TO_BE", {"stop_price": 100.0}),
        captured=2_001.0, review="review-old",
    )
    _register(
        ledger, trade,
        _shadow("REDUCE_TAKE", {"take_price": 120.0}),
        captured=2_002.0, review="review-new",
    )
    with pytest.raises(StaleDecisionError, match="superseded"):
        ledger.acknowledge_shadow_action(
            action_id=old["action_id"], trade=trade, executed=False,
            execution_price=110.0, execution_r=1.0,
        )
    ledger.close()


def test_not_executed_records_decision_without_changing_state(tmp_path):
    ledger = PositionLedger(str(tmp_path / "trades.db"))
    trade = _trade()
    action = _register(
        ledger, trade,
        _shadow("MOVE_TO_BE", {"stop_price": 100.0}),
    )
    result = ledger.acknowledge_shadow_action(
        action_id=action["action_id"], trade=trade, executed=False,
        execution_price=110.0, execution_r=1.0,
    )
    assert result["execution_status"] == "recommended_not_executed"
    assert result["position_state"]["active_stop_price"] == 90.0
    assert ledger.shadow_actions(trade["id"])[-1]["status"] == "recommended_not_executed"
    ledger.close()


def test_strategy_break_even_supersedes_a_less_protective_manual_stop(tmp_path):
    ledger = PositionLedger(str(tmp_path / "trades.db"))
    trade = _trade()
    action = _register(
        ledger, trade,
        _shadow("TIGHTEN_STOP", {"stop_price": 95.0}),
    )
    ledger.acknowledge_shadow_action(
        action_id=action["action_id"], trade=trade, executed=True,
        execution_price=110.0, execution_r=1.0,
    )
    trade["max_r"] = 1.5
    state = ledger.sync_be(trade)
    assert state["active_stop_price"] == 100.0
    assert state["active_stop_type"] == "BREAK_EVEN"
    assert ledger.events(trade["id"])[-1]["event_type"] == "BE_ARM"
    ledger.close()
