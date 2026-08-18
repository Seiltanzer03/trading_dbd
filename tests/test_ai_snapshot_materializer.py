import time

import pytest

from seiltanzer.ai_snapshot_materializer import (
    AISnapshotMaterializer,
    SnapshotNotReady,
)


class FakeJournal:
    def __init__(self, trade_id="trade-1"):
        self.trade_id = trade_id

    def active_trade(self):
        return {"id": self.trade_id} if self.trade_id else None


class FakeEngine:
    def __init__(self, trade_id="trade-1"):
        self.journal = FakeJournal(trade_id)


def snapshot(trade_id="trade-1"):
    return {
        "available": True,
        "trade": {"id": trade_id},
        "policy_manager": {"recommended_action": "HOLD"},
    }


def test_materializer_returns_fresh_same_trade_snapshot_without_rebuilding_on_request():
    engine = FakeEngine()
    calls = []

    def builder(_engine):
        calls.append(1)
        return snapshot()

    mat = AISnapshotMaterializer(engine, builder, startup_delay_sec=0,
                                 refresh_interval_sec=30, max_age_sec=90)
    mat._build_once()
    result = mat.cached_build_snapshot(engine)

    assert len(calls) == 1
    assert result["trade"]["id"] == "trade-1"
    assert result["materialization"]["request_path_recomputed"] is False
    assert mat.status()["ready"] is True


def test_materializer_never_serves_snapshot_after_active_trade_changes():
    engine = FakeEngine("trade-1")
    mat = AISnapshotMaterializer(engine, lambda _: snapshot("trade-1"),
                                 refresh_interval_sec=30, max_age_sec=90)
    mat._build_once()
    engine.journal.trade_id = "trade-2"

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)

    assert exc.value.status["reason"] == "TRADE_CHANGED"
    assert exc.value.status["ready"] is False
    assert mat._refresh.is_set()


def test_materializer_never_serves_stale_snapshot():
    engine = FakeEngine()
    mat = AISnapshotMaterializer(engine, lambda _: snapshot(),
                                 refresh_interval_sec=10, max_age_sec=20)
    mat._build_once()
    mat._built_at = time.time() - 21

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)

    assert exc.value.status["reason"] == "SNAPSHOT_STALE"


def test_failed_build_does_not_replace_last_valid_snapshot():
    engine = FakeEngine()
    answers = [snapshot(), RuntimeError("boom")]

    def builder(_):
        value = answers.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    mat = AISnapshotMaterializer(engine, builder,
                                 refresh_interval_sec=30, max_age_sec=90)
    mat._build_once()
    first_built = mat._built_at
    mat._build_once()

    assert mat.status()["last_error"].startswith("RuntimeError:boom")
    assert mat._built_at == first_built
    assert mat.cached_build_snapshot(engine)["trade"]["id"] == "trade-1"


def test_no_active_trade_is_fast_unavailable_not_a_fake_snapshot():
    engine = FakeEngine(None)
    mat = AISnapshotMaterializer(engine, lambda _: (_ for _ in ()).throw(AssertionError()),
                                 refresh_interval_sec=30, max_age_sec=90)
    mat._build_once()

    with pytest.raises(SnapshotNotReady) as exc:
        mat.cached_build_snapshot(engine)
    assert exc.value.status["reason"] == "NO_ACTIVE_TRADE"
    assert exc.value.status["ready"] is False
