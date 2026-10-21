from __future__ import annotations

import sqlite3

import pytest

from seiltanzer import journal as journal_module
from seiltanzer.forecast_outcomes import resolve_forecast_outcome
from seiltanzer.journal import Journal


def _journal_with_forecast(points):
    journal = Journal(":memory:")
    trade = journal.open_trade(1, "NAS100", "long", 100.0, 90.0, 120.0)
    trade_id = trade["id"]
    with journal._conn:
        journal._conn.execute(
            "INSERT INTO option_forecasts("
            "trade_id,ts,price,r,p_take,p_stop,p_unresolved,source,"
            "probability_measure,horizon_minutes,max_r,take_r,be_after_r) "
            "VALUES(?,1000,100,0,0.4,0.3,0.3,'synthetic-test',"
            "'risk_neutral_Q',60,0,2,1.5)", (trade_id,),
        )
        journal._conn.executemany(
            "INSERT INTO trade_market_path(trade_id,ts,r) VALUES(?,?,?)",
            [(trade_id, ts, r) for ts, r in points],
        )
    return journal, trade_id


@pytest.mark.parametrize("points", [
    [(1000, 0), (4000, 0.5), (5000, 0.8), (9000, 3)],
    [(1000, 0), (4600, 0.5), (5000, 0.8), (9000, 3)],
    [(1000, 0), (4600 + 0.5e-9, 0.5), (4600 + 2e-9, 0.6), (9000, 3)],
    [(1000, 0), (4000, 0.5), (4800, float("inf")),
     (4900, "invalid"), (5000, 0.8), (9000, 3)],
    [(1000, 0), (2000, 2.5), (4600, 0.5), (5000, 0.8), (9000, -2)],
    [(1000, 0), (2000, 1.6), (3000, 0), (5000, 0.8), (9000, 3)],
    [(1000, 0), (2000, 0.5)],
])
def test_q_calibration_reads_only_horizon_and_first_valid_after(
    monkeypatch, points,
):
    journal, trade_id = _journal_with_forecast(points)
    full_path = [{"ts": float(ts), "r": r} for ts, r in points]
    consumed = []

    def resolve_and_check(forecast, observed_path):
        consumed.extend(observed_path)
        actual = resolve_forecast_outcome(forecast, observed_path)
        assert actual == resolve_forecast_outcome(forecast, full_path)
        return actual

    monkeypatch.setattr(journal_module, "resolve_forecast_outcome", resolve_and_check)
    try:
        report = journal.q_calibration_report()
        assert report["eligible_forecasts"] == 1
        valid_after = next((ts for ts, r in points
                            if ts > 4600 + 1e-9
                            and _finite(r)), None)
        if valid_after is not None:
            assert consumed[-1]["ts"] == valid_after
        assert journal._conn.execute(
            "SELECT count(*) FROM trade_market_path WHERE trade_id=?", (trade_id,),
        ).fetchone()[0] == len(points)
    finally:
        journal.close()


def _finite(value):
    import math
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


@pytest.mark.parametrize("non_numeric_timestamp", [b"2000", "2_000"])
def test_q_calibration_preserves_legacy_non_numeric_timestamp_order(
    non_numeric_timestamp,
):
    journal, _ = _journal_with_forecast([
        (1000, 0), (4000, 0.5), (5000, 0.8),
        (non_numeric_timestamp, 2.5),
    ])
    try:
        # SQLite puts TEXT/BLOB after numbers; the established resolver sorts
        # float-convertible timestamps and must still see this in-horizon TAKE.
        report = journal.q_calibration_report()
        assert report["outcome_counts"] == {
            "take": 1, "stop_or_be": 0, "no_touch": 0,
        }
    finally:
        journal.close()


def test_q_calibration_large_future_tail_keeps_report_and_bounded_sqlite_work():
    journal, trade_id = _journal_with_forecast(
        [(1000, 0), (4000, 0.5), (5000, 0.8)],
    )
    try:
        expected = journal.q_calibration_report()
        assert expected["outcome_counts"]["no_touch"] == 1
        with journal._conn:
            journal._conn.executemany(
                "INSERT INTO trade_market_path(trade_id,ts,r) VALUES(?,?,?)",
                ((trade_id, 6000 + i, 3.0) for i in range(50_000)),
            )
        callbacks = 0

        def sqlite_budget():
            nonlocal callbacks
            callbacks += 1
            return int(callbacks > 100)

        journal._conn.set_progress_handler(sqlite_budget, 100)
        try:
            actual = journal.q_calibration_report()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"Q calibration scanned irrelevant future history: {exc}")
        finally:
            journal._conn.set_progress_handler(None, 0)
        assert actual == expected
        assert callbacks <= 100
    finally:
        journal.close()
