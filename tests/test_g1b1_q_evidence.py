import datetime as dt
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seiltanzer.config import Settings
from seiltanzer.g1_q_evidence_runtime import (
    G1B1_STAGE,
    Q_CAPABILITY_CONTRACT_VERSION,
    Q_CAPTURE_ATTEMPT_CONTRACT_VERSION,
    Q_EVIDENCE_CONTRACT_VERSION,
)
from seiltanzer.g1_q_routes import install_g1_q_routes
from seiltanzer.measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from seiltanzer.option_q_adapter import OPTION_Q_CONTRACT_VERSION
from seiltanzer.passive_learning import PassiveLearningEngine


def _option_metrics(captured, expiry, *, proxy="QQQ", transform="direct", points=7):
    strikes = [70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0][:points]
    density = [0.05, 0.2, 0.7, 1.0, 0.7, 0.2, 0.05][:points]
    return {
        "proxy": proxy,
        "proxy_spot": 100.0,
        "spot": 100.0,
        "proxy_transform": transform,
        "expiry": "2026-08-14",
        "expiry_ts_utc": expiry,
        "t_years": (expiry - captured) / (365.0 * 86400.0),
        "density": {"strikes": strikes, "q": density},
        "implied_move": {"move_frac": 0.02},
        "skew": 0.0,
    }


def _features(captured, expiry, *, instrument="NAS100", points=7, transform="direct"):
    proxy = "FXC" if instrument == "USDCAD" else "QQQ"
    metrics = _option_metrics(captured, expiry, proxy=proxy, transform=transform, points=points)
    return {
        "source_observation_ts": captured,
        "price_state": {"ts": captured, "price": 100.0, "available": True},
        "volatility": {"reference_volatility_annual": 0.20, "volatility_status": "valid"},
        "option_derivatives": {"available": True, "data": metrics},
        "option_distribution": metrics,
        "market_regime": "NORMAL",
        "session": "OPEN",
    }


def _provenance(*, direct=True):
    return {
        "price": {
            "source": "TradingView snapshot OANDA:NAS100USD" if direct else "Yahoo fallback",
            "kind": "direct" if direct else "proxy",
            "quality": 0.98 if direct else 0.55,
            "age_sec": 0.0,
        },
        "options": {
            "source": "yfinance QQQ options 2026-08-14",
            "kind": "proxy",
            "quality": 0.72,
            "age_sec": 0.0,
        },
    }


def _background_capture(engine, monkeypatch, *, instrument="NAS100", points=7, direct=True):
    import seiltanzer.measurement_q_runtime as measurement_runtime
    import seiltanzer.g1_q_evidence_runtime as q_runtime

    captured = dt.datetime(2026, 8, 11, 14, 0, tzinfo=dt.timezone.utc).timestamp()
    expiry = captured + 2 * 86400.0
    monkeypatch.setattr(measurement_runtime.time, "time", lambda: captured)
    monkeypatch.setattr(q_runtime.time, "time", lambda: captured)
    engine._f32a_background_capture = True
    try:
        ids = engine.capture_observation(
            instrument=instrument,
            captured_ts=captured,
            market_price=100.0,
            features=_features(
                captured, expiry, instrument=instrument, points=points,
                transform="inverse" if instrument == "USDCAD" else "direct",
            ),
            forecast={"reference_volatility_annual": 0.20},
            provenance=_provenance(direct=direct),
            trigger_reason="cadence",
            evidence_eligible=True,
        )
    finally:
        engine._f32a_background_capture = False
    return captured, expiry, ids


def test_native_q_capture_creates_immutable_attempt_and_frozen_cdf(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "q.db"), Settings(), cache=None)
    captured, expiry, ids = _background_capture(engine, monkeypatch)
    native = [item for item in ids if item.endswith("-native-expiry")]
    assert len(native) == 1

    attempt = dict(engine._conn.execute(
        "SELECT * FROM g1_q_capture_attempts WHERE attempt_origin='background_collector'"
    ).fetchone())
    assert attempt["observation_created"] == 1
    assert attempt["blocker_code"] is None
    assert attempt["relation"] == "DIRECT_PROXY"
    assert attempt["proxy_transform"] == "direct"
    assert attempt["capability_contract_version"] == Q_CAPABILITY_CONTRACT_VERSION
    assert attempt["attempt_contract_version"] == Q_CAPTURE_ATTEMPT_CONTRACT_VERSION

    row = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?", (native[0],)
    ).fetchone())
    forecast = json.loads(row["forecast_json"])
    assert forecast["probability_measure"] == "risk_neutral_Q_terminal"
    assert forecast["q_source_contract"] == OPTION_Q_CONTRACT_VERSION
    assert forecast["measurement_runtime_contract"] == MEASUREMENT_RUNTIME_VERSION
    assert forecast["source_expiry_ts_utc"] == pytest.approx(expiry)
    assert len(forecast["terminal_q_cdf"]["support"]) >= 5

    with pytest.raises(Exception):
        with engine._conn:
            engine._conn.execute(
                "UPDATE g1_q_capture_attempts SET blocker_code='CDF_INVALID' WHERE attempt_id=?",
                (attempt["attempt_id"],),
            )
    engine.close()


def test_invalid_density_is_diagnosed_without_relaxing_q_contract(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "qbad.db"), Settings(), cache=None)
    _, _, ids = _background_capture(engine, monkeypatch, points=4)
    assert any(item.endswith("-native-expiry") for item in ids)
    attempt = dict(engine._conn.execute(
        "SELECT * FROM g1_q_capture_attempts WHERE attempt_origin='background_collector'"
    ).fetchone())
    assert attempt["observation_created"] == 0
    assert attempt["blocker_code"] == "INSUFFICIENT_STRIKES"
    status = engine.g1_q_status()
    assert status["successful_q_capture_n"] == 0
    assert status["q_distribution_error_n"] == 1
    assert status["physical_probability_published"] is False
    engine.close()


def test_inverse_proxy_is_frozen_and_never_silently_direct(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "inverse.db"), Settings(), cache=None)
    _, _, ids = _background_capture(engine, monkeypatch, instrument="USDCAD")
    native = [item for item in ids if item.endswith("-native-expiry")][0]
    forecast = json.loads(engine._conn.execute(
        "SELECT forecast_json FROM passive_market_observations WHERE observation_id=?", (native,)
    ).fetchone()[0])
    assert forecast["proxy_symbol"] == "FXC"
    assert forecast["proxy_transform"] == "inverse"
    attempt = dict(engine._conn.execute(
        "SELECT * FROM g1_q_capture_attempts WHERE created_observation_id=?", (native,)
    ).fetchone())
    assert attempt["relation"] == "INVERSE_PROXY"
    assert attempt["proxy_transform"] == "inverse"
    assert attempt["observation_created"] == 1
    engine.close()


def test_manual_background_spoof_never_enters_production_q_telemetry(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "manual.db"), Settings(), cache=None)
    captured = dt.datetime(2026, 8, 11, 14, 0, tzinfo=dt.timezone.utc).timestamp()
    expiry = captured + 86400
    engine.capture_observation(
        instrument="NAS100", captured_ts=captured, market_price=100.0,
        features=_features(captured, expiry),
        forecast={"reference_volatility_annual": 0.20},
        provenance=_provenance(), trigger_reason="test", evidence_eligible=True,
        observation_origin="background_collector",
    )
    attempt = dict(engine._conn.execute("SELECT * FROM g1_q_capture_attempts").fetchone())
    assert attempt["attempt_origin"] == "test"
    assert engine.g1_q_status()["capture_attempt_n"] == 0
    engine.close()


def test_q_capture_resolution_flows_into_g1a_and_g1b(tmp_path, monkeypatch):
    engine = PassiveLearningEngine(str(tmp_path / "resolve.db"), Settings(), cache=None)
    _, _, ids = _background_capture(engine, monkeypatch)
    native_id = [item for item in ids if item.endswith("-native-expiry")][0]
    row = dict(engine._conn.execute(
        "SELECT * FROM passive_market_observations WHERE observation_id=?", (native_id,)
    ).fetchone())
    target = float(row["target_ts"])
    engine.record_market_point(
        "NAS100", target, 101.0,
        source="TradingView stream OANDA:NAS100USD", quality=0.99, kind="direct",
    )
    assert engine._resolve_one(row, target) == "resolved"
    engine._g1_sync_membership(limit=5000)
    membership = dict(engine._conn.execute(
        "SELECT * FROM g1_dataset_membership WHERE observation_id=?", (native_id,)
    ).fetchone())
    assert membership["q_to_p_eligible"] == 1
    outcome = json.loads(engine._conn.execute(
        "SELECT outcome_json FROM passive_market_observations WHERE observation_id=?", (native_id,)
    ).fetchone()[0])
    assert outcome["terminal"]["terminal_pit_q"] is not None

    status = engine.g1_q_status()
    assert status["successful_q_capture_n"] == 1
    assert status["resolved_q_observation_n"] == 1
    assert status["q_to_p_eligible_n"] == 1
    assert status["g1b_q_metrics_eligible_n"] == 1
    assert status["runtime_validated"] is True
    assert status["production_authority"] is False
    assert status["g1_training_allowed"] is False
    assert status["promotion_allowed"] is False
    engine.close()


def test_q_routes_are_read_only_and_expose_stage(tmp_path):
    engine = PassiveLearningEngine(str(tmp_path / "routes.db"), Settings(), cache=None)
    app = FastAPI()
    app.state.engine = SimpleNamespace(passive=engine)
    install_g1_q_routes(app)
    client = TestClient(app)
    status = client.get("/api/research/g1/q/status")
    assert status.status_code == 200
    body = status.json()
    assert body["g1_stage"] == G1B1_STAGE
    assert body["q_evidence_contract_version"] == Q_EVIDENCE_CONTRACT_VERSION
    assert body["calibrator_fitted"] is False
    assert client.get("/api/research/g1/q/instruments").status_code == 200
    assert client.get("/api/research/g1/q/blockers").status_code == 200
    assert client.get("/api/research/g1/q/attempts").status_code == 200
    assert client.get("/api/research/g1/q/attempts?instrument=BAD").status_code == 404
    engine.close()
