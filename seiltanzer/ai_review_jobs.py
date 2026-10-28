"""One bounded manual review across short HTTP requests; no automatic retry."""
from __future__ import annotations

import asyncio
import contextlib
from contextvars import ContextVar
import os
import time
import uuid

from fastapi.responses import JSONResponse

from .ai_api import error_body
from .position_state import StaleDecisionError

_DETACHED = ContextVar('ai_detached_review', default=False)
_JOB = ContextVar('ai_review_job', default=None)
MAX_JOB_AGE_SEC = 120.


@contextlib.contextmanager
def detached_review_scope():
    token = _DETACHED.set(True)
    try:
        yield
    finally:
        _DETACHED.reset(token)


def detached_provider_timeout():
    if not _DETACHED.get():
        return None
    try:
        value = float(os.environ.get('AI_PROVIDER_TIMEOUT_SEC', '45'))
    except (TypeError, ValueError):
        value = 45.
    if not 3. <= value <= 45.:
        value = 3. if value < 3. else 45.
    return value


def provider_transport_timeout():
    return detached_provider_timeout() or 8.


def bind_delivery_guard(guard):
    job = _JOB.get()
    if job is not None:
        job.guard = guard


def assert_review_deadline():
    job = _JOB.get()
    if job is not None and time.monotonic() - job.started >= MAX_JOB_AGE_SEC:
        raise StaleDecisionError('background review deadline expired')


class ReviewJobs:
    """Keep only the latest opaque job in memory, owned by the app event loop."""
    def __init__(self):
        self.task = None
        self.job_id = None
        self.started = 0.
        self.guard = None
        self.invalid = None

    def _pending(self):
        return JSONResponse(status_code=202, content={
            'ok': True, 'status': 'pending', 'review_job': {
                'job_id': self.job_id,
                'poll_url': '/api/ai/reviews/' + self.job_id,
                'retry_after_sec': 1,
            }}, headers={'Retry-After': '1', 'Cache-Control': 'no-store'})

    def start(self, work):
        if self.task is None or self.task.done():
            self.job_id = uuid.uuid4().hex
            self.started = time.monotonic()
            self.guard = self.invalid = None

            async def run():
                token = _JOB.set(self)
                try:
                    with detached_review_scope():
                        return await work()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Do not serialize raw provider/DB errors into public jobs.
                    return JSONResponse(status_code=500, content=error_body(
                        'ai_internal_error', 'Не удалось завершить ИИ-разбор',
                        'ai-' + self.job_id[:20], retriable=False))
                finally:
                    _JOB.reset(token)
            self.task = asyncio.create_task(run())
        return self._pending()

    async def poll(self, job_id):
        if job_id != self.job_id or self.task is None:
            return JSONResponse(status_code=404, content=error_body(
                'ai_review_not_found', 'Расчёт не найден: запросите новый разбор',
                'ai-' + uuid.uuid4().hex[:20], retriable=True))
        if time.monotonic() - self.started >= MAX_JOB_AGE_SEC:
            return JSONResponse(status_code=410, content=error_body(
                'ai_review_expired', 'Срок выдачи расчёта истёк: запросите новый разбор',
                'ai-' + self.job_id[:20], retriable=True))
        if not self.task.done():
            return self._pending()
        if self.task.cancelled():
            return JSONResponse(status_code=503, content=error_body(
                'ai_review_cancelled', 'Расчёт прерван: запросите новый разбор',
                'ai-' + self.job_id[:20], retriable=True))
        if self.invalid is not None:
            return self.invalid
        response = self.task.result()
        if response.status_code == 200 and self.guard is not None:
            from .ai_review_worker import review_executor, AIReviewWorkerBusy
            try:
                future = review_executor().submit(self.guard)
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), 3.)
            except (AIReviewWorkerBusy, asyncio.TimeoutError):
                return self._pending()
            except StaleDecisionError:
                if job_id != self.job_id:
                    return await self.poll(job_id)
                self.invalid = JSONResponse(status_code=409, content=error_body(
                    'stale_decision', 'Данные сделки изменились: запросите новый разбор',
                    'ai-' + self.job_id[:20], retriable=True))
                return self.invalid
            except Exception:
                return JSONResponse(status_code=503, content=error_body(
                    'ai_review_validation_unavailable', 'Не удалось проверить актуальность разбора',
                    'ai-' + self.job_id[:20], retriable=True))
            if job_id != self.job_id:
                return await self.poll(job_id)
        response.headers['Cache-Control'] = 'no-store'
        return response


def install_ai_review_jobs(app):
    existing = getattr(app.state, 'ai_review_jobs', None)
    if existing is not None:
        return existing
    jobs = ReviewJobs()
    app.state.ai_review_jobs = jobs

    @app.get('/api/ai/reviews/{job_id}')
    async def poll_ai_review(job_id: str):
        return await jobs.poll(job_id)

    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(inner_app):
        async with original_lifespan(inner_app):
            try:
                yield
            finally:
                if jobs.task is not None and not jobs.task.done():
                    jobs.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await jobs.task
    app.router.lifespan_context = lifespan
    return jobs
