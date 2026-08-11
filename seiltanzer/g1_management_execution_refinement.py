"""Execution-provenance boundary for G.1-M.

Policy edge is a frozen T0 counterfactual. Execution/compliance attribution is a
separate observed-position question. A user ACK is useful evidence but is never
a broker fill, and the actual terminal result must come from the event-sourced
position ledger rather than pretending the recommendation executed at T0.
"""
from __future__ import annotations

from .g1_management_runtime import (
    G1M_ATTRIBUTION_VERSION,
    ManagementEdgeRuntime,
    _json,
    _sha_text,
)


REFINEMENT_VERSION = "g1m-execution-provenance-v2"
_ORIGINAL_STATUS = ManagementEdgeRuntime.status


def _position_ledger_truth(self, *, trade_id: int, decision_id: str | None) -> dict:
    with self._lock:
        table = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='position_management_events'"
        ).fetchone()
        if table is None:
            return {
                "available": False,
                "terminal_known": False,
                "terminal_r": None,
                "latest_fraction_after": None,
                "decision_event": None,
            }
        rows = self._conn.execute(
            "SELECT * FROM position_management_events WHERE trade_id=? ORDER BY id",
            (int(trade_id),),
        ).fetchall()
    if not rows:
        return {
            "available": True,
            "terminal_known": False,
            "terminal_r": None,
            "latest_fraction_after": None,
            "decision_event": None,
        }
    total = 0.0
    for row in rows:
        closed = float(row["fraction_closed"] or 0.0)
        execution_r = row["execution_r"]
        if closed > 0.0 and execution_r is not None:
            total += closed * float(execution_r)
    latest_fraction = float(rows[-1]["fraction_after"])
    terminal_known = latest_fraction <= 1e-9
    decision_event = None
    if decision_id:
        for row in rows:
            if row["decision_id"] == decision_id:
                decision_event = {
                    "event_id": int(row["id"]),
                    "timestamp": float(row["timestamp"]),
                    "event_type": str(row["event_type"]),
                    "source": str(row["source"]),
                    "fraction_closed": float(row["fraction_closed"]),
                    "execution_price": row["execution_price"],
                    "execution_r": row["execution_r"],
                }
                break
    return {
        "available": True,
        "terminal_known": terminal_known,
        "terminal_r": total if terminal_known else None,
        "latest_fraction_after": latest_fraction,
        "decision_event": decision_event,
    }


def _write_execution_attribution(self, obs, values: dict[str, float]) -> None:
    observation_id = str(obs["observation_id"])
    review_id = str(obs["review_id"])
    trade_id = int(obs["trade_id"])
    production = str(obs["production_policy"])
    with self._lock:
        row = self._conn.execute(
            "SELECT decision_id,status,executed_ts,execution_price,execution_r "
            "FROM management_decisions WHERE review_id=? "
            "ORDER BY created_ts DESC,rowid DESC LIMIT 1", (review_id,),
        ).fetchone()
    status = str(row["status"]) if row else "unknown"
    decision_id = str(row["decision_id"]) if row else None
    ledger = _position_ledger_truth(self, trade_id=trade_id, decision_id=decision_id)

    if production == "HOLD":
        compliance = "NOT_REQUIRED"
    elif status == "executed" and ledger.get("decision_event") is not None:
        compliance = "FOLLOWED"
    elif status == "recommended_not_executed":
        compliance = "IGNORED"
    elif status == "executed":
        # ACK says executed but the economic event is absent: never silently call
        # this followed. This is a data-integrity gap for execution attribution.
        compliance = "ACK_WITHOUT_POSITION_EVENT"
    else:
        compliance = "UNKNOWN"

    production_result = values.get(production)
    actual_result = ledger.get("terminal_r")
    delta = (
        production_result - actual_result
        if production_result is not None and actual_result is not None
        else None
    )
    actual_policy = (
        production if compliance == "FOLLOWED"
        else "OBSERVED_POSITION_LEDGER" if actual_result is not None
        else None
    )
    event = ledger.get("decision_event")
    payload = {
        "contract_version": G1M_ATTRIBUTION_VERSION,
        "execution_provenance_version": REFINEMENT_VERSION,
        "decision_id": decision_id,
        "recommendation_status": status,
        "compliance_state": compliance,
        "production_policy": production,
        "actual_policy": actual_policy,
        "production_terminal_r": production_result,
        "actual_terminal_r": actual_result,
        "compliance_delta_r": delta,
        "execution_edge_eligible": bool(actual_result is not None),
        "execution_source": "POSITION_MANAGEMENT_EVENT_LEDGER",
        "user_ack_source": "management_decisions",
        "broker_confirmed": False,
        "broker_execution_id": None,
        "decision_execution_event": event,
        "decision_ack": {
            "executed_ts": row["executed_ts"] if row is not None else None,
            "execution_price": row["execution_price"] if row is not None else None,
            "execution_r": row["execution_r"] if row is not None else None,
        },
        "position_terminal_known": bool(ledger.get("terminal_known")),
        "position_latest_fraction_after": ledger.get("latest_fraction_after"),
        "interpretation": (
            "Actual terminal R comes from the event-sourced position ledger. "
            "User acknowledgement is compliance evidence only; broker fill is "
            "not available from this source and must not be inferred."
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
            (observation_id, decision_id, status, compliance, production, actual_policy,
             production_result, actual_result, delta, raw, _sha_text(raw)),
        )


def status_with_execution(self) -> dict:
    body = _ORIGINAL_STATUS(self)
    with self._lock:
        resolved = int(self._conn.execute(
            "SELECT COUNT(*) FROM g1m_execution_attribution "
            "WHERE actual_terminal_r IS NOT NULL"
        ).fetchone()[0])
        followed = int(self._conn.execute(
            "SELECT COUNT(*) FROM g1m_execution_attribution "
            "WHERE compliance_state='FOLLOWED' AND actual_terminal_r IS NOT NULL"
        ).fetchone()[0])
        ignored = int(self._conn.execute(
            "SELECT COUNT(*) FROM g1m_execution_attribution "
            "WHERE compliance_state='IGNORED' AND actual_terminal_r IS NOT NULL"
        ).fetchone()[0])
    body["execution_provenance_version"] = REFINEMENT_VERSION
    body["execution_edge_resolved_n"] = resolved
    body["compliance_followed_resolved_n"] = followed
    body["compliance_ignored_resolved_n"] = ignored
    body["broker_confirmed_execution_n"] = 0
    return body


def install_g1_management_execution_refinement() -> None:
    if getattr(ManagementEdgeRuntime, "_g1m_execution_refinement", None) == REFINEMENT_VERSION:
        return
    ManagementEdgeRuntime._write_execution_attribution = _write_execution_attribution
    ManagementEdgeRuntime.status = status_with_execution
    ManagementEdgeRuntime._g1m_execution_refinement = REFINEMENT_VERSION
