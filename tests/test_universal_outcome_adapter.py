from __future__ import annotations

import sqlite3
import threading

from seiltanzer.edge_discovery.universal_outcome_adapter import (
    ProspectiveUniversalOutcomeAdapter,
    resolve_historical_universal_outcome,
)


def test_historical_adapter_uses_immutable_ohlc_and_t0_rv():
    bars = [
        {"bar_end_ts": 1000.0, "close": 100.0, "high": 100.2, "low": 99.8},
        {"bar_end_ts": 1300.0, "close": 101.0, "high": 101.2, "low": 99.9},
        {"bar_end_ts": 1600.0, "close": 102.3, "high": 102.5, "low": 100.8},
    ]
    source = {"bars": bars}
    row = {
        "captured_ts": 1000.0,
        "target_ts": 1600.0,
        "horizon_minutes": 60,
        "features": {"realized_vol_60m": 0.02},
    }
    outcome = resolve_historical_universal_outcome(source, row)
    assert outcome["available"] is True
    assert outcome["evidence_source"] == "IMMUTABLE_HISTORICAL_WF_OHLC"
    assert outcome["barriers"]["up_1s_down_1s"]["label"] == "UP_FIRST"
    assert outcome["normalization_uses_future_data"] is False


class _Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._conn:
            self._conn.execute("""
                CREATE TABLE g1s_observations(
                    observation_id TEXT PRIMARY KEY, market_price REAL NOT NULL
                )""")
            self._conn.execute("""
                CREATE TABLE g1s_resolutions(
                    observation_id TEXT PRIMARY KEY, resolved_ts REAL,
                    path_quality_status TEXT
                )""")
            self._conn.execute("""
                CREATE TABLE passive_market_bars(
                    instrument TEXT, bar_start_ts REAL, bar_end_ts REAL,
                    high REAL, low REAL, close REAL, created_ts REAL
                )""")
            self._conn.execute(
                "INSERT INTO g1s_observations VALUES('obs-1',100.0)")
            self._conn.execute(
                "INSERT INTO g1s_resolutions VALUES('obs-1',1300.0,'complete')")
            self._conn.executemany(
                "INSERT INTO passive_market_bars VALUES(?,?,?,?,?,?,?)",
                [
                    ("NAS100", 1000.0, 1060.0, 101.0, 99.8, 100.8, 1050.0),
                    ("NAS100", 1060.0, 1120.0, 102.5, 100.6, 102.0, 1110.0),
                    # Recorded after the frozen resolution and must be excluded.
                    ("NAS100", 1120.0, 1180.0, 105.0, 101.5, 104.0, 1400.0),
                ],
            )


def test_prospective_adapter_uses_only_bars_recorded_by_resolution_time():
    runtime = _Runtime()
    adapter = ProspectiveUniversalOutcomeAdapter(runtime)
    rows = [{
        "observation_id": "obs-1",
        "instrument": "NAS100",
        "captured_ts": 1000.0,
        "target_ts": 1180.0,
        "resolved_ts": 1300.0,
        "horizon_minutes": 60,
        "outcome_available": True,
        "ede_features": {"vol.rv_60m": 0.02},
    }]
    result = adapter.attach(rows)[0]["universal_outcome"]
    assert result["available"] is True
    assert result["path_count"] == 2
    assert result["bars_created_no_later_than_resolution"] is True
    assert result["source_path_quality_status"] == "complete"
    # The excluded late-created 105 high cannot affect this result.
    assert result["mfe_log_return"] < 0.03


def test_unresolved_prospective_row_does_not_get_future_outcome():
    runtime = _Runtime()
    adapter = ProspectiveUniversalOutcomeAdapter(runtime)
    row = {
        "observation_id": "obs-1",
        "instrument": "NAS100",
        "captured_ts": 1000.0,
        "target_ts": 1180.0,
        "horizon_minutes": 60,
        "outcome_available": False,
        "ede_features": {"vol.rv_60m": 0.02},
    }
    attached = adapter.attach([row])[0]
    assert attached["universal_outcome"] is None
    assert attached["universal_outcome_reason"] == "OUTCOME_NOT_RESOLVED"
