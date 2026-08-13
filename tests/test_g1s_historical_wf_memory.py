from __future__ import annotations

from types import SimpleNamespace

import seiltanzer.g1_short_horizon_historical_wf_memory as mem


def test_memory_runner_materializes_each_horizon_before_building_next(monkeypatch):
    runtime = SimpleNamespace()
    events = []
    state = {"contract_version": None, "state": "PENDING", "run_count": 0,
             "provisional_count": 0}
    sources = [
        {"instrument": "NAS100", "source_id": "s1", "source_sha256": "a",
         "ticker": "^NDX", "bar_count": 2000, "calendar_span_days": 60.0,
         "first_bar_end_ts": 1.0, "last_bar_end_ts": 2.0},
        {"instrument": "SP500", "source_id": "s2", "source_sha256": "b",
         "ticker": "^GSPC", "bar_count": 2000, "calendar_span_days": 60.0,
         "first_bar_end_ts": 1.0, "last_bar_end_ts": 2.0},
    ]

    monkeypatch.setattr(mem, "HORIZONS", (15, 30, 60))
    monkeypatch.setattr(mem, "_ensure_tables", lambda _runtime: None)
    monkeypatch.setattr(mem, "_state", lambda _runtime: dict(state))

    def set_state(_runtime, **updates):
        state.update(updates)
    monkeypatch.setattr(mem, "_set_state", set_state)
    monkeypatch.setattr(mem, "_fetch_sources", lambda _runtime: (sources, {}))

    def build(source, horizon):
        events.append(("build", horizon, source["instrument"]))
        return [{
            "captured_ts": float(horizon), "instrument": source["instrument"],
            "historical_winner": False,
        }]
    monkeypatch.setattr(mem, "_build_horizon_rows", build)

    def materialize(_runtime, *, target, horizon, rows, **_kwargs):
        events.append(("materialize", horizon, target, len(rows)))
        return {"historical_winner": False, "target": target,
                "horizon_minutes": horizon}
    monkeypatch.setattr(mem, "_materialize_run", materialize)
    monkeypatch.setattr(mem.gc, "collect", lambda: 0)

    result = mem._run_once_memory_bounded(runtime, force=True)

    assert result["horizons_materialized_sequentially"] is True
    assert result["run_count"] == 6
    for horizon in (15, 30, 60):
        first_materialize = next(i for i,e in enumerate(events)
                                 if e[0] == "materialize" and e[1] == horizon)
        later_builds = [i for i,e in enumerate(events)
                        if e[0] == "build" and e[1] > horizon]
        if later_builds:
            assert first_materialize < min(later_builds)
    assert state["state"] == "COMPLETE"
    assert state["run_count"] == 6


def test_memory_runner_keeps_finalized_source_set_on_failure(monkeypatch):
    runtime = SimpleNamespace()
    state = {"contract_version": None, "state": "PENDING", "run_count": 0,
             "provisional_count": 0}
    source = {"instrument": "NAS100", "source_id": "s1", "source_sha256": "a",
              "ticker": "^NDX", "bar_count": 2000, "calendar_span_days": 60.0,
              "first_bar_end_ts": 1.0, "last_bar_end_ts": 2.0}

    monkeypatch.setattr(mem, "HORIZONS", (15,))
    monkeypatch.setattr(mem, "_ensure_tables", lambda _runtime: None)
    monkeypatch.setattr(mem, "_state", lambda _runtime: dict(state))
    monkeypatch.setattr(mem, "_set_state", lambda _runtime, **updates: state.update(updates))
    monkeypatch.setattr(mem, "_fetch_sources", lambda _runtime: ([source], {}))
    monkeypatch.setattr(mem, "_build_horizon_rows",
                        lambda _source, _horizon: (_ for _ in ()).throw(RuntimeError("forced")))

    try:
        mem._run_once_memory_bounded(runtime, force=True)
    except RuntimeError as exc:
        assert str(exc) == "forced"
    else:
        raise AssertionError("expected failure")

    assert state["state"] == "ERROR"
    assert state["source_set_sha256"]
    assert state["source_count"] == 1
    assert "RuntimeError: forced" in state["last_error"]
