"""Event-controlled optional work; isolated fixtures, no providers or accounts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from seiltanzer import edge_family_event_reaction as reaction
from test_edge_family_event_reaction import engine, feed, source


def frozen():
    return dict(captured_ts=1400., instrument='BTCUSD', trade_id=1,
                edge_family_sources={'event': [{'source_id': 'prior', 'published_at': 900}]})


def held(lock, ready, release):
    with lock:
        ready.set()
        assert release.wait(3)


def test_real_received_store_whole_read_refuses_busy_lock():
    from seiltanzer.macro_fomc_deterministic_bootstrap import FOMCDeterministicReleaseStore
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    runtime = SimpleNamespace(_conn=conn, _lock=threading.RLock())
    store = FOMCDeterministicReleaseStore(runtime)
    ready, release = threading.Event(), threading.Event()
    owner = threading.Thread(target=held, args=(runtime._lock, ready, release))
    owner.start(); assert ready.wait(1)
    try:
        assert store.latest_received(1400, nonblocking=True) == {
            'status': 'UNAVAILABLE', 'reason': 'REACTION_RELEASE_STORE_BUSY'}
    finally:
        release.set(); owner.join(1); conn.close()


def test_feed_busy_refuses_without_waiting():
    market = feed(); ready, release = threading.Event(), threading.Event()
    owner = threading.Thread(target=held, args=(market._intraday_lock, ready, release))
    owner.start(); assert ready.wait(1)
    # Release eventual old blocking implementation so RED never hangs.
    timer = threading.Timer(.6, release.set); timer.start()
    try:
        start = time.monotonic()
        result = reaction.build_received_event_reaction_source(source()['reaction_release'], market, 1400, 'BTCUSD')
        assert result['source'] is None
        assert result['rejections'][0]['reason'] == 'REACTION_FEED_BUSY'
        assert time.monotonic() - start < .3
    finally:
        release.set(); owner.join(1); timer.cancel()


def test_oversized_feed_is_refused_before_any_bar_copy():
    class Oversized(list):
        def __deepcopy__(self, memo):
            raise AssertionError('oversized collection copied')
    market = feed(); market.intraday_ohlcv = Oversized([None]*4097)
    result = reaction.build_received_event_reaction_source(source()['reaction_release'], market, 1400, 'BTCUSD')
    assert result['source'] is None
    assert result['rejections'][0]['reason'] == 'REACTION_FEED_BAR_BOUND_INVALID'


@pytest.mark.parametrize('cancel', [False, True])
def test_running_capture_late_completion_never_changes_frozen_review(monkeypatch, cancel):
    assert hasattr(reaction, 'attach_observed_event_reaction_bounded'), 'bounded attachment missing'
    began, release, ended = threading.Event(), threading.Event(), threading.Event()
    runtime = engine(); original = reaction.attach_observed_event_reaction
    seen = []
    def blocked(runtime, private):
        seen.append(private)
        began.set(); assert release.wait(3)
        original(runtime, private); ended.set()
    monkeypatch.setattr(reaction, 'attach_observed_event_reaction', blocked)
    async def run():
        live = frozen(); prior = deepcopy(live['edge_family_sources'])
        start = time.monotonic()
        task = asyncio.create_task(reaction.attach_observed_event_reaction_bounded(runtime, live))
        try:
            await asyncio.wait_for(asyncio.to_thread(began.wait, 1), 1.5)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
            else:
                await task
                assert live['edge_family_event_reaction_audit']['reason'] == 'REACTION_CAPTURE_BUDGET_EXCEEDED'
                assert time.monotonic()-start < 1
            assert live['edge_family_sources'] == prior
            assert seen[0] is not live and 'trade_id' not in seen[0]
            other = frozen()
            await reaction.attach_observed_event_reaction_bounded(runtime, other)
            assert other['edge_family_event_reaction_audit']['reason'] == 'REACTION_CAPTURE_IN_PROGRESS'
            from seiltanzer.decision_research import canonical_snapshot
            canonical = canonical_snapshot(live)
            encoded = json.dumps(live, sort_keys=True)
            release.set(); assert await asyncio.to_thread(ended.wait, 1)
            await asyncio.sleep(0)
            assert json.dumps(live, sort_keys=True) == encoded
            assert canonical_snapshot(live) == canonical
            monkeypatch.setattr(reaction, 'attach_observed_event_reaction', original)
            after = frozen()
            await reaction.attach_observed_event_reaction_bounded(runtime, after)
            assert after['edge_family_event_reaction_audit']['available'] is True
        finally:
            release.set()
    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False, True])
def test_queued_capture_budget_includes_executor_wait_and_cancel_recovers(monkeypatch, cancel):
    assert hasattr(reaction, 'attach_observed_event_reaction_bounded'), 'bounded attachment missing'
    occupied, release = threading.Event(), threading.Event()
    runtime = engine(); original = reaction.attach_observed_event_reaction
    calls = []
    def capture(runtime, private):
        calls.append(1); original(runtime, private)
    monkeypatch.setattr(reaction, 'attach_observed_event_reaction', capture)
    async def run():
        loop = asyncio.get_running_loop(); pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)
        blocker = loop.run_in_executor(None, held, threading.Lock(), occupied, release)
        assert occupied.wait(1)
        try:
            live = frozen(); task = asyncio.create_task(reaction.attach_observed_event_reaction_bounded(runtime, live))
            start = time.monotonic(); await asyncio.sleep(0)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
            else:
                await task
                assert live['edge_family_event_reaction_audit']['reason'] == 'REACTION_CAPTURE_BUDGET_EXCEEDED'
                assert time.monotonic()-start < 1
            assert calls == []
            release.set(); await blocker; await asyncio.sleep(0)
            after = frozen()
            await reaction.attach_observed_event_reaction_bounded(runtime, after)
            assert after['edge_family_event_reaction_audit']['available'] is True
            assert calls == [1]
        finally:
            release.set()
    asyncio.run(run())


def test_fast_bounded_capture_matches_synchronous_proofs():
    assert hasattr(reaction, 'attach_observed_event_reaction_bounded'), 'bounded attachment missing'
    expected, actual = frozen(), frozen()
    reaction.attach_observed_event_reaction(engine(), expected)
    asyncio.run(reaction.attach_observed_event_reaction_bounded(engine(), actual))
    assert actual == expected


def trace_module():
    import importlib.util
    assert importlib.util.find_spec('seiltanzer.ai_request_trace'), 'request trace missing'
    from seiltanzer import ai_request_trace
    return ai_request_trace


def events(caplog):
    return [json.loads(row.message) for row in caplog.records if row.name == 'seiltanzer.ai_request_trace']


def test_trace_warning_emission_sanitized_errors_and_thread_queue(caplog):
    module = trace_module()
    trace = module.AIRequestTrace()
    occupied, release = threading.Event(), threading.Event()
    async def run():
        pool = ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_running_loop(); loop.set_default_executor(pool)
        blocker = loop.run_in_executor(None, held, threading.Lock(), occupied, release)
        assert occupied.wait(1)
        task = asyncio.create_task(trace.to_thread('provider', lambda: 'secret-body-account-123'))
        await asyncio.sleep(0)
        assert any(row['event'] == 'submitted' for row in events(caplog))
        assert not any(row['event'] == 'worker_start' for row in events(caplog))
        release.set(); await blocker
        assert await task == 'secret-body-account-123'
        with pytest.raises(ValueError):
            with trace.span('review_identity'):
                raise ValueError('secret-exception-token-123')
        trace.finish(503)
    with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
        asyncio.run(run())
    rows = events(caplog)
    assert all(row['request_id'] == trace.request_id for row in rows)
    assert [row['event'] for row in rows if row['stage'] == 'provider'] == ['submitted', 'worker_start', 'worker_end']
    assert rows[-1]['status_code'] == 503
    assert any(row.get('exception_class') == 'ValueError' for row in rows)
    assert 'secret-' not in caplog.text
    assert all(row['elapsed_ms'] >= 0 for row in rows)


def test_materializer_and_api_correlate_preflight_route_and_final_status(tmp_path, monkeypatch, caplog):
    from test_ai_verdict_api import _client
    from seiltanzer.ai_snapshot_materializer import install_ai_snapshot_materializer
    from seiltanzer import app as app_module
    trace_module()
    app, client = _client(tmp_path, monkeypatch, lambda _: {'verdict': 'isolated secret-body-account-123'})
    # Existing helper opens only a test DB trade; no startup/network/provider.
    from seiltanzer import macro_t0_context
    monkeypatch.setattr(app.state.engine.passive, '_macro_data_factory', SimpleNamespace(), raising=False)
    monkeypatch.setattr(macro_t0_context, 'build_macro_t0_context', lambda *args: {})
    mat = install_ai_snapshot_materializer(app)
    monkeypatch.setattr(mat, '_event_reason', lambda: None)
    monkeypatch.setattr(app_module, 'build_snapshot', mat.builder)
    try:
        with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
            response = client.post('/api/ai/verdict', json={'token': 'secret-request-token-123'})
        assert response.status_code == 200
        rows = events(caplog); req_id = response.json()['request_id']
        assert rows and all(row['request_id'] == req_id for row in rows)
        stages = {row['stage'] for row in rows}
        assert {'preflight', 'route', 'cached_snapshot', 'position_finalization', 'family_bundle',
                'runtime_context', 'macro_context', 'reaction_capture', 'regime_management', 'review_identity',
                'provider', 'ensemble', 'publication', 'response'} <= stages
        assert rows[-1]['status_code'] == 200
        assert 'secret-' not in caplog.text
    finally:
        app.state.engine.close()
        monkeypatch.setattr(app_module, 'build_snapshot', mat.builder)


def test_materializer_early_warming_logs_final_503(tmp_path, monkeypatch, caplog):
    from test_ai_verdict_api import _client
    from seiltanzer.ai_snapshot_materializer import install_ai_snapshot_materializer
    trace_module()
    app, client = _client(tmp_path, monkeypatch, lambda _: {'verdict': 'never-called'})
    mat = install_ai_snapshot_materializer(app)
    monkeypatch.setattr(mat, '_event_reason', lambda: 'SNAPSHOT_WARMING')
    try:
        with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
            response = client.post('/api/ai/verdict')
        assert response.status_code == 503
        rows = events(caplog)
        assert {row['stage'] for row in rows} == {'preflight', 'response'}
        assert rows[-1]['status_code'] == 503
        assert len({row['request_id'] for row in rows}) == 1
    finally:
        app.state.engine.close()


@pytest.mark.parametrize('failure', [False, True])
def test_failing_trace_sink_preserves_success_error_and_direct_invocation(tmp_path, monkeypatch, failure):
    module = trace_module()
    from test_ai_verdict_api import _client
    def broken(*args, **kwargs):
        raise RuntimeError('secret-sink-token-123')
    def provider(_):
        if failure: raise ValueError('secret-provider-token-123')
        return {'verdict': 'isolated'}
    monkeypatch.setattr(module.LOGGER, 'warning', broken)
    app, _ = _client(tmp_path, monkeypatch, provider)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', None) == '/api/ai/verdict')
    try:
        response = asyncio.run(endpoint())
        assert response.status_code == (500 if failure else 200)
        body = json.loads(response.body)
        assert body['ok'] is (not failure)
        if failure: assert body['error']['code'] == 'ai_internal_error'
    finally:
        app.state.engine.close()


@pytest.mark.parametrize('bad', ['oversized', 'malformed', 'strategy', 'deep'])
def test_private_projection_refuses_bad_optional_input_without_copy_or_api_error(bad):
    class NoCopy(dict):
        def __deepcopy__(self, memo):
            raise AssertionError('bad source copied')
    live = frozen()
    if bad == 'oversized':
        live['edge_family_sources'] = NoCopy(event=[{'padding': 'x'*8001}])
    elif bad == 'malformed':
        live['edge_family_sources'] = NoCopy(event=[{'value': object()}])
    elif bad == 'strategy':
        live['strategy'] = ['unexpected']
    else:
        value = {}; nested = value
        for _ in range(20):
            nested['nested'] = {}; nested = nested['nested']
        live['edge_family_sources'] = NoCopy(event=[value])
    prior = live['edge_family_sources']
    runtime = engine()
    asyncio.run(reaction.attach_observed_event_reaction_bounded(runtime, live))
    assert live['edge_family_sources'] is prior
    assert live['edge_family_event_reaction_audit']['available'] is False
    # A projection refusal must not strand admission.
    after = frozen()
    asyncio.run(reaction.attach_observed_event_reaction_bounded(runtime, after))
    assert after['edge_family_event_reaction_audit']['available'] is True


def test_shared_sqlite_contention_is_covered_without_global_timeout_changes(tmp_path):
    from seiltanzer.macro_fomc_deterministic_bootstrap import FOMCDeterministicReleaseStore
    path = str(tmp_path / 'isolated-releases.sqlite')
    conn = sqlite3.connect(path, check_same_thread=False)
    runtime = SimpleNamespace(_conn=conn, _lock=threading.RLock())
    store = FOMCDeterministicReleaseStore(runtime)
    timeout = conn.execute('PRAGMA busy_timeout').fetchone()[0]
    other = sqlite3.connect(path, check_same_thread=False)
    other.execute('BEGIN EXCLUSIVE')
    observed = threading.Event()
    original = store.latest_received
    def read(cutoff, *, nonblocking=False):
        observed.set()
        return original(cutoff, nonblocking=nonblocking)
    store.latest_received = read
    live = frozen(); capture_engine = engine()
    capture_engine.passive._macro_data_factory.fomc_deterministic_store = store
    async def run():
        start = time.monotonic()
        try:
            await reaction.attach_observed_event_reaction_bounded(capture_engine, live)
            assert observed.is_set()
            assert time.monotonic()-start < 1
            assert live['edge_family_event_reaction_audit']['reason'] == 'REACTION_CAPTURE_BUDGET_EXCEEDED'
            after = frozen()
            await reaction.attach_observed_event_reaction_bounded(capture_engine, after)
            assert after['edge_family_event_reaction_audit']['reason'] == 'REACTION_CAPTURE_IN_PROGRESS'
        finally:
            other.rollback()
    try:
        asyncio.run(run())
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == timeout
        assert live['edge_family_sources'] == frozen()['edge_family_sources']
    finally:
        other.rollback(); other.close(); conn.close()


def test_worker_error_releases_admission_and_preserves_original_exception(monkeypatch):
    runtime = engine(); original = reaction.attach_observed_event_reaction
    def broken(runtime, private):
        raise RuntimeError('isolated-worker-error')
    monkeypatch.setattr(reaction, 'attach_observed_event_reaction', broken)
    with pytest.raises(RuntimeError, match='isolated-worker-error'):
        asyncio.run(reaction.attach_observed_event_reaction_bounded(runtime, frozen()))
    monkeypatch.setattr(reaction, 'attach_observed_event_reaction', original)
    live = frozen()
    asyncio.run(reaction.attach_observed_event_reaction_bounded(runtime, live))
    assert live['edge_family_event_reaction_audit']['available'] is True


def test_api_error_emits_no_exception_text_or_input_payload(tmp_path, monkeypatch, caplog):
    from test_ai_verdict_api import _client
    def broken(_):
        raise ValueError('secret-exception-token-123')
    app, client = _client(tmp_path, monkeypatch, broken)
    try:
        with caplog.at_level('WARNING'):
            response = client.post('/api/ai/verdict', headers={'Authorization': 'secret-header-token-123'},
                                   json={'account': 'secret-input-account-123'})
        assert response.status_code == 500
        rows = events(caplog)
        assert rows[-1]['status_code'] == 500
        assert all(row['request_id'] == response.json()['error']['request_id'] for row in rows)
        assert any(row.get('exception_class') == 'ValueError' for row in rows)
        assert 'secret-' not in caplog.text
        assert 'Traceback' not in caplog.text
    finally:
        app.state.engine.close()


def production_order_fixture(monkeypatch, *, trade_id=1, warming=False):
    """Real installers, fake journal/engine; no lifespan, accounts or networking."""
    from fastapi import FastAPI, Request
    from seiltanzer import app as app_module
    from seiltanzer.ai_snapshot_materializer import install_ai_snapshot_materializer
    from seiltanzer.ai_snapshot_runtime_guard import install_ai_snapshot_runtime_guard
    from test_ai_snapshot_materializer import FakeEngine, snapshot
    engine = FakeEngine(trade_id)
    app = FastAPI()

    @app.post('/api/ai/verdict')
    async def route(request: Request):
        assert engine is not None  # Retain actual installer's engine discovery.
        trace = request.state.ai_request_trace
        with trace.span('route'):
            return {'ok': True, 'request_id': trace.request_id}

    @app.get('/other')
    async def unrelated():
        return {'unchanged': True}

    monkeypatch.setattr(app_module, 'build_snapshot', lambda _: snapshot(trade_id=trade_id))
    materializer = install_ai_snapshot_materializer(app)
    monkeypatch.setattr(materializer, '_event_reason', lambda: 'SNAPSHOT_WARMING' if warming else None)
    install_ai_snapshot_runtime_guard(app, materializer)
    return app, engine, materializer


def test_production_guard_starts_trace_before_blocked_journal_and_preserves_no_trade(monkeypatch, caplog):
    from fastapi.testclient import TestClient
    app, runtime, _ = production_order_fixture(monkeypatch, trade_id=None)
    began, release = threading.Event(), threading.Event()
    def active_trade():
        began.set()
        assert release.wait(3)
        return None
    monkeypatch.setattr(runtime.journal, 'active_trade', active_trade)
    replies = []
    def request():
        replies.append(TestClient(app).post('/api/ai/verdict'))
    worker = threading.Thread(target=request)
    with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
        worker.start()
        try:
            assert began.wait(1)
            assert worker.is_alive()
            rows = events(caplog)
            assert [(row['stage'], row['event']) for row in rows] == [('runtime_guard', 'start')]
        finally:
            release.set(); worker.join(2)
    assert not worker.is_alive()
    assert replies[0].status_code == 400
    assert replies[0].json() == {'ok': False, 'error': {
        'code': 'no_active_trade', 'message': 'Нет активной сделки для ИИ-разбора', 'retriable': False}}
    rows = events(caplog)
    assert len({row['request_id'] for row in rows}) == 1
    final = [row for row in rows if row['stage'] == 'response']
    assert len(final) == 1 and final[0]['status_code'] == 400
    assert rows[-1] == final[0]


@pytest.mark.parametrize('warming', [False, True])
def test_production_guard_and_materializer_share_one_trace_and_one_final_status(monkeypatch, caplog, warming):
    from fastapi.testclient import TestClient
    app, _, _ = production_order_fixture(monkeypatch, warming=warming)
    with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
        response = TestClient(app).post('/api/ai/verdict')
    assert response.status_code == (503 if warming else 200)
    rows = events(caplog)
    assert rows[0]['stage'] == 'runtime_guard' and rows[0]['event'] == 'start'
    assert len({row['request_id'] for row in rows}) == 1
    assert any(row['stage'] == 'preflight' for row in rows)
    if warming:
        assert response.json()['error']['code'] == 'ai_snapshot_warming'
        assert not any(row['stage'] == 'route' for row in rows)
    else:
        assert response.json()['request_id'] == rows[0]['request_id']
        assert any(row['stage'] == 'route' for row in rows)
    final = [row for row in rows if row['stage'] == 'response']
    assert len(final) == 1 and final[0]['status_code'] == response.status_code
    assert rows[-1] == final[0]


@pytest.mark.parametrize('cancel', [False, True])
def test_production_guard_error_and_cancellation_remain_visible_and_finish_once(monkeypatch, caplog, cancel):
    from starlette.requests import Request
    app, _, _ = production_order_fixture(monkeypatch)
    outer, inner = [middleware.kwargs['dispatch'] for middleware in app.user_middleware]
    failure = asyncio.CancelledError('secret-cancellation-token') if cancel else ValueError('secret-error-token')
    async def failing_next(request):
        raise failure
    async def run():
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/ai/verdict',
                           'headers': [], 'query_string': b''})
        async def inward(request):
            return await inner(request, failing_next)
        with pytest.raises(type(failure)) as raised:
            await outer(request, inward)
        assert raised.value is failure
    with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
        asyncio.run(run())
    rows = events(caplog)
    assert rows[0]['stage'] == 'runtime_guard' and rows[0]['event'] == 'start'
    assert len({row['request_id'] for row in rows}) == 1
    final = [row for row in rows if row['stage'] == 'response']
    assert len(final) == 1 and final[0]['status_code'] == (499 if cancel else 500)
    assert 'secret-' not in caplog.text


def test_production_guard_does_not_trace_or_probe_non_ai_requests(monkeypatch, caplog):
    from fastapi.testclient import TestClient
    app, _, materializer = production_order_fixture(monkeypatch)
    def forbidden():
        raise AssertionError('non-AI journal probe')
    monkeypatch.setattr(materializer, 'current_trade_id', forbidden)
    with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
        response = TestClient(app).get('/other')
    assert response.status_code == 200 and response.json() == {'unchanged': True}
    assert events(caplog) == []


def test_production_guard_reports_materializer_race_converted_actual_status(monkeypatch, caplog):
    from fastapi.responses import JSONResponse
    from starlette.requests import Request
    from seiltanzer.ai_snapshot_materializer import _warming_response
    app, _, materializer = production_order_fixture(monkeypatch)
    outer, inner = [middleware.kwargs['dispatch'] for middleware in app.user_middleware]
    async def run():
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/ai/verdict',
                           'headers': [], 'query_string': b''})
        async def snapshot_race(request):
            return JSONResponse(status_code=500, content={'original': 'snapshot_error'})
        async def inward(request):
            return await inner(request, snapshot_race)
        return await outer(request, inward)
    with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
        response = asyncio.run(run())
    assert response.status_code == 503
    assert response.body == _warming_response(materializer.status()).body
    rows = events(caplog)
    assert len({row['request_id'] for row in rows}) == 1
    final = [row for row in rows if row['stage'] == 'response']
    assert len(final) == 1 and final[0]['status_code'] == 503


@pytest.mark.parametrize('no_trade', [False, True])
def test_production_guard_trace_sink_failure_preserves_exact_early_response(monkeypatch, no_trade):
    from fastapi.testclient import TestClient
    from seiltanzer.ai_snapshot_materializer import _warming_response
    app, _, materializer = production_order_fixture(monkeypatch, trade_id=None if no_trade else 1, warming=True)
    def broken(*args, **kwargs):
        raise RuntimeError('secret-sink-token')
    monkeypatch.setattr(trace_module().LOGGER, 'warning', broken)
    response = TestClient(app).post('/api/ai/verdict')
    assert response.status_code == (400 if no_trade else 503)
    if no_trade:
        assert response.json() == {'ok': False, 'error': {
            'code': 'no_active_trade', 'message': 'Нет активной сделки для ИИ-разбора', 'retriable': False}}
    else:
        assert response.content == _warming_response(materializer.status()).body


def test_production_guard_actual_api_route_reuses_outer_request_id(tmp_path, monkeypatch, caplog):
    from test_ai_verdict_api import _client
    from seiltanzer import app as app_module
    from seiltanzer.ai_snapshot_materializer import install_ai_snapshot_materializer
    from seiltanzer.ai_snapshot_runtime_guard import install_ai_snapshot_runtime_guard
    calls = []
    def provider(snapshot):
        calls.append(1)
        return {'verdict': 'isolated', 'model': 'fixture'}
    app, client = _client(tmp_path, monkeypatch, provider)
    materializer = install_ai_snapshot_materializer(app)
    monkeypatch.setattr(materializer, '_event_reason', lambda: None)
    monkeypatch.setattr(app_module, 'build_snapshot', materializer.builder)
    install_ai_snapshot_runtime_guard(app, materializer)
    try:
        with caplog.at_level('WARNING', logger='seiltanzer.ai_request_trace'):
            response = client.post('/api/ai/verdict')
        assert response.status_code == 200
        assert calls == [1]
        rows = events(caplog)
        assert rows[0]['stage'] == 'runtime_guard' and rows[0]['event'] == 'start'
        assert all(row['request_id'] == response.json()['request_id'] for row in rows)
        assert {'runtime_guard', 'preflight', 'route', 'publication'} <= {row['stage'] for row in rows}
        final = [row for row in rows if row['stage'] == 'response']
        assert len(final) == 1 and final[0]['status_code'] == 200
    finally:
        app.state.engine.close()
