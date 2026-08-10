import datetime as dt
import json
import math

import pytest

from seiltanzer.config import Settings
from seiltanzer.option_q_adapter import adapt_option_q_forecast
from seiltanzer.passive_learning import (
    PassiveLearningEngine,
    _advance_trading_time,
    _trading_seconds_between,
)
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION


def _forecast():
    return {"reference_volatility_annual": 0.20}


def _option_features(ts, expiry, *, proxy="FXC", proxy_spot=100.0):
    metrics = {
        "proxy": proxy,
        "proxy_spot": proxy_spot,
        "spot": proxy_spot,
        "expiry": "2026-08-14",
        "expiry_ts_utc": expiry,
        "t_years": (expiry - ts) / (365.0 * 86400.0),
        "density": {
            "strikes": [80.0, 90.0, 100.0, 110.0, 120.0],
            "q": [0.01, 0.03, 0.08, 0.04, 0.01],
        },
        "implied_move": {"move_frac": 0.01},
        "skew": 0.02,
    }
    return {
        "source_observation_ts": ts,
        "volatility": {"reference_volatility_annual": 0.20, "volatility_status": "valid"},
        "option_derivatives": {"available": True, "data": metrics},
        "option_distribution": dict(metrics),
    }


def test_inverse_proxy_uses_usdcad_config_and_reflects_quantiles():
    density = {
        "proxy": "FXC", "proxy_spot": 100.0, "spot": 100.0,
        "t_years": 0.02,
        "density": {
            "strikes": [80.0, 90.0, 100.0, 110.0, 120.0],
            "q": [0.01, 0.03, 0.08, 0.04, 0.01],
        },
        "implied_move": {"move_frac": 0.01},
    }
    inverse = adapt_option_q_forecast(
        density, 120, 0.01, "USDCAD", instrument_spot=1.30,
        horizon_kind="option_native_expiry",
    )
    direct_metrics = dict(density)
    direct_metrics["proxy_transform"] = "direct"
    direct = adapt_option_q_forecast(
        direct_metrics, 120, 0.01, "USDCAD", instrument_spot=1.30,
        horizon_kind="option_native_expiry",
    )
    assert inverse["proxy_transform"] == "inverse"
    assert inverse["proxy_transform_source"] == "instrument_config"
    assert inverse["measurement_runtime_contract"] == MEASUREMENT_RUNTIME_VERSION
    support, cdf = inverse["terminal_q_cdf"]["support"], inverse["terminal_q_cdf"]["cdf"]
    assert all(a < b for a, b in zip(support, support[1:]))
    assert all(a <= b for a, b in zip(cdf, cdf[1:]))
    assert cdf[0] == pytest.approx(0.0, abs=1e-6)
    assert cdf[-1] == pytest.approx(1.0, abs=1e-6)
    assert inverse["quantiles_log_return"]["q10"] == pytest.approx(
        -direct["quantiles_log_return"]["q90"], abs=0.04
    )
    assert inverse["quantiles_log_return"]["q90"] == pytest.approx(
        -direct["quantiles_log_return"]["q10"], abs=0.04
    )


def test_trading_horizon_is_exact_with_seconds_and_weekend():
    start = dt.datetime(2026, 1, 9, 20, 49, 23, tzinfo=dt.timezone.utc).timestamp()
    target = _advance_trading_time("NAS100", start, 20)
    assert _trading_seconds_between("NAS100", start, target) == pytest.approx(1200.0)
    local = dt.datetime.fromtimestamp(target, dt.timezone.utc)
    assert local.weekday() == 0
    assert local.second == 23


def test_known_expiry_is_allowed_but_future_source_timestamp_is_rejected(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "p.db"), Settings(), cache=None)
    ts = dt.datetime(2026, 8, 11, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    expiry = ts + 2 * 3600
    features = _option_features(ts, expiry)
    ids = engine.capture_observation(
        instrument="USDCAD", captured_ts=ts, market_price=1.30,
        features=features, forecast=_forecast(), provenance={}, trigger_reason="test",
    )
    assert len(ids) == 8
    native = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id LIKE '%native-expiry'"
    ).fetchone())
    frozen = json.loads(native["forecast_json"])
    assert native["target_ts"] == pytest.approx(expiry)
    assert native["observation_origin"] == "test"
    assert frozen["source_expiry_ts_utc"] == pytest.approx(expiry)
    assert frozen["proxy_transform"] == "inverse"
    assert frozen["measurement_runtime_contract"] == MEASUREMENT_RUNTIME_VERSION
    assert frozen["terminal_q_cdf"]["cdf"][-1] == pytest.approx(1.0)

    bad = _option_features(ts, expiry)
    bad["source_observation_ts"] = ts + 1
    with pytest.raises(ValueError, match="post-capture"):
        engine.capture_observation(
            instrument="USDCAD", captured_ts=ts, market_price=1.30,
            features=bad, forecast=_forecast(), provenance={}, trigger_reason="test",
        )
    engine.close()


def test_terminal_pit_uses_frozen_cdf_and_never_post_target_point(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "p.db"), Settings(), cache=None)
    ts = dt.datetime(2026, 8, 11, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    expiry = ts + 15 * 60
    ids = engine.capture_observation(
        instrument="USDCAD", captured_ts=ts, market_price=1.30,
        features=_option_features(ts, expiry), forecast=_forecast(),
        provenance={}, trigger_reason="test",
    )
    native_id = next(x for x in ids if x.endswith("native-expiry"))
    row = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?", (native_id,)
    ).fetchone())
    frozen = json.loads(row["forecast_json"])
    support, cdf = frozen["terminal_q_cdf"]["support"], frozen["terminal_q_cdf"]["cdf"]
    realized = float(support[2])
    price = 1.30 * math.exp(realized)
    engine.record_market_point(
        "USDCAD", expiry - 30, price, source="Swissquote OTC USD/CAD",
        quality=0.98, kind="direct",
    )
    engine.record_market_point(
        "USDCAD", expiry + 30, price * 1.2, source="Swissquote OTC USD/CAD",
        quality=0.98, kind="direct",
    )
    assert engine._resolve_one(row, expiry + 31) == "resolved"
    outcome = json.loads(engine._conn.execute(
        "SELECT outcome_json FROM passive_market_observations WHERE observation_id=?",
        (native_id,),
    ).fetchone()[0])
    assert outcome["terminal"]["terminal_price_ts"] == pytest.approx(expiry - 30)
    assert outcome["terminal"]["terminal_lookahead_used"] is False
    assert outcome["terminal"]["terminal_pit_q"] == pytest.approx(cdf[2], abs=1e-6)
    assert outcome["actual_quantile_placement"] == pytest.approx(cdf[2], abs=1e-6)
    engine.close()


def test_complete_authoritative_ohlc_makes_no_touch_clean(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "p.db"), Settings(), cache=None)
    ts = dt.datetime(2026, 8, 11, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    ids = engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features={"source_observation_ts": ts}, forecast=_forecast(),
        provenance={}, trigger_reason="test",
    )
    row = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?", (ids[0],)
    ).fetchone())
    target = float(row["target_ts"])
    for minute in range(15):
        start = ts + minute * 60
        engine.record_market_bar(
            "NAS100", start, start + 60, 100, 100.01, 99.99, 100,
            source="broker_oanda_1m", quality=0.99, kind="direct",
        )
    engine.record_market_point(
        "NAS100", target, 100, source="TradingView stream OANDA:NAS100USD",
        quality=0.98, kind="direct",
    )
    assert engine._resolve_one(row, target) == "resolved"
    outcome = json.loads(engine._conn.execute(
        "SELECT outcome_json FROM passive_market_observations WHERE observation_id=?",
        (ids[0],),
    ).fetchone()[0])
    assert outcome["first_touch"]["label"] == "no_touch"
    assert outcome["first_touch"]["clean_label"] is True
    assert outcome["first_touch"]["authoritative_path"] is True
    assert outcome["authoritative_path_coverage_ratio"] == pytest.approx(1.0)
    engine.close()


def test_yahoo_or_partial_ohlc_can_never_be_clean(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "p.db"), Settings(), cache=None)
    ts = dt.datetime(2026, 8, 11, 15, 0, 23, tzinfo=dt.timezone.utc).timestamp()
    ids = engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=100.0,
        features={"source_observation_ts": ts}, forecast=_forecast(),
        provenance={}, trigger_reason="test",
    )
    row = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?", (ids[0],)
    ).fetchone())
    target = float(row["target_ts"])
    cursor = math.ceil(ts / 60) * 60
    while cursor + 60 <= target:
        engine.record_market_bar(
            "NAS100", cursor, cursor + 60, 100, 100.01, 99.99, 100,
            source="yahoo_1m_direct", quality=1.0, kind="direct",
        )
        cursor += 60
    engine.record_market_point(
        "NAS100", target - 10, 100, source="TradingView stream OANDA:NAS100USD",
        quality=0.98, kind="direct",
    )
    assert engine._resolve_one(row, target + 1) == "resolved"
    outcome = json.loads(engine._conn.execute(
        "SELECT outcome_json FROM passive_market_observations WHERE observation_id=?",
        (ids[0],),
    ).fetchone()[0])
    assert outcome["first_touch"]["clean_label"] is False
    assert outcome["authoritative_path_coverage_ratio"] == 0.0
    assert outcome["partial_first_bar_unobserved_sec"] > 0
    engine.close()


def test_manual_cannot_self_assert_background_and_empty_readiness_is_false(tmp_path, monkeypatch):
    import seiltanzer.measurement_q_runtime as runtime

    engine = PassiveLearningEngine(str(tmp_path / "p.db"), Settings(), cache=None)
    ts = dt.datetime(2026, 8, 11, 15, 0, tzinfo=dt.timezone.utc).timestamp()
    external = engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=99,
        features={"source_observation_ts": ts}, forecast=_forecast(),
        provenance={"price": {"kind": "direct", "quality": .98, "age_sec": 0}},
        trigger_reason="cadence", observation_origin="background_collector",
    )
    origin = engine._conn.execute(
        "SELECT observation_origin FROM passive_market_observations WHERE observation_id=?",
        (external[0],),
    ).fetchone()[0]
    assert origin == "manual"

    monkeypatch.setattr(runtime.time, "time", lambda: ts + 1)
    engine._f32a_background_capture = True
    try:
        internal = engine.capture_observation(
            instrument="NAS100", captured_ts=ts, market_price=100,
            features={"source_observation_ts": ts}, forecast=_forecast(),
            provenance={"price": {"kind": "direct", "quality": .98, "age_sec": 0}},
            trigger_reason="cadence",
        )
    finally:
        engine._f32a_background_capture = False
    assert engine._conn.execute(
        "SELECT observation_origin FROM passive_market_observations WHERE observation_id=?",
        (internal[0],),
    ).fetchone()[0] == "background_collector"
    status = engine.status()
    assert status["evidence_eligible_n"] == 7
    assert status["observation_origin_counts"]["manual"] == 7
    assert status["observation_origin_counts"]["background_collector"] == 7
    assert status["g1_training_allowed"] is False
    assert status["promotion_allowed"] is False
    engine.close()


def test_empty_runtime_does_not_claim_validation(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "p.db"), Settings(), cache=None)
    integrity = engine.status()["measurement_integrity"]
    assert integrity["horizon_contract_runtime_validated"] is False
    assert integrity["terminal_q_contract_runtime_validated"] is False
    assert integrity["first_touch_contract_runtime_validated"] is False
    assert integrity["proxy_mapping_runtime_validated"] is False
    assert integrity["time_contract_runtime_validated"] is False
    engine.close()
