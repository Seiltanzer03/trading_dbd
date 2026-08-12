from __future__ import annotations

import json
import math

import pytest

from seiltanzer import g1_broad_market_evidence_v3 as v3
from seiltanzer import g1_operational_integrity as integrity
from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.passive_learning import PassiveLearningEngine


@pytest.fixture
def passive(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    engine = PassiveLearningEngine(
        str(tmp_path / "trades.db"),
        Settings(demo=True, data_dir=str(tmp_path)), cache,
    )
    yield engine
    engine.close()
    cache.close()


def _seed_bars(engine: PassiveLearningEngine, captured: float, minutes: int = 240):
    start = captured - minutes * 60.0
    rows = []
    for i in range(minutes):
        bar_start = start + i * 60.0
        bar_end = bar_start + 60.0
        close = 100.0 + 0.004 * i + 0.35 * math.sin(i / 13.0)
        previous = 100.0 + 0.004 * max(i - 1, 0) + 0.35 * math.sin(max(i - 1, 0) / 13.0)
        rows.append((
            "NAS100", bar_start, bar_end, previous,
            max(previous, close) + 0.03, min(previous, close) - 0.03, close,
            "test_direct_1m", 0.9, "direct", captured,
        ))
    with engine._lock, engine._conn:
        engine._conn.executemany(
            "INSERT OR REPLACE INTO passive_market_bars(" 
            "instrument,bar_start_ts,bar_end_ts,open,high,low,close,source,quality,kind,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows,
        )


def _forecast(ts: float):
    return {
        "version": "test", "probability_measure": "risk_neutral_Q",
        "reference_volatility_annual": 0.2,
        "standardized_barriers": {"1.0": {"up": 0.3, "down": 0.2, "no_touch": 0.5}},
        "quantiles_log_return": {"q10": -0.02, "q25": -0.01, "q50": 0.0,
                                 "q75": 0.01, "q90": 0.02},
        "forecast_created_ts": ts,
    }


def _features(ts: float):
    return {
        "source_observation_ts": ts,
        "price_state": {"price": 101.0, "available": True},
        "volatility": {"reference_volatility_annual": 0.20},
        "option_distribution": {"available": False},
        "cross_asset": {"available": False},
        "market_regime": "NORMAL",
        "missing_is_not_zero": True,
    }


def _capture(engine: PassiveLearningEngine, ts: float):
    return engine.capture_observation(
        instrument="NAS100", captured_ts=ts, market_price=101.0,
        features=_features(ts), forecast=_forecast(ts), provenance={},
        trigger_reason="test", evidence_eligible=True,
    )


def test_current_wavelet_api_is_used_without_fabricating_legacy_v2_fields(passive):
    captured = 2_000_000_000.0
    _seed_bars(passive, captured, minutes=240)
    ids = _capture(passive, captured)
    assert ids

    row = passive._conn.execute(
        "SELECT features_json FROM passive_market_observations WHERE observation_id=?",
        (ids[0],),
    ).fetchone()
    features = json.loads(row[0])
    v2_block = features["g1s_evidence_v2"]
    wavelet = v2_block["wavelet"]
    assert wavelet["contract_version"] == "g1s-v2-wavelet-compat-v1"
    assert wavelet["legacy_semantics_available"] is False
    assert wavelet["available"] is False
    assert wavelet["low_pct"] is None
    assert wavelet["high_pct"] is None
    assert wavelet["resonance"] is None
    assert wavelet["native_wavelet_v3"]["available"] is True

    v3_block = features["g1s_evidence_v3"]
    assert v3_block["wavelet"]["available"] is True
    assert v3_block["semantics"]["optional_feature_failure_does_not_cancel_core_t0"] is True


def test_forced_wavelet_failure_is_isolated_and_core_t0_is_still_persisted(passive, monkeypatch):
    captured = 2_000_100_000.0
    _seed_bars(passive, captured, minutes=240)

    def boom(*args, **kwargs):
        raise RuntimeError("forced wavelet failure")

    monkeypatch.setattr(v3, "compute_wavelet_analysis", boom)
    ids = _capture(passive, captured)
    assert ids, "optional Wavelet failure must not cancel the core observation"

    row = passive._conn.execute(
        "SELECT market_price,features_json FROM passive_market_observations WHERE observation_id=?",
        (ids[0],),
    ).fetchone()
    assert float(row["market_price"]) == pytest.approx(101.0)
    features = json.loads(row["features_json"])
    assert features["g1s_evidence_v2"]["wavelet"]["available"] is False
    assert features["g1s_evidence_v3"]["wavelet"]["available"] is False
    assert features["g1s_evidence_v3"]["wavelet"]["reason"] == "optional_feature_error"

    health = integrity._load_health(passive)
    assert "v2_wavelet" in health["errors_by_feature_family"]
    assert "v3_wavelet" in health["errors_by_feature_family"]
    assert "forced wavelet failure" in health["last_error"]


def test_meaningful_feature_error_persists_in_health_state(passive):
    integrity._record_feature_error(passive, "wavelet", RuntimeError("first meaningful error"))
    before = integrity._load_health(passive)
    assert "first meaningful error" in before["last_error"]
    assert before["last_error_ts"] is not None

    # A later successful telemetry write must not erase error history. This is
    # the stateful contract that prevents market_closed/no-trigger from making a
    # prior collector failure disappear from operational diagnostics.
    after = integrity._load_health(passive)
    assert after["last_error"] == before["last_error"]
    assert after["last_error_ts"] == before["last_error_ts"]
    assert after["errors_by_feature_family"]["wavelet"]["count"] == 1


def test_status_exposes_stateful_operational_fields(passive):
    status = passive.status()
    health = status["collector_health"]
    assert status["operational_collector_status"] in {"RUNNING", "DEGRADED", "STALLED"}
    for key in (
        "last_step_ts", "last_successful_eligible_capture_ts", "eligible_capture_age_sec",
        "eligible_captures_1h", "eligible_captures_24h", "last_error_ts", "last_error",
        "errors_by_feature_family", "consecutive_failed_capture_cycles",
    ):
        assert key in health
    assert health["optional_feature_failure_cancels_core_capture"] is False
    assert health["meaningful_error_persists_across_market_closed_cycle"] is True
