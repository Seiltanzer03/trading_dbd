from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from seiltanzer.production_resource_guard import (
    RESOURCE_GUARD_VERSION,
    _wrap_heavy_refresh,
    install_production_resource_guard,
    resource_guard_status,
)


def test_heavy_refreshes_are_serialized() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def source() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1

    guarded = _wrap_heavy_refresh(source)

    def run() -> None:
        barrier.wait()
        guarded()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert peak == 1


def test_install_is_idempotent_and_passive_feed_cache_is_bounded_to_one() -> None:
    from seiltanzer.data.feeds import MarketData
    from seiltanzer.passive_learning import PassiveLearningEngine

    install_production_resource_guard()
    first = PassiveLearningEngine._feed
    install_production_resource_guard()
    assert PassiveLearningEngine._feed is first
    assert MarketData._production_resource_guard_version == RESOURCE_GUARD_VERSION

    passive = object.__new__(PassiveLearningEngine)
    passive.settings = SimpleNamespace(demo=False)
    passive.cache = object()
    passive._feeds = {"OLD": object()}

    feed = passive._feed("NAS100")
    assert isinstance(feed, MarketData)
    assert list(passive._feeds) == ["NAS100"]
    assert passive._feed("NAS100") is feed


def test_resource_guard_status_is_observability_only() -> None:
    status = resource_guard_status()
    assert status["contract_version"] == RESOURCE_GUARD_VERSION
    assert status["heavy_feed_parallelism"] == 1
    assert status["passive_feed_cache_max"] == 1
    assert status["mathematics_changed"] is False
    if status["rss_bytes"] is not None:
        assert status["rss_bytes"] > 0
