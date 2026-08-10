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

    # Keep the legacy readiness key as a read-only compatibility alias while the
    # authoritative current name is pristine_f32_dataset_ready.
    from .passive_learning import PassiveLearningEngine
    if getattr(PassiveLearningEngine, "_measurement_compat_runtime", None) != MEASUREMENT_RUNTIME_VERSION:
        current_calibration = PassiveLearningEngine.calibration_report

        def calibration_with_legacy_alias(self):
            result = current_calibration(self)
            integrity = result.setdefault("measurement_integrity", {})
            integrity["pristine_f31_dataset_ready"] = bool(
                integrity.get("pristine_f32_dataset_ready", False)
            )
            return result

        PassiveLearningEngine.calibration_report = calibration_with_legacy_alias
        PassiveLearningEngine._measurement_compat_runtime = MEASUREMENT_RUNTIME_VERSION
