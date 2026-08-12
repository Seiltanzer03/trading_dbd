"""Restore the complete backup-table registry after storage refinements install.

Python loads the package (and G.1S registrations) before ``__main__`` calls
``install_storage_refinement``.  That legacy refinement replaces
``storage_runtime.CRITICAL_TABLES`` with its own older tuple, accidentally
forgetting G.1S/G.1-M.1/continuous/calibration ledgers.  Consequently a backup
created after schema migration could be perfectly valid SQLite yet still lack
those table counts in its manifest, making schema-complete readiness impossible.

This startup integrity layer is deliberately small: merge the live registries
after all storage/G.1-M installers have run, before ``prepare_storage`` creates
any manifest.  It changes backup identity only; it never touches database rows.
"""
from __future__ import annotations

from . import g1_short_horizon_integration as _g1s
from .g1_management_storage import G1M_CRITICAL_TABLES
from . import storage_runtime as _storage


SCHEMA_REGISTRY_INTEGRITY_VERSION = "storage-schema-registry-integrity-v1"


def current_research_critical_tables() -> tuple[str, ...]:
    return tuple(dict.fromkeys((*G1M_CRITICAL_TABLES, *_g1s.G1S_CRITICAL_TABLES)))


def install_storage_schema_registry_integrity() -> tuple[str, ...]:
    current = tuple(_storage.CRITICAL_TABLES)
    required = current_research_critical_tables()
    _storage.CRITICAL_TABLES = tuple(dict.fromkeys((*current, *required)))
    return tuple(_storage.CRITICAL_TABLES)
