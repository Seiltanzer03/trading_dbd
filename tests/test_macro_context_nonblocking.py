import threading
from types import SimpleNamespace

import pytest

from seiltanzer.macro_t0_context import build_macro_t0_context


@pytest.mark.parametrize('busy_store', ['factory', 'numeric', 'fomc'])
def test_ai_macro_context_does_not_wait_for_background_store(busy_store):
    locks = {name: threading.RLock() for name in ('factory', 'numeric', 'fomc')}
    factory = SimpleNamespace(_lock=locks['factory'],
        numeric_release_store=SimpleNamespace(_lock=locks['numeric']),
        fomc_deterministic_store=SimpleNamespace(_lock=locks['fomc']))
    acquired, release = threading.Event(), threading.Event()
    def hold():
        with locks[busy_store]:
            acquired.set()
            release.wait(2)
    worker = threading.Thread(target=hold)
    worker.start()
    assert acquired.wait(1)
    try:
        result = build_macro_t0_context(factory, 123., nonblocking=True)
        assert result['available'] is False
        assert result['reason'] == 'MACRO_CONTEXT_STORE_BUSY'
        assert result['candidate_vector'] == {}
        assert result['production_authority'] is False
        # Earlier acquired locks must be released after a later busy store.
        for name, lock in locks.items():
            if name != busy_store:
                probe_result = []
                def probe():
                    available = lock.acquire(blocking=False)
                    probe_result.append(available)
                    if available:
                        lock.release()
                probe_worker = threading.Thread(target=probe)
                probe_worker.start()
                probe_worker.join(1)
                assert probe_result == [True]
    finally:
        release.set()
        worker.join(2)


def test_nonblocking_macro_context_keeps_causal_semantics():
    import sqlite3
    from seiltanzer.macro_data_factory import MacroDataFactory
    runtime = SimpleNamespace(_lock=threading.RLock(),
        _conn=sqlite3.connect(':memory:'))
    runtime._conn.row_factory = sqlite3.Row
    factory = MacroDataFactory(runtime)
    try:
        expected = build_macro_t0_context(factory, 123.)
        assert build_macro_t0_context(factory, 123., nonblocking=True) == expected
    finally:
        runtime._conn.close()


def test_nonblocking_macro_context_rejects_store_without_lock():
    factory = SimpleNamespace(_lock=threading.RLock(),
                              numeric_release_store=object())
    result = build_macro_t0_context(factory, 123., nonblocking=True)
    assert result['reason'] == 'MACRO_CONTEXT_LOCK_UNAVAILABLE'
    assert result['available'] is False
    assert result['candidate_vector'] == {}
