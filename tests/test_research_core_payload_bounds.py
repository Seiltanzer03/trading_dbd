import tracemalloc

from test_g1s_short_horizon import runtime, _capture
from seiltanzer import g1_short_horizon_runtime as source_runtime
from seiltanzer import g1_short_horizon_champion_runtime as champion
from seiltanzer.g1_research_worker import _run_g1s_core, _run_maintenance_phase


def test_materialization_byte_budget_resumes_without_dropping_source_rows(runtime):
    rt, passive = runtime
    _capture(passive, 1_700_000_000.)
    assert rt.materialize_new(limit=500, max_payload_bytes=1) == 1
    first_watermark = rt._watermark()
    assert rt.materialize_new(limit=500, max_payload_bytes=1) == 1
    assert rt._watermark() > first_watermark
    for _ in range(5):
        rt.materialize_new(limit=500, max_payload_bytes=1)
    assert rt._conn.execute('SELECT COUNT(*) FROM g1s_observations').fetchone()[0] == 5
    assert rt.materialize_new(limit=500, max_payload_bytes=1) == 0


def test_materialization_time_budget_retains_remaining_rows(runtime, monkeypatch):
    rt, passive = runtime
    _capture(passive, 1_700_000_000.)
    ticks = iter([0., 2.])
    monkeypatch.setattr(source_runtime.time, 'monotonic', lambda: next(ticks))
    assert rt.materialize_new(limit=500, time_budget_sec=1.) == 1
    monkeypatch.undo()
    assert rt.materialize_new(limit=500) == 4


def _resolved_source(rt, passive):
    ts = 1_700_000_000.
    _capture(passive, ts)
    assert rt.materialize_new() == 5
    for minute in range(0, 241, 5):
        passive.record_market_point('XAU', ts + minute * 60., 100. + minute / 100.,
            source='test-direct', quality=1., kind='direct')
    assert passive.resolve_due(now=ts + 4 * 3600.)['resolved'] >= 4


def test_resolution_does_not_bulk_allocate_unused_frozen_payloads(runtime, monkeypatch):
    rt, passive = runtime
    capture = passive.capture_observation
    def capture_with_padding(**kwargs):
        kwargs['features'] = {**kwargs['features'], 'fixture_padding': 'x' * 2_000_000}
        return capture(**kwargs)
    monkeypatch.setattr(passive, 'capture_observation', capture_with_padding)
    _resolved_source(rt, passive)
    tracemalloc.start()
    try:
        assert rt.resolve_new(limit=500) >= 4
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Full-batch frozen copies formerly peak at30MB on this5-row fixture.
    # Allow the hash/source copies of one atomic2MB row, not the whole batch.
    assert peak < 12_000_000, peak


def test_worker_defers_full_history_progress_but_resolves_real_outcomes(runtime, monkeypatch):
    rt, passive = runtime
    _resolved_source(rt, passive)
    calls = []
    refresh = champion._refresh_progress
    def observed_refresh(obj):
        calls.append(obj)
        return refresh(obj)
    monkeypatch.setattr(champion, '_refresh_progress', observed_refresh)
    result = _run_g1s_core(rt)
    assert result['resolved'] >= 4
    assert calls == []
    outcome = _run_maintenance_phase(rt, object(), 'champion_progress')
    assert outcome['phase'] == 'champion_progress'
    assert calls == [rt]


def test_core_stage_identifies_the_operation_and_preserves_errors():
    state = {}
    class FailingRuntime:
        def materialize_new(self, **kwargs):
            assert state['core_stage'] == 'g1s:materialize'
            assert state['core_stage_started_ts'] > 0
            return 1
        def resolve_new(self, **kwargs):
            assert state['core_stage'] == 'g1s:resolve'
            raise RuntimeError('fixture resolution failure')
    import pytest
    with pytest.raises(RuntimeError, match='fixture resolution failure'):
        _run_g1s_core(FailingRuntime(), state=state)
    assert state['core_stage'] == 'g1s:resolve'
