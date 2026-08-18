from __future__ import annotations

import threading
import time

from seiltanzer.g1_short_horizon_evidence_nonblocking import (
    NONBLOCKING_EVIDENCE_VERSION,
    install_g1_short_horizon_evidence_nonblocking,
)


class _Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self.historical_reads = 0
        self.historical_refreshes = 0
        self.revision = 1

    def materialized_evidence_report(self, name):
        return {"report_name": name, "production_authority": False}

    def evidence_materialization_status(self):
        return {"reports": [], "request_time_full_history_scan": False,
                "production_authority": False}

    def materialize_evidence_reports(self, *args, **kwargs):
        return {"refreshed": False}

    def historical_walkforward_status(self):
        with self._lock:
            self.historical_reads += 1
            return {
                "contract_version": "g1s-historical-wf-real-bars-v1",
                "evidence_label": "HISTORICAL_WALK_FORWARD",
                "live_validation_label": "LIVE_PROSPECTIVE_OOS",
                "state": "COMPLETE",
                "source_count": 10,
                "run_count": 10,
                "provisional_count": 2,
                "interval": "5m",
                "requested_period": "60d",
                "sources": [{"instrument": "NAS100", "bar_count": 1001}],
                "runs": [{"target": "direction_up", "horizon_minutes": 15,
                          "fold_count": 4, "verdict": "PROVISIONAL_LEARNED"}],
                "historical_option_features": "UNAVAILABLE_NOT_SYNTHESIZED",
                "synthetic_option_history": False,
                "expanding_chronological_walk_forward": True,
                "purge_embargo": True,
                "shuffle": False,
                "dependency_group_total_weight_one": True,
                "historical_fold_outcomes_count_as_live_oos": False,
                "provisional_artifact_starts_separate_live_oos": True,
                "request_time_network_fetch": False,
                "request_time_full_history_scan": False,
                "auto_promotion": False,
                "production_authority": False,
                "revision": self.revision,
            }

    def materialize_historical_walkforward(self, *args, **kwargs):
        with self._lock:
            self.historical_refreshes += 1
            self.revision += 1
            return {"refreshed": True, "revision": self.revision}


def _hold_lock(runtime):
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


def test_historical_status_http_read_is_process_local_under_shared_lock():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)
    reads = runtime.historical_reads
    release, thread = _hold_lock(runtime)
    try:
        started = time.monotonic()
        body = runtime.historical_walkforward_status()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        thread.join(timeout=1.0)

    assert elapsed < 0.10
    assert body["state"] == "COMPLETE"
    assert body["revision"] == 1
    assert body["request_time_sqlite_access"] is False
    assert body["request_time_full_history_scan"] is False
    assert body["request_time_network_fetch"] is False
    assert body["nonblocking_evidence_version"] == NONBLOCKING_EVIDENCE_VERSION
    assert runtime.historical_reads == reads


def test_historical_worker_refresh_updates_process_local_snapshot():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)
    assert runtime.historical_walkforward_status()["revision"] == 1

    result = runtime.materialize_historical_walkforward(force=True)
    body = runtime.historical_walkforward_status()

    assert result["refreshed"] is True
    assert runtime.historical_refreshes == 1
    assert body["revision"] == 2
    assert body["production_authority"] is False
    assert body["auto_promotion"] is False
