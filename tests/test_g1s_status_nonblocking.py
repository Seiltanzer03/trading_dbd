from __future__ import annotations

import threading
import time

import pytest

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime
from seiltanzer.g1_short_horizon_status_nonblocking import (
    NONBLOCKING_STATUS_VERSION,
    install_g1_short_horizon_status_nonblocking,
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


def test_status_is_process_local_even_when_research_lock_is_held(runtime):
    install_g1_short_horizon_status_nonblocking(runtime)
    first = runtime.status()
    assert first["request_time_sqlite_access"] is False
    assert first["status_snapshot_cached"] is True
    assert first["status_materialization"]["nonblocking_status_version"] == NONBLOCKING_STATUS_VERSION

    acquired = threading.Event()
    release = threading.Event()

    def hold_runtime_lock():
        with runtime._lock:
            acquired.set()
            release.wait(timeout=3.0)

    thread = threading.Thread(target=hold_runtime_lock, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    try:
        started = time.perf_counter()
        body = runtime.status()
        elapsed = time.perf_counter() - started
    finally:
        release.set()
        thread.join(timeout=1.0)

    assert elapsed < 0.10
    assert body["request_time_sqlite_access"] is False
    assert body["status_materialization"]["request_time_sqlite_access"] is False


def test_dirty_cache_is_reported_building_without_fake_zero_lag(runtime):
    # Capture a deterministic mutation method before installing the wrapper so
    # the test proves mutation -> dirty propagation without needing market I/O.
    runtime.materialize_new = lambda *args, **kwargs: 1
    install_g1_short_horizon_status_nonblocking(runtime)

    assert runtime.materialize_new(limit=1) == 1
    dirty = runtime.status()
    assert dirty["status_materialization"]["cache_dirty"] is True
    assert dirty["status_materialization"]["presentation_state"] == "BUILDING"
    assert dirty["status_materialization"]["lag_rows"] is None

    # The existing durable incremental refresher remains authoritative.  Once it
    # completes, the process-local snapshot is atomically replaced and current.
    runtime.refresh_materialized_status(limit=10)
    current = runtime.status()
    assert current["status_materialization"]["cache_dirty"] is False
    assert current["request_time_sqlite_access"] is False


def test_missing_cache_fails_closed_without_sqlite_fallback(runtime):
    install_g1_short_horizon_status_nonblocking(runtime)
    runtime._g1s_status_snapshot_json = ""

    acquired = threading.Event()
    release = threading.Event()

    def hold_runtime_lock():
        with runtime._lock:
            acquired.set()
            release.wait(timeout=3.0)

    thread = threading.Thread(target=hold_runtime_lock, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    try:
        started = time.perf_counter()
        body = runtime.status()
        elapsed = time.perf_counter() - started
    finally:
        release.set()
        thread.join(timeout=1.0)

    assert elapsed < 0.10
    assert body["status"] == "UNAVAILABLE"
    assert body["reason"] == "NONBLOCKING_STATUS_CACHE_MISSING"
    assert body["request_time_sqlite_access"] is False
    assert body["authority"]["production_authority"] is False
