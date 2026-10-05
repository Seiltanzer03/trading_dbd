"""Failure-isolated request timings; no request or financial payloads are owned."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import time

from .ai_api import request_id

LOGGER = logging.getLogger('seiltanzer.ai_request_trace')
_CURRENT = ContextVar('ai_request_trace', default=None)
_STAGES = frozenset(('runtime_guard', 'preflight', 'route', 'cached_snapshot', 'position_finalization',
    'family_bundle', 'runtime_context', 'macro_context', 'reaction_capture',
    'regime_management', 'review_identity', 'provider', 'ensemble', 'publication',
    'response', 'snapshot_error', 'provider_fallback', 'internal_error',
    'stale_decision', 'journal_error'))


def current_trace():
    return _CURRENT.get()


class AIRequestTrace:
    def __init__(self):
        self.request_id = request_id()
        self.started = time.monotonic()

    def _emit(self, stage, event, *, since=None, exc=None, status_code=None):
        # Only code-owned stage/event names and numeric diagnostics reach logs.
        try:
            if stage not in _STAGES:
                return
            now = time.monotonic()
            row = dict(request_id=self.request_id, stage=stage, event=event,
                       elapsed_ms=round(max(0., now-self.started)*1000., 3))
            if since is not None:
                row['duration_ms'] = round(max(0., now-since)*1000., 3)
            if exc is not None:
                row['exception_class'] = type(exc).__name__[:80]
            if status_code is not None:
                row['status_code'] = int(status_code)
            LOGGER.warning(json.dumps(row, separators=(',', ':'), ensure_ascii=True))
        except Exception:
            # An unavailable handler cannot break successful work or hide errors.
            pass

    @contextmanager
    def activate(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)

    @contextmanager
    def span(self, stage):
        started = time.monotonic()
        self._emit(stage, 'start')
        try:
            yield
        except BaseException as exc:
            self._emit(stage, 'end', since=started, exc=exc)
            raise
        else:
            self._emit(stage, 'end', since=started)

    def error(self, stage, exc):
        self._emit(stage, 'error', exc=exc)

    def submitted_worker(self, stage, function, *args, **kwargs):
        submitted = time.monotonic()
        self._emit(stage, 'submitted')
        # Keep exactly one end event regardless of return/error/cancellation.
        def measured_worker():
            started = time.monotonic()
            self._emit(stage, 'worker_start', since=submitted)
            error = None
            try:
                return function(*args, **kwargs)
            except BaseException as exc:
                error = exc
                raise
            finally:
                self._emit(stage, 'worker_end', since=started, exc=error)
        return measured_worker

    async def to_thread(self, stage, function, *args, **kwargs):
        return await asyncio.to_thread(self.submitted_worker(stage, function, *args, **kwargs))

    def finish(self, status_code):
        self._emit('response', 'finish', status_code=status_code)
