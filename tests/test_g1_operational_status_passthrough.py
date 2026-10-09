from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import seiltanzer.g1_operational_status_passthrough as passthrough

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
    app = FastAPI()
    app.state.engine = SimpleNamespace(passive=passive)
    return app


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


def test_http_status_returns_exact_report_while_passive_writer_lock_is_held() -> None:
    passive = _Passive()
    app = FastAPI()
    app.state.engine = SimpleNamespace(passive=passive)
    install_operational_status_passthrough(app)
    app.add_api_route("/api/research/passive/status", passive.status)
    expected = passive.status()
    held = threading.Event()
    release = threading.Event()

    def writer():
        with passive._lock:
            held.set()
            release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(writer)
        assert held.wait(2)
        try:
            reading = pool.submit(lambda: TestClient(app).get("/api/research/passive/status"))
            response = reading.result(timeout=0.5)
        finally:
            release.set()
            writing.result(timeout=2)
        assert response.status_code == 200
        body = response.json()
        assert body["collector_health"]["eligible_captures_24h"] == expected["collector_health"]["eligible_captures_24h"]
        assert body["authority"] == "research_only"
        assert body["status_cache"]["request_path_sqlite"] is False
        assert passive.status_calls == 1


def test_failed_refresh_keeps_last_good_counts_and_exposes_unknown_health() -> None:
    passive = _Passive()
    passive._conn.executemany(
        "INSERT INTO passive_market_observations VALUES(?,1)",
        [(time.time() - 30,), (time.time() - 1800,)],
    )
    passive._conn.commit()
    app = _app(passive)
    install_operational_status_passthrough(app)
    before = passive.status()
    assert before["collector_health"]["eligible_captures_24h"] == 2
    cache = app.state.passive_status_cache
    passive._conn.close()
    assert cache.refresh_sync() is False
    after = passive.status()
    assert after["collector_health"]["eligible_captures_24h"] == before["collector_health"]["eligible_captures_24h"]
    assert after["status_cache"]["last_error"]
    assert after["status_cache"]["telemetry_status"] == "STALE"
    assert after["collector_health"]["last_known_operational_status"] == "RUNNING"
    assert after["operational_collector_status"] == "UNKNOWN"
    assert after["collector_status"] == "unknown"
    assert after["collector_health"]["production_authority"] is False


def test_refresh_updates_real_counts_without_mutating_previous_response() -> None:
    passive = _Passive()
    app = _app(passive)
    install_operational_status_passthrough(app)
    before = passive.status()
    passive._conn.execute(
        "INSERT INTO passive_market_observations VALUES(?,1)", (time.time() - 20,)
    )
    passive._conn.commit()
    assert app.state.passive_status_cache.refresh_sync() is True
    after = passive.status()
    assert before["collector_health"]["eligible_captures_24h"] == 0
    assert after["collector_health"]["eligible_captures_24h"] == 1
    assert after["status_cache"]["refresh_n"] == 2
    assert after["status_cache"]["telemetry_status"] == "FRESH"


def test_overdue_materialization_does_not_claim_healthy_collector(monkeypatch) -> None:
    passive = _Passive()
    app = _app(passive)
    install_operational_status_passthrough(app)
    before = passive.status()
    later = before["status_cache"]["refreshed_at"] + 121
    monkeypatch.setattr(passthrough.time, "time", lambda: later)
    after = passive.status()
    assert after["status_cache"]["telemetry_status"] == "STALE"
    assert after["status_cache"]["age_sec"] == 121
    assert after["operational_collector_status"] == "UNKNOWN"
    assert after["collector_health"]["last_known_operational_status"] == "RUNNING"
    assert before["operational_collector_status"] == "RUNNING"
