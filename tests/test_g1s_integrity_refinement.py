from __future__ import annotations

import sqlite3
import threading

import pytest

from seiltanzer import passive_learning as _pl
from seiltanzer.g1_management_local_runtime import ManagementLocalRuntime
from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime


class _Passive:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row


class _Engine:
    def __init__(self, path):
        self.passive = _Passive(path)
        self.management = object()


def test_g1m_local_trading_clock_failure_is_fail_closed(tmp_path, monkeypatch):
    engine = _Engine(tmp_path / "local.sqlite3")
    conn = engine.passive._conn
    conn.executescript("""
        CREATE TABLE g1m_management_observations(
            observation_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL,
            trade_id INTEGER NOT NULL,
            captured_ts REAL NOT NULL,
            origin TEXT NOT NULL,
            policy_edge_eligible INTEGER NOT NULL
        );
        CREATE TABLE g1m_observation_context(
            observation_id TEXT PRIMARY KEY,
            instrument TEXT NOT NULL
        );
    """)
    runtime = ManagementLocalRuntime(engine)
    captured = runtime.activation_ts + 1.0
    conn.execute(
        "INSERT INTO g1m_management_observations VALUES(?,?,?,?,?,?)",
        ("obs-1", "review-1", 1, captured, "LIVE_PROSPECTIVE", 1),
    )
    conn.execute("INSERT INTO g1m_observation_context VALUES(?,?)", ("obs-1", "NAS100"))
    conn.commit()

    def broken_clock(*_args, **_kwargs):
        raise ValueError("clock unavailable")

    monkeypatch.setattr(_pl, "_advance_trading_time", broken_clock)
    assert runtime.materialize_windows() == 0
    assert conn.execute("SELECT COUNT(*) FROM g1m_local_windows").fetchone()[0] == 0
    errors = conn.execute(
        "SELECT code,critical FROM g1m_local_contract_errors ORDER BY horizon_minutes"
    ).fetchall()
    assert len(errors) == 4
    assert {row["code"] for row in errors} == {"TRADING_TIME_ADVANCE_FAILED"}
    assert all(row["critical"] == 1 for row in errors)


def _audit_runtime(tmp_path):
    runtime = ShortHorizonRuntime.__new__(ShortHorizonRuntime)
    runtime._lock = threading.RLock()
    runtime._conn = sqlite3.connect(tmp_path / "audit.sqlite3", check_same_thread=False)
    runtime._conn.row_factory = sqlite3.Row
    runtime._conn.executescript("""
        CREATE TABLE g1_q_capture_attempts(
            attempt_id TEXT PRIMARY KEY,
            attempt_ts REAL NOT NULL,
            target_instrument TEXT NOT NULL,
            observation_created INTEGER NOT NULL,
            created_observation_id TEXT,
            blocker_code TEXT,
            requested_expiry_ts REAL
        );
        CREATE TABLE passive_market_observations(
            observation_id TEXT PRIMARY KEY,
            target_ts REAL,
            resolution_status TEXT,
            instrument TEXT
        );
        CREATE TABLE passive_market_bars(
            instrument TEXT,bar_end_ts REAL,kind TEXT,quality REAL
        );
        CREATE TABLE passive_market_path(
            instrument TEXT,ts REAL,kind TEXT,quality REAL
        );
    """)
    return runtime


def test_q_audit_separates_capture_blockers_from_captured_maturity(tmp_path):
    runtime = _audit_runtime(tmp_path)
    c = runtime._conn
    c.execute(
        "INSERT INTO g1_q_capture_attempts VALUES(?,?,?,?,?,?,?)",
        ("a-block", 1.0, "NAS100", 0, None, "MARKET_CLOSED", None),
    )
    c.execute("INSERT INTO passive_market_observations VALUES(?,?,?,?)",
              ("obs-future", 2000.0, "pending", "NAS100"))
    c.execute("INSERT INTO g1_q_capture_attempts VALUES(?,?,?,?,?,?,?)",
              ("a-future", 2.0, "NAS100", 1, "obs-future", None, 2000.0))
    c.execute("INSERT INTO passive_market_observations VALUES(?,?,?,?)",
              ("obs-due", 500.0, "pending", "NAS100"))
    c.execute("INSERT INTO g1_q_capture_attempts VALUES(?,?,?,?,?,?,?)",
              ("a-due", 3.0, "NAS100", 1, "obs-due", None, 500.0))
    c.execute("INSERT INTO passive_market_bars VALUES(?,?,?,?)",
              ("NAS100", 500.0, "direct", 0.99))
    c.execute("INSERT INTO passive_market_observations VALUES(?,?,?,?)",
              ("obs-blocked", 400.0, "insufficient_future_data", "SP500"))
    c.execute("INSERT INTO g1_q_capture_attempts VALUES(?,?,?,?,?,?,?)",
              ("a-res-block", 4.0, "SP500", 1, "obs-blocked", None, 400.0))
    c.commit()

    report = runtime.q_audit(now=1000.0, limit=20)
    assert report["attempt_n"] == 4
    assert report["captured_n"] == 3
    assert report["capture_blocked_n"] == 1
    assert report["capture_blockers"] == {"MARKET_CLOSED": 1}
    assert report["counts"]["NOT_DUE_YET"] == 1
    assert report["counts"]["DUE_BUT_NOT_RESOLVED"] == 1
    assert report["counts"]["RESOLUTION_BLOCKED"] == 1
    assert "CAPTURE_BLOCKED" not in report["counts"]
    assert report["overdue_is_contract_failure"] is True


def _trade_relevance_runtime(tmp_path):
    runtime = ShortHorizonRuntime.__new__(ShortHorizonRuntime)
    runtime._lock = threading.RLock()
    runtime._conn = sqlite3.connect(tmp_path / "trade-relevance.sqlite3", check_same_thread=False)
    runtime._conn.row_factory = sqlite3.Row
    runtime._conn.executescript("""
        CREATE TABLE g1s_trade_links(
            link_id TEXT PRIMARY KEY,trade_id INTEGER,observation_id TEXT,
            horizon_minutes INTEGER,forecast_age_sec REAL,created_ts REAL
        );
        CREATE TABLE trades(
            id INTEGER PRIMARY KEY,opened_at REAL,instrument TEXT,direction TEXT,
            setup INTEGER,result_r REAL,status TEXT
        );
        CREATE TABLE g1s_resolutions(
            observation_id TEXT PRIMARY KEY,direction_label TEXT,terminal_log_return REAL
        );
        CREATE TABLE g1s_shadow_predictions(
            prediction_id TEXT PRIMARY KEY,observation_id TEXT,model_id TEXT,
            created_ts REAL,p_up REAL
        );
        CREATE TABLE g1s_models(
            model_id TEXT PRIMARY KEY,model_family TEXT,feature_set TEXT,
            created_ts REAL,training_cutoff_ts REAL
        );
    """)
    c = runtime._conn
    c.execute("INSERT INTO trades VALUES(?,?,?,?,?,?,?)",
              (1, 1000.0, "NAS100", "long", 1, 1.2, "closed"))
    c.execute("INSERT INTO g1s_trade_links VALUES(?,?,?,?,?,?)",
              ("link-1", 1, "obs-1", 60, 300.0, 1001.0))
    c.execute("INSERT INTO g1s_resolutions VALUES(?,?,?)", ("obs-1", "UP", 0.01))
    c.commit()
    return runtime


def test_trade_relevance_does_not_call_realised_direction_a_prediction(tmp_path):
    runtime = _trade_relevance_runtime(tmp_path)
    report = runtime.trade_relevance()
    assert report["status"] == "NO_PROSPECTIVE_MODEL_PREDICTIONS"
    assert report["model_evaluable_n"] == 0
    item = report["items"][0]
    assert item["market_move_aligned_with_trade_descriptive"] is True
    assert item["market_move_is_model_prediction"] is False
    assert item["frozen_model_prediction_available_pre_entry"] is False
    assert "market_move_aligned_with_trade" not in item


def test_trade_relevance_uses_only_prediction_frozen_before_entry(tmp_path):
    runtime = _trade_relevance_runtime(tmp_path)
    c = runtime._conn
    c.execute("INSERT INTO g1s_models VALUES(?,?,?,?,?)",
              ("model-old", "REGULARIZED_LOGISTIC", "PRICE_ONLY_V1", 800.0, 700.0))
    c.execute("INSERT INTO g1s_shadow_predictions VALUES(?,?,?,?,?)",
              ("pred-old", "obs-1", "model-old", 900.0, 0.8))
    # A later prediction must not contaminate the pre-entry validation row.
    c.execute("INSERT INTO g1s_models VALUES(?,?,?,?,?)",
              ("model-late", "REGULARIZED_LOGISTIC", "PRICE_ONLY_V1", 1100.0, 900.0))
    c.execute("INSERT INTO g1s_shadow_predictions VALUES(?,?,?,?,?)",
              ("pred-late", "obs-1", "model-late", 1200.0, 0.1))
    c.commit()

    report = runtime.trade_relevance()
    assert report["status"] == "PROSPECTIVE_MODEL_EVALUATION_AVAILABLE"
    assert report["model_evaluable_n"] == 1
    assert report["unique_trades_with_pre_entry_prediction"] == 1
    assert report["brier_move_with_trade_direction"] == pytest.approx(0.04)
    assert report["baseline_0_5_brier"] == pytest.approx(0.25)
    item = report["items"][0]
    assert item["prediction_id"] == "pred-old"
    assert item["p_move_with_trade_direction"] == pytest.approx(0.8)
    assert item["frozen_model_prediction_available_pre_entry"] is True
