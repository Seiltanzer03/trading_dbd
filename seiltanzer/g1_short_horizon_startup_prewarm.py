"""Defer G.1S presentation prewarm until the HTTP server startup boundary."""
from __future__ import annotations

import threading
from typing import Any

from .g1_short_horizon_evidence_nonblocking import (
    prewarm_g1_short_horizon_evidence,
)
from .g1_short_horizon_status_nonblocking import (
    prewarm_g1_short_horizon_status,
)


STARTUP_PREWARM_VERSION = "g1s-startup-prewarm-v1"


def install_g1_short_horizon_startup_prewarm(app: Any, runtime: Any) -> None:
    """Launch durable presentation reads on one daemon after startup returns."""
    if getattr(app.state, "g1s_startup_prewarm_installed", False):
        return
    state: dict[str, Any] = {
        "version": STARTUP_PREWARM_VERSION,
        "state": "PENDING",
        "errors": {},
        "thread": None,
    }
    app.state.g1s_startup_prewarm = state
    app.state.g1s_startup_prewarm_installed = True

    def run() -> None:
        errors: dict[str, str] = {}
        for name, prewarm in (
            ("status", prewarm_g1_short_horizon_status),
            ("evidence", prewarm_g1_short_horizon_evidence),
        ):
            try:
                prewarm(runtime)
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {str(exc)[:240]}"
        state["errors"] = errors
        state["state"] = "READY" if not errors else "DEGRADED"

    def start() -> None:
        thread = state.get("thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return
        if state.get("state") == "READY":
            return
        state["state"] = "BUILDING"
        thread = threading.Thread(
            target=run, name="g1s-startup-prewarm", daemon=True,
        )
        state["thread"] = thread
        thread.start()

    app.add_event_handler("startup", start)
