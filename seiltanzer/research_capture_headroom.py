"""Admission for optional large research rows; trade and outcome paths are separate."""
from pathlib import Path
import shutil

CONTRACT = 'research-capture-headroom-v1'
OPERATIONAL_RESERVE_BYTES = 1024**3 + 32*1024**2
BATCH_MARGIN_BYTES = 128*1024**2
RESUME_MARGIN_BYTES = 128*1024**2


def capture_admission(database, *, previously_paused=False, batch_bytes=0):
    """Measure actual available bytes, with a higher threshold after a pause."""
    if type(batch_bytes) is not int or batch_bytes < 0:
        raise ValueError('Invalid optional research batch size')
    minimum = (OPERATIONAL_RESERVE_BYTES + max(BATCH_MARGIN_BYTES, batch_bytes)
               + (RESUME_MARGIN_BYTES if previously_paused else 0))
    result = dict(contract=CONTRACT, admitted=False, free_bytes=None,
                  minimum_free_bytes=minimum, reason='RESEARCH_CAPTURE_CAPACITY_UNAVAILABLE')
    if str(database) == ':memory:':
        return dict(result, admitted=True, minimum_free_bytes=0, reason='IN_MEMORY_DATABASE')
    try:
        if database is None or not str(database): return result
        free = shutil.disk_usage(Path(database).resolve().parent).free
        if type(free) is not int or free < 0: return result
    except (OSError, ValueError):
        return result
    admitted = free >= minimum
    return dict(result, admitted=admitted, free_bytes=free,
                reason='ADMITTED' if admitted else 'RESEARCH_CAPTURE_PAUSED_LOW_STORAGE')
