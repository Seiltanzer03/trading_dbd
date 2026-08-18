from __future__ import annotations

import threading
import time

from seiltanzer.g1_short_horizon_evidence_materialization import REPORT_NAMES
from seiltanzer.g1_short_horizon_evidence_nonblocking import (
    NONBLOCKING_EVIDENCE_VERSION,
    install_g1_short_horizon_evidence_nonblocking,
)


class _Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self.revision = 1
        self.report_reads = 0
        self.status_reads = 0
        self.refreshes = 0

    def materialized_evidence_report(self, name: str):
        with self._lock:
            self.report_reads += 1
            return {
                "report_name": name,
                "revision": self.revision,
                "production_authority": False,
                "edge_claim_allowed": False,
                "materialization": {
                    "report_name": name,
                    "generated_ts": time.time(),
                    "request_time_full_history_scan": False,
                },
            }

    def evidence_materialization_status(self):
        with self._lock:
            self.status_reads += 1
            return {
                "contract_version": "g1s-evidence-materialization-v1",
                "reports": [
                    {"report_name": name, "generated_ts": time.time()}
                    for name in REPORT_NAMES
                ],
                "request_time_full_history_scan": False,
                "production_authority": False,
            }

    def materialize_evidence_reports(self, *args, **kwargs):
        with self._lock:
            self.refreshes += 1
            self.revision += 1
            return {"refreshed": True, "revision": self.revision}


def _hold_lock(runtime: _Runtime):
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with runtime._lock:
            acquired.set()
            release.wait(timeout=2.0)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    return release, thread


def test_http_evidence_reads_do_not_touch_shared_sqlite_lock():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)
    initial_report_reads = runtime.report_reads
    initial_status_reads = runtime.status_reads

    release, thread = _hold_lock(runtime)
    try:
        started = time.monotonic()
        report = runtime.materialized_evidence_report("calibration_oos")
        status = runtime.evidence_materialization_status()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        thread.join(timeout=1.0)

    assert elapsed < 0.10
    assert report["revision"] == 1
    assert report["request_time_sqlite_access"] is False
    assert report["materialization"]["request_time_sqlite_access"] is False
    assert status["request_time_sqlite_access"] is False
    assert status["nonblocking_evidence_version"] == NONBLOCKING_EVIDENCE_VERSION
    assert runtime.report_reads == initial_report_reads
    assert runtime.status_reads == initial_status_reads


def test_worker_refresh_replaces_process_local_evidence_snapshots():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)
    before = runtime.materialized_evidence_report("calibration_oos")
    assert before["revision"] == 1

    result = runtime.materialize_evidence_reports(force=True)
    after = runtime.materialized_evidence_report("calibration_oos")

    assert result["refreshed"] is True
    assert runtime.refreshes == 1
    assert after["revision"] == 2
    assert after["production_authority"] is False
    assert after["edge_claim_allowed"] is False


def test_missing_process_cache_fails_closed_without_database_fallback():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)
    runtime._g1s_evidence_report_cache = {}
    reads = runtime.report_reads

    body = runtime.materialized_evidence_report("calibration_oos")

    assert body["status"] == "BUILDING"
    assert body["request_time_sqlite_access"] is False
    assert body["production_authority"] is False
    assert runtime.report_reads == reads
