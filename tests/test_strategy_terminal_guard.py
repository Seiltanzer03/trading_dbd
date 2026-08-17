from __future__ import annotations

from seiltanzer import strategy_terminal_guard as guard
from seiltanzer.position_state import PositionLedger


def _trade() -> dict:
    return {
        "id": 117,
        "opened_at": 1_786_970_000.0,
        "entry": 4393.0,
        "stop": 4380.0,
        "take": 4410.0,
        "max_r": 2.5,
    }


def _snapshot(ledger: PositionLedger, trade: dict, current: float) -> dict:
    state = ledger.sync_be(trade)
    risk = abs(trade["entry"] - trade["stop"])
    current_r = (current - trade["entry"]) / risk
    return {
        "captured_ts": 1_786_975_900.0,
        "position_state": state,
        "trade_geometry": {
            "current": current,
            "entry": trade["entry"],
            "original_stop": trade["stop"],
            "active_risk_barrier": trade["entry"],
            "active_risk_barrier_type": "BREAK_EVEN",
            "final_take": trade["take"],
            "current_r": current_r,
            "r_to_final_take": (trade["take"] - current) / risk,
        },
        "policy_manager": {
            "recommendation": {"policy": "HOLD"},
            "management_arbiter": {
                "winner": "STRATEGY",
                "effective_policy": "HOLD",
                "model_policy": "HOLD",
                "reason": "AI overlay inactive",
            },
        },
    }


def test_final_take_crossed_requires_manual_strategy_exit_and_records_take_exit(tmp_path):
    original_preview = PositionLedger.preview_decision
    original_ack = PositionLedger.acknowledge
    original_installed = guard._INSTALLED
    guard._INSTALLED = False
    ledger = PositionLedger(str(tmp_path / "position.db"))
    trade = _trade()
    try:
        guard.install_strategy_terminal_guard()
        snapshot = _snapshot(ledger, trade, 4424.49)
        decision = ledger.preview_decision(snapshot, trade)

        assert decision["authority"] == "STRATEGY"
        assert decision["policy"] == "EXIT"
        assert decision["model_policy"] == "HOLD"
        assert decision["strategy_terminal_event"] == "FINAL_TAKE_REACHED"
        assert decision["manual_execution_required"] is True
        assert decision["execution_status"] == "pending_execution"
        assert decision["incremental_close_fraction"] == 1.0
        assert decision["remaining_fraction_after_action"] == 0.0
        assert snapshot["policy_manager"]["management_arbiter"]["winner"] == "STRATEGY"
        assert snapshot["policy_manager"]["management_arbiter"]["effective_policy"] == "EXIT"

        snapshot["policy_manager"]["management_decision"] = decision
        ledger.register_decision(snapshot, "review-117-test", trade)
        result = ledger.acknowledge(
            decision_id=decision["decision_id"], trade=trade, executed=True,
            execution_price=4424.49, execution_r=(4424.49 - 4393.0) / 13.0,
        )
        assert result["execution_status"] == "executed"
        assert result["position_state"]["remaining_position_fraction"] == 0.0
        latest = ledger.events(trade["id"])[-1]
        assert latest["event_type"] == "TAKE_EXIT"
        assert latest["source"] == "human_confirmed_strategy"
        assert latest["metadata"]["authority"] == "STRATEGY"
    finally:
        ledger.close()
        PositionLedger.preview_decision = original_preview
        PositionLedger.acknowledge = original_ack
        guard._INSTALLED = original_installed


def test_below_final_take_does_not_override_hold(tmp_path):
    original_preview = PositionLedger.preview_decision
    original_ack = PositionLedger.acknowledge
    original_installed = guard._INSTALLED
    guard._INSTALLED = False
    ledger = PositionLedger(str(tmp_path / "position.db"))
    trade = _trade()
    try:
        guard.install_strategy_terminal_guard()
        snapshot = _snapshot(ledger, trade, 4405.55)
        decision = ledger.preview_decision(snapshot, trade)
        assert decision["policy"] == "HOLD"
        assert decision["manual_execution_required"] is False
        assert decision.get("strategy_terminal_event") is None
        assert snapshot["policy_manager"]["management_arbiter"]["effective_policy"] == "HOLD"
    finally:
        ledger.close()
        PositionLedger.preview_decision = original_preview
        PositionLedger.acknowledge = original_ack
        guard._INSTALLED = original_installed
