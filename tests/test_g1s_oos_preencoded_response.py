from __future__ import annotations

import json
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

    def materialized_evidence_report(self, name: str):
        with self._lock:
            self.report_reads += 1
            return {
                "report_name": name,
                "revision": self.revision,
                "items": [
                    {
                        "model_id": f"m-{i}",
                        "reliability": [
                            {"bin": j, "n": 50, "mean_probability": 0.5,
                             "event_rate": 0.5}
                            for j in range(10)
                        ],
                    }
                    for i in range(250)
                ] if name == "probability_oos" else [],
                "production_authority": False,
                "edge_claim_allowed": False,
                "materialization": {
                    "report_name": name,
                    "generated_ts": time.time() - 2.0,
                    "request_time_full_history_scan": False,
                },
            }

    def evidence_materialization_status(self):
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
        self.revision += 1
        return {"refreshed": True, "revision": self.revision}


def test_probability_oos_preencoded_json_preserves_contract_and_dynamic_age():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)

    before_reads = runtime.report_reads
    first = json.loads(runtime.materialized_evidence_json("probability_oos"))
    time.sleep(0.01)
    second = json.loads(runtime.materialized_evidence_json("probability_oos"))

    assert first["report_name"] == "probability_oos"
    assert len(first["items"]) == 250
    assert first["production_authority"] is False
    assert first["edge_claim_allowed"] is False
    assert first["request_time_sqlite_access"] is False
    assert first["materialization"]["request_time_sqlite_access"] is False
    assert first["materialization"]["nonblocking_evidence_version"] == NONBLOCKING_EVIDENCE_VERSION
    assert second["materialization"]["age_sec"] > first["materialization"]["age_sec"]
    assert runtime.report_reads == before_reads


def test_probability_oos_preencoded_read_does_not_wait_for_runtime_lock():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with runtime._lock:
            acquired.set()
            release.wait(timeout=2.0)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    try:
        started = time.monotonic()
        body = json.loads(runtime.materialized_evidence_json("probability_oos"))
        elapsed = time.monotonic() - started
    finally:
        release.set()
        thread.join(timeout=1.0)

    assert elapsed < 0.10
    assert body["revision"] == 1
    assert body["request_time_sqlite_access"] is False


def test_worker_refresh_replaces_preencoded_probability_snapshot():
    runtime = _Runtime()
    install_g1_short_horizon_evidence_nonblocking(runtime)
    before = json.loads(runtime.materialized_evidence_json("probability_oos"))

    result = runtime.materialize_evidence_reports(force=True)
    after = json.loads(runtime.materialized_evidence_json("probability_oos"))

    assert result["refreshed"] is True
    assert before["revision"] == 1
    assert after["revision"] == 2
    assert after["production_authority"] is False
    assert after["edge_claim_allowed"] is False
