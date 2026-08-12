"""Read-only G.1-M.1 prospective eligibility diagnostics.

This module deliberately does not change eligibility semantics.  It explains why
upstream G.1-M observations do or do not reach evidence-eligible local windows,
using only persisted immutable ledgers and bounded aggregate SQL.
"""
from __future__ import annotations

from .g1_management_local_runtime import ManagementLocalRuntime


DIAGNOSTICS_VERSION = "g1m-local-eligibility-diagnostics-v1"
KNOWN_UPSTREAM_EXCLUSIONS = (
    "INVALID_REMAINING_FRACTION",
    "MISSING_T0_R",
    "MISSING_T0_PRICE",
    "MISSING_EXECUTION_INPUTS",
    "NON_PROSPECTIVE_ORIGIN",
)


def _count(row, key: str) -> int:
    if row is None:
        return 0
    try:
        return int(row[key] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def eligibility_diagnostics(self: ManagementLocalRuntime) -> dict:
    """Explain the G.1-M -> G.1-M.1 eligibility funnel without mutating it."""
    activation = float(self.activation_ts)
    with self._lock:
        upstream = self._conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN policy_edge_eligible=1 THEN 1 ELSE 0 END) AS eligible,
                SUM(CASE WHEN policy_edge_eligible=0 THEN 1 ELSE 0 END) AS ineligible,
                SUM(CASE WHEN policy_edge_eligible=1 AND captured_ts<? THEN 1 ELSE 0 END)
                    AS pre_local_activation,
                SUM(CASE WHEN policy_edge_eligible=1 AND captured_ts>=? THEN 1 ELSE 0 END)
                    AS post_local_activation
            FROM g1m_management_observations
        """, (activation, activation)).fetchone()
        exclusion_rows = self._conn.execute("""
            SELECT COALESCE(NULLIF(exclusion_reason,''),'UNSPECIFIED_UPSTREAM_INELIGIBLE') reason,
                   COUNT(*) n
            FROM g1m_management_observations
            WHERE policy_edge_eligible=0
            GROUP BY COALESCE(NULLIF(exclusion_reason,''),'UNSPECIFIED_UPSTREAM_INELIGIBLE')
            ORDER BY n DESC, reason
        """).fetchall()
        context = self._conn.execute("""
            SELECT
                SUM(CASE WHEN g.policy_edge_eligible=1 AND g.captured_ts>=?
                              AND c.observation_id IS NULL THEN 1 ELSE 0 END)
                    AS missing_context,
                SUM(CASE WHEN g.policy_edge_eligible=1 AND g.captured_ts>=?
                              AND c.observation_id IS NOT NULL
                              AND (c.instrument IS NULL OR TRIM(c.instrument)='') THEN 1 ELSE 0 END)
                    AS missing_instrument
            FROM g1m_management_observations g
            LEFT JOIN g1m_observation_context c USING(observation_id)
        """, (activation, activation)).fetchone()
        windows = self._conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN w.evidence_eligible=1 THEN 1 ELSE 0 END) AS eligible,
                SUM(CASE WHEN w.evidence_eligible=0 THEN 1 ELSE 0 END) AS ineligible,
                SUM(CASE WHEN w.evidence_eligible=1 AND o.window_id IS NULL THEN 1 ELSE 0 END)
                    AS eligible_pending,
                SUM(CASE WHEN w.evidence_eligible=1 AND o.window_id IS NOT NULL THEN 1 ELSE 0 END)
                    AS eligible_resolved,
                COUNT(DISTINCT CASE WHEN w.evidence_eligible=1 THEN w.observation_id END)
                    AS eligible_observations
            FROM g1m_local_windows w
            LEFT JOIN g1m_local_outcomes o USING(window_id)
        """).fetchone()

    exclusions = {str(row["reason"]): int(row["n"] or 0) for row in exclusion_rows}
    for reason in KNOWN_UPSTREAM_EXCLUSIONS:
        exclusions.setdefault(reason, 0)

    upstream_eligible = _count(upstream, "eligible")
    post_activation = _count(upstream, "post_local_activation")
    missing_context = _count(context, "missing_context")
    missing_instrument = _count(context, "missing_instrument")
    eligible_windows = _count(windows, "eligible")
    eligible_resolved = _count(windows, "eligible_resolved")

    if upstream_eligible == 0:
        state = "NO_UPSTREAM_ELIGIBLE_G1M_OBSERVATIONS"
    elif post_activation == 0:
        state = "ALL_UPSTREAM_ELIGIBLE_PREDATE_LOCAL_ACTIVATION"
    elif missing_context or missing_instrument:
        state = "POST_ACTIVATION_SOURCE_CONTEXT_BLOCKED"
    elif eligible_windows == 0:
        state = "ELIGIBLE_WINDOWS_NOT_MATERIALIZED_YET"
    elif eligible_resolved == 0:
        state = "ELIGIBLE_WINDOWS_PENDING_OUTCOME"
    else:
        state = "ELIGIBLE_EVIDENCE_ACTIVE"

    observation_counts = {
        "TOTAL": _count(upstream, "total"),
        "UPSTREAM_ELIGIBLE": upstream_eligible,
        "UPSTREAM_INELIGIBLE": _count(upstream, "ineligible"),
        "PRE_LOCAL_ACTIVATION": _count(upstream, "pre_local_activation"),
        "POST_LOCAL_ACTIVATION_UPSTREAM_ELIGIBLE": post_activation,
        "MISSING_FROZEN_CONTEXT": missing_context,
        "MISSING_CONTEXT_INSTRUMENT": missing_instrument,
        **exclusions,
    }
    window_counts = {
        "TOTAL": _count(windows, "total"),
        "ELIGIBLE": eligible_windows,
        "INELIGIBLE": _count(windows, "ineligible"),
        "ELIGIBLE_PENDING": _count(windows, "eligible_pending"),
        "ELIGIBLE_RESOLVED": eligible_resolved,
        "ELIGIBLE_OBSERVATIONS": _count(windows, "eligible_observations"),
    }

    # Flat requested labels are also published, but units are explicit so an
    # observation count can never be confused with a 15/30/60/120m window count.
    counts = {
        "PRE_LOCAL_ACTIVATION": observation_counts["PRE_LOCAL_ACTIVATION"],
        "UPSTREAM_ELIGIBLE": observation_counts["UPSTREAM_ELIGIBLE"],
        "UPSTREAM_INELIGIBLE": observation_counts["UPSTREAM_INELIGIBLE"],
        **{reason: observation_counts[reason] for reason in KNOWN_UPSTREAM_EXCLUSIONS},
        "ELIGIBLE_PENDING": window_counts["ELIGIBLE_PENDING"],
        "ELIGIBLE_RESOLVED": window_counts["ELIGIBLE_RESOLVED"],
    }
    count_units = {
        key: ("window" if key in {"ELIGIBLE_PENDING", "ELIGIBLE_RESOLVED"} else "observation")
        for key in counts
    }

    return {
        "contract_version": DIAGNOSTICS_VERSION,
        "local_contract_version": getattr(self, "activation_ts", None) is not None
            and "g1m-local-feedback-v1" or None,
        "activation_ts": activation,
        "eligibility_formula": "upstream policy_edge_eligible AND captured_ts >= local activation_ts",
        "state": state,
        "counts": counts,
        "count_units": count_units,
        "observation_counts": observation_counts,
        "window_counts": window_counts,
        "upstream_exclusion_reasons": exclusions,
        "retroactive_eligibility_allowed": False,
        "semantics_changed": False,
        "authority": {
            "research_only": True,
            "production_authority": False,
            "policy_promotion_allowed": False,
            "edge_claim_allowed": False,
        },
    }


def install_g1_management_local_diagnostics() -> None:
    if getattr(ManagementLocalRuntime, "_eligibility_diagnostics_version", None) == DIAGNOSTICS_VERSION:
        return
    ManagementLocalRuntime.eligibility_diagnostics = eligibility_diagnostics
    ManagementLocalRuntime._eligibility_diagnostics_version = DIAGNOSTICS_VERSION
