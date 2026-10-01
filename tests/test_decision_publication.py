import pytest

from seiltanzer.position_state import PositionLedger, StaleDecisionError


def _trade():
    return {"id": 1, "opened_at": 1000., "entry": 100., "stop": 90.,
            "take": 125., "max_r": 0., "status": "open", "direction": "long"}


def _snapshot(ledger, trade, policy, captured):
    row = {"captured_ts": captured, "trade_id": 1,
           "position_state": ledger.state(trade),
           "policy_manager": {"recommendation": {"policy": policy}}}
    row["policy_manager"]["management_decision"] = ledger.preview_decision(row, trade)
    return row


def _extended(stop):
    return {"status": "ok", "policy": "TIGHTEN_STOP",
        "quant_evaluation": {"status": "eligible", "production_authority": True},
        "working_action": {"status": "READY_FOR_MANUAL_CONFIRMATION", "policy": "TIGHTEN_STOP",
            "confidence": .65, "parameters": {"stop_price": stop}, "instruction_ru": "Подтянуть стоп",
            "manual_confirmation_required": True, "automatic_execution_allowed": False}}


def test_failed_publication_restores_prior_pending_in_both_ledgers(tmp_path):
    ledger, trade = PositionLedger(str(tmp_path / "trades.db")), _trade()
    prior = _snapshot(ledger, trade, "CLOSE_25", 2000.)
    old = ledger.register_decision(prior, "old", trade)
    old_extended = ledger.register_shadow_action(prior, "old", trade, _extended(92.))
    new = _snapshot(ledger, trade, "CLOSE_50", 2001.)
    new_id = new["policy_manager"]["management_decision"]["decision_id"]
    new_extended = None
    with pytest.raises(RuntimeError, match="journal unavailable"):
        with ledger.decision_publication(1, "new"):
            ledger.register_decision(new, "new", trade)
            new_extended = ledger.register_shadow_action(new, "new", trade, _extended(94.))
            ledger.supersede_other_pending_actions(1, new_extended["action_id"])
            raise RuntimeError("journal unavailable")
    statuses = {row["decision_id"]: row["status"] for row in ledger._conn.execute(
        "SELECT decision_id,status FROM management_decisions")}
    extended = {row["action_id"]: row["status"] for row in ledger._conn.execute(
        "SELECT action_id,status FROM llm_shadow_manual_actions")}
    assert statuses[old["decision_id"]] == "pending_execution"
    assert extended[old_extended["action_id"]] == "pending_execution"
    assert statuses[new_id] == "publication_failed"
    assert extended[new_extended["action_id"]] == "publication_failed"
    with pytest.raises(StaleDecisionError):
        ledger.acknowledge(decision_id=new_id, trade=trade, executed=True,
                           execution_price=105., execution_r=.5)
    with pytest.raises(StaleDecisionError):
        ledger.acknowledge_shadow_action(action_id=new_extended["action_id"], trade=trade,
                                         executed=True, execution_price=105., execution_r=.5)
    restored = ledger.acknowledge_shadow_action(action_id=old_extended['action_id'], trade=trade,
                                                executed=True, execution_price=105., execution_r=.5)
    assert restored['execution_status'] == 'executed'
    ledger.close()


def test_successful_publication_keeps_only_one_pending_manual_action(tmp_path):
    ledger, trade = PositionLedger(str(tmp_path / "trades.db")), _trade()
    prior = _snapshot(ledger, trade, "CLOSE_25", 2000.)
    ledger.register_decision(prior, "old", trade)
    ledger.register_shadow_action(prior, "old", trade, _extended(92.))
    current = _snapshot(ledger, trade, "HOLD", 2001.)
    with ledger.decision_publication(1, "new"):
        ledger.register_decision(current, "new", trade)
        selected = ledger.register_shadow_action(current, "new", trade, _extended(94.))
        ledger.supersede_other_pending_actions(1, selected["action_id"])
    pending_base = ledger._conn.execute(
        "SELECT COUNT(*) FROM management_decisions WHERE status='pending_execution'").fetchone()[0]
    pending_extended = ledger._conn.execute(
        "SELECT action_id FROM llm_shadow_manual_actions WHERE status='pending_execution'").fetchall()
    assert pending_base == 0
    assert [row[0] for row in pending_extended] == [selected["action_id"]]
    ledger.close()
