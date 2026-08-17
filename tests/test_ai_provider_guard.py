from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from seiltanzer import ai_provider_guard


def test_provider_timeout_configuration_is_bounded(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SEC", "0.1")
    assert ai_provider_guard.provider_timeout_sec() == 5.0
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SEC", "999")
    assert ai_provider_guard.provider_timeout_sec() == 45.0
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SEC", "bad")
    assert ai_provider_guard.provider_timeout_sec() == ai_provider_guard.DEFAULT_PROVIDER_TIMEOUT_SEC
    monkeypatch.delenv("AI_PROVIDER_TIMEOUT_SEC", raising=False)
    assert ai_provider_guard.provider_timeout_sec() == 25.0


def test_bounded_provider_call_returns_fast_result():
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = ai_provider_guard.bounded_provider_call(
            lambda snapshot: {"verdict": snapshot["value"]},
            {"value": "ok"},
            timeout_sec=0.2,
            executor=executor,
        )
    assert result == {"verdict": "ok"}


def test_bounded_provider_call_times_out_without_waiting_for_provider():
    executor = ThreadPoolExecutor(max_workers=1)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="provider_timeout_after"):
            ai_provider_guard.bounded_provider_call(
                lambda snapshot: (time.sleep(0.15) or {"verdict": "late"}),
                {},
                timeout_sec=0.02,
                executor=executor,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 0.10
    finally:
        executor.shutdown(wait=True)
