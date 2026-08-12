from __future__ import annotations

import json
import sqlite3
import threading
import time
from types import SimpleNamespace

from seiltanzer.g1_operational_integrity import HEALTH_STATE_KEY
from seiltanzer.g1_operational_status_passthrough import (
    STATUS_PASSTHROUGH_VERSION,
    install_operational_status_passthrough,
)


class _Passive:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.RLock()
        self.settings = SimpleNamespace(demo=True)
        self.budget = {"base_observation_cadence_sec": 60.0}
        self.status_calls = 0
        self._conn.executescript(
            """
            CREATE TABLE passive_collector_state(
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_ts REAL NOT NULL
            );
            CREATE TABLE passive_market_observations(
                captured_ts REAL NOT NULL,
                evidence_eligible INTEGER NOT NULL
            );
            """
        )

    def status(self) -> dict:
        self.status_calls += 1
        return {
            "materialized_status": True,
            "materialization_contract_version": "bounded-materialized-status-v1",
            "budget": dict(self.budget),
            "authority": "research_only",
        }


def _app(passive: _Passive):
    return SimpleNamespace(
        state=SimpleNamespace(engine=SimpleNamespace(passive=passive))
    )


def test_bounded_status_exposes_persisted_health_and_recent_eligible_stream() -> None:
    passive = _Passive()
    now = time.time()
    health = {
        "version": "g1-operational-integrity-p0-v1",
        "last_step_ts": now - 5,
        "last_successful_eligible_capture_ts": now - 120,
        "last_error_ts": now - 20,
        "last_error": "v3_wavelet: TypeError: forced",
        "errors_by_feature_family": {
            "v3_wavelet": {"count": 1, "last_error": "TypeError: forced"}
        },
        "consecutive_failed_capture_cycles": 2,
    }
    passive._conn.execute(
        "INSERT INTO passive_collector_state(key,value_json,updated_ts) VALUES(?,?,?)",
        (HEALTH_STATE_KEY, json.dumps(health), now),
    )
    passive._conn.executemany(
        "INSERT INTO passive_market_observations(captured_ts,evidence_eligible) VALUES(?,?)",
        [
            (now - 30, 1),
            (now - 1800, 1),
            (now - 10, 0),
            (now - 90000, 1),
        ],
    )
    passive._conn.commit()

    app = _app(passive)
    install_operational_status_passthrough(app)
    body = passive.status()

    assert passive.status_calls == 1
    assert body["materialized_status"] is True
    assert body["collector_health_bounded"] is True
    assert body["operational_collector_status"] == "DEGRADED"
    out = body["collector_health"]
    assert out["status_passthrough_version"] == STATUS_PASSTHROUGH_VERSION
    assert out["eligible_captures_1h"] == 2
    assert out["eligible_captures_24h"] == 2
    assert 0 <= out["eligible_capture_age_sec"] < 120
    assert out["last_error"].startswith("v3_wavelet")
    assert out["errors_by_feature_family"]["v3_wavelet"]["count"] == 1
    assert out["bounded_recent_query_hours"] == 24
    assert out["request_time_full_history_scan"] is False
    assert out["production_authority"] is False


def test_install_is_idempotent_and_does_not_stack_status_wrappers() -> None:
    passive = _Passive()
    app = _app(passive)
    install_operational_status_passthrough(app)
    first_status = passive.status
    install_operational_status_passthrough(app)
    second_status = passive.status

    assert first_status == second_status
    body = passive.status()
    assert passive.status_calls == 1
    assert body["collector_health_bounded"] is True
