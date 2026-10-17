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
    assert "mountShadowWorkingAction" in app
    assert "ВЫПОЛНЕНО" in ui
    assert "НЕ ВЫПОЛНЕНО" in ui
    assert "/api/ai/decision/ack" in ui
    assert "/api/ai/shadow-action/ack" in ui


def test_pre_tombstone_orphan_acks_migrate_cleanly_and_pass_fk_check(tmp_path):
    import pytest
    import sqlite3
    from seiltanzer.ai_decision_state import PRE_TOMBSTONE_DELETE_CUTOFF_TS

    db_path = tmp_path / "trades.db"
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute(
            """
            CREATE TABLE trades (
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
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
                deleted_at REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO trades(id,opened_at,setup,instrument,direction,entry,stop,take,status) "
            "VALUES(101, 1787100000.0, 12, 'XAU', 'long', 4100.0, 4050.0, 4200.0, 'closed')"
        )
        conn.execute(
            "INSERT INTO trades(id,opened_at,setup,instrument,direction,entry,stop,take,status) "
            "VALUES(130, 1788100000.0, 12, 'XAU', 'short', 4100.0, 4150.0, 4000.0, 'closed')"
        )
        conn.execute(
            """
            CREATE TABLE ai_decision_acks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                decision_id TEXT NOT NULL,
                policy TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('executed','not_executed')),
                ts REAL NOT NULL,
                note TEXT DEFAULT '',
                UNIQUE(trade_id, decision_id),
                FOREIGN KEY(trade_id) REFERENCES trades(id)
            )
            """
        )
        expected_rows = [
            (1, 77, "T77-1785951040040-CLOSE_10", "CLOSE_10", "executed", 1785951076.6243782, ""),
            (2, 96, "T96-1785994287780-CLOSE_50", "CLOSE_50", "executed", 1785994440.2296243, ""),
            (3, 101, "T101-1787303505926-CLOSE_25", "CLOSE_25", "executed", 1787303551.849059, ""),
            (4, 130, "T130-1788190665107-CLOSE_25", "CLOSE_25", "executed", 1788190944.4563332, ""),
        ]
        conn.executemany(
            "INSERT INTO ai_decision_acks(id,trade_id,decision_id,policy,status,ts,note) "
            "VALUES(?,?,?,?,?,?,?)",
            expected_rows,
        )
    # Before migration, PRAGMA foreign_key_check reports the two pre-tombstone rows.
    assert len(conn.execute("PRAGMA foreign_key_check").fetchall()) == 2
    conn.close()

    # Opening Journal runs the idempotent migration on existing ai_decision_acks.
    journal = Journal(str(db_path))
    try:
        journal._conn.execute("PRAGMA foreign_keys=ON")
        assert journal._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert journal._conn.execute("PRAGMA foreign_key_check(ai_decision_acks)").fetchall() == []

        # Zero fabricated trades rows.
        trade_ids = [
            r[0] for r in journal._conn.execute("SELECT id FROM trades ORDER BY id").fetchall()
        ]
        assert trade_ids == [101, 130]

        # All 4 historical ACK rows and their autoincrement sequence remain intact.
        actual_rows = [
            tuple(r)
            for r in journal._conn.execute(
                "SELECT id,trade_id,decision_id,policy,status,ts,note "
                "FROM ai_decision_acks ORDER BY id"
            ).fetchall()
        ]
        assert actual_rows == expected_rows
        seq = journal._conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='ai_decision_acks'"
        ).fetchone()[0]
        assert seq == 4

        origins = dict(
            journal._conn.execute(
                "SELECT trade_id, lifecycle_origin FROM trade_lifecycle_identities ORDER BY trade_id"
            ).fetchall()
        )
        assert origins == {
            77: "pre_tombstone_hard_deleted_parent",
            96: "pre_tombstone_hard_deleted_parent",
            101: "trades_row_present",
            130: "trades_row_present",
        }

        # New ACKs cannot be inserted for pre-tombstone deleted trades or missing trades.
        for invalid_trade_id in (77, 96, 999):
            with pytest.raises(sqlite3.IntegrityError):
                with journal._conn:
                    journal._conn.execute(
                        "INSERT INTO ai_decision_acks(trade_id,decision_id,policy,status,ts,note) "
                        "VALUES(?,?,?,?,?,?)",
                        (invalid_trade_id, "NEW-DEC", "CLOSE_10", "executed", PRE_TOMBSTONE_DELETE_CUTOFF_TS + 10.0, ""),
                    )

        # Hard-deleting a live trade referenced by ai_decision_acks is forbidden.
        with pytest.raises(sqlite3.IntegrityError):
            with journal._conn:
                journal._conn.execute("DELETE FROM trades WHERE id=101")
    finally:
        journal.close()


def test_post_tombstone_orphan_ack_is_rejected_by_migration(tmp_path):
    import pytest
    import sqlite3
    from seiltanzer.ai_decision_state import (
        PRE_TOMBSTONE_DELETE_CUTOFF_TS,
        ensure_connection_schema,
    )

    db_path = tmp_path / "post_tombstone_orphan.db"
    conn = sqlite3.connect(str(db_path))
    try:
        with conn:
            conn.execute(
                "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, opened_at REAL NOT NULL)"
            )
            conn.execute(
                """
                CREATE TABLE ai_decision_acks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    decision_id TEXT NOT NULL,
                    policy TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('executed','not_executed')),
                    ts REAL NOT NULL,
                    note TEXT DEFAULT '',
                    UNIQUE(trade_id, decision_id),
                    FOREIGN KEY(trade_id) REFERENCES trades(id)
                )
                """
            )
            conn.execute(
                "INSERT INTO ai_decision_acks(id,trade_id,decision_id,policy,status,ts,note) "
                "VALUES(1, 105, 'T105-POST', 'CLOSE_10', 'executed', ?, '')",
                (PRE_TOMBSTONE_DELETE_CUTOFF_TS + 100.0,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="post-tombstone orphan"):
            with conn:
                ensure_connection_schema(conn)
    finally:
        conn.close()

