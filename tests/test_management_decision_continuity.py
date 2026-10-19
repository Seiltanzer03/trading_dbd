import pytest

from seiltanzer.app import _extended_manual_decision
from seiltanzer.ai_verdict_v9 import _previous_text
from seiltanzer.position_state import PositionLedger
from test_decision_publication import _snapshot, _trade
from test_llm_shadow_action_execution import _shadow


@pytest.mark.parametrize("acknowledgement,expected_status", [
    (None, "pending_execution"),
    (True, "executed"),
    (False, "recommended_not_executed"),
])
def test_next_review_uses_durable_extended_decision_status(tmp_path, acknowledgement, expected_status):
    ledger, trade = PositionLedger(str(tmp_path / "trades.db")), _trade()
    first = _snapshot(ledger, trade, "HOLD", 2000.)
    ledger.register_decision(first, "first", trade)
    action = ledger.register_shadow_action(first, "first", trade,
        _shadow("MOVE_TO_BE", {"stop_price": 100.}))
    if acknowledgement is not None:
        ledger.acknowledge_shadow_action(action_id=action["action_id"], trade=trade,
            executed=acknowledgement, execution_price=105., execution_r=.5)
    next_review = _snapshot(ledger, trade, "HOLD", 2001.)
    decision = next_review["policy_manager"]["management_decision"]
    assert decision["previous"]["decision_id"] == action["action_id"]
    assert decision["previous"]["policy"] == "MOVE_TO_BE"
    assert decision["previous"]["execution_status"] == expected_status
    assert action["action_id"] in _previous_text(decision)
    assert ledger.state(trade)["active_stop_price"] == (100. if acknowledgement is True else 90.)
    ledger.close()


def test_extended_selection_keeps_previous_lifecycle_without_copying_base_execution():
    previous = {"decision_id": "older-action", "policy": "MOVE_TO_BE",
                "execution_status": "executed"}
    base = {"policy": "HOLD", "manual_execution_required": False,
            "remaining_fraction_before_action": .75, "previous": previous}
    action = {"policy": "TIGHTEN_STOP", "action_id": "current-action",
              "execution_status": "pending_execution", "production_authority": True}
    decision = _extended_manual_decision(base, action)
    assert decision["previous"] == previous
    assert decision["decision_id"] == "current-action"
    assert decision["execution_status"] == "pending_execution"
    assert decision["manual_execution_required"] is True


def test_failed_publication_and_other_trade_are_not_previous_decisions(tmp_path):
    ledger, trade = PositionLedger(str(tmp_path / "trades.db")), _trade()
    first = _snapshot(ledger, trade, "HOLD", 2000.)
    old = ledger.register_decision(first, "first", trade)
    failed = _snapshot(ledger, trade, "CLOSE_25", 2001.)
    with pytest.raises(RuntimeError):
        with ledger.decision_publication(trade["id"], "failed"):
            ledger.register_decision(failed, "failed", trade)
            raise RuntimeError("publication failed")
    another = {**trade, "id": 2}
    ledger.register_decision(_snapshot(ledger, another, "CLOSE_50", 2002.), "another", another)
    next_review = _snapshot(ledger, trade, "HOLD", 2003.)
    assert next_review["policy_manager"]["management_decision"]["previous"]["decision_id"] == old["decision_id"]
    ledger.close()


def test_preopening_decision_cannot_become_current_lifecycle_continuity(tmp_path):
    ledger, trade = PositionLedger(str(tmp_path / "trades.db")), _trade()
    ledger.register_decision(_snapshot(ledger, trade, "HOLD", 2000.), "old", trade)
    later_lifecycle = {**trade, "opened_at": 3000.}
    current = _snapshot(ledger, later_lifecycle, "HOLD", 4000.)
    assert current["policy_manager"]["management_decision"]["previous"] is None
    ledger.close()
