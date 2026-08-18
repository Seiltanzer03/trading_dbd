"""Low-frequency automatic refresh of official FOMC statements.

The official page fetch is cheap; semantic extraction is SHA-cached by
MacroDataFactory, so an unchanged statement does not call the LLM.  This runtime
is delayed until after application/AI startup and never runs in a request path.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .fomc_official_source import refresh_latest_fomc
from .research_llm_cost_guard import guarded_macro_extractor


FOMC_RUNTIME_VERSION = "fomc-official-runtime-v1"


class FOMCOfficialRuntime:
    def __init__(self, factory, *, poll_sec: float = 3600.0,
                 startup_delay_sec: float = 120.0) -> None:
        self.factory = factory
        self.poll_sec = max(900.0, float(poll_sec))
        self.startup_delay_sec = max(30.0, float(startup_delay_sec))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.running = False
        self.last_started_at: float | None = None
        self.last_finished_at: float | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="fomc-official-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "IN_PROGRESS", "research_only": True}
            self.running = True
        self.last_started_at = time.time()
        try:
            try:
                result = refresh_latest_fomc(
                    self.factory, extractor=guarded_macro_extractor)
                self.last_result = result
                self.last_error = None if result.get("status") in {"VALID", "CACHED"} else str(result.get("reason") or result.get("status"))[:180]
            except (RuntimeError, ValueError) as exc:
                self.last_error = f"{type(exc).__name__}:{str(exc)[:180]}"
                self.last_result = {
                    "status": "UNAVAILABLE", "reason": self.last_error,
                    "research_only": True, "production_authority": False,
                }
            return self.last_result
        finally:
            self.last_finished_at = time.time()
            with self._lock:
                self.running = False

    def _run(self) -> None:
        if self._stop.wait(self.startup_delay_sec):
            return
        while not self._stop.is_set():
            self.refresh()
            self._wake.wait(self.poll_sec)
            self._wake.clear()

    def status(self) -> dict[str, Any]:
        return {
            "contract_version": FOMC_RUNTIME_VERSION,
            "running": self.running,
            "poll_sec": self.poll_sec,
            "startup_delay_sec": self.startup_delay_sec,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "official_source_only": True,
            "semantic_llm_sha_cached": True,
            "research_only": True,
            "production_authority": False,
        }
