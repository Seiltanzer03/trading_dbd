from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import seiltanzer.g1_research_worker as research_worker
from seiltanzer.g1_short_horizon_runtime import HORIZONS
from seiltanzer.g1_trade_link_catchup import materialize_trade_links_bounded
from seiltanzer.research_acceptance_gate import (
    gate_owner_matches,
    release_acceptance_gate,
    worker_acceptance_gate_state,
    write_acceptance_gate,
)


def _load_script(name: str):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, root / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gate_allows_required_cycle_then_pauses_new_cycles(tmp_path):
    gate = tmp_path / "acceptance.json"
    write_acceptance_gate("smoke-17", "abc123", ttl_seconds=100, path=gate, now=100.0)
    pending = worker_acceptance_gate_state(
        process_started_ts=90.0, last_finished_ts=None, path=gate, now=101.0
    )
    assert pending["active"] is True
    assert pending["pause"] is False
    paused = worker_acceptance_gate_state(
        process_started_ts=90.0, last_finished_ts=105.0, path=gate, now=106.0
    )
    assert paused["active"] is True
    assert paused["pause"] is True
    assert paused["reason"] == "PRODUCTION_ACCEPTANCE_ACTIVE"


def test_gate_from_previous_service_generation_is_ignored(tmp_path):
    gate = tmp_path / "acceptance.json"
    write_acceptance_gate("smoke-old", "oldsha", ttl_seconds=100, path=gate, now=100.0)
    state = worker_acceptance_gate_state(
        process_started_ts=110.0, last_finished_ts=None, path=gate, now=111.0
    )
    assert state["active"] is False
    assert state["reason"] == "STALE_SERVICE_GENERATION_GATE"


def test_expired_gate_cannot_pause_worker(tmp_path):
    gate = tmp_path / "acceptance.json"
    write_acceptance_gate("smoke-17", "abc123", ttl_seconds=5, path=gate, now=100.0)
    state = worker_acceptance_gate_state(
        process_started_ts=90.0, last_finished_ts=101.0, path=gate, now=106.0
    )
    assert state["active"] is False
    assert state["reason"] == "ACCEPTANCE_GATE_EXPIRED"


def test_new_exact_run_supersedes_old_owner_safely(tmp_path):
    gate = tmp_path / "acceptance.json"
    write_acceptance_gate("smoke-1", "sha-1", ttl_seconds=100, path=gate, now=100.0)
    write_acceptance_gate("smoke-2", "sha-2", ttl_seconds=100, path=gate, now=101.0)
    assert release_acceptance_gate("smoke-1", "sha-1", path=gate) is False
    assert gate_owner_matches("smoke-2", "sha-2", path=gate, now=102.0) is True
    assert release_acceptance_gate("smoke-2", "sha-2", path=gate) is True


def test_worker_runs_bounded_core_once_and_skips_all_maintenance_under_gate(monkeypatch):
    @asynccontextmanager
    async def original_lifespan(_app):
        yield

    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(short_horizon=object(), management_local=object())
        ),
        router=SimpleNamespace(lifespan_context=original_lifespan),
    )
    calls = []

    def fake_gate(*, process_started_ts, last_finished_ts):
        del process_started_ts
        return {
            "active": True,
            "pause": last_finished_ts is not None,
            "reason": (
                "PRODUCTION_ACCEPTANCE_ACTIVE"
                if last_finished_ts is not None
                else "REQUIRED_WORKER_CYCLE_PENDING"
            ),
            "acceptance_run_id": "991",
            "expected_sha": "sha",
            "expires_at": 99999999999.0,
        }

    monkeypatch.setattr(research_worker, "RESEARCH_STARTUP_GRACE_SEC", 0.001)
    monkeypatch.setattr(research_worker, "RESEARCH_INTERVAL_SEC", 0.001)
    monkeypatch.setattr(research_worker, "worker_acceptance_gate_state", fake_gate)
    monkeypatch.setattr(
        research_worker,
        "_run_g1s_core",
        lambda _runtime: calls.append("g1s-core") or {"batch_limit": 500},
    )
    monkeypatch.setattr(
        research_worker,
        "_run_g1m_local_core",
        lambda _runtime: calls.append("g1m-core") or {"batch_limit": 100},
    )
    monkeypatch.setattr(
        research_worker,
        "_run_maintenance_phase",
        lambda *_args: calls.append("MAINTENANCE-MUST-NOT-RUN") or {},
    )
    research_worker.install_research_worker(app)

    async def exercise():
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.02)

    asyncio.run(exercise())
    assert calls == ["g1s-core", "g1m-core"]
    worker = app.state.g1_research_worker
    assert worker["acceptance_pause_active"] is True
    assert worker["maintenance_running"] is False
    assert worker["last_result"]["g1s"]["batch_limit"] == 500
    assert worker["last_result"]["g1m_local"]["batch_limit"] == 100


def test_worker_without_gate_eventually_schedules_optional_maintenance(monkeypatch):
    @asynccontextmanager
    async def original_lifespan(_app):
        yield

    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(short_horizon=object(), management_local=object())
        ),
        router=SimpleNamespace(lifespan_context=original_lifespan),
    )
    calls = []
    no_gate = {
        "active": False, "pause": False, "reason": "NO_ACTIVE_ACCEPTANCE_GATE",
        "acceptance_run_id": None, "expected_sha": None, "expires_at": None,
    }
    monkeypatch.setattr(research_worker, "RESEARCH_STARTUP_GRACE_SEC", 0.001)
    monkeypatch.setattr(research_worker, "RESEARCH_INTERVAL_SEC", 0.001)
    monkeypatch.setattr(research_worker, "worker_acceptance_gate_state", lambda **_kw: no_gate)
    monkeypatch.setattr(research_worker, "_run_g1s_core", lambda _r: {"batch_limit": 500})
    monkeypatch.setattr(research_worker, "_run_g1m_local_core", lambda _r: {"batch_limit": 100})
    monkeypatch.setattr(
        research_worker,
        "_run_maintenance_phase",
        lambda _r, _e, phase: calls.append(phase) or {"phase": phase},
    )
    research_worker.install_research_worker(app)

    async def exercise():
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.02)

    asyncio.run(exercise())
    assert calls
    assert calls[0] == research_worker.MAINTENANCE_PHASES[0]


def _trade_link_runtime():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE trades(
            id INTEGER PRIMARY KEY, opened_at REAL, instrument TEXT, direction TEXT,
            setup TEXT, result_r REAL, status TEXT
        );
        CREATE TABLE g1s_observations(
            observation_id TEXT PRIMARY KEY, captured_ts REAL, instrument TEXT,
            horizon_minutes INTEGER, measurement_eligible INTEGER
        );
        CREATE TABLE g1s_trade_links(
            link_id TEXT PRIMARY KEY, trade_id INTEGER, observation_id TEXT,
            horizon_minutes INTEGER, forecast_age_sec REAL, link_json TEXT,
            link_sha256 TEXT, created_ts REAL
        );
        """
    )
    for trade_id, opened_at in ((1, 1000.0), (2, 1100.0)):
        conn.execute(
            "INSERT INTO trades VALUES(?,?,?,?,?,?,?)",
            (trade_id, opened_at, "NAS100", "LONG", "test", None, "OPEN"),
        )
        for horizon in HORIZONS:
            conn.execute(
                "INSERT INTO g1s_observations VALUES(?,?,?,?,1)",
                (f"obs-{trade_id}-{horizon}", opened_at - 1.0, "NAS100", horizon),
            )
    conn.commit()
    return SimpleNamespace(_conn=conn, _lock=threading.RLock())


def test_trade_link_catchup_is_bounded_and_progresses_to_next_missing_trade():
    runtime = _trade_link_runtime()
    first = materialize_trade_links_bounded(runtime, limit=1)
    assert first["trades_scanned"] == 1
    assert first["links_created"] == len(HORIZONS)
    linked_ids = [r[0] for r in runtime._conn.execute(
        "SELECT DISTINCT trade_id FROM g1s_trade_links ORDER BY trade_id"
    )]
    assert linked_ids == [1]

    second = materialize_trade_links_bounded(runtime, limit=1)
    assert second["trades_scanned"] == 1
    assert second["links_created"] == len(HORIZONS)
    linked_ids = [r[0] for r in runtime._conn.execute(
        "SELECT DISTINCT trade_id FROM g1s_trade_links ORDER BY trade_id"
    )]
    assert linked_ids == [1, 2]

    done = materialize_trade_links_bounded(runtime, limit=1)
    assert done["trades_scanned"] == 0
    assert done["links_created"] == 0


def test_exact_run_markers_bind_sha_and_acceptance_run_id(tmp_path):
    orchestration = _load_script("production_research_acceptance")
    marker = orchestration.write_marker(
        "post-research", "991", "sha-good", marker_dir=tmp_path
    )
    assert orchestration.wait_marker(
        "post-research", "991", "sha-good", timeout_seconds=0,
        poll_seconds=0.1, marker_dir=tmp_path,
    ) == marker
    try:
        orchestration.wait_marker(
            "post-research", "991", "sha-wrong", timeout_seconds=0,
            poll_seconds=0.1, marker_dir=tmp_path,
        )
    except RuntimeError as exc:
        assert "POST_RESEARCH_MARKER_MISMATCH" in str(exc)
    else:
        raise AssertionError("wrong-SHA marker unexpectedly passed")

    payload = marker.read_text(encoding="utf-8")
    assert '"acceptance_run_id":"991"' in payload
    assert '"expected_sha":"sha-good"' in payload

    assert orchestration.write_marker(
        "post-research", "991", "sha-good", marker_dir=tmp_path
    ) == marker
    try:
        orchestration.write_marker(
            "post-research", "991", "sha-other", marker_dir=tmp_path
        )
    except RuntimeError as exc:
        assert "DIFFERENT_OWNER" in str(exc)
    else:
        raise AssertionError("immutable marker was overwritten")


def test_post_research_waits_if_optional_maintenance_was_already_running():
    check = _load_script("production_post_research_check")
    result = {"g1s": {"batch_limit": 500}, "g1m_local": {"batch_limit": 100}}
    assert check._cycle_finished(
        {"last_started_ts": 10.0, "last_finished_ts": 12.0,
         "maintenance_running": False, "acceptance_pause_active": True,
         "current_phase": "acceptance_pause"},
        result,
    ) is True
    assert check._cycle_finished(
        {"last_started_ts": 10.0, "last_finished_ts": 12.0,
         "maintenance_running": True, "acceptance_pause_active": False,
         "current_phase": "maintenance:status_refresh"},
        result,
    ) is False
    assert check._cycle_finished(
        {"last_started_ts": 20.0, "last_finished_ts": 12.0,
         "maintenance_running": False, "acceptance_pause_active": True,
         "current_phase": "acceptance_pause"},
        result,
    ) is False


def test_post_research_requires_observed_acceptance_pause():
    check = _load_script("production_post_research_check")
    result = {"g1s": {"batch_limit": 500}, "g1m_local": {"batch_limit": 100}}
    worker = {
        "last_started_ts": 10.0,
        "last_finished_ts": 12.0,
        "maintenance_running": False,
        "acceptance_pause_active": False,
        "current_phase": "idle",
    }
    assert check._cycle_finished(worker, result) is False
