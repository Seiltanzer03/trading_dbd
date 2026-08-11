import json
import math
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.config import Settings
from seiltanzer.g1_baseline_routes import install_g1_baseline_routes
from seiltanzer.g1_baseline_runtime import G1_BASELINE_CONTRACT_VERSION
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from seiltanzer.option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION
from seiltanzer.passive_learning import PASSIVE_SCHEMA_VERSION, PassiveLearningEngine


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _cdf_value(value):
    support = [-0.2, -0.1, 0.0, 0.1, 0.2]
    cdf = [0.0, 0.2, 0.4, 0.75, 1.0]
    if value <= support[0]:
        return cdf[0]
    if value >= support[-1]:
        return cdf[-1]
    for index in range(1, len(support)):
        if value <= support[index]:
            weight = (value - support[index - 1]) / (support[index] - support[index - 1])
            return cdf[index - 1] + weight * (cdf[index] - cdf[index - 1])
    return 1.0


def _fixed_forecast(horizon=15):
    return {
        "version": "passive-forecast-f32-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "horizon_kind": "fixed_trading_time",
        "horizon_minutes": horizon,
        "probability_measure": "unavailable",
        "q_source_contract": "unavailable",
        "q_terminal_distribution_available": False,
        "q_first_touch_available": False,
        "physical_probability_published": False,
        "variance_clock_version": "variance-clock-f31-v1",
        "gaussian_reference_quantiles_log_return": {
            "q10": -0.02,
            "q25": -0.01,
            "q50": 0.0,
            "q75": 0.01,
            "q90": 0.02,
        },
    }


def _q_forecast(captured, target, *, instrument="USDCAD"):
    return {
        "version": "passive-forecast-f32-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "horizon_kind": "option_native_expiry",
        "horizon_minutes": int(round((target - captured) / 60)),
        "probability_measure": "risk_neutral_Q_terminal",
        "q_source_contract": OPTION_Q_CONTRACT_VERSION,
        "q_terminal_distribution_available": True,
        "q_first_touch_available": False,
        "physical_probability_published": False,
        "terminal_q_cdf": {
            "support": [-0.2, -0.1, 0.0, 0.1, 0.2],
            "cdf": [0.0, 0.2, 0.4, 0.75, 1.0],
        },
        "source_expiry_ts_utc": target,
        "calendar_ttm_seconds": target - captured,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
        "q_source_instrument": "FXC",
        "q_target_instrument": instrument,
        "proxy_symbol": "FXC",
        "proxy_transform": "inverse",
    }


def _outcome(target, log_return, *, pit=None):
    price = 100.0 * math.exp(log_return)
    return {
        "version": "passive-resolver-f32a-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "future_log_return": log_return,
        "terminal": {
            "terminal_price": price,
            "terminal_price_ts": target,
            "terminal_age_to_target_sec": 0.0,
            "terminal_lookahead_used": False,
            "terminal_authoritative": True,
            "clean_label": True,
            "terminal_log_return": log_return,
            "terminal_pit_q": pit,
        },
        "first_touch": {
            "label": "no_touch",
            "clean_label": False,
            "authoritative_path": False,
        },
    }


def _insert_resolved(
    engine,
    observation_id,
    *,
    anchor,
    captured,
    target,
    instrument="NAS100",
    horizon=15,
    forecast=None,
    outcome=None,
    resolved=None,
):
    forecast = forecast or _fixed_forecast(horizon)
    outcome = outcome or _outcome(target, 0.005)
    resolved = target if resolved is None else resolved
    with engine._lock, engine._conn:
        engine._conn.execute(
            "INSERT INTO passive_market_observations("
            "observation_id,anchor_group_id,captured_ts,target_ts,instrument,horizon_minutes,"
            "trigger_reason,market_price,price_source,price_age_sec,price_quality,price_kind,"
            "option_source,option_age_sec,option_quality,option_kind,market_regime,session,"
            "feature_contract_version,forecast_model_version,calibrator_version,scenario_version,"
            "features_json,forecast_json,evidence_eligible,resolution_status,resolved_ts,outcome_json,"
            "calendar_elapsed,trading_elapsed,market_open_fraction,retrospective_replay,"
            "observation_origin,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                observation_id, anchor, captured, target, instrument, horizon,
                "cadence", 100.0, "authoritative_test_feed", 0.0, 0.99, "direct",
                "option_test", 0.0, 0.99, "proxy", "NORMAL", "OPEN",
                PASSIVE_SCHEMA_VERSION, "passive-forecast-f32-v1", "identity-only-unpromoted",
                "standardized-geometry-f31-v1",
                _json({"measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION}),
                _json(forecast), 1, "resolved", resolved, _json(outcome),
                target - captured, target - captured, 1.0, 0,
                "background_collector", captured,
            ),
        )


def test_q_identity_scores_pit_quantiles_and_authority(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    captured1 = 1_800_000_000.0
    target1 = captured1 + 86400
    captured2 = target1
    target2 = captured2 + 86400
    lr1, lr2 = 0.05, -0.05
    _insert_resolved(
        engine, "q1", anchor="a1", captured=captured1, target=target1,
        instrument="USDCAD", horizon=1440,
        forecast=_q_forecast(captured1, target1),
        outcome=_outcome(target1, lr1, pit=_cdf_value(lr1)),
    )
    _insert_resolved(
        engine, "q2", anchor="a2", captured=captured2, target=target2,
        instrument="USDCAD", horizon=1440,
        forecast=_q_forecast(captured2, target2),
        outcome=_outcome(target2, lr2, pit=_cdf_value(lr2)),
    )
    before_registry = engine._conn.execute("SELECT COUNT(*) FROM research_model_registry").fetchone()[0]
    report = engine.g1_baseline_status()
    q = report["terminal_q_identity"]
    assert report["g1_stage"] == "G.1B"
    assert report["baseline_contract_version"] == G1_BASELINE_CONTRACT_VERSION
    assert q["raw_q_eligible_n"] == 2
    assert q["effective_q_n"] == 2
    assert q["metrics_eligible_n"] == 2
    assert q["pit_contract_mismatch_n"] == 0
    assert q["direction_event"]["q_identity"]["metrics"]["brier"] == pytest.approx(0.26)
    assert q["pit"]["n"] == 2
    assert q["quantiles"]["levels"]["q50"]["n"] == 2
    assert report["calibrator_fitted"] is False
    assert report["physical_probability_published"] is False
    assert report["g1_training_allowed"] is False
    assert report["promotion_allowed"] is False
    assert report["production_replacement_allowed"] is False
    after_registry = engine._conn.execute("SELECT COUNT(*) FROM research_model_registry").fetchone()[0]
    assert after_registry == before_registry
    engine.close()


def test_prequential_base_rate_is_strictly_past_only(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    rows = []
    for index, outcome in enumerate((1, 0, 1)):
        rows.append({
            "observation_id": f"r{index}",
            "base_cohort_id": "cohort",
            "captured_ts": index * 100.0,
            "target_ts": index * 100.0 + 50.0,
            "outcome": {"future_log_return": 0.01 if outcome else -0.01},
        })
    probabilities, outcomes, meta = engine._g1b_prequential_base_rate(rows)
    assert probabilities == pytest.approx([0.5, 2 / 3, 0.5])
    assert outcomes == [1, 0, 1]
    assert meta["past_only"] is True
    assert meta["cold_start_n"] == 1
    assert meta["max_history_before_prediction"] == 2
    engine.close()


def test_effective_sample_rows_remove_anchor_and_window_dependence(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    rows = [
        {"observation_id": "a-15", "instrument": "NAS100", "base_cohort_id": "c", "dependency_group_id": "a", "captured_ts": 0.0, "target_ts": 100.0},
        {"observation_id": "a-dup", "instrument": "NAS100", "base_cohort_id": "c", "dependency_group_id": "a", "captured_ts": 0.0, "target_ts": 100.0},
        {"observation_id": "b", "instrument": "NAS100", "base_cohort_id": "c", "dependency_group_id": "b", "captured_ts": 50.0, "target_ts": 150.0},
        {"observation_id": "c", "instrument": "NAS100", "base_cohort_id": "c", "dependency_group_id": "c", "captured_ts": 100.0, "target_ts": 200.0},
    ]
    selected = engine._g1b_effective_sample_rows(rows)
    assert [row["observation_id"] for row in selected] == ["a-15", "c"]
    engine.close()


def test_fixed_reference_quantiles_are_scored_without_becoming_q(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 900
    _insert_resolved(
        engine, "fixed", anchor="a", captured=captured, target=target,
        forecast=_fixed_forecast(15), outcome=_outcome(target, 0.005),
    )
    report = engine.g1_baseline_status()
    fixed = report["fixed_horizon_reference"]
    q = report["terminal_q_identity"]
    assert fixed["raw_n"] == 1
    assert fixed["effective_n"] == 1
    assert fixed["quantiles"]["levels"]["q50"]["coverage"] == 0.0
    assert fixed["quantiles"]["levels"]["q75"]["coverage"] == 1.0
    assert "not_Q_not_physical_P" in fixed["semantics"]
    assert q["raw_q_eligible_n"] == 0
    assert q["metrics_eligible_n"] == 0
    engine.close()


def test_q_pit_mismatch_fails_closed_for_metrics(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 86400
    _insert_resolved(
        engine, "qbad", anchor="a", captured=captured, target=target,
        instrument="USDCAD", horizon=1440,
        forecast=_q_forecast(captured, target),
        outcome=_outcome(target, 0.05, pit=0.1),
    )
    report = engine.g1_baseline_status()
    q = report["terminal_q_identity"]
    assert q["raw_q_eligible_n"] == 1
    assert q["effective_q_n"] == 1
    assert q["pit_contract_mismatch_n"] == 1
    assert q["metrics_eligible_n"] == 0
    assert q["direction_event"]["q_identity"]["metrics"]["n"] == 0
    engine.close()


def test_frozen_cut_reproducibly_excludes_later_observations(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    captured1 = 1_800_000_000.0
    target1 = captured1 + 900
    captured2 = target1 + 100
    target2 = captured2 + 900
    _insert_resolved(engine, "first", anchor="a1", captured=captured1, target=target1)
    engine._g1_sync_membership()
    cut = engine.create_g1_dataset_cut(target1 + 1)
    _insert_resolved(engine, "later", anchor="a2", captured=captured2, target=target2)
    live = engine.g1_baseline_status()
    frozen1 = engine.g1_baseline_status(cut_id=cut["cut_id"])
    frozen2 = engine.g1_baseline_status(cut_id=cut["cut_id"])
    assert live["raw_forecast_eval_n"] == 2
    assert frozen1["raw_forecast_eval_n"] == 1
    assert frozen1["source_scope"] == "frozen_g1a_dataset_cut"
    assert frozen1["sample_manifest_sha256"] == frozen2["sample_manifest_sha256"]
    engine.close()


def test_baseline_routes_and_unknown_cut(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    app = FastAPI()
    app.state.engine = SimpleNamespace(passive=engine)
    install_g1_baseline_routes(app)
    client = TestClient(app)
    response = client.get("/api/research/g1/baselines/status")
    assert response.status_code == 200
    assert response.json()["g1_stage"] == "G.1B"
    response = client.get("/api/research/g1/baselines/cohorts")
    assert response.status_code == 200
    response = client.get("/api/research/g1/baselines/status?cut_id=missing")
    assert response.status_code == 404
    engine.close()


def test_source_mutation_is_excluded_from_g1b(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    captured = 1_800_000_000.0
    target = captured + 900
    _insert_resolved(engine, "mutable", anchor="a", captured=captured, target=target)
    first = engine.g1_baseline_status()
    assert first["raw_forecast_eval_n"] == 1
    with engine._lock, engine._conn:
        engine._conn.execute(
            "UPDATE passive_market_observations SET outcome_json=? WHERE observation_id='mutable'",
            (_json(_outcome(target, -0.02)),),
        )
    second = engine.g1_baseline_status()
    assert second["raw_forecast_eval_n"] == 0
    error_n = engine._conn.execute(
        "SELECT COUNT(*) FROM g1_contract_errors WHERE error_type='SOURCE_MUTATED'"
    ).fetchone()[0]
    assert error_n >= 1
    engine.close()


def test_empty_g1b_is_honest_and_non_authoritative(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1b.db"), Settings(), cache=None)
    report = engine.g1_baseline_status()
    assert report["raw_forecast_eval_n"] == 0
    assert report["effective_n"] == 0
    assert report["evidence_status"] == "INSUFFICIENT"
    assert report["terminal_q_identity"]["pit"]["n"] == 0
    assert report["terminal_q_identity"]["direction_event"]["q_identity"]["metrics"]["brier"] is None
    assert report["calibrator_fitted"] is False
    assert report["promotion_allowed"] is False
    engine.close()
