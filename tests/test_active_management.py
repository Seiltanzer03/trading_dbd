from dataclasses import replace
import asyncio

from seiltanzer.execution_simulator import ExecutionSpec, replay_execution_path
from seiltanzer.active_management import select_active_management


def spec():
    return ExecutionSpec(0, 0, 3, (1, 2), .1, 1.5)


def test_spike_closes_current_remainder_once_then_ladder_continues():
    # .1 at 1R; frozen .25 of T0 size at 1.25R; .65 exits at -1R.
    result = replay_execution_path([0, 1.4, .2, -1], replace(spec(),
                                  spike_r=1.25, spike_fraction=.25))
    assert abs(result.outcome_r - (.1 + .25 * 1.25 - .65)) < 1e-9
    assert sum(e['type'] == 'spike' for e in result.events) == 1


def test_time_stop_does_not_resurrect_a_position_stopped_before_deadline():
    timed = replace(spec(), time_stop_fraction=.5)
    result = replay_execution_path([0, -1.5, 3, 4], timed)
    assert result.outcome_r == -1
    assert result.exit_reason == 'stop'


def test_time_stop_interpolates_price_and_preserves_realized_ladder():
    result = replay_execution_path([0, 1, 1.4, -1],
                                   replace(spec(), time_stop_fraction=.5))
    assert abs(result.outcome_r - (.1 + .9 * 1.2)) < 1e-9
    assert result.exit_reason == 'time_stop'


def test_be_never_widens_previously_tightened_stop():
    result = replay_execution_path([1, 2, .5],
        ExecutionSpec(1, 1, 3, (), 0, 1.5, stop_r=.8))
    assert result.outcome_r == .8


def test_candidate_selection_keeps_hold_when_data_unavailable():
    snapshot = {}
    assert select_active_management(snapshot) is None
    assert len(snapshot['active_management_candidates']) == 7
    assert all(r['status'] == 'blocked' for r in snapshot['active_management_candidates'])


def test_deterministic_manager_selects_a_real_counterfactual_without_llm():
    from test_extended_policy_evaluation import _snapshot
    snapshot = _snapshot()
    snapshot.update(captured_ts=1_900_000_000, strategy={'direction': 'long'})
    snapshot['policy_manager']['inputs'].update(drift_R=-2, sigma_R=.3)
    result = select_active_management(snapshot)
    assert result is not None
    assert result['source'] == 'deterministic_active_management'
    assert result['quant_evaluation']['paired_delta_ci95_lower_r'] > .03
    assert result['automatic_execution_allowed'] is False
    assert result['production_authority'] is False  # not registered yet
    assert result['statistically_validated_advantage'] is False


def test_current_deploy_lease_expedites_core_without_skipping_it(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from seiltanzer import g1_research_worker as worker
    @asynccontextmanager
    async def life(app):
        yield
    app = SimpleNamespace(state=SimpleNamespace(engine=SimpleNamespace(
        short_horizon=object(), management_local=object())),
        router=SimpleNamespace(lifespan_context=life))
    calls = []
    monkeypatch.setattr(worker, 'worker_acceptance_gate_state', lambda **kw:
        {'active': True, 'pause': kw['last_finished_ts'] is not None,
         'reason': 'test', 'acceptance_run_id': '1', 'expected_sha': 'sha', 'expires_at': None})
    monkeypatch.setattr(worker, '_apply_memory_pressure', lambda s: False)
    monkeypatch.setattr(worker, '_run_g1s_core', lambda r: calls.append('g1s') or {'batch_limit': 500})
    monkeypatch.setattr(worker, '_run_g1m_local_core', lambda r: calls.append('g1m') or {'batch_limit': 100})
    worker.install_research_worker(app)
    async def exercise():
        async with app.router.lifespan_context(app):
            for _ in range(100):
                await asyncio.sleep(.005)
                if len(calls) == 2:
                    break
    asyncio.run(exercise())
    assert calls == ['g1s', 'g1m']
    assert app.state.g1_research_worker['last_finished_ts'] is not None
