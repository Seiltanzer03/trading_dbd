from __future__ import annotations

import seiltanzer.__main__ as entrypoint


def test_startup_diagnostics_are_bounded_and_flush_phase_markers(monkeypatch, capsys):
    calls: list[tuple[int, bool]] = []
    times = iter((100.0, 100.0, 112.345))
    monkeypatch.setattr(entrypoint.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        entrypoint.faulthandler,
        "dump_traceback_later",
        lambda seconds, *, repeat: calls.append((seconds, repeat)),
    )

    started = entrypoint._arm_startup_diagnostics()
    entrypoint._startup_marker(started, "storage.runtime.begin")

    assert calls == [(entrypoint._STARTUP_TRACEBACK_AFTER_SEC, False)]
    assert capsys.readouterr().out.splitlines() == [
        "STARTUP_PHASE phase=begin elapsed_sec=0.000",
        "STARTUP_PHASE phase=storage.runtime.begin elapsed_sec=12.345",
    ]
