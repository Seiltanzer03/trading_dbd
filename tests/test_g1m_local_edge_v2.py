from __future__ import annotations

import json
import sqlite3
import threading

from seiltanzer.g1_management_local_edge_v2 import (
    _maturity,
    _summarize,
    management_edge_v2,
)


class _Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE g1m_local_windows(
                window_id TEXT PRIMARY KEY,horizon_minutes INTEGER,trade_id INTEGER,
                observation_id TEXT,evidence_eligible INTEGER,origin TEXT,
                instrument TEXT,captured_ts REAL
            );
            CREATE TABLE g1m_local_outcomes(
                window_id TEXT PRIMARY KEY,mfe_r REAL,mae_r REAL
            );
            CREATE TABLE g1m_management_observations(
                observation_id TEXT PRIMARY KEY,production_policy TEXT,current_r REAL
            );
            CREATE TABLE g1m_observation_context(
                observation_id TEXT PRIMARY KEY,instrument TEXT
            );
            CREATE TABLE g1m_local_policy_outcomes(
                window_id TEXT,policy_name TEXT,terminal_r REAL,regret_r REAL,
                PRIMARY KEY(window_id,policy_name)
            );
            CREATE TABLE g1m_t0_feature_context_v2(
                observation_id TEXT PRIMARY KEY,context_json TEXT
            );
        """)

    def add_window(self, *, window_id: str, trade_id: int, eligible: bool,
                   production: str = "CLOSE_25", hold: float = 1.0,
                   close25: float = 0.75, close50: float = 0.5,
                   exit_r: float = 0.0, current_r: float = 0.2):
        observation_id = f"obs-{window_id}"
        self._conn.execute(
            "INSERT INTO g1m_local_windows VALUES(?,?,?,?,?,?,?,?)",
            (window_id, 15, trade_id, observation_id, int(eligible),
             "LIVE_PROSPECTIVE" if eligible else "RESEARCH_BACKFILL",
             "NAS100", float(trade_id)),
        )
        self._conn.execute(
            "INSERT INTO g1m_local_outcomes VALUES(?,?,?)", (window_id, 1.2, -0.4))
        self._conn.execute(
            "INSERT INTO g1m_management_observations VALUES(?,?,?)",
            (observation_id, production, current_r),
        )
        self._conn.execute(
            "INSERT INTO g1m_observation_context VALUES(?,?)", (observation_id, "NAS100"))
        context = {
            "market_context": {"macro": {"regime": "TREND"},
                               "cross_asset": {"confirmation": "SAME"}},
            "option_derivatives": {
                "option_state_attribution": {"positive": ["dP_take_dt"], "negative": []}
            },
        }
        self._conn.execute(
            "INSERT INTO g1m_t0_feature_context_v2 VALUES(?,?)",
            (observation_id, json.dumps(context)),
        )
        values = {
            "HOLD": hold,
            "CLOSE_10": hold * 0.9,
            "CLOSE_25": close25,
            "CLOSE_50": close50,
            "EXIT": exit_r,
            "ORIGINAL_PLAN": hold * 0.95,
            "PRODUCTION_POLICY": close25 if production == "CLOSE_25" else hold,
        }
        best = max(values.values())
        for policy, terminal in values.items():
            self._conn.execute(
                "INSERT INTO g1m_local_policy_outcomes VALUES(?,?,?,?)",
                (window_id, policy, terminal, best-terminal),
            )
        self._conn.commit()


def test_dependency_weighting_uses_one_effective_unit_per_trade():
    records = [
        {"trade_id": 1, "delta_r": 0.2, "regret_reduction_r": 0.2, "mfe_r": 1.0, "mae_r": -0.2},
        {"trade_id": 1, "delta_r": 0.4, "regret_reduction_r": 0.4, "mfe_r": 1.1, "mae_r": -0.3},
        {"trade_id": 2, "delta_r": -0.1, "regret_reduction_r": -0.1, "mfe_r": 0.8, "mae_r": -0.5},
    ]
    result = _summarize(records, maturity_from_sample=True)
    assert result["raw_n"] == 3
    assert result["effective_n"] == 2
    assert result["unique_trades"] == 2
    # Trade 1 contributes its mean 0.3 exactly once, trade 2 contributes -0.1.
    assert abs(result["mean_delta_r"] - 0.1) < 1e-12
    assert result["maturity"] == "INSUFFICIENT"


def test_local_maturity_thresholds_do_not_claim_robust_edge():
    assert _maturity(29) == "INSUFFICIENT"
    assert _maturity(30) == "EARLY"
    assert _maturity(75) == "RESEARCH"
    assert _maturity(150) == "PROVISIONAL"
    assert _maturity(10_000) == "PROVISIONAL"


def test_pairwise_action_sign_and_prospective_boundary():
    runtime = _Runtime()
    runtime.add_window(window_id="w-live", trade_id=1, eligible=True)
    report = management_edge_v2(runtime)

    hold_exit = next(
        row for row in report["pairwise"]
        if row["horizon_minutes"] == 15
        and row["left_action"] == "HOLD"
        and row["right_action"] == "EXIT"
    )
    assert hold_exit["prospective"]["raw_n"] == 1
    assert hold_exit["prospective"]["effective_n"] == 1
    assert hold_exit["prospective"]["mean_delta_r"] == 1.0

    production_hold = next(
        row for row in report["pairwise"]
        if row["horizon_minutes"] == 15
        and row["left_action"] == "PRODUCTION_POLICY"
        and row["right_action"] == "HOLD"
    )
    assert production_hold["prospective"]["mean_delta_r"] == -0.25
    assert report["production_authority"] is False
    assert report["auto_promotion"] is False
    assert report["may_trigger_exit_or_close"] is False

    # An older descriptive row may change descriptive diagnostics but must never
    # raise prospective N/maturity or become hidden prospective evidence.
    runtime.add_window(
        window_id="w-old", trade_id=2, eligible=False,
        hold=-2.0, close25=2.0, close50=2.0, exit_r=2.0,
    )
    report2 = management_edge_v2(runtime)
    hold_exit2 = next(
        row for row in report2["pairwise"]
        if row["horizon_minutes"] == 15
        and row["left_action"] == "HOLD"
        and row["right_action"] == "EXIT"
    )
    assert hold_exit2["descriptive_all"]["raw_n"] == 2
    assert hold_exit2["prospective"]["raw_n"] == 1
    assert hold_exit2["prospective"]["mean_delta_r"] == 1.0
    assert hold_exit2["prospective"]["maturity"] == "INSUFFICIENT"
    assert report2["dataset"]["descriptive_rows_never_raise_prospective_maturity"] is True
