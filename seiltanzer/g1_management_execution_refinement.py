"""Execution-provenance boundary for G.1-M.

A user acknowledgement is useful compliance evidence but is not a broker fill.
Keep that distinction explicit so a future execution connector can add broker
truth without reinterpreting historical rows.
"""
from __future__ import annotations

from .g1_management_runtime import (
    G1M_ATTRIBUTION_VERSION,
    ManagementEdgeRuntime,
    _json,
    _sha_text,
)


REFINEMENT_VERSION = "g1m-execution-provenance-v1"


def _write_execution_attribution(self, obs, values: dict[str, float]) -> None:
    observation_id = str(obs["observation_id"])
    review_id = str(obs["review_id"])
    production = str(obs["production_policy"])
    with self._lock:
        row = self._conn.execute(
            "SELECT decision_id,status FROM management_decisions WHERE review_id=? "
            "ORDER BY created_ts DESC,rowid DESC LIMIT 1", (review_id,),
        ).fetchone()
    status = str(row["status"]) if row else "unknown"
    decision_id = str(row["decision_id"]) if row else None

    if production == "HOLD":
        compliance, actual = "NOT_REQUIRED", "HOLD"
    elif status == "executed":
        compliance, actual = "FOLLOWED", production
    elif status == "recommended_not_executed":
        compliance, actual = "IGNORED", "HOLD"
    else:
        compliance, actual = "UNKNOWN", None

    production_result = values.get(production)
    actual_result = values.get(actual) if actual else None
    delta = (
        production_result - actual_result
        if production_result is not None and actual_result is not None
        else None
    )
    payload = {
        "contract_version": G1M_ATTRIBUTION_VERSION,
        "execution_provenance_version": REFINEMENT_VERSION,
        "decision_id": decision_id,
        "recommendation_status": status,
        "compliance_state": compliance,
        "production_policy": production,
        "actual_policy": actual,
        "production_terminal_r": production_result,
        "actual_terminal_r": actual_result,
        "compliance_delta_r": delta,
        "execution_source": "USER_ACK_LEDGER" if row is not None else "UNKNOWN",
        "broker_confirmed": False,
        "broker_execution_id": None,
        "interpretation": (
            "User acknowledgement/compliance evidence only; broker fill is not "
            "available from this source and must not be inferred."
        ),
    }
    raw = _json(payload)
    with self._lock, self._conn:
        self._conn.execute(
            "INSERT OR IGNORE INTO g1m_execution_attribution("
            "observation_id,decision_id,recommendation_status,compliance_state,"
            "production_policy,actual_policy,production_terminal_r,actual_terminal_r,"
            "compliance_delta_r,attribution_json,attribution_sha256)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (observation_id, decision_id, status, compliance, production, actual,
             production_result, actual_result, delta, raw, _sha_text(raw)),
        )


def install_g1_management_execution_refinement() -> None:
    if getattr(ManagementEdgeRuntime, "_g1m_execution_refinement", None) == REFINEMENT_VERSION:
        return
    ManagementEdgeRuntime._write_execution_attribution = _write_execution_attribution
    ManagementEdgeRuntime._g1m_execution_refinement = REFINEMENT_VERSION
