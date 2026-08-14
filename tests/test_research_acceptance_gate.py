from __future__ import annotations

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import seiltanzer.g1_research_worker as research_worker
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
        process_started_ts=90.0,
        last_finished_ts=None,
        path=gate,
        now=101.0,
    )
    assert pending["active"] is True
    assert pending["pause"] is False
    assert pending["reason"] == "REQUIRED_WORKER_CYCLE_PENDING"

    paused = worker_acceptance_gate_state(
        process_started_ts=90.0,
        last_finished_ts=105.0,
        path=gate,
        now=106.0,
    )
    assert paused["active"] is True
    assert paused["pause"] is True
    assert paused["smoke_run_id"] == "smoke-17"
    assert paused["expected_sha"] == "abc123"


def test_gate_from_previous_service_generation_is_ignored(tmp_path):
    gate = tmp_path / "acceptance.json"
    write_acceptance_gate("smoke-old", "oldsha", ttl_seconds=100, path=gate, now=100.0)
    state = worker_acceptance_gate_state(
        process_started_ts=110.0,
        last_finished_ts=None,
        path=gate,
        now=111.0,
    )
    assert state["active"] is False
    assert state["pause"] is False
    assert state["reason"] == "STALE_SERVICE_GENERATION_GATE"


def test_expired_gate_cannot_pause_worker(tmp_path):
    gate = tmp_path / "acceptance.json"
    write_acceptance_gate("smoke-17", "abc123", ttl_seconds=5, path=gate, now=100.0)
    state = worker_acceptance_gate_state(
        process_started_ts=90.0,
        last_finished_ts=101.0,
        path=gate,
        now=106.0,
    )
    assert state["active"] is False
    assert state["pause"] is False
    assert state["reason"] == "ACCEPTANCE_GATE_EXPIRED"


def test_new_exact_run_supersedes_old_owner_safely(tmp_path):
    gate = tmp_path / "acceptance.json"
    write_acceptance_gate("smoke-1", "sha-1", ttl_seconds=100, path=gate, now=100.0)
    write_acceptance_gate("smoke-2", "sha-2", ttl_seconds=100, path=gate, now=101.0)

    assert release_acceptance_gate("smoke-1", "sha-1", path=gate) is False
    assert gate_owner_matches("smoke-2", "sha-2", path=gate, now=102.0) is True
    assert release_acceptance_gate("smoke-2", "sha-2", path=gate) is True
    assert not gate.exists()


def test_worker_runs_required_cycle_once_then_defers_new_cycles(monkeypatch):
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
            "smoke_run_id": "991",
            "expected_sha": "sha",
            "expires_at": 99999999999.0,
        }

    monkeypatch.setattr(research_worker, "RESEARCH_STARTUP_GRACE_SEC", 0.001)
    monkeypatch.setattr(research_worker, "RESEARCH_INTERVAL_SEC", 0.001)
    monkeypatch.setattr(research_worker, "worker_acceptance_gate_state", fake_gate)
    monkeypatch.setattr(
        research_worker,
        "_run_g1s_bounded",
        lambda _runtime: calls.append("g1s") or {"batch_limit": 500},
    )
    monkeypatch.setattr(
        research_worker,
        "_run_g1m_local_bounded",
        lambda _runtime: calls.append("g1m") or {"batch_limit": 100},
    )
    monkeypatch.setattr(
        research_worker,
        "_run_ede_shadow_bounded",
        lambda _engine: calls.append("shadow") or {"refreshed": False},
    )
    research_worker.install_research_worker(app)

    async def exercise():
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.02)

    asyncio.run(exercise())
    assert calls == ["g1s", "g1m", "shadow"]
    assert app.state.g1_research_worker["acceptance_pause_active"] is True
    assert app.state.g1_research_worker["running"] is False


def test_exact_run_markers_reject_sha_mismatch(tmp_path):
    orchestration = _load_script("production_research_acceptance")
    marker = orchestration.write_marker(
        "post-research", "991", "sha-good", marker_dir=tmp_path
    )
    assert marker.read_text(encoding="utf-8").strip() == "sha-good"
    assert orchestration.wait_marker(
        "post-research",
        "991",
        "sha-good",
        timeout_seconds=0,
        poll_seconds=0.1,
        marker_dir=tmp_path,
    ) == marker

    try:
        orchestration.wait_marker(
            "post-research",
            "991",
            "sha-wrong",
            timeout_seconds=0,
            poll_seconds=0.1,
            marker_dir=tmp_path,
        )
    except RuntimeError as exc:
        assert "POST_RESEARCH_SHA_MISMATCH" in str(exc)
    else:
        raise AssertionError("stale/wrong-SHA marker unexpectedly passed")


def test_post_research_does_not_accept_previous_result_during_new_cycle():
    check = _load_script("production_post_research_check")
    result = {"g1s": {"batch_limit": 500}, "g1m_local": {"batch_limit": 100}}
    assert check._cycle_finished(
        {"last_started_ts": 10.0, "last_finished_ts": 12.0}, result
    ) is True
    assert check._cycle_finished(
        {"last_started_ts": 20.0, "last_finished_ts": 12.0}, result
    ) is False
