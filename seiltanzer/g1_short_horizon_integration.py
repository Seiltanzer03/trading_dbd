"""Install G.1S and G.1-M.1 on the existing passive research scheduler.

Import order matters: this installer runs after G.1-M, therefore it wraps the
already-integrated Engine/PassiveLearningEngine rather than bypassing management
measurement. Market/decision authority is untouched.
"""
from __future__ import annotations

from .engine import Engine
from .passive_learning import PassiveLearningEngine
from .g1_short_horizon_runtime import ShortHorizonRuntime
from .g1_management_local_runtime import ManagementLocalRuntime
from . import storage_runtime as _storage


_INSTALLED = False

G1S_CRITICAL_TABLES = (
    "g1s_observations", "g1s_resolutions", "g1s_models",
    "g1s_shadow_predictions", "g1s_trade_links", "g1s_barrier_outcomes",
    "g1s_training_cuts", "g1s_model_cut_links",
    "g1m_local_windows", "g1m_local_outcomes", "g1m_local_policy_outcomes",
    "research_materialization_state",
)


def install_g1_short_horizon_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Include every economically/research-authoritative fast-learning ledger in
    # verified backup manifests. Later refinements create the barrier/cut tables;
    # missing tables are initially reported as None and become mandatory after the
    # first schema-aware backup on production.
    _storage.CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_storage.CRITICAL_TABLES, *G1S_CRITICAL_TABLES)))

    previous_engine_init = Engine.__init__
    previous_engine_close = Engine.close
    previous_passive_step = PassiveLearningEngine.step

    def engine_init(self, *args, **kwargs):
        previous_engine_init(self, *args, **kwargs)
        self.short_horizon = ShortHorizonRuntime(self)
        self.management_local = ManagementLocalRuntime(self)
        self.passive._g1s_runtime = self.short_horizon
        self.passive._g1m_local_runtime = self.management_local

    def engine_close(self, *args, **kwargs):
        # Runtimes share the passive SQLite connection, so they do not close it.
        return previous_engine_close(self, *args, **kwargs)

    def passive_step(self, *args, **kwargs):
        result = previous_passive_step(self, *args, **kwargs)
        g1s = getattr(self, "_g1s_runtime", None)
        local = getattr(self, "_g1m_local_runtime", None)
        if g1s is not None:
            result["g1s"] = g1s.step()
        if local is not None:
            result["g1m_local"] = local.step()
        return result

    Engine.__init__ = engine_init
    Engine.close = engine_close
    PassiveLearningEngine.step = passive_step


def ensure_g1s_schema_backup(storage) -> str | None:
    """Create one verified snapshot after first G.1S/G.1-M.1 schema creation.

    `prepare_storage` intentionally snapshots before Engine constructors. On the
    first deploy that snapshot therefore cannot contain the new research ledgers.
    Once `create_app` has created them, emit exactly one schema-identity snapshot;
    subsequent restarts return to the normal backup cadence.
    """
    latest = storage._last_verified("local")
    counts = (latest or {}).get("critical_table_counts") or {}
    if latest and all(counts.get(table) is not None for table in G1S_CRITICAL_TABLES):
        return None
    result = storage.create_backup(kind="local", reason="g1s-schema-identity")
    return result.backup_id
