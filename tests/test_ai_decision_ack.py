import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import seiltanzer.ai_policy as policy
import seiltanzer.ai_verdict_v12 as verdict_v12
from seiltanzer.ai_decision_state import latest_ack, record_ack
from seiltanzer.app_extensions import install_ai_decision_routes
from seiltanzer.journal import Journal


def _journal_with_open_trade(tmp_path):
    journal = Journal(str(tmp_path / "trades.db"))
    with journal._lock, journal._conn:
        cur = journal._conn.execute(
            "INSERT INTO trades(opened_at,setup,instrument,direction,entry,stop,take,status) "
            "VALUES(?,?,?,?,?,?,?,'open')",
            (1_000.0, 12, "XAU", "long", 4100.0, 4050.0, 4200.0),
        )
    return journal, int(cur.lastrowid)


def _pending_decision(trade_id, decision_id="T1-1000-CLOSE_10"):
    return {
        "trade_id": trade_id,
        "decision_id": decision_id,
        "issued_at": 1_000.0,
        "authority": "AI_OVERRIDE",
        "policy": "CLOSE_10",
        "model_policy": "CLOSE_10",
        "execution_status": "pending_execution",
        "manual_execution_required": True,
        "incremental_close_fraction": 0.10,
        "remaining_fraction_after_action": 0.90,
        "continuity": "new_ai_override",
        "instruction_ru": "ЗАКРЫТЬ 10% ТЕКУЩЕГО ОСТАТКА",
    }


def _record_decision(journal, trade_id, decision):
    snapshot = {
        "trade_id": trade_id,
        "policy_manager": {"management_decision": decision},
    }
    journal.record_ai_verdict(trade_id, snapshot, "test verdict", "test")


def test_executed_ack_is_persisted_idempotently(tmp_path):
    journal, trade_id = _journal_with_open_trade(tmp_path)
    decision = _pending_decision(trade_id)
    _record_decision(journal, trade_id, decision)

    first = record_ack(journal, trade_id, decision["decision_id"], "executed")
    second = record_ack(journal, trade_id, decision["decision_id"], "executed")

    assert first["status"] == "executed"
    assert second["status"] == "executed"
    assert latest_ack(journal, trade_id, decision["decision_id"])["policy"] == "CLOSE_10"


def test_stale_decision_ack_is_rejected(tmp_path):
    journal, trade_id = _journal_with_open_trade(tmp_path)
    old = _pending_decision(trade_id, "OLD")
    new = _pending_decision(trade_id, "NEW")
    _record_decision(journal, trade_id, old)
    _record_decision(journal, trade_id, new)

    try:
        record_ack(journal, trade_id, "OLD", "executed")
    except ValueError as exc:
        assert "заменено новым" in str(exc)
    else:
        raise AssertionError("stale decision was acknowledged")


def test_ack_endpoint_records_broker_execution_state(tmp_path):
    journal, trade_id = _journal_with_open_trade(tmp_path)
    decision = _pending_decision(trade_id)
    _record_decision(journal, trade_id, decision)

    app = FastAPI()
    app.state.engine = SimpleNamespace(journal=journal)
    install_ai_decision_routes(app)
    client = TestClient(app)

    response = client.post(
        "/api/ai/decision/ack",
        json={
            "trade_id": trade_id,
            "decision_id": decision["decision_id"],
            "status": "executed",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ack"]["status"] == "executed"
    assert "не повторит" in body["message"]


def test_not_executed_keeps_same_decision_id():
    previous = _pending_decision(75, "T75-1000-CLOSE_10")
    previous["execution_status"] = "not_executed"
    previous["not_executed_at"] = 1_010.0
    result = {
        "recommendation": {"policy": "CLOSE_10"},
        "gate": {
            "policy": "CLOSE_10",
            "status": "confirmed_degraded_manual",
            "working_action_confirmed": True,
            "degraded_authority_overlay": {"selected": {"policy": "CLOSE_10"}},
        },
        "policies": {
            "HOLD": {"expected_final_r": 2.2, "cvar10_r": 0.9},
            "CLOSE_10": {"expected_final_r": 2.19, "cvar10_r": 1.1},
        },
        "management_arbiter": {
            "winner": "AI",
            "effective_policy": "CLOSE_10",
            "model_policy": "CLOSE_10",
            "reason": "test",
        },
    }

    current = policy.resolve_management_sequence(result, previous, trade_id=75, captured_ts=1_020.0)
    assert current["decision_id"] == previous["decision_id"]
    assert current["execution_status"] == "pending_execution"
    assert current["continuity"] == "continue_same_pending_decision"
    assert current["last_ack_status"] == "not_executed"


def test_executed_ack_prevents_repeating_same_close(tmp_path):
    journal, trade_id = _journal_with_open_trade(tmp_path)
    previous = _pending_decision(trade_id)
    _record_decision(journal, trade_id, previous)
    record_ack(journal, trade_id, previous["decision_id"], "executed")

    engine = SimpleNamespace(journal=journal)
    applied = verdict_v12._apply_ack(engine, trade_id, previous)
    assert applied["execution_status"] == "executed"
    assert applied["last_executed_policy"] == "CLOSE_10"
    assert applied["last_executed_decision_id"] == previous["decision_id"]
    assert applied["executed_action_count"] == 1


def test_frontend_has_one_authoritative_ack_ui():
    util = open("seiltanzer/web/js/util.js", encoding="utf-8").read()
    app = open("seiltanzer/web/js/app.js", encoding="utf-8").read()
    ui = open("seiltanzer/web/js/management_ui.js", encoding="utf-8").read()
    assert "import './ai_decision_ack.js'" not in util
    assert "mountManagementDecision" in app
    assert "ВЫПОЛНЕНО" in ui
    assert "НЕ ВЫПОЛНЕНО" in ui
    assert "/api/ai/decision/ack" in ui
