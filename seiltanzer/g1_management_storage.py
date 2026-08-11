"""Extend the verified-backup identity with economically authoritative G.1-M ledgers."""
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
