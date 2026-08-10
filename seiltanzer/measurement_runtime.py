"""Install the narrow Phase F.3.2a measurement-integrity closure.

No threads or network I/O run at import time.  The adapters preserve public API
contracts and leave historical observations immutable/quarantined.
"""
from __future__ import annotations

from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION, install_q_runtime


def install_measurement_runtime() -> None:
    install_q_runtime()
    # Import after Q/capture patching so path runtime captures the current status
    # surface while replacing only resolution/cohort/readiness behavior.
    from .measurement_path_runtime import install_path_runtime
    install_path_runtime()
