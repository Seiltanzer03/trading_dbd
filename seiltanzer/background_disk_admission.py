"""Admission for optional bulk writers; no decision or trading authority."""
from __future__ import annotations

import shutil
import time

MIN_FREE_BYTES = 1024**3 + 32*1024**2
REASON = 'DISK_HEADROOM_UNAVAILABLE'


def disk_state(engine):
    settings = getattr(engine, 'settings', None)
    demo = getattr(settings, 'demo', None)
    result = dict(contract_version='background-disk-admission-v1',
                  minimum_free_bytes=MIN_FREE_BYTES, free_bytes=None,
                  pause_background=True, reason=REASON)
    if demo is True:
        return dict(result, pause_background=False, reason='DEMO_MODE')
    path = getattr(settings, 'data_dir', None)
    if demo is not False or not isinstance(path, str) or not path:
        return result
    try:
        free = shutil.disk_usage(path).free
    except (OSError, ValueError):
        return result
    if type(free) is not int or free < 0:
        return result
    return dict(result, free_bytes=free, pause_background=free < MIN_FREE_BYTES,
                reason=REASON if free < MIN_FREE_BYTES else 'AVAILABLE')


def passive_step(engine):
    """Gate the entire installed step chain before any capture/materialization."""
    pressure = disk_state(engine)
    if pressure['pause_background']:
        passive = engine.passive
        passive._last_skip_reason = REASON
        passive._last_step_error = REASON
        passive._last_step_ts = time.time()
        return dict(skipped=True, reason=REASON, created=[], disk_pressure=pressure)
    return engine.passive.step()


def apply_research_pause(state, engine):
    pressure = disk_state(engine)
    state['disk_pressure'] = pressure
    state['disk_pause_active'] = pressure['pause_background']
    if not pressure['pause_background']:
        return False
    state['current_phase'] = 'disk_pressure_pause'
    state['maintenance_running'] = False
    state['maintenance_phase'] = None
    return True
