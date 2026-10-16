"""Present full local-edge evidence without HTTP-time SQLite or recalculation.

The existing local worker owns writes and report refresh. Missing/stale reports
are explicit; no history is truncated and no evidence/decision gate changes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import threading
import time
import types

VERSION = 'g1m-local-edge-nonblocking-v1'
MAX_REPORT_BYTES = 8 * 1024**2


@dataclass(frozen=True)
class _Report:
    raw: bytes
    captured_ts: float
    generation: int


@dataclass(frozen=True)
class _State:
    generation: int = 0
    active: int = 0
    report: _Report | None = None
    last_error: str | None = None


def _cold(error):
    return dict(contract_version='g1m-local-management-edge-v2',
        verdict='LOCAL_EDGE_UNAVAILABLE' if error else 'LOCAL_EDGE_BUILDING',
        research_only=True, production_authority=False, production_directional_authority=False,
        auto_promotion=False, may_trigger_exit_or_close=False, edge_claim_allowed=False,
        management_shadow={'activated':False,'auto_activation':False},
        active_edge_decision_attribution=dict(production_authority=False,
            production_directional_authority=False,decision_weight_applied=False,
            auto_promotion=False,automatic_execution=False,may_trigger_exit_or_close=False,
            edge_claim_allowed=False))


def install_local_edge_nonblocking(runtime):
    """Install once, without startup SQL; refresh after existing worker resolution."""
    if getattr(runtime, '_g1m_local_edge_nonblocking_version', None) == VERSION:
        return
    original_edge = runtime.edge
    original_materialize = runtime.materialize_windows
    original_resolve = runtime.resolve_due
    state, guard = _State(), threading.Lock()

    def refresh(self):
        nonlocal state
        current = state
        if current.active:
            return False
        try:
            body = dict(original_edge())
            body.pop('report_materialization', None)
            raw = json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
            if len(raw) > MAX_REPORT_BYTES:
                raise ValueError('Local edge presentation exceeds byte budget')
            report = _Report(raw,time.time(),current.generation)
        except Exception as exc:
            with guard:
                if state.generation == current.generation:
                    state = replace(state,last_error=type(exc).__name__)
            raise
        with guard:
            if state.generation != current.generation or state.active:
                return False  # Never let an older computation replace new evidence.
            state = replace(state,report=report,last_error=None)
        return True

    def mutate(operation, *args, **kwargs):
        nonlocal state
        with guard:
            state = replace(state,generation=state.generation+1,active=state.active+1,last_error=None)
            generation = state.generation
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            with guard:
                if state.generation == generation:
                    state = replace(state,last_error=type(exc).__name__)
            raise
        finally:
            with guard:
                state = replace(state,active=state.active-1)

    def materialize(self, *args, **kwargs):
        return mutate(original_materialize,*args,**kwargs)

    def resolve(self, *args, **kwargs):
        result = mutate(original_resolve,*args,**kwargs)
        refresh(self)
        return result

    def encoded(self):
        # One immutable state reference: readers never acquire even the tiny
        # presentation-state lock, let alone the shared research/SQLite lock.
        current = state
        report = current.report
        dirty = report is None or current.active > 0 or report.generation != current.generation
        meta = dict(version=VERSION,cached_snapshot=report is not None,cache_dirty=dirty,
            request_time_sqlite_access=False,request_time_recalculation=False,
            snapshot_captured_ts=report.captured_ts if report else None,
            snapshot_age_sec=max(0.0,time.time()-report.captured_ts) if report else None,
            presentation_state=('UNAVAILABLE' if report is None and current.last_error else
                                'BUILDING' if dirty else 'READY'),last_error_class=current.last_error)
        raw = report.raw if report else json.dumps(_cold(current.last_error),separators=(',',':')).encode()
        return raw[:-1]+b',"report_materialization":'+json.dumps(meta,separators=(',',':'),allow_nan=False).encode()+b'}'

    def edge(self):
        return json.loads(encoded(self))  # Fresh mutable caller copy, never cache aliasing.

    runtime.edge = types.MethodType(edge,runtime)
    runtime.materialized_edge_json = types.MethodType(encoded,runtime)
    runtime.refresh_edge_report = types.MethodType(refresh,runtime)
    runtime.materialize_windows = types.MethodType(materialize,runtime)
    runtime.resolve_due = types.MethodType(resolve,runtime)
    runtime._g1m_local_edge_nonblocking_version = VERSION
