from __future__ import annotations

import time

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.g1_research_worker import _run_g1s_bounded
from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime
from seiltanzer.passive_learning import PassiveLearningEngine
from seiltanzer import measurement_q_runtime as _mq


def _capture(passive, ts, price=100.0):
    features = {
        "source_observation_ts": ts,
        "price_state": {"price": price, "ts": ts, "available": True},
        "market_regime": "test-regime",
        "session": "OPEN",
        "volatility": {"reference_volatility_annual": 0.20},
    }
    forecast = {"reference_volatility_annual": 0.20, "forecast_created_ts": ts}
    provenance = {
        "price": {"source": "test-direct", "age_sec": 0.0, "quality": 1.0, "kind": "direct"},
        "options": {"source": None, "age_sec": None, "quality": 0.0, "kind": "unavailable"},
    }
    old = _mq.time.time
    passive._f32a_background_capture = True
    _mq.time.time = lambda: float(ts)
    try:
        return passive.capture_observation(
            instrument="XAU", captured_ts=ts, market_price=price,
            features=features, forecast=forecast, provenance=provenance,
            trigger_reason="cadence", evidence_eligible=True,
            observation_origin="background_collector",
        )
    finally:
        passive._f32a_background_capture = False
        _mq.time.time = old


def test_status_is_incremental_idempotent_and_does_not_load_full_history(tmp_path, monkeypatch):
    cache = DiskCache(str(tmp_path / "cache.db"))
    passive = PassiveLearningEngine(
        str(tmp_path / "trades.db"), Settings(demo=False, data_dir=str(tmp_path)), cache)
    engine = type("Engine", (), {"passive": passive})()
    runtime = ShortHorizonRuntime(engine)
    try:
        ts = 1_700_000_000.0
        _capture(passive, ts)
        assert runtime.materialize_new() == 5
        passive.record_market_point("XAU", ts, 100.0,
                                    source="test-direct", quality=1.0, kind="direct")
        for minute in range(5, 61, 5):
            passive.record_market_point(
                "XAU", ts + minute*60.0, 100.0 + minute/100.0,
                source="test-direct", quality=1.0, kind="direct")
        passive.resolve_due(now=ts + 60*60.0)
        assert runtime.resolve_new() >= 3

        first = runtime.refresh_materialized_status(limit=10000)
        assert first["observations_processed"] == 5
        assert first["resolutions_processed"] >= 3
        second = runtime.refresh_materialized_status(limit=10000)
        assert second["observations_processed"] == 0
        assert second["resolutions_processed"] == 0

        # Request-time status must not fall back to _resolved_eligible/full-row loading.
        monkeypatch.setattr(runtime, "_resolved_eligible", lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("full history scan from status")))
        status = runtime.status()
        by_h = {row["horizon_minutes"]: row for row in status["horizons"]}
        assert by_h[15]["raw_resolved"] >= 1
        assert by_h[30]["raw_resolved"] >= 1
        assert by_h[60]["raw_resolved"] >= 1
        assert status["status_materialization"]["lag_rows"] == 0
        assert status["authority"]["production_authority"] is False
    finally:
        passive.close(); cache.close()


class _WorkerRuntime:
    def __init__(self):
        self.fit_calls = 0
        self.ready = False

    def materialize_new(self, limit): return 0
    def resolve_new(self, limit): return 0
    def refresh_materialized_status(self, limit): return {"observations_processed": 0, "resolutions_processed": 0}
    def materialize_trade_links(self): return 0
    def fit_if_ready(self): self.fit_calls += 1; return 1
    def status(self): return {"horizons": [{"fit_allowed": self.ready}]}


def test_worker_does_not_scan_training_cut_every_ten_seconds(monkeypatch):
    runtime = _WorkerRuntime()
    monkeypatch.setattr(
        "seiltanzer.g1_short_horizon_refinement._materialize_barriers",
        lambda _runtime, limit: 0)
    monkeypatch.setattr(
        "seiltanzer.g1_short_horizon_metrics_refinement._materialize_path_metrics",
        lambda _runtime, limit: 0)

    runtime.ready = False
    first = _run_g1s_bounded(runtime)
    assert first["fit_gate_due"] is True
    assert first["fit_gate_ready"] is False
    assert runtime.fit_calls == 0

    # Simulate a later gate becoming ready; clear only the gate timestamp once.
    runtime.ready = True
    runtime._g1s_worker_last_fit_gate_ts = 0.0
    second = _run_g1s_bounded(runtime)
    assert second["fit_gate_ready"] is True
    assert runtime.fit_calls == 1

    third = _run_g1s_bounded(runtime)
    assert third["fit_gate_due"] is False
    assert runtime.fit_calls == 1
