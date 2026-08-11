import json
import math
import sqlite3
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.config import Settings
from seiltanzer.g1_shadow_routes import install_g1_shadow_routes
from seiltanzer.g1_shadow_runtime import (
    G1C_CONTRACT_VERSION,
    G1C_PREDICTION_CONTRACT_VERSION,
    _dependency_weights,
    _fit_beta,
    _fit_isotonic,
    _fit_platt,
    _predict_parameters,
)
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from seiltanzer.option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION
from seiltanzer.passive_learning import PASSIVE_SCHEMA_VERSION, PassiveLearningEngine


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _cdf_for_q(q_up):
    f0 = 1.0 - float(q_up)
    return {
        "support": [-0.2, -0.1, 0.0, 0.1, 0.2],
        "cdf": [0.0, 0.5 * f0, f0, f0 + 0.5 * q_up, 1.0],
    }


def _cdf_value(cdf_obj, value):
    support = cdf_obj["support"]
    cdf = cdf_obj["cdf"]
    if value <= support[0]:
        return cdf[0]
    if value >= support[-1]:
        return cdf[-1]
    for index in range(1, len(support)):
        if value <= support[index]:
            weight = (value - support[index - 1]) / (support[index] - support[index - 1])
            return cdf[index - 1] + weight * (cdf[index] - cdf[index - 1])
    return cdf[-1]


def _q_forecast(captured, target, q_up, instrument="USDCAD"):
    return {
        "version": "passive-forecast-f32-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "horizon_kind": "option_native_expiry",
        "horizon_minutes": int(round((target - captured) / 60.0)),
        "probability_measure": "risk_neutral_Q_terminal",
        "q_source_contract": OPTION_Q_CONTRACT_VERSION,
        "q_terminal_distribution_available": True,
        "q_first_touch_available": False,
        "physical_probability_published": False,
        "terminal_q_cdf": _cdf_for_q(q_up),
        "source_expiry_ts_utc": target,
        "calendar_ttm_seconds": target - captured,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
        "q_source_instrument": "FXC",
        "q_target_instrument": instrument,
        "proxy_symbol": "FXC",
        "proxy_transform": "inverse",
    }


def _outcome(target, log_return, cdf_obj):
    return {
        "version": "passive-resolver-f32a-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "future_log_return": log_return,
        "terminal": {
            "terminal_price": 100.0 * math.exp(log_return),
            "terminal_price_ts": target,
            "terminal_age_to_target_sec": 0.0,
            "terminal_lookahead_used": False,
            "terminal_authoritative": True,
            "clean_label": True,
            "terminal_log_return": log_return,
            "terminal_pit_q": _cdf_value(cdf_obj, log_return),
        },
        "first_touch": {
            "label": "no_touch",
            "clean_label": False,
            "authoritative_path": False,
        },
    }


def _insert_resolved_q(engine, observation_id, *, captured, q_up, outcome_positive, anchor=None):
    target = captured + 86400.0
    forecast = _q_forecast(captured, target, q_up)
    log_return = 0.03 if outcome_positive else -0.03
    outcome = _outcome(target, log_return, forecast["terminal_q_cdf"])
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
                observation_id, anchor or observation_id, captured, target, "USDCAD", 1440,
                "cadence", 100.0, "authoritative_test_feed", 0.0, 0.99, "direct",
                "option_test", 0.0, 0.99, "proxy", "NORMAL", "OPEN",
                PASSIVE_SCHEMA_VERSION, "passive-forecast-f32-v1", "identity-only-unpromoted",
                "standardized-geometry-f31-v1",
                _json({"measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION}),
                _json(forecast), 1, "resolved", target, _json(outcome),
                target - captured, target - captured, 1.0, 0,
                "background_collector", captured,
            ),
        )


def _insert_pending_q(engine, observation_id, *, captured, q_up):
    target = captured + 86400.0
    forecast = _q_forecast(captured, target, q_up)
    with engine._lock, engine._conn:
        engine._conn.execute(
            "INSERT INTO passive_market_observations("
            "observation_id,anchor_group_id,captured_ts,target_ts,instrument,horizon_minutes,"
            "trigger_reason,market_price,price_source,price_age_sec,price_quality,price_kind,"
            "option_source,option_age_sec,option_quality,option_kind,market_regime,session,"
            "feature_contract_version,forecast_model_version,calibrator_version,scenario_version,"
            "features_json,forecast_json,evidence_eligible,resolution_status,retrospective_replay,"
            "observation_origin,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                observation_id, observation_id, captured, target, "USDCAD", 1440,
                "cadence", 100.0, "authoritative_test_feed", 0.0, 0.99, "direct",
                "option_test", 0.0, 0.99, "proxy", "NORMAL", "OPEN",
                PASSIVE_SCHEMA_VERSION, "passive-forecast-f32-v1", "identity-only-unpromoted",
                "standardized-geometry-f31-v1",
                _json({"measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION}),
                _json(forecast), 1, "pending", 0, "background_collector", captured,
            ),
        )


def _populate_training(engine, n=60):
    # Two-day spacing with one-day targets makes all rows non-overlapping for
    # conservative G.1A effective-N accounting.
    base = time.time() - (n * 2 + 20) * 86400.0
    for index in range(n):
        captured = base + index * 2 * 86400.0
        q_up = 0.20 + 0.60 * ((index % 20) / 19.0)
        # Deliberately imperfect relationship so calibration has something to fit.
        outcome_positive = (index % 4) in (0, 1) if q_up >= 0.5 else (index % 4) == 0
        _insert_resolved_q(
            engine, f"q-{index:03d}", captured=captured, q_up=q_up,
            outcome_positive=outcome_positive,
        )
    engine._g1_sync_membership(limit=5000)
    return base


def test_empty_g1c_is_honest_and_non_authoritative(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    status = engine.g1c_status()
    assert status["g1_stage"] == "G.1C"
    assert status["g1c_contract_version"] == G1C_CONTRACT_VERSION
    assert status["q_eligible"] == 0
    assert status["frozen_model_n"] == 0
    assert status["prospective_shadow_prediction_n"] == 0
    assert status["fit_readiness"]["platt"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert status["physical_probability_published"] is False
    assert status["production_authority"] is False
    assert status["production_model_training_allowed"] is False
    assert status["promotion_allowed"] is False
    assert status["edge_claim"] is False
    engine.close()


def test_dependency_group_total_weight_is_one():
    rows = [
        {"observation_id": "a1", "dependency_group_id": "a"},
        {"observation_id": "a2", "dependency_group_id": "a"},
        {"observation_id": "a3", "dependency_group_id": "a"},
        {"observation_id": "b1", "dependency_group_id": "b"},
    ]
    weights = _dependency_weights(rows)
    assert sum(weights[:3]) == pytest.approx(1.0)
    assert weights[3] == pytest.approx(1.0)


def test_platt_beta_and_isotonic_are_deterministic_monotone():
    rows = []
    for index in range(80):
        q = 0.05 + 0.90 * index / 79.0
        rows.append({
            "raw_q": q,
            "outcome_y": 1 if index > 38 else 0,
            "dependency_group_id": f"d-{index}",
            "observation_id": f"o-{index}",
        })
    weights = _dependency_weights(rows)
    platt1 = _fit_platt(rows, weights)
    platt2 = _fit_platt(rows, weights)
    beta1 = _fit_beta(rows, weights)
    beta2 = _fit_beta(rows, weights)
    iso1 = _fit_isotonic(rows, weights)
    iso2 = _fit_isotonic(rows, weights)
    assert platt1 == pytest.approx(platt2)
    assert beta1 == pytest.approx(beta2)
    assert iso1 == iso2
    for family, params in (("PLATT", platt1), ("BETA", beta1), ("ISOTONIC", iso1)):
        values = [_predict_parameters(family, params, q) for q in (1e-12, 0.1, 0.3, 0.5, 0.7, 0.9, 1 - 1e-12)]
        assert all(0.0 <= value <= 1.0 for value in values)
        assert all(values[index] <= values[index + 1] + 1e-12 for index in range(len(values) - 1))


def test_fit_thresholds_and_immutable_registry(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    _populate_training(engine, 59)
    insufficient = engine.g1c_refit(force=True)
    assert insufficient["status"] == "INSUFFICIENT_EVIDENCE"
    assert engine.g1c_status()["frozen_model_n"] == 0

    # Add the 60th independent observation with both classes already present.
    latest = time.time() - 2 * 86400.0
    _insert_resolved_q(engine, "q-059-extra", captured=latest - 86400.0, q_up=0.62, outcome_positive=True)
    engine._g1_sync_membership(limit=5000)
    result = engine.g1c_refit(force=True)
    assert result["status"] == "FITTED_UNVALIDATED"
    assert result["models_created"] >= 2
    status = engine.g1c_status()
    assert status["frozen_model_n"] >= 2
    assert status["calibrator_fitted"] is True
    assert status["oos_validated"] is False
    assert status["edge_claim"] is False
    with pytest.raises(sqlite3.DatabaseError):
        with engine._lock, engine._conn:
            engine._conn.execute("UPDATE g1c_shadow_models SET status='BROKEN'")
    with pytest.raises(sqlite3.DatabaseError):
        with engine._lock, engine._conn:
            engine._conn.execute("DELETE FROM g1c_fit_runs")
    engine.close()


def test_source_mutation_rejects_training_cut(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    _populate_training(engine, 60)
    cut = engine.create_g1_dataset_cut(time.time())
    with engine._lock, engine._conn:
        row = engine._conn.execute(
            "SELECT observation_id FROM g1_dataset_cut_members WHERE cut_id=? AND q_to_p_eligible=1 LIMIT 1",
            (cut["cut_id"],),
        ).fetchone()
        # F.3.2a normally makes source rows immutable. This test deliberately
        # simulates storage tampering beyond that first defence so G.1C's own
        # source-hash revalidation is exercised independently.
        trigger_names = [
            value[0] for value in engine._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='passive_market_observations' AND sql LIKE '%BEFORE UPDATE%'"
            ).fetchall()
        ]
        assert trigger_names
        for trigger_name in trigger_names:
            engine._conn.execute(f'DROP TRIGGER "{trigger_name}"')
        engine._conn.execute(
            "UPDATE passive_market_observations SET market_price=market_price+1 WHERE observation_id=?",
            (row[0],),
        )
    from seiltanzer.g1_shadow_runtime import _revalidate_cut
    with pytest.raises(ValueError, match="TRAINING_CUT_MUTATED"):
        _revalidate_cut(engine, cut["cut_id"])
    engine.close()


def test_prospective_shadow_prediction_uses_only_preexisting_models(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    _populate_training(engine, 60)
    fit = engine.g1c_refit(force=True)
    assert fit["models_created"] >= 2
    latest_model = engine._conn.execute(
        "SELECT MAX(created_ts) FROM g1c_shadow_models"
    ).fetchone()[0]
    captured = max(time.time() + 2.0, float(latest_model) + 1.0)
    _insert_pending_q(engine, "future-q", captured=captured, q_up=0.43)
    result = engine.g1c_predict_observation("future-q")
    assert result["predictions_created"] >= 2
    rows = engine.g1c_predictions(limit=50)["items"]
    future = [row for row in rows if row["observation_id"] == "future-q"]
    assert future
    assert all(row["training_cutoff"] < captured for row in future)
    assert all(row["captured_ts"] == pytest.approx(captured) for row in future)
    assert all(row["production_used"] is False for row in future)
    assert all(0.0 <= row["shadow_calibrated_probability"] <= 1.0 for row in future)
    before = [(row["prediction_id"], row["shadow_calibrated_probability"]) for row in future]
    second = engine.g1c_predict_observation("future-q")
    assert second["predictions_created"] == 0
    after = [
        (row["prediction_id"], row["shadow_calibrated_probability"])
        for row in engine.g1c_predictions(limit=50)["items"] if row["observation_id"] == "future-q"
    ]
    assert sorted(before) == sorted(after)
    with pytest.raises(sqlite3.DatabaseError):
        with engine._lock, engine._conn:
            engine._conn.execute(
                "UPDATE g1c_shadow_predictions SET shadow_calibrated_probability=0.99 WHERE observation_id='future-q'"
            )
    engine.close()


def test_model_created_after_observation_cannot_backfill_prospective_prediction(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    captured = time.time() - 30.0
    _insert_pending_q(engine, "already-captured", captured=captured, q_up=0.55)
    _populate_training(engine, 60)
    fit = engine.g1c_refit(force=True)
    assert fit["models_created"] >= 2
    result = engine.g1c_predict_observation("already-captured")
    assert result["predictions_created"] == 0
    assert result["status"] == "NO_ELIGIBLE_FROZEN_MODEL"
    engine.close()


def test_g1c_routes_are_read_only_and_report_insufficient_evidence(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    app = FastAPI()
    app.state.engine = SimpleNamespace(passive=engine)
    install_g1_shadow_routes(app)
    client = TestClient(app)
    for endpoint in (
        "/api/research/g1/calibrators/status",
        "/api/research/g1/calibrators/models",
        "/api/research/g1/calibrators/cohorts",
        "/api/research/g1/calibrators/predictions",
    ):
        response = client.get(endpoint)
        assert response.status_code == 200
        assert response.json()["g1_stage"] == "G.1C"
    status = client.get("/api/research/g1/calibrators/status").json()
    assert status["frozen_model_n"] == 0
    assert status["production_authority"] is False
    engine.close()
