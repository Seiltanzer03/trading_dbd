import pytest
from types import SimpleNamespace

from seiltanzer import g1_research_worker as worker
from seiltanzer import production_resource_guard as guard


def test_maintenance_phases_yield_under_memory_pressure(monkeypatch):
    calls = {"trimmed": 0}
    monkeypatch.setattr(
        worker,
        "memory_pressure_state",
        lambda: {
            "level": "soft",
            "pause_background": True,
            "rss_mib": 700.0,
            "soft_mib": 649,
            "hard_mib": 845,
            "critical_mib": 1042,
        },
    )
    monkeypatch.setattr(worker, "trim_memory_for_pressure", lambda: calls.update(trimmed=calls["trimmed"] + 1))

    dummy_runtime = SimpleNamespace()
    dummy_engine = SimpleNamespace(short_horizon=dummy_runtime)

    for phase in ("ede_shadow", "evidence_reports", "historical_walk_forward", "fit_models"):
        result = worker._run_maintenance_phase(dummy_runtime, dummy_engine, phase)
        assert result.get("skipped") is True
        assert result.get("reason") == "MEMORY_PRESSURE_YIELD"
        assert result.get("phase") == phase

    assert calls["trimmed"] > 0


def test_maintenance_phases_trim_memory_in_finally(monkeypatch):
    calls = {"trimmed": 0, "refreshed": False}
    monkeypatch.setattr(
        worker,
        "memory_pressure_state",
        lambda: {
            "level": "normal",
            "pause_background": False,
            "rss_mib": 350.0,
        },
    )
    monkeypatch.setattr(worker, "trim_memory_for_pressure", lambda: calls.update(trimmed=calls["trimmed"] + 1))

    class DummyRuntime:
        def refresh_materialized_status(self, limit):
            calls["refreshed"] = True
            return {"status": "ok", "limit": limit}

    runtime = DummyRuntime()
    engine = SimpleNamespace(short_horizon=runtime)

    result = worker._run_maintenance_phase(runtime, engine, "status_refresh")
    assert calls["refreshed"] is True
    assert result["phase"] == "status_refresh"
    assert calls["trimmed"] >= 1
