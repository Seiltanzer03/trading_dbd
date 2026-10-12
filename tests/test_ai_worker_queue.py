import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest


def test_ai_review_worker_does_not_wait_behind_market_executor():
    from seiltanzer.ai_request_trace import AIRequestTrace
    release=threading.Event();started=threading.Event()
    def feed():started.set();release.wait(3)
    async def run():
        loop=asyncio.get_running_loop();pool=ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)
        pending=loop.run_in_executor(None,feed)
        assert started.wait(1)
        trace=AIRequestTrace();private=ThreadPoolExecutor(max_workers=1);trace.executor=private
        try:
            result=await asyncio.wait_for(trace.to_thread('ensemble',lambda:42),.5)
            assert result==42
        finally:release.set();await pending;private.shutdown(wait=True)
    asyncio.run(run())


def test_cancelled_http_wait_retains_worker_admission_until_actual_completion():
    from seiltanzer.ai_request_trace import AIRequestTrace
    from seiltanzer.ai_review_worker import review_executor
    release=threading.Event();started=threading.Event()
    def work():started.set();release.wait(3)
    async def run():
        pool=review_executor();trace=AIRequestTrace();trace.executor=pool
        task=asyncio.create_task(trace.to_thread('provider',work))
        try:
            while not started.is_set():await asyncio.sleep(.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert pool.busy
            with pytest.raises(RuntimeError,match='AI_REVIEW_WORKER_BUSY'):
                pool.submit(lambda:None)
        finally:
            release.set()
            while pool.busy:await asyncio.sleep(.001)
        assert await trace.to_thread('ensemble',lambda:43)==43
    asyncio.run(run())


def test_real_api_review_finishes_when_background_executor_is_fully_occupied(tmp_path,monkeypatch):
    import httpx
    from test_ai_verdict_api import _client
    app,_=_client(tmp_path,monkeypatch,lambda _: {'verdict':'isolated','model':'fixture'})
    release=threading.Event();started=threading.Event()
    def feed():started.set();release.wait(4)
    async def run():
        loop=asyncio.get_running_loop();loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        pending=loop.run_in_executor(None,feed)
        assert started.wait(1)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await asyncio.wait_for(client.post('/api/ai/verdict'),2.)
                assert response.status_code==200,response.text
        finally:release.set();await pending
    try:asyncio.run(run())
    finally:app.state.engine.close()


def test_busy_review_worker_refuses_api_before_provider_or_publication(tmp_path,monkeypatch):
    from seiltanzer.ai_review_worker import review_executor
    from test_ai_verdict_api import _client
    def forbidden(_):raise AssertionError('busy review admitted provider')
    app,client=_client(tmp_path,monkeypatch,forbidden)
    release=threading.Event();started=threading.Event()
    def held():started.set();release.wait(3)
    pool=review_executor();pending=pool.submit(held)
    assert started.wait(1)
    try:
        response=client.post('/api/ai/verdict')
        assert response.status_code==429
        assert response.json()['error']['code']=='ai_request_in_progress'
    finally:release.set();pending.result(1);app.state.engine.close()


@pytest.mark.parametrize('stage',['provider','ensemble','publication'])
def test_stage_admission_race_returns_busy_instead_of_provider_or_journal_failure(tmp_path,monkeypatch,stage):
    from seiltanzer.ai_review_worker import AIReviewWorkerBusy
    from seiltanzer.ai_request_trace import AIRequestTrace
    from test_ai_verdict_api import _client
    original=AIRequestTrace.to_thread
    async def raced(self,current,function,*args,**kwargs):
        if current==stage:raise AIReviewWorkerBusy('AI_REVIEW_WORKER_BUSY')
        return await original(self,current,function,*args,**kwargs)
    app,client=_client(tmp_path,monkeypatch,lambda _: {'verdict':'isolated','model':'fixture'})
    monkeypatch.setattr(AIRequestTrace,'to_thread',raced)
    try:
        response=client.post('/api/ai/verdict')
        assert response.status_code==429,f'Unexpected HTTP {response.status_code}'
        assert response.json()['error']['code']=='ai_request_in_progress'
    finally:app.state.engine.close()
