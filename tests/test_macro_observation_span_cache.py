from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace
from seiltanzer.macro_bls_historical_bootstrap import (
    observation_span,
    _OBSERVATION_SPAN_CACHE,
)


def test_observation_span_cached_and_bounded():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE g1s_observations (observation_id TEXT, captured_ts REAL)"
    )
    conn.execute(
        "INSERT INTO g1s_observations VALUES ('obs1', 1000.0), ('obs2', 2000.0)"
    )
    conn.commit()

    lock = threading.Lock()
    runtime = SimpleNamespace(_conn=conn, _lock=lock)
    _OBSERVATION_SPAN_CACHE.clear()

    span1 = observation_span(runtime)
    assert span1 == (1000.0, 2000.0)

    # Modify table to verify subsequent call reads from memory cache
    conn.execute("DELETE FROM g1s_observations")
    conn.commit()

    span2 = observation_span(runtime)
    assert span2 == (1000.0, 2000.0)

    # When lock is held by another thread, observation_span returns cached value without blocking
    barrier = threading.Barrier(2)
    def hold_lock():
        with lock:
            barrier.wait()
            barrier.wait()

    t = threading.Thread(target=hold_lock, daemon=True)
    t.start()
    barrier.wait()
    try:
        span3 = observation_span(runtime)
        assert span3 == (1000.0, 2000.0)
    finally:
        barrier.wait()
        t.join(timeout=2.0)
