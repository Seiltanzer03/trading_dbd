import sqlite3
import time

import pytest

from seiltanzer.journal import Journal
from seiltanzer.position_state import PositionLedger


@pytest.fixture
def stores(tmp_path):
    db_path = tmp_path / "trades.db"
    journal = Journal(str(db_path))
    position = PositionLedger(str(db_path))
    # The historical bug becomes a real FK failure once SQLite enforcement is
    # enabled because trade_market_path references trades and hard-delete forgot
    # that canonical research path. Production deletion must be safe either way.
    journal._conn.execute("PRAGMA foreign_keys=ON")
    yield journal, position
    position.close()
    journal.close()


def _open_trade(journal: Journal) -> dict:
    return journal.open_trade(3, "NAS100", "long", 100.0, 99.0, 102.5)


def _seed_pending_decision(position: PositionLedger, trade: dict,
                           decision_id: str = "decision-delete-test") -> str:
    position.ensure_trade(trade)
    with position._lock, position._conn:
        position._conn.execute(
            "INSERT INTO management_decisions("
            "decision_id,review_id,trade_id,created_ts,policy,status,"
            "close_fraction_current,remaining_before,remaining_after,"
            "geometry_version,entry,original_stop,take_price,payload_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id, "review-delete-test", int(trade["id"]), time.time(),
                "CLOSE_25", "pending_execution", 0.25, 1.0, 0.75, "geom-test",
                float(trade["entry"]), float(trade["stop"]), float(trade["take"]),
                "{}",
            ),
        )
    return decision_id


def _seed_research(journal: Journal, trade: dict) -> None:
    now = time.time()
    journal.record_option_forecast(
        trade["id"], price=100.0, r=0.0,
        p_take=0.4, p_stop=0.3, p_unresolved=0.3,
        option_edge=0.1, option_ev=0.2,
        chain_ts=now, chain_age_sec=1.0, source="delete-regression",
        min_interval_sec=0,
        horizon_minutes=15.0, max_r=0.0, take_r=2.5, be_after_r=1.5,
    )
    journal.record_ai_verdict(
        trade["id"], {"trade_id": trade["id"]},
        "retain immutable evidence", "delete-regression",
    )
    journal.record_decision_market_point(
        trade["id"], ts=now + 1.0, price=100.1, r=0.1,
        min_interval_sec=0,
    )


def test_delete_hides_operational_trade_but_preserves_research_and_audit(stores):
    journal, position = stores
    trade = _open_trade(journal)
    decision_id = _seed_pending_decision(position, trade)
    _seed_research(journal, trade)

    # This used to be vulnerable to FOREIGN KEY constraint failed because the
    # hard-delete path did not remove trade_market_path. The corrected contract
    # does not destroy that immutable path at all.
    journal.delete_trade(trade["id"])

    assert journal.active_trade() is None
    assert journal.list_trades() == []
    with pytest.raises(ValueError, match="не найдена"):
        journal.get_trade(trade["id"])

    raw_trade = journal._conn.execute(
        "SELECT status,deleted_at FROM trades WHERE id=?", (trade["id"],)
    ).fetchone()
    assert raw_trade is not None
    assert raw_trade["status"] == "open"  # no fabricated economic exit
    assert raw_trade["deleted_at"] is not None

    for table in ("option_forecasts", "ai_verdicts", "trade_market_path"):
        count = journal._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE trade_id=?", (trade["id"],)
        ).fetchone()[0]
        assert count >= 1, f"{table} evidence was unexpectedly destroyed"

    # Event history remains an audit trail; only actionable pending management
    # is terminated so no phantom CLOSE/EXIT survives user deletion.
    assert position._conn.execute(
        "SELECT COUNT(*) FROM position_management_events WHERE trade_id=?",
        (trade["id"],),
    ).fetchone()[0] >= 1
    status = position._conn.execute(
        "SELECT status FROM management_decisions WHERE decision_id=?",
        (decision_id,),
    ).fetchone()[0]
    assert status == "superseded"

    # Preserve the established API contract: a repeated delete is controlled
    # not-found (mapped by FastAPI to 400), never an unhandled 500.
    with pytest.raises(ValueError, match="не найдена"):
        journal.delete_trade(trade["id"])


def test_deleted_open_trade_does_not_block_next_trade_or_accept_stale_mutations(stores):
    journal, _position = stores
    trade = _open_trade(journal)
    journal.update_max_r(trade["id"], 0.4)
    journal.delete_trade(trade["id"])

    before = journal._conn.execute(
        "SELECT status,notes,zones,max_r,edge_at_open,deleted_at "
        "FROM trades WHERE id=?",
        (trade["id"],),
    ).fetchone()

    # Public/user mutations must fail before touching a retained tombstone.
    with pytest.raises(ValueError, match="не найдена"):
        journal.update_zones(trade["id"], [{"price": 999.0}])
    with pytest.raises(ValueError, match="не найдена"):
        journal.edit_trade(trade["id"], notes="stale edit")
    with pytest.raises(ValueError, match="не найдена"):
        journal.close_trade(trade["id"], 1.0, notes="stale close")

    # Internal market-loop updates may race with the delete boundary; they must
    # quietly become no-ops rather than changing the retained audit row.
    journal.update_max_r(trade["id"], 9.0)
    journal.update_edge_at_open(trade["id"], 0.99)

    after = journal._conn.execute(
        "SELECT status,notes,zones,max_r,edge_at_open,deleted_at "
        "FROM trades WHERE id=?",
        (trade["id"],),
    ).fetchone()
    assert dict(after) == dict(before)

    # A tombstoned trade deliberately keeps economic status='open' for audit,
    # but operational active_trade() ignores it, so a new trade can be opened.
    replacement = journal.open_trade(
        3, "NAS100", "long", 101.0, 100.0, 103.5,
        notes="replacement after delete",
    )
    assert replacement["id"] != trade["id"]
    assert journal.active_trade()["id"] == replacement["id"]


def test_deleted_closed_trade_leaves_user_stats_but_retains_raw_economic_row(stores):
    journal, _position = stores
    trade = journal.add_closed(
        3, "long", 100.0, 99.0, 102.5, 1.25,
        notes="closed delete stats regression",
    )
    assert journal.journal_counts(3) == (1, 1)
    assert journal.setup_stats(3, min_journal_trades=1).source == "journal"

    journal.delete_trade(trade["id"])

    assert journal.journal_counts(3) == (0, 0)
    assert journal.setup_stats(3, min_journal_trades=1).source == "builtin"
    raw = journal._conn.execute(
        "SELECT status,result_r,deleted_at FROM trades WHERE id=?",
        (trade["id"],),
    ).fetchone()
    assert raw["status"] == "closed"
    assert raw["result_r"] == pytest.approx(1.25)
    assert raw["deleted_at"] is not None


def test_delete_transaction_rolls_back_management_if_tombstone_write_fails(stores):
    journal, position = stores
    trade = _open_trade(journal)
    decision_id = _seed_pending_decision(
        position, trade, decision_id="decision-delete-rollback"
    )

    with journal._conn:
        journal._conn.execute(
            "CREATE TRIGGER fail_trade_tombstone "
            "BEFORE UPDATE OF deleted_at ON trades "
            "WHEN NEW.id=OLD.id BEGIN "
            "SELECT RAISE(ABORT, 'forced tombstone failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced tombstone failure"):
        journal.delete_trade(trade["id"])

    raw_trade = journal._conn.execute(
        "SELECT deleted_at FROM trades WHERE id=?", (trade["id"],)
    ).fetchone()
    assert raw_trade["deleted_at"] is None
    status = position._conn.execute(
        "SELECT status FROM management_decisions WHERE decision_id=?",
        (decision_id,),
    ).fetchone()[0]
    assert status == "pending_execution"
    assert journal.active_trade()["id"] == trade["id"]
