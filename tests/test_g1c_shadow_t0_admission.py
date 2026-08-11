import json
import time

from seiltanzer.config import Settings
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from seiltanzer.option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION
from seiltanzer.passive_learning import PASSIVE_SCHEMA_VERSION, PassiveLearningEngine


def test_manual_q_row_cannot_receive_shadow_prediction(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "g1c.db"), Settings(), cache=None)
    captured = time.time()
    target = captured + 86400.0
    forecast = {
        "version": "passive-forecast-f32-v1",
        "measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION,
        "horizon_kind": "option_native_expiry",
        "horizon_minutes": 1440,
        "probability_measure": "risk_neutral_Q_terminal",
        "q_source_contract": OPTION_Q_CONTRACT_VERSION,
        "q_terminal_distribution_available": True,
        "q_first_touch_available": False,
        "terminal_q_cdf": {
            "support": [-0.2, -0.1, 0.0, 0.1, 0.2],
            "cdf": [0.0, 0.2, 0.45, 0.75, 1.0],
        },
        "source_expiry_ts_utc": target,
        "calendar_ttm_seconds": target - captured,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
        "q_source_instrument": "FXC",
        "q_target_instrument": "USDCAD",
        "proxy_symbol": "FXC",
        "proxy_transform": "inverse",
    }
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
                "manual-q", "manual-q", captured, target, "USDCAD", 1440,
                "manual", 100.0, "authoritative_test_feed", 0.0, 0.99, "direct",
                "option_test", 0.0, 0.99, "proxy", "NORMAL", "OPEN",
                PASSIVE_SCHEMA_VERSION, "passive-forecast-f32-v1", "identity-only-unpromoted",
                "standardized-geometry-f31-v1",
                json.dumps({"measurement_runtime_contract": MEASUREMENT_RUNTIME_VERSION}),
                json.dumps(forecast), 1, "pending", 0, "manual", captured,
            ),
        )
    result = engine.g1c_predict_observation("manual-q")
    assert result["predictions_created"] == 0
    assert result["status"] == "PREDICTION_T0_CONTRACT_REJECTED"
    assert result["blocker"] == "NOT_BACKGROUND_COLLECTOR"
    errors = engine._conn.execute(
        "SELECT error_type,detail FROM g1c_contract_errors WHERE observation_id='manual-q'"
    ).fetchall()
    assert errors
    assert errors[-1][0] == "PREDICTION_T0_CONTRACT_REJECTED"
    assert errors[-1][1] == "NOT_BACKGROUND_COLLECTOR"
    engine.close()
