"""Extend verified-backup identity with economically authoritative G.1-M ledgers."""
from __future__ import annotations

from . import storage_runtime as _storage


G1M_CRITICAL_TABLES = (
    "g1m_runtime_activation",
    "g1m_management_observations",
    "g1m_observation_context",
    "g1m_counterfactual_policies",
    "g1m_t0_policy_metrics",
    "g1m_resolutions",
    "g1m_policy_outcomes",
    "g1m_execution_attribution",
    "g1m_contract_errors",
    "g1m_research_cuts",
)


def install_g1_management_storage() -> None:
    current = tuple(_storage.CRITICAL_TABLES)
    _storage.CRITICAL_TABLES = current + tuple(
        table for table in G1M_CRITICAL_TABLES if table not in current
    )


def ensure_g1m_schema_backup(storage) -> str | None:
    """Create one verified snapshot after the first G.1-M schema migration.

    The normal pre-start snapshot intentionally happens before Engine schema
    constructors. On the very first G.1-M deploy that snapshot cannot contain the
    new ledgers. Once create_app has materialised them, create exactly one schema
    identity snapshot; later starts reuse normal cadence.
    """
    latest = storage._last_verified("local")
    counts = (latest or {}).get("critical_table_counts") or {}
    if latest and all(counts.get(table) is not None for table in G1M_CRITICAL_TABLES):
        return None
    result = storage.create_backup(kind="local", reason="g1m-schema-identity")
    return result.backup_id
