from __future__ import annotations

import pytest

from scripts import production_functional_smoke as smoke


def _ok_result() -> dict:
    return {
        "status": "OK",
        "no_placeholders": True,
        "production_authority": False,
        "errors": {},
    }


def test_macro_refresh_immediate_ok_does_not_poll(monkeypatch):
    def _unexpected_poll(*_args, **_kwargs):
        raise AssertionError("completed refresh must not poll runtime")

    monkeypatch.setattr(smoke, "assert_route", _unexpected_poll)
    assert smoke._wait_for_macro_numeric_refresh(_ok_result(), wait_sec=1.0) == _ok_result()


def test_macro_refresh_in_progress_waits_for_existing_worker(monkeypatch):
    states = [
        {
            "numeric": {
                "running": True,
                "last_error": None,
                "last_result": None,
            }
        },
        {
            "numeric": {
                "running": False,
                "last_error": None,
                "last_result": _ok_result(),
            }
        },
    ]

    def _status(path: str, *, timeout: float = 5.0):
        assert path == "/api/research/macro/status"
        assert timeout == 5.0
        return states.pop(0)

    monkeypatch.setattr(smoke, "assert_route", _status)
    result = smoke._wait_for_macro_numeric_refresh(
        {"status": "IN_PROGRESS", "research_only": True},
        wait_sec=5.0,
        poll_sec=0.0,
    )
    assert result == _ok_result()
    assert states == []


def test_macro_refresh_timeout_remains_failure(monkeypatch):
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        smoke,
        "assert_route",
        lambda *_args, **_kwargs: {
            "numeric": {"running": True, "last_error": None, "last_result": None}
        },
    )

    with pytest.raises(AssertionError, match="macro_numeric_refresh_timeout"):
        smoke._wait_for_macro_numeric_refresh(
            {"status": "IN_PROGRESS", "research_only": True},
            wait_sec=1.0,
            poll_sec=0.0,
        )


def test_macro_refresh_worker_error_fails_closed(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "assert_route",
        lambda *_args, **_kwargs: {
            "numeric": {
                "running": False,
                "last_error": "official_source_failed",
                "last_result": _ok_result(),
            }
        },
    )

    with pytest.raises(AssertionError):
        smoke._wait_for_macro_numeric_refresh(
            {"status": "IN_PROGRESS", "research_only": True},
            wait_sec=1.0,
            poll_sec=0.0,
        )


def test_macro_refresh_placeholder_result_fails_closed(monkeypatch):
    bad = _ok_result()
    bad["no_placeholders"] = False
    monkeypatch.setattr(
        smoke,
        "assert_route",
        lambda *_args, **_kwargs: {
            "numeric": {"running": False, "last_error": None, "last_result": bad}
        },
    )

    with pytest.raises(AssertionError):
        smoke._wait_for_macro_numeric_refresh(
            {"status": "IN_PROGRESS", "research_only": True},
            wait_sec=1.0,
            poll_sec=0.0,
        )
