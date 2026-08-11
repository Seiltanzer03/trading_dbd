import datetime as dt
import json

from seiltanzer.config import Settings
from seiltanzer.passive_learning import PassiveLearningEngine


def _metrics(captured: float, expiry: float) -> dict:
    return {
        "proxy": "QQQ",
        "proxy_spot": 100.0,
        "spot": 100.0,
        "proxy_transform": "direct",
        "expiry": "2026-08-14",
        "expiry_ts_utc": expiry,
        "t_years": (expiry - captured) / (365.0 * 86400.0),
        "density": {
            "strikes": [70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0],
            "q": [0.05, 0.2, 0.7, 1.0, 0.7, 0.2, 0.05],
        },
        "implied_move": {"move_frac": 0.02},
        "skew": 0.0,
    }


def _capture(
    engine: PassiveLearningEngine,
    monkeypatch,
    *,
    option_age=0.0,
    price_age=0.0,
    price_kind="direct",
):
    import seiltanzer.g1_q_evidence_runtime as q_runtime
    import seiltanzer.measurement_q_runtime as measurement_runtime

    captured = dt.datetime(2026, 8, 11, 14, 0, tzinfo=dt.timezone.utc).timestamp()
    expiry = captured + 2 * 86400.0
    monkeypatch.setattr(q_runtime.time, "time", lambda: captured)
    monkeypatch.setattr(measurement_runtime.time, "time", lambda: captured)
    metrics = _metrics(captured, expiry)
    features = {
        "source_observation_ts": captured,
        "price_state": {"ts": captured, "price": 100.0, "available": True},
        "volatility": {"reference_volatility_annual": 0.20, "volatility_status": "valid"},
        "option_derivatives": {"available": True, "data": metrics},
        "option_distribution": metrics,
        "market_regime": "NORMAL",
        "session": "OPEN",
    }
    provenance = {
        "price": {
            "source": "TradingView snapshot OANDA:NAS100USD",
            "kind": price_kind,
            "quality": 0.98,
            "age_sec": price_age,
        },
        "options": {
            "source": "yfinance QQQ options 2026-08-14",
            "kind": "proxy",
            "quality": 0.72,
            "age_sec": option_age,
        },
    }
    engine._f32a_background_capture = True
    try:
        ids = engine.capture_observation(
            instrument="NAS100",
            captured_ts=captured,
            market_price=100.0,
            features=features,
            forecast={"reference_volatility_annual": 0.20},
            provenance=provenance,
            trigger_reason="cadence",
            evidence_eligible=True,
        )
    finally:
        engine._f32a_background_capture = False
    attempt = dict(engine._conn.execute(
        "SELECT * FROM g1_q_capture_attempts WHERE attempt_origin='background_collector'"
    ).fetchone())
    stored_features = json.loads(engine._conn.execute(
        "SELECT features_json FROM passive_market_observations WHERE observation_id=?",
        (ids[0],),
    ).fetchone()[0])
    return attempt, stored_features, ids


def test_stale_option_snapshot_cannot_count_or_create_native_q(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "stale-option.db"), Settings(), cache=None)
    attempt, stored_features, ids = _capture(engine, monkeypatch, option_age=3601.0)
    assert attempt["observation_created"] == 0
    assert attempt["blocker_code"] == "OPTION_CHAIN_STALE"
    assert not any(item.endswith("-native-expiry") for item in ids)
    assert "_g1b1_refined_pre_blocker" not in stored_features
    status = engine.g1_q_status()
    assert status["successful_q_capture_n"] == 0
    assert status["q_evidence_integrity_contract_version"] == "g1-q-evidence-integrity-v1"
    engine.close()


def test_unknown_option_freshness_fails_closed(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "unknown-option-age.db"), Settings(), cache=None)
    attempt, _, ids = _capture(engine, monkeypatch, option_age=None)
    assert attempt["observation_created"] == 0
    assert attempt["blocker_code"] == "Q_SOURCE_STALE"
    assert not any(item.endswith("-native-expiry") for item in ids)
    assert engine.g1_q_status()["runtime_validated"] is False
    engine.close()


def test_proxy_target_price_cannot_establish_pristine_q_capture(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "proxy-target.db"), Settings(), cache=None)
    attempt, _, ids = _capture(engine, monkeypatch, price_kind="proxy")
    assert attempt["observation_created"] == 0
    assert attempt["blocker_code"] == "TARGET_PRICE_NON_DIRECT"
    assert not any(item.endswith("-native-expiry") for item in ids)
    assert engine.g1_q_status()["data_available"] is False
    engine.close()


def test_stale_target_price_cannot_establish_pristine_q_capture(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "stale-target.db"), Settings(), cache=None)
    attempt, _, ids = _capture(engine, monkeypatch, price_age=61.0)
    assert attempt["observation_created"] == 0
    assert attempt["blocker_code"] == "TARGET_PRICE_STALE"
    assert not any(item.endswith("-native-expiry") for item in ids)
    status = engine.g1_q_status()
    assert status["capture_attempt_n"] == 1
    assert status["successful_q_capture_n"] == 0
    assert status["production_authority"] is False
    engine.close()
