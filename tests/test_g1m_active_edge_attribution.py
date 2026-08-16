from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

from seiltanzer.g1_management_active_edge_attribution import (
    ATTRIBUTION_VERSION,
    _decorate_window,
    _window_records,
    build_active_edge_decision_attribution,
)


def _context(*, supporting, opposing, strict_supporting=0, strict_opposing=0,
             groups=None):
    return {
        "supporting_position_n": supporting,
        "opposing_position_n": opposing,
        "net_position_vote": supporting - opposing,
        "strict_supporting_position_n": strict_supporting,
        "strict_opposing_position_n": strict_opposing,
        "strict_net_position_vote": strict_supporting - strict_opposing,
        "high_risk_only_supporting_position_n": max(0, supporting - strict_supporting),
        "high_risk_only_opposing_position_n": max(0, opposing - strict_opposing),
        "matched_groups": list(groups or []),
    }


def _window(trade_id, observation_id, *, supporting, opposing, hold, exit_value,
            close50, close25, strict_supporting=0, strict_opposing=0,
            horizon=60, groups=None):
    return _decorate_window({
        "window_id": f"window-{observation_id}-{horizon}",
        "horizon_minutes": horizon,
        "trade_id": trade_id,
        "observation_id": observation_id,
        "evidence_eligible": True,
        "origin": "LIVE_PROSPECTIVE",
        "active_edge_context_json": json.dumps(_context(
            supporting=supporting,
            opposing=opposing,
            strict_supporting=strict_supporting,
            strict_opposing=strict_opposing,
            groups=groups,
        )),
        "policies": {
            "HOLD": hold,
            "EXIT": exit_value,
            "CLOSE_50": close50,
            "CLOSE_25": close25,
        },
    })


def test_aligned_metric_rewards_support_hold_and_oppose_exit():
    group_support = [{
        "target_id": "RETURN_SIGMA",
        "target_family": "RETURN",
        "signal_horizon_minutes": 60,
        "matched_n": 4,
        "supporting_n": 3,
        "opposing_n": 1,
        "net_vote": 2,
        "net_vote_ratio": 0.5,
    }]
    group_oppose = [{
        "target_id": "RETURN_SIGMA",
        "target_family": "RETURN",
        "signal_horizon_minutes": 60,
        "matched_n": 4,
        "supporting_n": 1,
        "opposing_n": 3,
        "net_vote": -2,
        "net_vote_ratio": -0.5,
    }]
    windows = [
        _window(1, "a", supporting=4, opposing=1, strict_supporting=1,
                hold=1.0, exit_value=0.0, close50=0.50, close25=0.75,
                groups=group_support),
        # Same trade, second review: must not count as a second effective trade.
        _window(1, "b", supporting=3, opposing=1, strict_supporting=1,
                hold=0.6, exit_value=0.0, close50=0.30, close25=0.45,
                groups=group_support),
        _window(2, "c", supporting=1, opposing=4, strict_opposing=1,
                hold=-1.0, exit_value=0.0, close50=-0.50, close25=-0.75,
                groups=group_oppose),
    ]

    report = build_active_edge_decision_attribution(windows, {
        "sidecar_observation_n": 3,
        "available_sidecar_observation_n": 3,
    })
    assert report["contract_version"] == ATTRIBUTION_VERSION
    assert report["verdict"] == "INSUFFICIENT_DECISION_EDGE_DATA"
    all_60 = next(row for row in report["variants"]
                  if row["variant"] == "ALL_ACTIVE"
                  and row["local_horizon_minutes"] == 60)
    metric = all_60["hold_vs_exit"]
    assert metric["raw_n"] == 3
    assert metric["effective_n"] == 2
    # Trade 1 contributes avg(+1,+0.6)=+0.8; trade 2 contributes +1.0.
    assert metric["mean_aligned_delta_r"] == 0.9
    assert metric["positive_rate"] == 1.0
    assert metric["edge_claim_allowed"] is False
    assert report["decision_weight_applied"] is False
    assert report["production_authority"] is False
    assert report["automatic_execution"] is False

    # Three raw windows are enough to surface a descriptive target/horizon cell,
    # while the same trade clustering still keeps effective_n at two.
    group = next(row for row in report["target_horizon_attribution"]
                 if row["target_id"] == "RETURN_SIGMA"
                 and row["signal_horizon_minutes"] == 60
                 and row["local_horizon_minutes"] == 60)
    assert group["raw_window_n"] == 3
    assert group["hold_vs_exit"]["effective_n"] == 2
    assert group["post_selection_descriptive_only"] is True


def test_strict_and_lowered_gate_contributions_are_separated():
    windows = [
        _window(10, "x", supporting=5, opposing=1,
                strict_supporting=1, strict_opposing=0,
                hold=1.2, exit_value=0.0, close50=0.6, close25=0.9),
        _window(11, "y", supporting=1, opposing=5,
                strict_supporting=0, strict_opposing=1,
                hold=-0.8, exit_value=0.0, close50=-0.4, close25=-0.6),
    ]
    report = build_active_edge_decision_attribution(windows)
    strict = next(row for row in report["variants"]
                  if row["variant"] == "STRICT_REFERENCE"
                  and row["local_horizon_minutes"] == 60)
    risky = next(row for row in report["variants"]
                 if row["variant"] == "HIGH_RISK_ONLY"
                 and row["local_horizon_minutes"] == 60)
    assert strict["directional_window_n"] == 2
    assert risky["directional_window_n"] == 2
    assert strict["hold_vs_exit"]["positive_rate"] == 1.0
    assert risky["hold_vs_exit"]["positive_rate"] == 1.0
    assert report["strict_vs_high_risk_interpretation"]["HIGH_RISK_ONLY"].startswith(
        "active signals added")


def test_wrong_edge_direction_produces_negative_alignment_utility():
    windows = [
        _window(20, "bad", supporting=4, opposing=0,
                hold=-0.7, exit_value=0.0, close50=-0.35, close25=-0.52),
    ]
    report = build_active_edge_decision_attribution(windows)
    all_60 = next(row for row in report["variants"]
                  if row["variant"] == "ALL_ACTIVE"
                  and row["local_horizon_minutes"] == 60)
    assert all_60["hold_vs_exit"]["mean_aligned_delta_r"] == -0.7
    assert all_60["hold_vs_exit"]["negative_rate"] == 1.0
    assert report["edge_claim_allowed"] is False


def test_window_records_joins_real_sidecar_and_local_policy_tables():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE g1m_active_edge_t0(
            observation_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL UNIQUE,
            available INTEGER NOT NULL,
            context_json TEXT NOT NULL
        );
        CREATE TABLE g1m_local_windows(
            window_id TEXT PRIMARY KEY,
            observation_id TEXT NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            trade_id INTEGER NOT NULL,
            evidence_eligible INTEGER NOT NULL,
            origin TEXT NOT NULL,
            captured_ts REAL NOT NULL
        );
        CREATE TABLE g1m_local_outcomes(
            window_id TEXT PRIMARY KEY,
            observation_id TEXT NOT NULL
        );
        CREATE TABLE g1m_local_policy_outcomes(
            window_id TEXT NOT NULL,
            policy_name TEXT NOT NULL,
            terminal_r REAL NOT NULL,
            PRIMARY KEY(window_id,policy_name)
        );
    """)
    context = json.dumps(_context(
        supporting=4, opposing=1, strict_supporting=1, strict_opposing=0))
    conn.execute(
        "INSERT INTO g1m_active_edge_t0 VALUES(?,?,?,?)",
        ("obs-1", "review-1", 1, context),
    )
    conn.execute(
        "INSERT INTO g1m_local_windows VALUES(?,?,?,?,?,?,?)",
        ("win-1", "obs-1", 60, 42, 1, "LIVE_PROSPECTIVE", 1000.0),
    )
    conn.execute("INSERT INTO g1m_local_outcomes VALUES(?,?)", ("win-1", "obs-1"))
    for policy, terminal in {
        "HOLD": 1.0, "EXIT": 0.0, "CLOSE_50": 0.5, "CLOSE_25": 0.75,
    }.items():
        conn.execute(
            "INSERT INTO g1m_local_policy_outcomes VALUES(?,?,?)",
            ("win-1", policy, terminal),
        )
    conn.commit()

    runtime = SimpleNamespace(_conn=conn, _lock=threading.RLock())
    windows, coverage = _window_records(runtime)
    assert coverage["sidecar_observation_n"] == 1
    assert coverage["available_sidecar_observation_n"] == 1
    assert coverage["resolved_prospective_window_n"] == 1
    assert coverage["resolved_unique_trade_n"] == 1
    assert len(windows) == 1
    row = windows[0]
    assert row["trade_id"] == 42
    assert row["all_active_net_vote"] == 3
    assert row["all_active_hold_vs_exit_aligned_r"] == 1.0
    assert row["strict_reference_net_vote"] == 1
    assert row["high_risk_only_net_vote"] == 2


def test_empty_report_waits_for_prospective_resolved_windows():
    report = build_active_edge_decision_attribution([], {
        "sidecar_observation_n": 0,
        "available_sidecar_observation_n": 0,
    })
    assert report["verdict"] == "AWAITING_RESOLVED_POST_ACTIVATION_WINDOWS"
    assert report["coverage"]["attributed_window_n"] == 0
    assert report["maturity_contract"]["overall"] == "INSUFFICIENT"
    assert report["decision_weight_applied"] is False
    assert report["may_trigger_exit_or_close"] is False
