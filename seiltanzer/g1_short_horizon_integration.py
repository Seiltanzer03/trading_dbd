"""Install G.1S and G.1-M.1 runtimes without touching the market collector loop.

The runtimes share the durable passive SQLite source of truth, but their expensive
materialization/model work is scheduled by a separate research worker. Production
market collection and decision authority therefore remain independent.
"""
from __future__ import annotations

from .engine import Engine
from .g1_short_horizon_runtime import ShortHorizonRuntime
from .g1_management_local_runtime import ManagementLocalRuntime
from .g1_management_local_diagnostics import install_g1_management_local_diagnostics
from . import storage_runtime as _storage


_INSTALLED = False

G1S_CRITICAL_TABLES = (
    "g1s_observations", "g1s_resolutions", "g1s_models",
    "g1s_shadow_predictions", "g1s_trade_links", "g1s_barrier_outcomes",
    "g1s_training_cuts", "g1s_model_cut_links",
    "g1s_path_metrics", "g1s_dependency_groups",
    "g1m_local_windows", "g1m_local_outcomes", "g1m_local_policy_outcomes",
    "g1m_local_contract_errors", "research_materialization_state",
)


def install_g1_short_horizon_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    install_g1_management_local_diagnostics()
    _storage.CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_storage.CRITICAL_TABLES, *G1S_CRITICAL_TABLES)))

    previous_engine_init = Engine.__init__
    previous_engine_close = Engine.close

    def engine_init(self, *args, **kwargs):
        previous_engine_init(self, *args, **kwargs)
        self.short_horizon = ShortHorizonRuntime(self)
        self.management_local = ManagementLocalRuntime(self)
        self.passive._g1s_runtime = self.short_horizon
        self.passive._g1m_local_runtime = self.management_local

    def engine_close(self, *args, **kwargs):
        # Runtimes share the passive SQLite connection, so they do not close it.
        return previous_engine_close(self, *args, **kwargs)

    Engine.__init__ = engine_init
    Engine.close = engine_close


def ensure_g1s_schema_backup(storage) -> str | None:
    """Create one verified snapshot after first G.1S/G.1-M.1 schema creation."""
    latest = storage._last_verified("local")
    counts = (latest or {}).get("critical_table_counts") or {}
    if latest and all(counts.get(table) is not None for table in G1S_CRITICAL_TABLES):
        return None
    result = storage.create_backup(kind="local", reason="g1s-schema-identity")
    return result.backup_id
