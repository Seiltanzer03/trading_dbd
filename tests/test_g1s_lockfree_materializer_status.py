from __future__ import annotations

import threading
import time
import pytest

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.g1_short_horizon_runtime import (
    G1S_MATERIALIZER_VERSION,
    ShortHorizonRuntime,
)
from seiltanzer.passive_learning import PassiveLearningEngine


class _Engine:
    def __init__(self, passive):
        self.passive = passive


@pytest.fixture
def runtime(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    passive = PassiveLearningEngine(
        str(tmp_path / "trades.db"),
        Settings(demo=False, data_dir=str(tmp_path)),
        cache,
    )
    rt = ShortHorizonRuntime(_Engine(passive))
    yield rt
    passive.close()
    cache.close()


def test_materializer_status_is_lock_free_even_when_passive_lock_is_held(runtime):
    # Populate a fake materializer state row
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR REPLACE INTO g1s_materialization_state("
            "materializer, contract_version, source_watermark, last_started_ts, "
            "last_success_ts, last_duration_ms, processed_n, last_error"
            ") VALUES ('fixed_horizon_t0', 'v1', 42, 100.0, 101.0, 1000.0, 10, NULL)"
        )
        runtime._refresh_materializer_cache_under_lock()

    # Now hold the runtime lock in another thread, simulating passive collector contention
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        with runtime._lock:
            lock_acquired.set()
            release_lock.wait(timeout=5.0)

    thread = threading.Thread(target=hold_lock, daemon=True)
    thread.start()
    assert lock_acquired.wait(timeout=2.0)

    # Calling materializer_status must NOT block even while self._lock is held
    t0 = time.monotonic()
    status = runtime.materializer_status()
    elapsed = time.monotonic() - t0

    release_lock.set()
    thread.join(timeout=2.0)

    assert elapsed < 0.1, f"materializer_status blocked for {elapsed:.3f}s"
    assert status["contract_version"] == G1S_MATERIALIZER_VERSION
    assert len(status["items"]) == 1
    assert status["items"][0]["materializer"] == "fixed_horizon_t0"
    assert status["items"][0]["source_watermark"] == 42
