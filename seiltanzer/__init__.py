"""Seiltanzer Terminal — локальный дашборд поддержки решений.

Показывает вероятность дохождения открытой сделки до тейка раньше стопа,
обогащённую данными опционного рынка. Не сигнальный сервис: каждое число
выводимо из реально полученных данных.
"""

__version__ = "0.1.0"

# Keep analytics behavior identical whether production is started through the
# console script, `python -m seiltanzer`, uvicorn, or tests importing create_app.
# The installer only replaces the three prototype analytics payload methods;
# it does not start threads or perform network I/O at import time.
from .analytics_runtime import install_analytics_runtime as _install_analytics_runtime

_install_analytics_runtime()
del _install_analytics_runtime

# Phase F.3.2a closes passive measurement runtime semantics without rewriting
# historical observations. Like analytics_runtime, this only installs adapters;
# it starts no threads and performs no network I/O at import time.
from .measurement_runtime import install_measurement_runtime as _install_measurement_runtime

_install_measurement_runtime()
del _install_measurement_runtime

# Phase G.1A adds a deterministic prospective research-dataset boundary on top
# of F.3.2a. It creates no calibrator and grants no production authority.
from .g1_dataset_runtime import install_g1_dataset_runtime as _install_g1_dataset_runtime

_install_g1_dataset_runtime()
del _install_g1_dataset_runtime

# Keep measurement-validity distinct from research-evidence admission and make
# runtime readiness depend on observed current-contract evidence, not legacy rows.
from .g1_dataset_refinement import install_g1_dataset_refinement as _install_g1_dataset_refinement

_install_g1_dataset_refinement()
del _install_g1_dataset_refinement

# Phase G.1B measures frozen baselines and Q identity calibration on top of the
# G.1A boundary. It is read-only research: no calibrator fitting or authority.
from .g1_baseline_runtime import install_g1_baseline_runtime as _install_g1_baseline_runtime

_install_g1_baseline_runtime()
del _install_g1_baseline_runtime

# Keep G.1B evidence N consistent with G.1A aggregate dependency semantics and
# make historical baselines respect recorded outcome-availability timestamps.
from .g1_baseline_refinement import install_g1_baseline_refinement as _install_g1_baseline_refinement

_install_g1_baseline_refinement()
del _install_g1_baseline_refinement

# Phase G.1B.1 makes every prospective option-native Q capture attempt
# observable and immutable. It does not relax G.1A admission or fit Q->P.
from .g1_q_evidence_runtime import install_g1_q_evidence_runtime as _install_g1_q_evidence_runtime

_install_g1_q_evidence_runtime()
del _install_g1_q_evidence_runtime

# Fail closed on source freshness, direct target-price provenance and the frozen
# source/target/proxy mapping before a Q attempt is counted as successful.
from .g1_q_evidence_refinement import install_g1_q_evidence_refinement as _install_g1_q_evidence_refinement

_install_g1_q_evidence_refinement()
del _install_g1_q_evidence_refinement

# Q evidence has an independent persisted cadence so a fresh fixed-horizon row
# cannot hide option-source availability after restart. Successful runs write
# only the native-expiry Q row; fixed horizons retain their own cadence.
from .g1_q_collector import install_g1_q_collector as _install_g1_q_collector

_install_g1_q_collector()
del _install_g1_q_collector

# Freeze the native Q ACT/365 clock from exact expiry minus the independent
# collector's T0, matching the F.3.2a expiry contract end-to-end.
from .g1_q_collector_refinement import install_g1_q_collector_refinement as _install_g1_q_collector_refinement

_install_g1_q_collector_refinement()
del _install_g1_q_collector_refinement

# Phase G.1C is the first trainable layer, but remains strictly research-only.
# It consumes only immutable G.1A Q-eligible cuts, freezes challenger model
# artifacts, and records prospective shadow predictions for later G.1D OOS.
from .g1_shadow_runtime import install_g1_shadow_runtime as _install_g1_shadow_runtime

_install_g1_shadow_runtime()
del _install_g1_shadow_runtime

# Prospective predictions fail closed on exact T0 provenance and model scopes
# separate native/direct/inverse semantics rather than pooling unlike Q sources.
from .g1_shadow_refinement import install_g1_shadow_refinement as _install_g1_shadow_refinement

_install_g1_shadow_refinement()
del _install_g1_shadow_refinement

# Model rows are immutable in normal operation, but prediction also re-hashes
# the artifact so storage corruption cannot silently enter the prospective OOS ledger.
from .g1_shadow_artifact_refinement import install_g1_shadow_artifact_refinement as _install_g1_shadow_artifact_refinement

_install_g1_shadow_artifact_refinement()
del _install_g1_shadow_artifact_refinement
