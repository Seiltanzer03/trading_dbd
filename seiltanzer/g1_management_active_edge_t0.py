"""Freeze active high-risk edge context beside immutable G.1-M observations.

The active edge is already part of the frozen production decision snapshot.  This
sidecar extracts a small, deterministic T0 record so later G.1-M analysis can
measure whether the early edge actually improved HOLD/REDUCE/EXIT decisions.
It never changes the production policy, execution path or promotion authority.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from typing import Any

from .g1_management_runtime import ManagementEdgeRuntime, _json, _sha_text


G1M_ACTIVE_EDGE_T0_VERSION = "g1m-active-edge-t0-v1"
MAX_FROZEN_SIGNALS = 8
_INSTALLED = False


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nonnegative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _bounded_text(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


def _compact_signal(value: Any) -> dict[str, Any] | None:
    row = _mapping(value)
    if not row:
        return None
    return {
        "candidate_id": _bounded_text(row.get("candidate_id"), 220),
        "source": _bounded_text(row.get("source"), 32),
        "target_id": _bounded_text(row.get("target_id"), 96),
        "horizon_minutes": _nonnegative_int(row.get("horizon_minutes")),
        "primary_improvement": _finite(row.get("primary_improvement")),
        "q_value_diagnostic": _finite(row.get("q_value_diagnostic")),
        "fold_positive": _nonnegative_int(row.get("fold_positive")),
        "strict_reference_qualified": bool(row.get("strict_reference_qualified")),
        "conditions_match_current_t0": (
            row.get("conditions_match_current_t0")
            if isinstance(row.get("conditions_match_current_t0"), bool)
            else None
        ),
        "market_bias": _bounded_text(row.get("market_bias"), 64),
        "position_relation": _bounded_text(row.get("position_relation"), 64),
    }


def compact_active_edge_t0(snapshot: Any) -> dict[str, Any]:
    """Return a bounded, deterministic research record from one frozen T0."""
    root = _mapping(snapshot)
    manager = _mapping(root.get("policy_manager"))
    evidence = _mapping(manager.get("evidence"))
    summary = _mapping(evidence.get("active_high_risk_edge"))
    ede = _mapping(root.get("ede_causal_context"))
    context = _mapping(ede.get("active_high_risk"))

    signals: list[dict[str, Any]] = []
    raw_signals = context.get("signals")
    if isinstance(raw_signals, list):
        for item in raw_signals[:MAX_FROZEN_SIGNALS]:
            compact = _compact_signal(item)
            if compact is not None:
                signals.append(compact)

    policy = _bounded_text(summary.get("edge_policy") or context.get("edge_policy"), 160)
    risk = _bounded_text(
        summary.get("risk_acceptance") or context.get("risk_acceptance"), 160)
    available = bool(summary.get("available") or context.get("available"))
    matched = _nonnegative_int(
        summary.get("matched_structured_signal_n", context.get("matched_structured_signal_n")))
    supporting = _nonnegative_int(
        summary.get("supporting_position_n", context.get("supporting_position_n")))
    opposing = _nonnegative_int(
        summary.get("opposing_position_n", context.get("opposing_position_n")))

    # Recompute the net vote from the frozen counts rather than trusting a
    # potentially inconsistent duplicate field in the source snapshot.
    net_vote = supporting - opposing
    ml_signal_n = sum(row.get("source") == "ML" for row in signals)
    strict_reference_n = sum(bool(row.get("strict_reference_qualified")) for row in signals)

    return {
        "contract_version": G1M_ACTIVE_EDGE_T0_VERSION,
        "source_context_contract_version": _bounded_text(
            context.get("contract_version"), 160),
        "edge_policy": policy,
        "risk_acceptance": risk,
        "available": available,
        "matched_structured_signal_n": matched,
        "supporting_position_n": supporting,
        "opposing_position_n": opposing,
        "net_position_vote": net_vote,
        "ml_signal_n": ml_signal_n,
        "strict_reference_signal_n": strict_reference_n,
        "signals": signals,
        "decision_weight_applied": False,
        "research_only": True,
        "production_authority": False,
        "automatic_execution": False,
        "auto_promotion": False,
    }


def _ensure_active_edge_tables(runtime: ManagementEdgeRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1m_active_edge_t0 (
                observation_id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL UNIQUE,
                captured_ts REAL NOT NULL,
                contract_version TEXT NOT NULL,
                edge_policy TEXT,
                risk_acceptance TEXT,
                available INTEGER NOT NULL,
                matched_structured_signal_n INTEGER NOT NULL,
                supporting_position_n INTEGER NOT NULL,
                opposing_position_n INTEGER NOT NULL,
                net_position_vote INTEGER NOT NULL,
                ml_signal_n INTEGER NOT NULL,
                strict_reference_signal_n INTEGER NOT NULL,
                context_json TEXT NOT NULL,
                context_sha256 TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1m_active_edge_t0_immutable_update
            BEFORE UPDATE ON g1m_active_edge_t0
            BEGIN SELECT RAISE(ABORT,'immutable G1M active-edge T0 row'); END
        """)
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1m_active_edge_t0_immutable_delete
            BEFORE DELETE ON g1m_active_edge_t0
            BEGIN SELECT RAISE(ABORT,'immutable G1M active-edge T0 row'); END
        """)


def _load_snapshot(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _store_active_edge_t0(runtime: ManagementEdgeRuntime, source: Any) -> bool:
    review_id = str(source["review_id"])
    with runtime._lock:
        observation = runtime._conn.execute(
            "SELECT observation_id,captured_ts FROM g1m_management_observations "
            "WHERE review_id=?", (review_id,),
        ).fetchone()
    if observation is None:
        return False

    context = compact_active_edge_t0(_load_snapshot(source["snapshot_json"]))
    raw = _json(context)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1m_active_edge_t0("
            "observation_id,review_id,captured_ts,contract_version,edge_policy,"
            "risk_acceptance,available,matched_structured_signal_n,"
            "supporting_position_n,opposing_position_n,net_position_vote,ml_signal_n,"
            "strict_reference_signal_n,context_json,context_sha256,created_ts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(observation["observation_id"]), review_id,
                float(observation["captured_ts"]), G1M_ACTIVE_EDGE_T0_VERSION,
                context["edge_policy"], context["risk_acceptance"],
                int(context["available"]), context["matched_structured_signal_n"],
                context["supporting_position_n"], context["opposing_position_n"],
                context["net_position_vote"], context["ml_signal_n"],
                context["strict_reference_signal_n"], raw, _sha_text(raw), time.time(),
            ),
        )
    return True


def install_g1_management_active_edge_t0() -> None:
    """Install the passive G1-M T0 freezer on the fully refined runtime."""
    global _INSTALLED
    if _INSTALLED or getattr(
            ManagementEdgeRuntime, "_g1m_active_edge_t0_version", None
    ) == G1M_ACTIVE_EDGE_T0_VERSION:
        return
    _INSTALLED = True

    original_ensure = ManagementEdgeRuntime._ensure_tables
    original_capture = ManagementEdgeRuntime._capture_observation
    original_status = ManagementEdgeRuntime.status
    original_decision = ManagementEdgeRuntime.decision

    def ensure_tables(self) -> None:
        original_ensure(self)
        _ensure_active_edge_tables(self)

    def capture_observation(self, source: sqlite3.Row) -> bool:
        inserted = original_capture(self, source)
        if not inserted:
            return False
        try:
            _store_active_edge_t0(self, source)
        except Exception as exc:
            # The sidecar is evidence-only and must never cancel a valid G1-M
            # observation or alter a production decision. Surface the defect in
            # the existing immutable contract-error ledger instead.
            try:
                self._error(
                    code="ACTIVE_EDGE_T0_SIDECAR_EXCEPTION",
                    detail=str(exc),
                    critical=False,
                    review_id=str(source["review_id"]),
                    trade_id=int(source["trade_id"]),
                )
            except Exception:
                pass
        return True

    def status(self) -> dict:
        body = original_status(self)
        with self._lock:
            row = self._conn.execute("""
                SELECT COUNT(*) AS captured_n,
                       SUM(CASE WHEN available=1 THEN 1 ELSE 0 END) AS available_n,
                       SUM(matched_structured_signal_n) AS matched_n,
                       SUM(supporting_position_n) AS supporting_n,
                       SUM(opposing_position_n) AS opposing_n,
                       SUM(ml_signal_n) AS ml_n
                FROM g1m_active_edge_t0
            """).fetchone()
        body["active_edge_t0"] = {
            "contract_version": G1M_ACTIVE_EDGE_T0_VERSION,
            "captured_n": int(row["captured_n"] or 0),
            "available_n": int(row["available_n"] or 0),
            "matched_structured_signal_n": int(row["matched_n"] or 0),
            "supporting_position_n": int(row["supporting_n"] or 0),
            "opposing_position_n": int(row["opposing_n"] or 0),
            "ml_signal_n": int(row["ml_n"] or 0),
            "decision_weight_applied": False,
            "production_authority": False,
        }
        return body

    def decision(self, observation_id: str) -> dict | None:
        body = original_decision(self, observation_id)
        if body is None:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM g1m_active_edge_t0 WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
        if row is None:
            body["active_edge_t0"] = None
        else:
            frozen = dict(row)
            frozen["context"] = _load_snapshot(frozen.get("context_json"))
            body["active_edge_t0"] = frozen
        return body

    ManagementEdgeRuntime._ensure_tables = ensure_tables
    ManagementEdgeRuntime._capture_observation = capture_observation
    ManagementEdgeRuntime.status = status
    ManagementEdgeRuntime.decision = decision
    ManagementEdgeRuntime._g1m_active_edge_t0_version = G1M_ACTIVE_EDGE_T0_VERSION
