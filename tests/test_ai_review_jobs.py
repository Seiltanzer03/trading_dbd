import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest


def test_background_api_returns_quickly_and_polls_one_review(tmp_path, monkeypatch):
    from test_ai_verdict_api import _client
    from seiltanzer.ai_review_jobs import install_ai_review_jobs
    from seiltanzer.ai_provider_guard import bounded_provider_call
    from seiltanzer.ai_request_trace import current_trace
    calls = []
    def provider(snapshot):
        # An old trace must not spend a new synchronous request allowance.
        current_trace().started -= 12
        def complete(_):
            calls.append(1)
            time.sleep(.12)
            return {'verdict': 'complete independent review', 'model': 'test'}
        with ThreadPoolExecutor(max_workers=1) as pool:
            return bounded_provider_call(complete, snapshot, executor=pool)
    app, _ = _client(tmp_path, monkeypatch, provider)
    install_ai_review_jobs(app)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            started = time.monotonic()
            response = await c.post('/api/ai/verdict')
            assert response.status_code == 202
            assert time.monotonic()-started < .1
            url = response.json()['review_job']['poll_url']
            again = await c.post('/api/ai/verdict')
            assert again.json()['review_job']['poll_url'] == url
            assert (await c.get(url)).status_code == 202
            for _ in range(100):
                response = await c.get(url)
                if response.status_code != 202: break
                await asyncio.sleep(.01)
            assert response.status_code == 200, response.text
            assert response.json()['mode'] == 'llm'
            assert calls == [1]
            assert (await c.get(url)).json() == response.json()
            assert calls == [1]
            trade = app.state.engine.journal.active_trade()
            app.state.engine.journal.close_trade(trade['id'], 0.)
            stale = await c.get(url)
            assert stale.status_code == 409
            assert 'verdict' not in stale.json()
            assert calls == [1]
    try: asyncio.run(run())
    finally: app.state.engine.close()


def test_job_guard_rejects_changed_position_and_expired_result():
    from fastapi.responses import JSONResponse
    from seiltanzer.ai_review_jobs import ReviewJobs, bind_delivery_guard
    from seiltanzer.position_state import StaleDecisionError
    async def run():
        jobs = ReviewJobs()
        changed = False
        def guard():
            if changed: raise StaleDecisionError('position changed')
        async def work():
            bind_delivery_guard(guard)
            return JSONResponse({'ok': True, 'verdict': 'original'})
        pending = jobs.start(work)
        job_id = json.loads(pending.body)['review_job']['job_id']
        await asyncio.sleep(.01)
        assert (await jobs.poll(job_id)).status_code == 200
        changed = True
        invalid = await jobs.poll(job_id)
        assert invalid.status_code == 409
        assert json.loads(invalid.body)['error']['code'] == 'stale_decision'
        assert 'original' not in invalid.body.decode()
        jobs.started -= 121
        assert (await jobs.poll(job_id)).status_code == 410
        assert (await jobs.poll('unknown')).status_code == 404
    asyncio.run(run())


def test_failed_job_is_structured_and_never_retried():
    from seiltanzer.ai_review_jobs import ReviewJobs
    calls = []
    async def run():
        jobs = ReviewJobs()
        async def work():
            calls.append(1)
            raise ValueError('private failure')
        job_id = json.loads(jobs.start(work).body)['review_job']['job_id']
        await asyncio.sleep(.01)
        for _ in range(2):
            response = await jobs.poll(job_id)
            assert response.status_code == 500
            assert 'private failure' not in response.body.decode()
        assert calls == [1]
    asyncio.run(run())


def test_detached_provider_budget_and_transport_are_bounded(monkeypatch):
    from seiltanzer.ai_review_jobs import detached_review_scope, provider_transport_timeout
    from seiltanzer import ai_provider_guard as guard
    from seiltanzer.ai_request_trace import AIRequestTrace
    monkeypatch.delenv('AI_PROVIDER_TIMEOUT_SEC', raising=False)
    trace = AIRequestTrace()
    trace.started -= 20
    with trace.activate(), detached_review_scope():
        assert provider_transport_timeout() == 45
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert guard.bounded_provider_call(lambda _: 42, {}, executor=pool) == 42
        monkeypatch.setenv('AI_PROVIDER_TIMEOUT_SEC', '999')
        assert provider_transport_timeout() == 45
        monkeypatch.setenv('AI_PROVIDER_TIMEOUT_SEC', '6')
        assert provider_transport_timeout() == 6
    assert provider_transport_timeout() == 8


def test_actual_independent_provider_transport_uses_detached_bound(monkeypatch):
    from seiltanzer.ai_review_jobs import detached_review_scope
    from seiltanzer.ai_runtime_report_v20 import request_explanation_with_shadow
    from test_ai_provider_explanation import _snapshot
    from seiltanzer import ai_provider_transport
    from seiltanzer.llm_decision_shadow import VALID_POLICIES
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only')
    monkeypatch.delenv('AI_PROVIDER_TIMEOUT_SEC', raising=False)
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs['timeout'])
        shadow = {'policy': 'HOLD', 'confidence': .7,
                  'policy_scores': {policy: float(policy == 'HOLD') for policy in VALID_POLICIES},
                  'reason_ru': 'Независимый разбор', 'key_evidence': [],
                  'counter_evidence': [], 'evidence_families': [],
                  'invalidation_conditions': [], 'family_assessments': {}}
        return httpx.Response(200, json={'choices': [{'message': {
            'content': json.dumps({'shadow_decision': shadow})}}]},
            request=httpx.Request('POST', url))
    monkeypatch.setattr(ai_provider_transport, 'post_json', post)
    snapshot = _snapshot()
    snapshot['policy_manager']['selection_rule'] = {'cvar_floor_r': -1., 'eligible': ['HOLD']}
    with detached_review_scope():
        result = request_explanation_with_shadow(snapshot, authoritative_snapshot=snapshot)
    assert calls == [45.]
    assert result['llm_shadow_decision']['status'] == 'ok'


def test_cancelled_poll_does_not_cancel_admitted_review():
    from fastapi.responses import JSONResponse
    from seiltanzer.ai_review_jobs import ReviewJobs
    async def run():
        jobs = ReviewJobs()
        release = asyncio.Event()
        async def work():
            await release.wait()
            return JSONResponse({'ok': True})
        job_id = json.loads(jobs.start(work).body)['review_job']['job_id']
        poll = asyncio.create_task(jobs.poll(job_id))
        poll.cancel()
        with pytest.raises(asyncio.CancelledError): await poll
        assert not jobs.task.cancelled()
        release.set()
        await asyncio.sleep(.01)
        assert (await jobs.poll(job_id)).status_code == 200
    asyncio.run(run())


def test_old_poll_cannot_poison_a_new_job_during_delivery_validation():
    import threading
    from fastapi.responses import JSONResponse
    from seiltanzer.ai_review_jobs import ReviewJobs, bind_delivery_guard
    from seiltanzer.position_state import StaleDecisionError
    entered, release = threading.Event(), threading.Event()
    async def run():
        jobs = ReviewJobs()
        def guard():
            entered.set()
            release.wait(2)
            raise StaleDecisionError('old job stale')
        async def old():
            bind_delivery_guard(guard)
            return JSONResponse({'ok': True, 'verdict': 'old'})
        async def new():
            return JSONResponse({'ok': True, 'verdict': 'new'})
        old_id = json.loads(jobs.start(old).body)['review_job']['job_id']
        await asyncio.sleep(.01)
        polling = asyncio.create_task(jobs.poll(old_id))
        while not entered.is_set(): await asyncio.sleep(.001)
        new_id = json.loads(jobs.start(new).body)['review_job']['job_id']
        release.set()
        await polling
        await asyncio.sleep(.01)
        response = await jobs.poll(new_id)
        assert response.status_code == 200
        assert json.loads(response.body)['verdict'] == 'new'
    asyncio.run(run())


def test_expired_background_job_cannot_publish_after_client_deadline():
    from fastapi.responses import JSONResponse
    from seiltanzer.ai_review_jobs import ReviewJobs, assert_review_deadline
    from seiltanzer.position_state import StaleDecisionError
    async def run():
        jobs = ReviewJobs()
        async def work():
            jobs.started -= 121
            with pytest.raises(StaleDecisionError, match='deadline'):
                assert_review_deadline()
            return JSONResponse({'ok': False})
        jobs.start(work)
        await jobs.task
    asyncio.run(run())
