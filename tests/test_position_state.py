import pytest
from seiltanzer.position_state import PositionLedger, StaleDecisionError

def trade(trade_id=1, **updates):
    row = {"id": trade_id, "opened_at": 1000., "entry": 100., "stop": 90.,
           "take": 125., "max_r": 0., "status": "open"}
    row.update(updates)
    return row

def snapshot(ledger, row, policy, captured=2000.):
    state = ledger.state(row)
    snap = {"captured_ts": captured, "trade_id": row["id"],
            "position_state": state,
            "policy_manager": {"recommendation": {"policy": policy}}}
    snap["policy_manager"]["management_decision"] = ledger.preview_decision(snap, row)
    return snap

def test_relative_fraction_semantics_and_idempotency(tmp_path):
    ledger, row = PositionLedger(str(tmp_path/"trades.db")), trade()
    first = snapshot(ledger, row, "CLOSE_25")
    d1 = first["policy_manager"]["management_decision"]
    ledger.register_decision(first, "review-1", row)
    out = ledger.acknowledge(decision_id=d1["decision_id"], trade=row,
        executed=True, execution_price=105, execution_r=.5)
    assert out["position_state"]["remaining_position_fraction"] == pytest.approx(.75)
    again = ledger.acknowledge(decision_id=d1["decision_id"], trade=row,
        executed=True, execution_price=106, execution_r=.6)
    assert again["idempotent"] is True
    assert again["position_state"]["remaining_position_fraction"] == pytest.approx(.75)
    second = snapshot(ledger, row, "CLOSE_50", captured=2001.)
    d2 = second["policy_manager"]["management_decision"]
    ledger.register_decision(second, "review-2", row)
    out = ledger.acknowledge(decision_id=d2["decision_id"], trade=row,
        executed=True, execution_price=106, execution_r=.6)
    assert out["position_state"]["remaining_position_fraction"] == pytest.approx(.375)
    ledger.close()

def test_exit_and_not_executed(tmp_path):
    ledger, row = PositionLedger(str(tmp_path/"trades.db")), trade()
    no = snapshot(ledger, row, "CLOSE_25")
    decision = no["policy_manager"]["management_decision"]
    ledger.register_decision(no, "review-no", row)
    result = ledger.acknowledge(decision_id=decision["decision_id"], trade=row,
        executed=False, execution_price=105, execution_r=.5)
    assert result["execution_status"] == "recommended_not_executed"
    assert result["position_state"]["remaining_position_fraction"] == 1.
    exit_snap = snapshot(ledger, row, "EXIT", captured=2001.)
    exit_d = exit_snap["policy_manager"]["management_decision"]
    ledger.register_decision(exit_snap, "review-exit", row)
    result = ledger.acknowledge(decision_id=exit_d["decision_id"], trade=row,
        executed=True, execution_price=105, execution_r=.5)
    assert result["position_state"]["remaining_position_fraction"] == 0.
    ledger.close()

def test_new_review_supersedes_old(tmp_path):
    ledger, row = PositionLedger(str(tmp_path/"trades.db")), trade()
    old = snapshot(ledger, row, "CLOSE_25")
    old_d = old["policy_manager"]["management_decision"]
    ledger.register_decision(old, "review-old", row)
    new = snapshot(ledger, row, "CLOSE_10", captured=2001.)
    ledger.register_decision(new, "review-new", row)
    with pytest.raises(StaleDecisionError):
        ledger.acknowledge(decision_id=old_d["decision_id"], trade=row,
            executed=True, execution_price=105, execution_r=.5)
    ledger.close()

def test_original_stop_and_break_even_are_distinct(tmp_path):
    ledger, row = PositionLedger(str(tmp_path/"trades.db")), trade(max_r=1.5)
    state = ledger.sync_be(row)
    assert state["original_stop"] == 90
    assert state["active_stop_price"] == 100
    assert state["active_stop_type"] == "BREAK_EVEN"
    assert any(e["event_type"] == "BE_ARM" for e in ledger.events(row["id"]))
    ledger.close()
