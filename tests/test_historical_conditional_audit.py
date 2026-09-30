import json
import sqlite3

from seiltanzer.historical_conditional_audit import audit_database, replay_rules


def _snapshot():
    return {
        "captured_ts": 1000,
        "strategy": {"direction": "long"},
        "trade_geometry": {
            "entry": 100, "original_stop": 90, "active_risk_barrier": 90,
            "current": 102, "final_take": 125,
        },
        "policy_manager": {
            "inputs": {
                "r0": .2, "T": 2.5, "max_r": .2,
                "rungs": [1.0, 1.5, 2.0], "rung_fraction": .1,
                "be_after": 1.5, "horizon_minutes": 20,
            },
            "option_derivative_state": {"gex_geometry": {
                "distance_to_zero_gamma": -.1,
            }},
        },
    }


def test_replay_on_same_recorded_path_books_first_rung_and_time_exit():
    points = [
        {"ts": 1000, "r": .2}, {"ts": 1300, "r": 1.2},
        {"ts": 1600, "r": 1.4}, {"ts": 1900, "r": .5},
        {"ts": 2200, "r": -.5}, {"ts": 2500, "r": -.8},
        {"ts": 2800, "r": -1.2},
    ]
    result = replay_rules(_snapshot(), points)
    assert result["TRAIL_GAMMA_FLIP"]["target_r"] == .1
    assert result["SCALE_OUT_ON_SPIKE"]["target_r"] == 1.0
    assert result["SCALE_OUT_ON_SPIKE"]["fraction_of_remaining"] == .25
    assert result["SCALE_OUT_ON_SPIKE"]["delta_r"] > 0
    assert result["TIME_STOP"]["deadline_minutes"] == 10
    assert result["TIME_STOP"]["delta_r"] > 0


def test_no_gamma_level_or_complete_time_path_does_not_create_result():
    snapshot = _snapshot()
    del snapshot["policy_manager"]["option_derivative_state"]
    points = [
        {"ts": 1000, "r": .2}, {"ts": 1100, "r": .4},
        {"ts": 1200, "r": .5},
    ]
    result = replay_rules(snapshot, points)
    assert result["TRAIL_GAMMA_FLIP"]["reason"] == "T0_GAMMA_STOP_NOT_TIGHTER"
    assert result["TIME_STOP"]["reason"] == "NO_COMPLETE_RECORDED_PATH_TO_DEADLINE"
    assert result["SCALE_OUT_ON_SPIKE"]["delta_r"] == 0
    assert result["SCALE_OUT_ON_SPIKE"]["triggered"] is False


def test_existing_active_stop_is_the_baseline_and_gamma_must_tighten_it():
    snapshot = _snapshot()
    snapshot["policy_manager"]["inputs"]["stop_r"] = 0.15
    points = [
        {"ts": 1000, "r": .2}, {"ts": 1100, "r": .4},
        {"ts": 1200, "r": .14}, {"ts": 1300, "r": -.5},
    ]
    result = replay_rules(snapshot, points)
    assert result["TRAIL_GAMMA_FLIP"]["reason"] == "T0_GAMMA_STOP_NOT_TIGHTER"
    assert result["SCALE_OUT_ON_SPIKE"]["baseline_r"] == .15


def test_additional_candidates_share_rung_and_require_frozen_t0_evidence():
    snapshot = _snapshot()
    snapshot["policy_manager"]["inputs"]["max_r"] = 1.2
    snapshot["policy_manager"]["inputs"]["r0"] = .8
    snapshot["policy_manager"]["combined_edge_soft_weight"] = {
        "available": True, "direction_score": -.7,
        "active_edge_component_weight": .2,
    }
    snapshot["trade_geometry"]["current"] = 108
    points = [
        {"ts": 1000, "r": .8}, {"ts": 1100, "r": 1.3},
        {"ts": 1200, "r": .4}, {"ts": 1300, "r": -.3},
    ]
    result = replay_rules(snapshot, points)
    assert result["PROTECT_GAIN"]["target_r"] == .5
    assert result["PROTECT_GAIN"]["delta_r"] > 0
    assert result["EXIT_ON_THESIS_BREAK"]["variant_r"] == .8
    assert result["EXIT_ON_THESIS_BREAK"]["delta_r"] > 0
    assert result["PARTIAL_AT_RUNG"] == result["SCALE_OUT_ON_SPIKE"]

    snapshot["policy_manager"]["combined_edge_soft_weight"]["direction_score"] = .7
    snapshot["policy_manager"]["inputs"]["stop_r"] = .6
    blocked = replay_rules(snapshot, points)
    assert blocked["PROTECT_GAIN"]["reason"] == "T0_GAIN_STOP_NOT_ELIGIBLE"
    assert blocked["EXIT_ON_THESIS_BREAK"]["reason"] == "NO_FROZEN_OPPOSING_EDGE_AT_T0"


def test_database_audit_deduplicates_trades_and_reports_coverage(tmp_path):
    path = tmp_path / "history.sqlite3"
    points = [(1000, .2), (1300, 1.2), (1600, 1.4), (1900, .5),
              (2200, -.5), (2500, -.8), (2800, -1.2)]
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE decision_snapshots(
                review_id TEXT PRIMARY KEY, trade_id INTEGER, captured_ts REAL,
                snapshot_json TEXT);
            CREATE TABLE decision_path_points(review_id TEXT, ts REAL, r REAL);
            CREATE TABLE decision_replays(review_id TEXT PRIMARY KEY);
        """)
        for review_id in ("r1", "r2"):
            snapshot = _snapshot()
            snapshot["strategy"]["instrument"] = "XAUUSD"
            db.execute("INSERT INTO decision_snapshots VALUES(?,?,?,?)",
                       (review_id, 7, 1000, json.dumps(snapshot)))
            db.execute("INSERT INTO decision_replays VALUES(?)", (review_id,))
            db.executemany("INSERT INTO decision_path_points VALUES(?,?,?)",
                           ((review_id, ts, r) for ts, r in points))
    result = audit_database(str(path))
    assert result["historical_resolved_reviews"] == 2
    for rule in ("TRAIL_GAMMA_FLIP", "SCALE_OUT_ON_SPIKE", "TIME_STOP",
                 "PARTIAL_AT_RUNG"):
        item = result["rules"][rule]
        assert item["independent_trade_n"] == 1
        assert item["by_instrument"]["XAUUSD"]["trade_n"] == 1
        assert item["excluded"]["SAME_TRADE_DUPLICATE"] == 1
