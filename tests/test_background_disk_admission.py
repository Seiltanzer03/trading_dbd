import sqlite3
from types import SimpleNamespace

import pytest


def module():
    from seiltanzer import background_disk_admission
    return background_disk_admission


def engine(tmp_path):
    conn = sqlite3.connect(tmp_path/'fact.sqlite3'); conn.execute('CREATE TABLE fact(n)'); conn.commit()
    passive = SimpleNamespace(_instrument_cursor=5, _last_successful_capture_ts=123.,
                              _last_step_ts=None, _last_step_error=None, _last_skip_reason=None)
    def step():
        conn.execute('INSERT INTO fact VALUES(1)'); conn.commit()
        passive._instrument_cursor += 1
        return {'created':['actual']}
    passive.step = step
    return SimpleNamespace(settings=SimpleNamespace(demo=False,data_dir=str(tmp_path)),passive=passive),conn


@pytest.mark.parametrize('failure',['low','unknown'])
def test_optional_capture_cannot_write_or_advance_when_disk_admission_fails(tmp_path, monkeypatch, failure):
    mod=module(); obj,conn=engine(tmp_path)
    def capacity(_):
        if failure=='unknown': raise OSError('filesystem unavailable')
        return SimpleNamespace(free=mod.MIN_FREE_BYTES-1)
    monkeypatch.setattr(mod.shutil,'disk_usage',capacity)
    result=mod.passive_step(obj)
    assert result['skipped'] is True and result['reason']=='DISK_HEADROOM_UNAVAILABLE'
    assert conn.execute('SELECT COUNT(*) FROM fact').fetchone()[0]==0
    assert obj.passive._instrument_cursor==5 and obj.passive._last_successful_capture_ts==123.
    assert obj.passive._last_skip_reason=='DISK_HEADROOM_UNAVAILABLE'
    assert obj.passive._last_step_error=='DISK_HEADROOM_UNAVAILABLE'
    conn.close()


def test_healthy_recheck_resumes_real_capture_without_fabricating_missing_records(tmp_path, monkeypatch):
    mod=module(); obj,conn=engine(tmp_path); free=0
    monkeypatch.setattr(mod.shutil,'disk_usage',lambda _:SimpleNamespace(free=free))
    assert mod.passive_step(obj)['skipped'] is True
    free=mod.MIN_FREE_BYTES
    assert mod.passive_step(obj)=={'created':['actual']}
    assert conn.execute('SELECT COUNT(*) FROM fact').fetchone()[0]==1
    conn.close()


def test_research_state_pauses_and_recovers_with_exact_threshold(tmp_path, monkeypatch):
    mod=module(); obj,conn=engine(tmp_path); state={};free=1024**3
    monkeypatch.setattr(mod.shutil,'disk_usage',lambda _:SimpleNamespace(free=free))
    assert mod.apply_research_pause(state,obj) is True
    assert state['current_phase']=='disk_pressure_pause' and state['disk_pause_active'] is True
    assert state['maintenance_running'] is False
    free=1024**3+32*1024**2
    assert mod.apply_research_pause(state,obj) is False
    assert state['disk_pause_active'] is False
    conn.close()


def test_demo_does_not_read_host_disk_or_change_existing_step(tmp_path, monkeypatch):
    mod=module(); obj,conn=engine(tmp_path);obj.settings.demo=True
    monkeypatch.setattr(mod.shutil,'disk_usage',lambda _:pytest.fail('demo host disk read'))
    assert mod.passive_step(obj)=={'created':['actual']}
    conn.close()


@pytest.mark.parametrize('kind',['settings','path'])
def test_unknown_production_configuration_fails_closed(tmp_path, kind):
    mod=module();obj,conn=engine(tmp_path)
    if kind=='settings':del obj.settings
    else:obj.settings.data_dir=None
    assert mod.passive_step(obj)['skipped'] is True
    assert conn.execute('SELECT COUNT(*) FROM fact').fetchone()[0]==0
    conn.close()


def test_worker_rechecks_disk_between_independent_core_writers(tmp_path, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from seiltanzer import g1_research_worker as worker
    mod=module();obj,conn=engine(tmp_path);free=mod.MIN_FREE_BYTES;calls=[]
    obj.short_horizon=object();obj.management_local=object()
    @asynccontextmanager
    async def lifespan(_app):yield
    app=SimpleNamespace(state=SimpleNamespace(engine=obj),router=SimpleNamespace(lifespan_context=lifespan))
    monkeypatch.setattr(mod.shutil,'disk_usage',lambda _:SimpleNamespace(free=free))
    monkeypatch.setattr(worker,'RESEARCH_STARTUP_GRACE_SEC',0)
    monkeypatch.setattr(worker,'RESEARCH_INTERVAL_SEC',0.005)
    monkeypatch.setattr(worker,'memory_pressure_state',lambda:dict(pause_background=False))
    monkeypatch.setattr(worker,'worker_acceptance_gate_state',lambda **_:dict(active=False,pause=False,reason='none',acceptance_run_id=None,expected_sha=None,expires_at=None))
    def g1s(*args,**kwargs):
        nonlocal free
        calls.append('g1s');free=0
        return {'materialized':1}
    monkeypatch.setattr(worker,'_run_g1s_core',g1s)
    monkeypatch.setattr(worker,'_run_g1m_local_core',lambda *args,**kwargs:calls.append('g1m'))
    worker.install_research_worker(app)
    async def exercise():
        async with app.router.lifespan_context(app):
            for _ in range(100):
                if app.state.g1_research_worker.get('disk_pause_active'):break
                await asyncio.sleep(0.005)
            assert app.state.g1_research_worker['disk_pause_active'] is True
    asyncio.run(exercise())
    assert calls==['g1s']
    assert app.state.g1_research_worker['last_error']=='DISK_HEADROOM_UNAVAILABLE'
    assert app.state.g1_research_worker['last_result'] is None
    conn.close()
