from __future__ import annotations

import sqlite3
import threading

from seiltanzer.g1_short_horizon_evidence_materialization import (
    _ensure_table,
    materialization_status,
    materialize_evidence_reports,
    materialized_report,
)


class _Runtime:
    def __init__(self, path):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for table in (
            "g1s_resolutions", "g1s_shadow_predictions", "g1s_return_predictions",
            "g1s_calibrated_predictions", "g1s_trade_links", "g1m_local_outcomes",
            "g1_q_capture_attempts", "passive_market_observations",
        ):
            self._conn.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY)")
        self.calls = []
        _ensure_table(self)

    def _report(self, name):
        self.calls.append(name)
        return {"kind": name, "production_authority": False}

    def prospective_oos(self): return self._report("probability")
    def continuous_oos(self): return self._report("continuous")
    def calibration_oos(self): return self._report("calibration")
    def ablation(self): return self._report("ablation")
    def trade_relevance(self): return self._report("trade")
    def final_report(self): return self._report("final")


def test_missing_materialization_returns_building_without_live_scan(tmp_path):
    runtime = _Runtime(tmp_path/"cache.sqlite3")
    body = materialized_report(runtime, "probability_oos")
    assert body["status"] == "BUILDING"
    assert body["request_time_full_history_scan"] is False
    assert runtime.calls == []


def test_worker_refresh_writes_all_frozen_reports_and_http_read_does_not_recompute(tmp_path):
    runtime = _Runtime(tmp_path/"worker.sqlite3")
    result = materialize_evidence_reports(runtime, force=True)
    assert result["refreshed"] is True
    assert runtime.calls == ["probability", "continuous", "calibration", "ablation", "trade", "final"]
    runtime.calls.clear()
    body = materialized_report(runtime, "probability_oos")
    assert body["kind"] == "probability"
    assert body["materialization"]["request_time_full_history_scan"] is False
    assert runtime.calls == []


def test_unchanged_source_signature_skips_recomputation(tmp_path):
    runtime = _Runtime(tmp_path/"unchanged.sqlite3")
    materialize_evidence_reports(runtime, force=True)
    runtime.calls.clear()
    result = materialize_evidence_reports(runtime, force=False)
    assert result["refreshed"] is False
    assert result["reason"] == "SOURCE_UNCHANGED"
    assert runtime.calls == []
    status = materialization_status(runtime)
    assert len(status["reports"]) == 6
    assert status["request_time_full_history_scan"] is False
