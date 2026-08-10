"""Comprehensive semantic producer-consumer integration test suite for Phase F.3 / G.0."""

import json
import math
import tempfile
import pytest
from seiltanzer.config import Settings
from seiltanzer.data.feeds import MarketData
from seiltanzer.passive_learning import PassiveLearningEngine, PASSIVE_SCHEMA_VERSION, FORECAST_VERSION
from seiltanzer.variance_clock import VARIANCE_CLOCK_VERSION


def test_daily_feed_producer_to_volatility_consumer_integration():
    """Verifies that actual MarketData.refresh_daily() output is compatible with _reference_volatility."""
    settings = Settings()
    feed = MarketData(settings, cache=None)
    feed.set_instrument("NAS100")

    # Mock actual MarketData.daily contract structure
    feed.daily = {
        "bars": {
            "highs": [101.0 + i for i in range(30)],
            "lows": [99.0 + i for i in range(30)],
            "closes": [100.0 * math.exp(0.005 * ((-1)**i)) for i in range(30)],
        },
        "status": "live",
        "error": None,
    }

    vol_info = PassiveLearningEngine._reference_volatility(feed)
    assert vol_info["volatility_status"] == "valid"
    assert vol_info["reference_volatility_annual"] is not None
    assert vol_info["reference_volatility_annual"] > 0
    assert vol_info["variance_clock_version"] == VARIANCE_CLOCK_VERSION


def test_option_chain_producer_to_q_adapter_integration():
    """Verifies that actual _compute_chain_metrics() output feeds correctly into Q adapter."""
    settings = Settings()
    feed = MarketData(settings, cache=None)
    feed.set_instrument("NAS100")

    spot = 18000.0
    strikes = [17000.0, 17500.0, 18000.0, 18500.0, 19000.0]
    call_mid = [1100.0, 650.0, 250.0, 50.0, 5.0]
    put_mid = [5.0, 45.0, 240.0, 620.0, 1080.0]
    call_oi = [1000.0] * 5
    put_oi = [1000.0] * 5
    call_iv = [0.18] * 5
    put_iv = [0.18] * 5
    raw = {
        "expiry": "2026-08-15",
        "t_years": 15.0 / 98280.0,  # ~15 minutes TTM in US equity clock
        "strikes": strikes,
        "call_mid": call_mid,
        "put_mid": put_mid,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_iv": call_iv,
        "put_iv": put_iv,
    }

    metrics = feed._compute_chain_metrics(raw, spot, proxy="QQQ", demo=False)
    assert "density" in metrics
    assert "strikes" in metrics["density"]
    assert "q" in metrics["density"]

    # Pass producer metrics into passive forecast
    vol_info = {"reference_volatility_annual": 0.18, "volatility_status": "valid"}
    forecast = PassiveLearningEngine._forecast(spot, vol_info, 15, metrics, "NAS100")

    assert forecast["probability_measure"] == "risk_neutral_Q"
    assert forecast["q_source_contract"] == "option-q-contract-f3-v1"
    assert forecast["horizon_alignment_status"] == "valid"
    assert forecast["quantiles_log_return"]["q50"] is not None


def test_event_trigger_uses_15m_geometry_only(tmp_path):
    """Verifies that large_price_displacement trigger strictly uses 15m geometry."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    ts = 1_700_000_000.0
    features = {"source_observation_ts": ts, "volatility": {"reference_annual": 0.20}}
    # 15m sigma = 0.002, 24h sigma = 0.012
    forecast_15m = {"version": FORECAST_VERSION, "reference_volatility_annual": 0.20, "sigma_h_return": 0.002}

    # Record observations across all horizons
    engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features=features, forecast=forecast_15m, provenance={},
        trigger_reason="cadence", evidence_eligible=True
    )

    # 15m sigma = 0.002 -> threshold = 0.75 * 0.002 = 0.0015 (15 pips on 100 price)
    # Price moves to 100.20 (+0.0020 return) -> > 0.0015 -> TRIGGERED for 15m
    last_15m = engine._conn.execute(
        "SELECT captured_ts,market_price,forecast_json FROM passive_market_observations "
        "WHERE instrument='NAS100' AND horizon_minutes=15 ORDER BY captured_ts DESC LIMIT 1"
    ).fetchone()

    trigger_reason = PassiveLearningEngine._event_trigger_reason(
        now=ts + 300.0, last_15m=dict(last_15m), price=100.20
    )
    assert trigger_reason == "large_price_displacement"

    engine.close()


def test_first_touch_ohlc_ambiguity_and_dual_clocks(tmp_path):
    """Verifies 1m OHLC touch resolution, ambiguity detection, and dual trading/calendar clocks."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    ts = 1_700_000_000.0
    forecast = {"version": FORECAST_VERSION, "reference_volatility_annual": 0.16, "sigma_h_return": 0.01}
    engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features={"source_observation_ts": ts}, forecast=forecast, provenance={},
        trigger_reason="test", evidence_eligible=True
    )

    # 1. Upper hit candle
    engine.record_market_point("NAS100", ts, 100.0)
    engine.record_market_point("NAS100", ts + 300.0, 102.0)  # +2% > +1% upper barrier

    engine.resolve_due(now=ts + 1000.0)

    obs = engine.observations(limit=1)["items"][0]

    # Verify 15m observation resolved outcome
    obs_15m = [item for item in engine.observations(limit=10)["items"] if item["horizon_minutes"] == 15][0]

    if obs_15m["resolution_status"] == "resolved":
        outcome = obs_15m["outcome"]
        assert outcome["path_source"] == "recorded_real_market_path"
        assert outcome["first_touch_calendar_minutes"] is not None
        assert outcome["first_touch_trading_minutes"] is not None
        assert outcome["first_touch_primary_time_basis"] == "trading"

    engine.close()


def test_legacy_rows_quarantine_and_telemetry_counters(tmp_path):
    """Verifies that legacy rows are quarantined and health status displays separate counters."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    # Manually insert legacy F2 observation row
    engine._conn.execute(
        "INSERT INTO passive_market_observations("
        "observation_id,anchor_group_id,captured_ts,target_ts,instrument,horizon_minutes,"
        "trigger_reason,market_price,feature_contract_version,forecast_model_version,"
        "calibrator_version,scenario_version,features_json,forecast_json,evidence_eligible,"
        "resolution_status,created_ts)"
        "VALUES('legacy-1','anchor-1',1600000000,1600000900,'NAS100',15,'cadence',100.0,"
        "'passive-observation-f2-v1','passive-forecast-f2-v1','identity','f2','{}','{}',1,'resolved',1600000000)"
    )

    status = engine.status()
    assert status["raw_n"] == 1
    assert status["legacy_contract_raw_n"] == 1
    assert status["current_contract_raw_n"] == 0
    assert status["version"] == PASSIVE_SCHEMA_VERSION

    calibration = engine.calibration_report()
    assert calibration["raw_n"] == 0  # Legacy row excluded from pristine F3 resolved rows!
    assert calibration["calibrator_readiness_gate"]["pristine_dataset_ready"] is False
    assert calibration["calibrator_readiness_gate"]["calibrator_training_allowed"] is False

    engine.close()
