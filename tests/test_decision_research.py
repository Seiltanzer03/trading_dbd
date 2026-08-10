import json

import pytest

from seiltanzer.decision_research import (
    canonical_snapshot,
    counterfactual_replay,
    validate_no_future_timestamps,
)
from seiltanzer.journal import Journal


def _snapshot(trade_id=1, captured=1_700_000_000.0):
    return {
        "captured_ts": captured,
        "trade_id": trade_id,
        "demo": False,
        "strategy": {"direction": "long"},
        "observation": {
            "position": {"r": 0.0, "max_r": 0.0},
            "exact_levels": {
                "entry": 100.0, "stop": 99.0, "take": 102.0,
                "current": 100.0,
            },
        },
        "policy_manager": {
            "version": "quant-policy-test",
            "inputs": {
                "r0": 0.0, "max_r": 0.0, "T": 2.0,
                "rungs": [1.0, 1.5, 1.75], "rung_fraction": 0.10,
                "be_after": 1.5,
            },
            "recommendation": {"policy": "HOLD"},
            "management_decision": {
                "decision_id": "D-1", "policy": "HOLD",
            },
            "shadow_policy_contract": {"new_candidate_policy": "CLOSE_25"},
            "execution_cost_model": {
                "immediate_full_close_r": 0.01,
                "deferred_full_close_r": 0.01,
            },
            "scenario_geometry": {
                "execution_contract": {
                    "simulator_version": "execution-simulator-f0-v1",
                },
            },
            "policies": {
                name: {"expected_final_r": 0.1, "median_final_r": 0.0,
                       "cvar10_r": -0.2, "p_final_loss": 0.4,
                       "execution_cost_r": 0.01}
                for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
            },
        },
        "feeds": {"chain_ts": captured - 60.0},
    }


def test_future_timestamp_is_rejected_before_snapshot_persistence():
    snapshot = _snapshot()
    snapshot["feeds"]["chain_ts"] = snapshot["captured_ts"] + 5.0
    with pytest.raises(ValueError, match="post-capture"):
        validate_no_future_timestamps(snapshot, snapshot["captured_ts"])


def test_canonical_snapshot_has_stable_content_hash_and_versions():
    first = canonical_snapshot(_snapshot())
    second = canonical_snapshot(_snapshot())
    assert first == second
    assert len(first["snapshot_sha256"]) == 64
    assert first["production_policy"] == "HOLD"
    assert first["shadow_candidate"] == "CLOSE_25"
    assert first["simulator_version"] == "execution-simulator-f0-v1"


def test_counterfactual_replay_uses_same_be_ladder_and_cost_contract():
    snapshot = _snapshot()
    path = [
        {"ts": snapshot["captured_ts"], "price": 100.0, "r": 0.0},
        {"ts": snapshot["captured_ts"] + 60, "price": 101.5, "r": 1.5},
        {"ts": snapshot["captured_ts"] + 120, "price": 100.0, "r": 0.0},
        {"ts": snapshot["captured_ts"] + 180, "price": 102.0, "r": 2.0},
    ]
    result = counterfactual_replay(snapshot, path)
    assert result["execution_path"]["exit_reason"] == "breakeven"
    assert result["execution_path"]["filled_rungs"] == [1.0, 1.5]
    assert result["policies"]["HOLD"]["gross_realized_r"] == 0.25
    assert result["policies"]["HOLD"]["net_realized_r"] == 0.24
    assert result["policies"]["EXIT"]["net_realized_r"] == -0.01
    assert result["best_realized_policy"] == "HOLD"
    assert result["production_regret_r"] == 0.0
    assert result["shadow_regret_r"] > 0.0


def test_journal_persists_immutable_review_path_and_resolution(tmp_path):
    journal = Journal(str(tmp_path / "research.db"))
    trade = journal.open_trade(3, "NAS100", "long", 100.0, 99.0, 102.0)
    snapshot = _snapshot(trade_id=trade["id"])
    journal.record_ai_verdict(trade["id"], snapshot, "verdict", "test")
    journal.record_ai_verdict(trade["id"], snapshot, "same review", "test")
    assert journal._conn.execute(
        "SELECT COUNT(*) FROM decision_snapshots").fetchone()[0] == 1
    stored = journal._conn.execute(
        "SELECT snapshot_json,snapshot_sha256 FROM decision_snapshots").fetchone()
    assert canonical_snapshot(json.loads(stored["snapshot_json"]))["snapshot_sha256"] == stored["snapshot_sha256"]

    journal.record_decision_market_point(
        trade["id"], ts=snapshot["captured_ts"] + 60, price=101.5, r=1.5,
        min_interval_sec=0.0)
    journal.record_decision_market_point(
        trade["id"], ts=snapshot["captured_ts"] + 120, price=100.0, r=0.0,
        min_interval_sec=0.0)
    journal.close_trade(trade["id"], 0.25)
    report = journal.counterfactual_report(trade["id"])
    assert report["resolved_observations"] == 1
    assert report["items"][0]["replay"]["best_realized_policy"] == "HOLD"
    assert report["promotion_allowed"] is False
    journal.close()


def test_human_override_is_frozen_before_outcome_and_scored_after(tmp_path):
    journal = Journal(str(tmp_path / "human.db"))
    trade = journal.open_trade(3, "NAS100", "long", 100.0, 99.0, 102.0)
    snapshot = _snapshot(trade_id=trade["id"])
    journal.record_ai_verdict(trade["id"], snapshot, "verdict", "test")
    review_id = journal._conn.execute(
        "SELECT review_id FROM decision_snapshots").fetchone()[0]
    human = journal.record_human_decision(
        review_id, "CLOSE_25", "model_disagreement", "price action")
    assert human["outcome_known_at_record"] is False
    journal.record_decision_market_point(
        trade["id"], ts=snapshot["captured_ts"] + 60, price=101.5, r=1.5,
        min_interval_sec=0.0)
    journal.record_decision_market_point(
        trade["id"], ts=snapshot["captured_ts"] + 120, price=100.0, r=0.0,
        min_interval_sec=0.0)
    journal.close_trade(trade["id"], 0.25)
    item = journal.counterfactual_report(trade["id"])["items"][0]
    assert item["human_decision"]["policy"] == "CLOSE_25"
    assert item["human_decision"]["override_delta_vs_model_r"] < 0
    with pytest.raises(ValueError, match="before outcome"):
        journal.record_human_decision(review_id, "HOLD", "other")
    journal.close()


def test_experiment_registry_consumes_oos_once_and_never_promotes(tmp_path):
    journal = Journal(str(tmp_path / "experiments.db"))
    registered = journal.register_experiment(
        experiment_id="EXP-001", hypothesis="tail hazard improves regret",
        features=["option_distribution.tail", "option_distribution.hazard"],
        formula="tanh(z_tail * z_hazard)", thresholds={"material": 0.4},
        train_period=(1.0, 2.0), validation_period=(2.0, 3.0),
        test_period=(3.0, 4.0),
    )
    assert registered["promotion_allowed"] is False
    evaluated = journal.record_experiment_result(
        "EXP-001", {"brier_delta": -0.01, "regret_delta_r": -0.03})
    assert evaluated["oos_consumed"] is True
    with pytest.raises(ValueError, match="already consumed"):
        journal.record_experiment_result("EXP-001", {"brier_delta": -0.02})
    report = journal.experiment_report()
    assert report["experiments"][0]["status"] == "evaluated"
    assert report["promotion_allowed"] is False
    journal.close()
