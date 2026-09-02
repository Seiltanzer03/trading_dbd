"""Small bootstrap that keeps scalability install explicit and production-only."""
from __future__ import annotations

from . import passive_learning as _pl
from . import research_scalability as _runtime
from .g1_operational_status_passthrough import install_operational_status_passthrough
from .passive_calibration_nonblocking import install_passive_calibration_nonblocking

# research_scalability deliberately avoids another heavyweight import cycle; bind
# the already-installed passive contract before any bounded endpoint is called.
_runtime._pl = _pl


def install_research_scalability(app):
    result = _runtime.install_research_scalability(app)
    # Calibration is already bounded by research_scalability, but it still shares
    # the passive SQLite/writer lock. Materialize that exact bounded report away
    # from HTTP so functional smoke/readers cannot queue behind research work.
    install_passive_calibration_nonblocking(app)
    # G.1E.2 intentionally replaces PassiveLearningEngine.status with a bounded
    # presentation view. Re-attach only the persisted/bounded P0 health telemetry
    # afterwards; never call the legacy full-history status on request paths.
    install_operational_status_passthrough(app)
    return result
