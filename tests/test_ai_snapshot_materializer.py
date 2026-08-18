import time

import pytest

from seiltanzer.ai_snapshot_materializer import (
    AISnapshotMaterializer,
    SnapshotNotReady,
)


class FakeJournal:
    def __init__(self, trade_id=1):
        self.trade_id = trade_id
        self.entry = 100.0
        self.stop = 90.0
        self.take = 130.0
        self.direction = "long"

    def active_trade(self):
        if self.trade_id is None:
            return None
        return {
            "id": self.trade_id,
            "entry": self.entry,
            "stop": self.stop,
            "take": self.take,
            "direction": self.direction,
        }


class FakeMarket:
    def __init__(self):
        self.price = {"value": 101.0}
        self.chain = {"asof_ts": 1000.0, "source": "test"}


class FakeEngine:
    def __init__(self, trade_id=1):
        self.journal = FakeJournal(trade_id)
        self.market = FakeMarket()

    def _current_instrument_price(self, _trade):
        return self.market.price["value"]


def snapshot(trade_id=1, current_r=0.10, next_rung=0.20):
    return {
        "captured_ts": time.time(),
        "trade_id": trade_id,
        "trade_geometry": {"current_r": current_r},
        "policy_manager": {
            "recommendation": {"next_rung_r": next_rung},
            "cancellation_boundary": {"available": False},
            "first_touch_clock": {"risk_barrier_r": -1.0},
        },
    }


def test_materializer_returns_same_review_without_periodic_heavy_rebuild():
    engine = FakeEngine()
    calls = []

    def builder(_engine):
        calls.append(1)
        return snapshot()

    mat = AISnapshotMaterializer(engine, builder, startup_delay_sec=0,
                                 watch_interval_sec=1)
    mat._build_once()
    # Wall-clock age alone must not force another 6,500-path calculation.
    mat._built_at = time.time() - 3600
    result = mat.cached_build_snapshot(engine)

    assert len(calls) == 1
    assert result["trade_id"] == 1
    assert result["materialization"]["request_path_recomputed"] is False
    assert result["next_review_trigger"]["movement_delta_r"] == 0.15
    assert result["next_review_trigger"]["lower_price"] == 99.5
    assert result["next_review_trigger"]["upper_price"] == 102.5
    assert mat.status()["periodic_heavy_recompute"] is False
    assert mat.status()["ready"] is True


def test_price_move_0_15r_invalidates_and_requests_refresh():
    engine = FakeEngine()
    mat = AISnapshotMaterializer(engine, lambda _: snapshot(),
                                 watch_interval_sec=1)
    mat._build_once()
    engine.market.price["value"] = 102.5  # 0.25R vs 0.10R baseline

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)

    assert exc.value.status["reason"] == "PRICE_MOVED_0_15R"
    assert exc.value.status["ready"] is False
    assert mat._wake.is_set()


def test_strategy_boundary_crossing_invalidates_before_0_15r():
    engine = FakeEngine()
    mat = AISnapshotMaterializer(engine, lambda _: snapshot(next_rung=0.18),
                                 watch_interval_sec=1)
    mat._build_once()
    engine.market.price["value"] = 101.9  # 0.19R: only +0.09R, but crosses 0.18R rung

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)

    assert exc.value.status["reason"] == "STRATEGY_OR_RISK_BOUNDARY_CROSSED"


def test_new_option_chain_invalidates_review_without_network_call():
    engine = FakeEngine()
    mat = AISnapshotMaterializer(engine, lambda _: snapshot(),
                                 watch_interval_sec=1)
    mat._build_once()
    engine.market.chain["asof_ts"] = 1100.0

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)

    assert exc.value.status["reason"] == "NEW_OPTION_CHAIN"


def test_materializer_never_serves_snapshot_after_active_trade_changes():
    engine = FakeEngine(1)
    mat = AISnapshotMaterializer(engine, lambda _: snapshot(1),
                                 watch_interval_sec=1)
    mat._build_once()
    engine.journal.trade_id = 2

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)

    assert exc.value.status["reason"] == "TRADE_CHANGED"
    assert exc.value.status["ready"] is False


def test_failed_event_build_does_not_revalidate_old_crossed_snapshot():
    engine = FakeEngine()
    answers = [snapshot(), RuntimeError("boom")]

    def builder(_):
        value = answers.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    mat = AISnapshotMaterializer(engine, builder, watch_interval_sec=1)
    mat._build_once()
    first_built = mat._built_at
    mat.request_refresh("PRICE_MOVED_0_15R")
    mat._build_once()

    assert mat.status()["last_error"].startswith("RuntimeError:boom")
    assert mat._built_at == first_built
    with pytest.raises(SnapshotNotReady):
        mat.cached_build_snapshot(engine)


def test_no_active_trade_is_fast_unavailable_not_a_fake_snapshot():
    engine = FakeEngine(None)
    mat = AISnapshotMaterializer(engine, lambda _: (_ for _ in ()).throw(AssertionError()),
                                 watch_interval_sec=1)
    mat._build_once()

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)
    assert exc.value.status["reason"] == "NO_ACTIVE_TRADE"
    assert exc.value.status["ready"] is False
