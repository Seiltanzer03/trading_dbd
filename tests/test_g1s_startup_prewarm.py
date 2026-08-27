from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from seiltanzer.g1_short_horizon_evidence_nonblocking import (
    install_g1_short_horizon_evidence_nonblocking,
)
from seiltanzer.g1_short_horizon_startup_prewarm import (
    install_g1_short_horizon_startup_prewarm,
)
from seiltanzer.g1_short_horizon_status_nonblocking import (
    install_g1_short_horizon_status_nonblocking,
)


class _App:
    def __init__(self):
        self.state = SimpleNamespace()
        self.handlers = {}

    def add_event_handler(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)


class _Runtime:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.status_reads = 0
        self.report_reads = 0

    def status(self):
        self.status_reads += 1
        self.started.set()
        assert self.release.wait(2.0)
        return {
            "status": "READY",
            "status_materialization": {"presentation_state": "READY"},
            "authority": {"production_authority": False},
        }

    def refresh_materialized_status(self, *args, **kwargs):
        return {"refreshed": True}

    def materialize_new(self, *args, **kwargs):
        return 0

    def resolve_new(self, *args, **kwargs):
        return 0

    def fit_if_ready(self, *args, **kwargs):
        return 0

    def _error(self, *args, **kwargs):
        return None

    def cuts(self, limit=100):
        return {"items": [], "production_authority": False}

    def barriers(self, limit=500):
        return {"items": [], "production_authority": False}

    def path_metrics(self, limit=500):
        return {"items": [], "production_authority": False}

    def materialized_evidence_report(self, name):
        self.report_reads += 1
        return {
            "report_name": name,
            "production_authority": False,
            "edge_claim_allowed": False,
        }

    def evidence_materialization_status(self):
        return {"reports": [], "production_authority": False}

    def materialize_evidence_reports(self, *args, **kwargs):
        return {"refreshed": False}


def test_production_prewarm_returns_startup_without_request_time_sqlite():
    runtime = _Runtime()
    app = _App()
    install_g1_short_horizon_status_nonblocking(runtime, prewarm=False)
    install_g1_short_horizon_evidence_nonblocking(runtime, prewarm=False)

    assert runtime.status_reads == 0
    assert runtime.report_reads == 0
    assert runtime.status()["status"] == "UNAVAILABLE"
    assert runtime.materialized_evidence_report("calibration_oos")["status"] == "BUILDING"

    install_g1_short_horizon_startup_prewarm(app, runtime)
    started = time.monotonic()
    app.handlers["startup"][0]()
    elapsed = time.monotonic() - started

    assert elapsed < 0.10
    assert runtime.started.wait(1.0)
    assert app.state.g1s_startup_prewarm["state"] == "BUILDING"
    assert runtime.status()["status"] == "UNAVAILABLE"

    runtime.release.set()
    thread = app.state.g1s_startup_prewarm["thread"]
    thread.join(2.0)
    assert not thread.is_alive()
    assert app.state.g1s_startup_prewarm["state"] == "READY"
    assert app.state.g1s_startup_prewarm["errors"] == {}
    assert runtime.status()["status"] == "READY"
    assert runtime.materialized_evidence_report(
        "calibration_oos",
    )["production_authority"] is False
