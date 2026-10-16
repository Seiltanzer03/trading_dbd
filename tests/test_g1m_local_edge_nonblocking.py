"""Local management diagnostics must never recompute history on HTTP."""
import json
import sqlite3
import threading
import time
from types import MethodType, SimpleNamespace

import pytest


class Runtime:
    def __init__(self):
        self._lock=threading.RLock()
        self._conn=sqlite3.connect(':memory:',check_same_thread=False)
        self._conn.execute('CREATE TABLE facts(value)')
        self._conn.execute('INSERT INTO facts VALUES(1)')
        self.fail=False

    def edge(self):
        if self.fail:raise ValueError('private provider/body must not leak')
        with self._lock:
            value=self._conn.execute('SELECT value FROM facts').fetchone()[0]
        return dict(contract_version='g1m-local-management-edge-v2',dataset={'observations':value},
                    pairwise=[{'prospective':{'effective_n':value,'mean_delta_r':0.123}}],
                    production_authority=False,production_directional_authority=False,
                    auto_promotion=False,may_trigger_exit_or_close=False,edge_claim_allowed=False,
                    management_shadow={'auto_activation':False},
                    active_edge_decision_attribution={'decision_weight_applied':False})

    def materialize_windows(self,*,limit=1):
        with self._lock:self._conn.execute('UPDATE facts SET value=value+?',(limit,))
        return limit

    def resolve_due(self,*,limit=1):
        return limit


def install(runtime):
    from seiltanzer.g1_management_local_edge_nonblocking import install_local_edge_nonblocking
    install_local_edge_nonblocking(runtime)


def test_install_and_cold_http_do_not_read_sqlite():
    runtime=Runtime();runtime._conn.set_authorizer(lambda *_:sqlite3.SQLITE_DENY)
    install(runtime)
    body=runtime.edge()
    assert body['verdict']=='LOCAL_EDGE_BUILDING'
    assert body['report_materialization']['cached_snapshot'] is False
    assert body['production_authority'] is False
    assert 'dataset' not in body  # Missing evidence is not an observed zero.


def test_worker_snapshot_preserves_statistics_and_http_is_sql_free():
    runtime=Runtime();expected=runtime.edge();install(runtime)
    runtime.resolve_due()
    runtime._conn.set_authorizer(lambda *_:sqlite3.SQLITE_DENY)
    body=runtime.edge();meta=body.pop('report_materialization')
    assert body==expected
    assert meta['request_time_sqlite_access'] is False
    assert meta['request_time_recalculation'] is False
    assert meta['cache_dirty'] is False
    body['dataset']['observations']=999
    assert runtime.edge()['dataset']['observations']==1


def test_reads_do_not_wait_for_worker_lock_and_age_advances(monkeypatch):
    runtime=Runtime();install(runtime);runtime.resolve_due()
    acquired=threading.Event();release=threading.Event()
    def hold():
        with runtime._lock:acquired.set();release.wait(2)
    thread=threading.Thread(target=hold,daemon=True);thread.start();assert acquired.wait(1)
    try:
        before=time.monotonic();first=runtime.edge()
        assert time.monotonic()-before<0.1
        from seiltanzer import g1_management_local_edge_nonblocking as mod
        now=mod.time.time();monkeypatch.setattr(mod.time,'time',lambda:now+5)
        second=json.loads(runtime.materialized_edge_json())
        assert second['report_materialization']['snapshot_age_sec']>first['report_materialization']['snapshot_age_sec']
    finally:release.set();thread.join(1)


def test_materialization_marks_previous_report_dirty_until_resolve():
    runtime=Runtime();install(runtime);runtime.resolve_due()
    assert runtime.materialize_windows(limit=3)==3
    old=runtime.edge()
    assert old['dataset']['observations']==1
    assert old['report_materialization']['cache_dirty'] is True
    assert old['report_materialization']['presentation_state']=='BUILDING'
    assert runtime.resolve_due(limit=2)==2
    current=runtime.edge()
    assert current['dataset']['observations']==4
    assert current['report_materialization']['cache_dirty'] is False


def test_refresh_failure_keeps_old_report_explicitly_dirty_without_private_error():
    runtime=Runtime();install(runtime);runtime.resolve_due();runtime.fail=True
    with pytest.raises(ValueError):runtime.resolve_due()
    encoded=runtime.materialized_edge_json();body=json.loads(encoded)
    assert body['dataset']['observations']==1
    assert body['report_materialization']['cache_dirty'] is True
    assert body['report_materialization']['last_error_class']=='ValueError'
    assert b'private provider' not in encoded
    runtime.fail=False;runtime.resolve_due()
    assert runtime.edge()['report_materialization']['last_error_class'] is None


def test_slow_old_refresh_cannot_overwrite_newer_worker_generation():
    runtime=Runtime();original=runtime.edge
    entered=threading.Event();release=threading.Event();calls=0
    def delayed():
        nonlocal calls
        calls+=1;body=original()
        if calls==2:entered.set();release.wait(2)
        return body
    runtime.edge=delayed;install(runtime);runtime.resolve_due()
    old=threading.Thread(target=runtime.resolve_due,daemon=True);old.start();assert entered.wait(1)
    try:
        runtime.materialize_windows(limit=4);runtime.resolve_due()
        assert runtime.edge()['dataset']['observations']==5
    finally:release.set();old.join(1)
    assert runtime.edge()['dataset']['observations']==5
    assert runtime.edge()['report_materialization']['cache_dirty'] is False


def test_actual_math_keeps_entire_descriptive_and_prospective_cohort():
    from test_g1m_local_edge_v2 import _Runtime
    from seiltanzer.g1_management_local_edge_v2 import management_edge_v2
    runtime=_Runtime()
    runtime.add_window(window_id='live',trade_id=1,eligible=True)
    runtime.add_window(window_id='old',trade_id=2,eligible=False)
    runtime.edge=MethodType(management_edge_v2,runtime)
    runtime.materialize_windows=lambda **_:0;runtime.resolve_due=lambda **_:0
    expected=runtime.edge();install(runtime);runtime.resolve_due()
    actual=runtime.edge();actual.pop('report_materialization')
    assert actual==expected and actual['dataset']['resolved_windows']==2


def test_http_uses_preencoded_body_without_dict_renderer():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from seiltanzer.g1_short_horizon_routes import install_g1_short_horizon_routes
    class Other:
        def __getattr__(self,name):return lambda *args,**kwargs:{}
    runtime=Runtime();runtime.outcomes=lambda **_:{};runtime.status=lambda:{}
    install(runtime);runtime.resolve_due();runtime.edge=lambda:pytest.fail('HTTP must use preencoded renderer')
    app=FastAPI();app.state.engine=SimpleNamespace(short_horizon=Other(),management_local=runtime)
    install_g1_short_horizon_routes(app)
    response=TestClient(app).get('/api/research/g1/management/local-edge')
    assert response.status_code==200 and response.json()['dataset']['observations']==1
    assert response.headers['cache-control']=='no-store'


@pytest.mark.parametrize('state,cached,error,expected',[
    ('READY',True,None,'Возраст снимка'),
    ('BUILDING',True,None,'обновляется'),
    ('BUILDING',False,None,'ещё не построен'),
    ('BUILDING',True,'ValueError','ошибкой')])
def test_ui_distinguishes_cached_building_and_unavailable_history(state,cached,error,expected):
    import subprocess
    from seiltanzer.g1_intelligence_page_refinement import _TWO_SPEED_SCRIPT
    start=_TWO_SPEED_SCRIPT.index(' function renderLocal(')
    end=_TWO_SPEED_SCRIPT.index(' async function refreshTwoSpeed',start)
    body={'report_materialization':dict(presentation_state=state,cached_snapshot=cached,
                                      snapshot_age_sec=5,last_error_class=error)}
    script=('const host={};const document={querySelector:()=>host};'
            'const fmt=n=>n==null?"—":String(n);'+_TWO_SPEED_SCRIPT[start:end]
            +'renderLocal({},'+json.dumps(body)+');console.log(host.innerHTML);')
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True,timeout=5)
    assert expected in result.stdout
