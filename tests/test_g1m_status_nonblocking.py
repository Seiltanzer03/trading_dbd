from __future__ import annotations

import threading
import time

from seiltanzer.g1_management_status_nonblocking import (
    NONBLOCKING_STATUS_VERSION,
    install_g1_management_status_nonblocking,
)


class _Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self.value = 1
        self.step_entered = threading.Event()
        self.step_release = threading.Event()

    def status(self):
        # Deliberately model the production failure mode: the durable status
        # reader needs the shared research lock.
        with self._lock:
            return {
                "g1_stage": "G.1-M",
                "g1m_contract_version": "g1m-management-edge-v1",
                "evidence_status": "COLLECTING",
                "observations": self.value,
                "authority": {
                    "research_only": True,
                    "production_authority": False,
                    "auto_execution_allowed": False,
                    "policy_promotion_allowed": False,
                    "oos_validated": False,
                    "edge_claim_allowed": False,
                },
            }

    def step(self):
        with self._lock:
            self.step_entered.set()
            self.step_release.wait(timeout=3.0)
            self.value += 1
            return {"captured": 1, "resolved": 0}


def test_status_is_process_local_even_when_research_lock_is_held():
    runtime = _Runtime()
    install_g1_management_status_nonblocking(runtime)
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
    assert body["observations"] == 1
    assert body["request_time_sqlite_access"] is False
    assert body["status_materialization"]["request_time_sqlite_access"] is False


def test_background_step_marks_snapshot_building_then_refreshes_atomically():
    runtime = _Runtime()
    install_g1_management_status_nonblocking(runtime)

    thread = threading.Thread(target=runtime.step, daemon=True)
    thread.start()
    assert runtime.step_entered.wait(timeout=1.0)
    try:
        started = time.perf_counter()
        dirty = runtime.status()
        elapsed = time.perf_counter() - started
        assert elapsed < 0.10
        assert dirty["observations"] == 1
        assert dirty["status_materialization"]["cache_dirty"] is True
        assert dirty["status_materialization"]["presentation_state"] == "BUILDING"
    finally:
        runtime.step_release.set()
        thread.join(timeout=1.0)

    current = runtime.status()
    assert current["observations"] == 2
    assert current["status_materialization"]["cache_dirty"] is False
    assert current["request_time_sqlite_access"] is False


def test_missing_cache_fails_closed_without_lock_fallback():
    runtime = _Runtime()
    install_g1_management_status_nonblocking(runtime)
    runtime._g1m_status_snapshot_json = ""

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
    assert body["evidence_status"] == "UNAVAILABLE"
    assert body["reason"] == "NONBLOCKING_STATUS_CACHE_MISSING"
    assert body["request_time_sqlite_access"] is False
    assert body["authority"]["production_authority"] is False
