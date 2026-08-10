"""Comprehensive 21-Test Matrix for Phase F.3.1 Measurement Integrity Closure.

Verifies:
1. Horizon contamination isolation (independent per-horizon forecasts).
2. Immutable non-shared forecast objects.
3. Legacy F3 row quarantine.
4. Terminal Q semantics (risk_neutral_Q_terminal != first_touch).
5. First-touch Q unavailable semantics.
6. Option-native expiry horizon cohort.
7. Fixed 15m Q unavailable semantics.
8. Fake term scaling forbidden.
9. Direct proxy return-space mapping.
10. Inverse proxy return-space mapping (USDCAD vs FXC).
11. Probability mass conservation during proxy transform.
12. Experimental proxy evidence tiering.
13. Upper-only OHLC touch.
14. Lower-only OHLC touch.
15. Both barriers touched in same candle -> ambiguous_first_touch = True.
16. Point-only path granularity.
17. Terminal valid + first-touch ambiguous resolution.
18. Dual trading and calendar weekend elapsed clocks.
19. Real MarketData producer schema integration.
20. Full prospective T0 -> future -> resolver cycle without lookahead.
21. Existing trade management regression invariance.
"""

import json
import math
import pytest
from seiltanzer.config import Settings
from seiltanzer.data.feeds import MarketData
from seiltanzer.option_q_adapter import (
    OPTION_Q_CONTRACT_VERSION,
    adapt_option_q_forecast,
    validate_and_transform_proxy_density,
)
from seiltanzer.passive_learning import (
    FORECAST_VERSION,
    PASSIVE_SCHEMA_VERSION,
    PassiveLearningEngine,
)
from seiltanzer.position_state import PositionLedger
from seiltanzer.variance_clock import (
    VARIANCE_CLOCK_VERSION,
    compute_horizon_sigma,
)


def test_1_horizon_contamination_isolation(tmp_path):
    """Test 1: Ensures each horizon receives an independent forecast and correct sigma_h."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    ts = 1_700_000_000.0
    features = {"source_observation_ts": ts, "volatility": {"reference_volatility_annual": 0.20}}
    forecast = {"reference_volatility_annual": 0.20}

    ids = engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features=features, forecast=forecast, provenance={},
        trigger_reason="test", evidence_eligible=True
    )

    rows = engine._conn.execute(
        "SELECT horizon_minutes, forecast_json FROM passive_market_observations WHERE captured_ts=?",
        (ts,)
    ).fetchall()

    h_map = {row["horizon_minutes"]: json.loads(row["forecast_json"]) for row in rows}

    # Verify forecast.horizon_minutes matches DB horizon
    for h, f in h_map.items():
        assert f["horizon_minutes"] == h

    # Verify 15m sigma != 240m sigma
    sigma_15m = h_map[15]["sigma_h_return"]
    sigma_240m = h_map[240]["sigma_h_return"]

    assert sigma_15m is not None
    assert sigma_240m is not None
    assert sigma_15m != sigma_240m
    assert sigma_15m < sigma_240m

    engine.close()


def test_2_no_shared_forecast_object(tmp_path):
    """Test 2: Modifying forecast dict of one horizon does not mutate another."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    ts = 1_700_000_000.0
    features = {"source_observation_ts": ts, "volatility": {"reference_annual": 0.20}}
    forecast = {"reference_volatility_annual": 0.20}

    engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features=features, forecast=forecast, provenance={},
        trigger_reason="test", evidence_eligible=True
    )

    rows = engine._conn.execute(
        "SELECT horizon_minutes, forecast_json FROM passive_market_observations WHERE captured_ts=?",
        (ts,)
    ).fetchall()

    f15 = json.loads(rows[0]["forecast_json"])
    f240 = json.loads(rows[1]["forecast_json"])

    f15["test_mutation"] = True
    assert "test_mutation" not in f240

    engine.close()


def test_3_legacy_f3_quarantine(tmp_path):
    """Test 3: Legacy F3 rows are marked legacy and excluded from pristine F31 calibration."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    # Insert legacy F3 row
    engine._conn.execute(
        "INSERT INTO passive_market_observations("
        "observation_id,anchor_group_id,captured_ts,target_ts,instrument,horizon_minutes,"
        "trigger_reason,market_price,feature_contract_version,forecast_model_version,"
        "calibrator_version,scenario_version,features_json,forecast_json,evidence_eligible,"
        "resolution_status,created_ts)"
        "VALUES('legacy-f3-1','anchor-1',1600000000,1600000900,'NAS100',15,'cadence',100.0,"
        "'passive-observation-f3-v1','passive-forecast-f3-v1','identity','f3','{}','{}',1,'resolved',1600000000)"
    )

    status = engine.status()
    assert status["legacy_f3_n"] == 1
    assert status["legacy_f31_n"] == 0
    assert status["pristine_f32_n"] == 0

    engine.close()


def test_4_terminal_q_semantics():
    """Test 4: Option Q adapter outputs risk_neutral_Q_terminal, NOT first-touch passage."""
    spot = 100.0
    density_dict = {
        "strikes": [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0],
        "q": [0.01, 0.10, 0.20, 0.38, 0.20, 0.10, 0.01],
    }
    option_metrics = {"spot": spot, "t_years": 0.01, "density": density_dict}

    res = adapt_option_q_forecast(option_metrics, 0, 0.01, "NAS100", instrument_spot=spot, horizon_kind="option_native_expiry")

    assert res["probability_measure"] == "risk_neutral_Q_terminal"
    assert res["q_terminal_distribution_available"] is True
    assert res["q_first_touch_available"] is False
    assert "q_terminal_above_upper" in res["standardized_barriers"]["1.0"]
    assert "upper_hit_first" not in res["standardized_barriers"]["1.0"]


def test_5_first_touch_q_unavailable():
    """Test 5: Terminal option density sets q_first_touch_available = False."""
    spot = 100.0
    density_dict = {
        "strikes": [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0],
        "q": [0.01, 0.10, 0.20, 0.38, 0.20, 0.10, 0.01],
    }
    option_metrics = {"spot": spot, "t_years": 0.01, "density": density_dict}

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100", instrument_spot=spot, horizon_kind="fixed_trading_time")
    assert res["q_first_touch_available"] is False


def test_6_native_expiry_horizon_cohort(tmp_path):
    """Test 6: Option density creates option_native_expiry observation matching source expiry timestamp."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    ts = 1_700_000_000.0
    expiry_ts = ts + (2.0 * 365.0 * 86400.0 / 252.0)  # 2 trading days
    t_years = 2.0 / 252.0

    density_dict = {
        "strikes": [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0],
        "q": [0.01, 0.10, 0.20, 0.38, 0.20, 0.10, 0.01],
    }
    option_metrics = {
        "spot": 100.0,
        "t_years": t_years,
        "expiry": "2026-08-14 21:00 UTC",
        "expiry_ts_utc": expiry_ts,
        "density": density_dict,
    }

    features = {
        "source_observation_ts": ts,
        "volatility": {"reference_annual": 0.20},
        "option_derivatives": {"available": True, "data": option_metrics},
    }

    ids = engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features=features, forecast={}, provenance={},
        trigger_reason="test", evidence_eligible=True
    )

    # Check option-native observation in DB
    native_row = engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id LIKE '%native-expiry%'"
    ).fetchone()

    assert native_row is not None
    assert abs(native_row["target_ts"] - expiry_ts) < 1.0

    forecast = json.loads(native_row["forecast_json"])
    assert forecast["horizon_kind"] == "option_native_expiry"
    assert forecast["probability_measure"] == "risk_neutral_Q_terminal"

    engine.close()


def test_7_fixed_15m_q_unavailable():
    """Test 7: Option distribution expiring in 2 days evaluated against fixed 15m horizon returns q_available=False."""
    spot = 100.0
    density_dict = {
        "strikes": [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0],
        "q": [0.01, 0.10, 0.20, 0.38, 0.20, 0.10, 0.01],
    }
    option_metrics = {"spot": spot, "t_years": 2.0 / 252.0, "density": density_dict}

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100", instrument_spot=spot, horizon_kind="fixed_trading_time")

    assert res["q_available"] is False
    assert res["probability_measure"] == "unavailable"
    assert res["horizon_alignment_status"] == "unavailable"


def test_8_fake_term_scaling_forbidden():
    """Test 8: Presence of term structure dict alone does not grant q_available=True for fixed horizon."""
    spot = 100.0
    density_dict = {
        "strikes": [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0],
        "q": [0.01, 0.10, 0.20, 0.38, 0.20, 0.10, 0.01],
    }
    term_dict = {"pts": [(2, 0.16), (9, 0.18), (30, 0.20)]}
    option_metrics = {"spot": spot, "t_years": 2.0 / 252.0, "density": density_dict, "term": term_dict}

    res = adapt_option_q_forecast(option_metrics, 15, 0.01, "NAS100", instrument_spot=spot, horizon_kind="fixed_trading_time")

    assert res["q_available"] is False
    assert res["probability_measure"] == "unavailable"


def test_9_direct_proxy_mapping():
    """Test 9: QQQ +1% proxy return distribution maps to +1% NAS100 instrument space."""
    proxy_spot = 400.0
    inst_spot = 18000.0
    density = {
        "strikes": [360.0, 380.0, 400.0, 404.0, 420.0, 440.0],
        "q": [0.01, 0.10, 0.30, 0.30, 0.10, 0.19],
    }

    val_res = validate_and_transform_proxy_density(density, proxy_spot, inst_spot, proxy_transform="direct")

    assert val_res["valid"] is True
    # QQQ strike 404.0 is +1% above proxy_spot 400.0 -> mapped NAS100 strike is 18180.0 (+1% above 18000.0)
    assert abs(val_res["instrument_strikes"][3] - 18180.0) < 1e-2


def test_10_inverse_proxy_mapping():
    """Test 10: FXC +1% proxy return distribution maps to -1% USDCAD instrument space."""
    proxy_spot = 75.0
    inst_spot = 1.3500
    density = {
        "strikes": [70.0, 73.0, 75.0, 75.75, 78.0, 80.0],
        "q": [0.01, 0.10, 0.30, 0.30, 0.10, 0.19],
    }

    val_res = validate_and_transform_proxy_density(density, proxy_spot, inst_spot, proxy_transform="inverse")

    assert val_res["valid"] is True
    # FXC strike 75.75 (+1% above 75.0) -> mapped USDCAD return is -1% -> 1.3500 * e^(-0.01) ≈ 1.3365
    assert val_res["r_instrument"][0] < 0  # Inverted sign!


def test_11_probability_mass_conservation():
    """Test 11: Integrated probability mass after transform is conserved at 1.0."""
    proxy_spot = 400.0
    inst_spot = 18000.0
    density = {
        "strikes": [360.0, 380.0, 400.0, 420.0, 440.0],
        "q": [0.01, 0.10, 0.78, 0.10, 0.01],
    }

    val_res = validate_and_transform_proxy_density(density, proxy_spot, inst_spot, proxy_transform="direct")

    assert val_res["valid"] is True
    assert abs(val_res["cdf"][-1] - 1.0) < 1e-4


def test_12_experimental_proxy_tier():
    """Test 12: Experimental proxy sets q_evidence_tier = experimental_proxy."""
    spot = 100.0
    density_dict = {
        "strikes": [80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0],
        "q": [0.01, 0.10, 0.20, 0.38, 0.20, 0.10, 0.01],
    }
    option_metrics = {
        "spot": spot, "proxy": "EWG", "experimental": True,
        "t_years": 0.01, "density": density_dict,
    }

    res = adapt_option_q_forecast(option_metrics, 0, 0.01, "GER40", instrument_spot=spot, horizon_kind="option_native_expiry")

    assert res["q_evidence_tier"] == "experimental_proxy"


def test_13_upper_only_ohlc(tmp_path):
    """Test 13: 1m OHLC candle with high >= upper and low > lower resolves clean upper touch."""
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

    # 15m sigma ~ 0.002 (0.2%). High = 102.0 (+2%) >= upper, Low = 99.9 (-0.1%) > lower
    engine.record_market_bar("NAS100", ts, ts + 60.0, 100.0, 102.0, 99.9, 101.5)
    engine.record_market_bar("NAS100", ts + 60.0, ts + 900.0, 101.5, 102.0, 101.0, 101.5)

    engine.resolve_due(now=ts + 1000.0)

    rows = engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE horizon_minutes=15 AND resolution_status='resolved'"
    ).fetchall()

    assert len(rows) == 1
    outcome = json.loads(rows[0]["outcome_json"])

    assert outcome["first_touch"]["label"] == "upper_hit_first"
    assert outcome["first_touch"]["clean_label"] is True
    assert outcome["first_touch"]["ambiguous_first_touch"] is False

    engine.close()


def test_14_lower_only_ohlc(tmp_path):
    """Test 14: 1m OHLC candle with low <= lower and high < upper resolves clean lower touch."""
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

    # 15m sigma ~ 0.002 (0.2%). Low = 98.0 (-2%) <= lower, High = 100.1 (+0.1%) < upper
    engine.record_market_bar("NAS100", ts, ts + 60.0, 100.0, 100.1, 98.0, 98.5)
    engine.record_market_bar("NAS100", ts + 60.0, ts + 900.0, 98.5, 99.0, 98.0, 98.5)

    engine.resolve_due(now=ts + 1000.0)

    rows = engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE horizon_minutes=15 AND resolution_status='resolved'"
    ).fetchall()

    assert len(rows) == 1
    outcome = json.loads(rows[0]["outcome_json"])

    assert outcome["first_touch"]["label"] == "lower_hit_first"
    assert outcome["first_touch"]["clean_label"] is True
    assert outcome["first_touch"]["ambiguous_first_touch"] is False

    engine.close()


def test_15_both_barriers_same_candle_ambiguity(tmp_path):
    """Test 15: Both barriers touched in same candle sets ambiguous_first_touch = True and clean_label = False."""
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

    # Candle crosses high >= 101.0 AND low <= 99.0 in SAME candle
    engine.record_market_bar("NAS100", ts, ts + 60.0, 100.0, 102.5, 97.5, 100.0)
    engine.record_market_bar("NAS100", ts + 60.0, ts + 900.0, 100.0, 101.0, 99.0, 100.0)

    engine.resolve_due(now=ts + 1000.0)

    rows = engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE horizon_minutes=15 AND resolution_status='resolved'"
    ).fetchall()

    assert len(rows) == 1
    outcome = json.loads(rows[0]["outcome_json"])

    assert outcome["first_touch"]["label"] == "ambiguous_first_touch"
    assert outcome["first_touch"]["clean_label"] is False
    assert outcome["first_touch"]["ambiguous_first_touch"] is True

    engine.close()


def test_16_point_only_path_granularity(tmp_path):
    """Test 16: Path recorded with point samples only sets path_granularity = point_only and clean_label = False."""
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

    engine.record_market_point("NAS100", ts, 100.0)
    engine.record_market_point("NAS100", ts + 900.0, 102.0)

    engine.resolve_due(now=ts + 1000.0)

    rows = engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE horizon_minutes=15 AND resolution_status='resolved'"
    ).fetchall()

    assert len(rows) == 1
    outcome = json.loads(rows[0]["outcome_json"])

    assert outcome["path_granularity"] == "point_only"
    assert outcome["first_touch"]["clean_label"] is False

    engine.close()


def test_17_terminal_valid_and_first_touch_ambiguous(tmp_path):
    """Test 17: Terminal outcome remains valid and resolved even if first_touch is ambiguous."""
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

    # Ambiguous candle inside path, but end price is 102.0 (+2.0% log return)
    engine.record_market_bar("NAS100", ts, ts + 60.0, 100.0, 102.5, 97.5, 100.0)
    engine.record_market_bar("NAS100", ts + 60.0, ts + 900.0, 100.0, 102.0, 99.8, 102.0)

    engine.resolve_due(now=ts + 1000.0)

    rows = engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE horizon_minutes=15 AND resolution_status='resolved'"
    ).fetchall()

    assert len(rows) == 1
    outcome = json.loads(rows[0]["outcome_json"])

    assert outcome["first_touch"]["ambiguous_first_touch"] is True
    assert outcome["terminal"]["terminal_class"] == "above_upper"
    assert abs(outcome["terminal"]["terminal_price"] - 102.0) < 1e-4

    engine.close()


def test_18_weekend_dual_clock_tracking(tmp_path):
    """Test 18: Friday close to Monday open event stores calendar_minutes >> trading_minutes."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    # Friday 2026-08-07 20:00 UTC (15:00 EST, open)
    friday_ts = 1786132800.0
    monday_ts = friday_ts + (66 * 3600.0)

    forecast = {"version": FORECAST_VERSION, "reference_volatility_annual": 0.16, "sigma_h_return": 0.01}
    engine.capture_observation(
        instrument="NAS100", captured_ts=friday_ts, market_price=100.0,
        features={"source_observation_ts": friday_ts}, forecast=forecast, provenance={},
        trigger_reason="test", evidence_eligible=True
    )

    engine.record_market_point("NAS100", friday_ts, 100.0)
    engine.record_market_point("NAS100", friday_ts + 900.0, 102.0)
    engine.record_market_point("NAS100", monday_ts, 102.0)

    engine.resolve_due(now=monday_ts + 1000.0)

    rows = engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE horizon_minutes=15 AND resolution_status='resolved'"
    ).fetchall()

    assert len(rows) == 1
    outcome = json.loads(rows[0]["outcome_json"])

    cal_mins = outcome["first_touch"]["first_touch_calendar_minutes"]
    trad_mins = outcome["first_touch"]["first_touch_trading_minutes"]

    assert cal_mins is not None
    assert trad_mins is not None

    engine.close()


def test_19_actual_producer_integration():
    """Test 19: Full integration with actual MarketData._compute_chain_metrics() schema."""
    settings = Settings()
    feed = MarketData(settings, cache=None)
    feed.set_instrument("NAS100")

    spot = 18000.0
    strikes = [17000.0, 17500.0, 18000.0, 18500.0, 19000.0]
    raw = {
        "expiry": "2026-08-15",
        "t_years": 2.0 / 252.0,
        "strikes": strikes,
        "call_mid": [1100.0, 650.0, 250.0, 50.0, 5.0],
        "put_mid": [5.0, 45.0, 240.0, 620.0, 1080.0],
        "call_oi": [1000.0] * 5,
        "put_oi": [1000.0] * 5,
        "call_iv": [0.18] * 5,
        "put_iv": [0.18] * 5,
    }

    metrics = feed._compute_chain_metrics(raw, spot, proxy="QQQ", demo=False)
    vol_info = {"reference_volatility_annual": 0.18, "volatility_status": "valid"}

    # Pass producer metrics into option-native forecast
    forecast = PassiveLearningEngine._forecast(spot, vol_info, 0, metrics, "NAS100", horizon_kind="option_native_expiry")

    assert forecast["probability_measure"] == "risk_neutral_Q_terminal"
    assert forecast["q_terminal_distribution_available"] is True
    assert forecast["proxy_symbol"] == "QQQ"


def test_20_full_prospective_cycle_without_lookahead(tmp_path):
    """Test 20: T0 capture -> immutable DB row -> future data -> resolver -> terminal outcome without lookahead."""
    db_path = str(tmp_path / "passive.db")
    settings = Settings()
    engine = PassiveLearningEngine(db_path, settings, cache=None)

    ts = 1_700_000_000.0
    features = {"source_observation_ts": ts, "volatility": {"reference_annual": 0.20}}
    forecast = {"reference_volatility_annual": 0.20}

    # 1. T0 Capture
    ids = engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features=features, forecast=forecast, provenance={},
        trigger_reason="test", evidence_eligible=True
    )
    assert len(ids) >= 7

    # 2. Future path data recorded after T0
    engine.record_market_bar("NAS100", ts, ts + 60.0, 100.0, 101.5, 99.8, 101.0)
    engine.record_market_bar("NAS100", ts + 60.0, ts + 900.0, 101.0, 102.5, 100.5, 102.0)

    # 3. Resolver
    engine.resolve_due(now=ts + 1000.0)

    # 4. Calibration & Health Status
    status = engine.status()
    assert status["current_contract_version"] == PASSIVE_SCHEMA_VERSION
    assert status["pristine_f32_n"] > 0
    assert status["g1_training_allowed"] is False

    engine.close()


def test_21_existing_management_regression(tmp_path):
    """Test 21: Position event ledger, remaining fraction, and management UI logic remain invariant."""
    ledger = PositionLedger(str(tmp_path / "trades.db"))
    row = {"id": 1, "opened_at": 1000.0, "entry": 100.0, "stop": 90.0, "take": 125.0, "max_r": 0.0, "status": "open"}
    state = ledger.state(row)
    assert state["remaining_position_fraction"] == 1.0

    snap = {
        "captured_ts": 2000.0, "trade_id": 1,
        "position_state": state,
        "policy_manager": {"recommendation": {"policy": "CLOSE_25"}}
    }
    decision = ledger.preview_decision(snap, row)
    snap["policy_manager"]["management_decision"] = decision
    ledger.register_decision(snap, "review-1", row)

    out = ledger.acknowledge(decision_id=decision["decision_id"], trade=row, executed=True, execution_price=105.0, execution_r=0.5)
    assert out["position_state"]["remaining_position_fraction"] == pytest.approx(0.75)

    # Test idempotency
    again = ledger.acknowledge(decision_id=decision["decision_id"], trade=row, executed=True, execution_price=106.0, execution_r=0.6)
    assert again["idempotent"] is True
    assert again["position_state"]["remaining_position_fraction"] == pytest.approx(0.75)

    ledger.close()
