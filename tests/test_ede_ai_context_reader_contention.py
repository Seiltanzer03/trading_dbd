from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from seiltanzer.edge_discovery import ai_context


@pytest.fixture
def engine(tmp_path):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE g1s_observations("
        "instrument TEXT,captured_ts REAL,frozen_features_json TEXT)")
    for instrument, captured in [("NAS100", 80), ("NAS100", 90),
                                 ("NAS100", 110), ("EURUSD", 95)]:
        frozen = {"g1s_evidence_v3": {
            "captured_ts": captured,
            "price_volatility": {"available": True, "ret_5m": 0.02},
        }}
        connection.execute("INSERT INTO g1s_observations VALUES(?,?,?)",
                           (instrument, captured, json.dumps(frozen)))
    connection.commit()
    result = SimpleNamespace(
        settings=SimpleNamespace(data_dir=str(tmp_path), ede_context_max_age_sec=20),
        short_horizon=SimpleNamespace(_conn=connection, _lock=threading.RLock()),
    )
    try:
        yield result
    finally:
        connection.close()


def _snapshot(cutoff=100, instrument="NAS100"):
    return {"captured_ts": cutoff,
            "strategy": {"instrument": instrument, "direction": "long"},
            "policy_manager": {"management_decision": {"policy": "HOLD"}}}


def _during_research_lock(engine, call):
    held, release, done = threading.Event(), threading.Event(), threading.Event()
    outcome = {}

    def owner():
        with engine.short_horizon._lock:
            held.set()
            release.wait(5)

    def reader():
        try:
            outcome["result"] = call()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    owning_thread = threading.Thread(target=owner)
    reading_thread = threading.Thread(target=reader)
    owning_thread.start()
    assert held.wait(1)
    try:
        reading_thread.start()
        returned_while_busy = done.wait(1)
    finally:
        release.set()
        owning_thread.join(2)
        reading_thread.join(2)
    assert not owning_thread.is_alive() and not reading_thread.is_alive()
    assert returned_while_busy, "Optional EDE reader blocked behind research work"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def test_busy_reader_without_cache_is_explicit_and_never_grants_authority(engine):
    snapshot = _snapshot()
    result = _during_research_lock(
        engine, lambda: ai_context.build_ai_ede_context(engine, snapshot))
    assert result["current_context_read"] == {
        "available": False, "deferred": True, "cache_used": False,
        "reason": "RESEARCH_READER_BUSY",
    }
    assert result["current_observation_t0"] is None
    assert result["confidence_modifier"] == 0.0
    assert result["authority"]["production_directional_authority"] is False
    assert any("RESEARCH_READER_BUSY" in line for line in result["context_lines_ru"])
    assert snapshot["policy_manager"]["management_decision"] == {"policy": "HOLD"}


def test_busy_reader_uses_original_causal_fresh_last_good_without_aliasing(engine):
    initial = ai_context._latest_frozen_context(engine, _snapshot())
    assert initial["observation_t0"] == 90
    initial["_raw_frozen"]["g1s_evidence_v3"]["price_volatility"]["ret_5m"] = 999
    result = _during_research_lock(
        engine, lambda: ai_context._latest_frozen_context(engine, _snapshot(105)))
    assert result["observation_t0"] == 90
    assert result["_raw_frozen"]["g1s_evidence_v3"]["price_volatility"]["ret_5m"] == 0.02
    assert result["_context_read"] == {
        "available": True, "deferred": True, "cache_used": True,
        "reason": "RESEARCH_READER_BUSY",
    }
    result["_raw_frozen"]["g1s_evidence_v3"]["price_volatility"]["ret_5m"] = 888
    again = _during_research_lock(
        engine, lambda: ai_context._latest_frozen_context(engine, _snapshot(105)))
    assert again["_raw_frozen"]["g1s_evidence_v3"]["price_volatility"]["ret_5m"] == 0.02


@pytest.mark.parametrize("cutoff,instrument,max_age", [
    (80, "NAS100", 20),  # cached90 would be lookahead
    (111, "NAS100", 20),  # original receipt is too old, never redated
    (100, "EURUSD", 20),
    (100, "NAS100", 0),
    (100, "NAS100", float("inf")),
])
def test_busy_reader_rejects_future_stale_wrong_instrument_or_invalid_age(
    engine, cutoff, instrument, max_age,
):
    ai_context._latest_frozen_context(engine, _snapshot())
    engine.settings.ede_context_max_age_sec = max_age
    result = _during_research_lock(
        engine, lambda: ai_context._latest_frozen_context(
            engine, _snapshot(cutoff, instrument)))
    assert "observation_t0" not in result
    assert result["_context_read"]["available"] is False
    assert result["_context_read"]["cache_used"] is False


def test_free_reader_still_selects_latest_not_after_cutoff_and_releases_lock(engine):
    result = ai_context._latest_frozen_context(engine, _snapshot())
    assert result["observation_t0"] == 90
    acquired = []

    def another_reader():
        success = engine.short_horizon._lock.acquire(blocking=False)
        acquired.append(success)
        if success:
            engine.short_horizon._lock.release()

    thread = threading.Thread(target=another_reader)
    thread.start(); thread.join(1)
    assert acquired == [True]


def test_cache_age_boundary_preserves_public_observation_time_and_busy_reason(engine):
    ai_context._latest_frozen_context(engine, _snapshot())
    result = _during_research_lock(
        engine, lambda: ai_context.build_ai_ede_context(engine, _snapshot(110)))
    assert result["current_observation_t0"] == 90
    assert result["configured_max_age_sec"] == 20
    assert result["current_context_read"]["cache_used"] is True
    assert any("RESEARCH_READER_BUSY" in line for line in result["context_lines_ru"])
    assert result["authority"]["may_trigger_exit_or_close"] is False


def test_free_reader_does_not_require_runtime_support_for_cache_attributes(engine):
    class SlottedRuntime:
        __slots__ = ("_conn", "_lock")

    runtime = SlottedRuntime()
    runtime._conn = engine.short_horizon._conn
    runtime._lock = engine.short_horizon._lock
    engine.short_horizon = runtime
    assert ai_context._latest_frozen_context(engine, _snapshot())["observation_t0"] == 90


def test_query_failure_releases_lock_and_does_not_fabricate_context(engine):
    engine.short_horizon._conn.execute("DROP TABLE g1s_observations")
    assert ai_context._latest_frozen_context(engine, _snapshot()) == {}
    acquired = []

    def another_reader():
        success = engine.short_horizon._lock.acquire(blocking=False)
        acquired.append(success)
        if success:
            engine.short_horizon._lock.release()

    thread = threading.Thread(target=another_reader)
    thread.start(); thread.join(1)
    assert acquired == [True]
