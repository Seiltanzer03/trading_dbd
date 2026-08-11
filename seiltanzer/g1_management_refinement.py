"""Integrity refinements for Phase G.1-M.

Keeps the base runtime compact while tightening prospective admission, freezing
cohort context at capture time, making contract errors immutable and exposing
reproducible research cuts.  No production action logic is touched.
"""
from __future__ import annotations

import hashlib
import json
import time

from .g1_management_runtime import (
    G1M_RESEARCH_CUT_VERSION,
    ManagementEdgeRuntime,
)


REFINEMENT_VERSION = "g1m-integrity-refinement-v1"


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _sha(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_ORIGINAL_ENSURE = ManagementEdgeRuntime._ensure_tables
_ORIGINAL_CAPTURE = ManagementEdgeRuntime._capture_observation
_ORIGINAL_STATUS = ManagementEdgeRuntime.status


def ensure_tables_refined(self) -> None:
    _ORIGINAL_ENSURE(self)
    with self._lock, self._conn:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1m_observation_context (
                observation_id TEXT PRIMARY KEY,
                instrument TEXT,
                direction TEXT,
                setup INTEGER,
                context_json TEXT NOT NULL,
                context_sha256 TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1m_context_instrument "
            "ON g1m_observation_context(instrument,observation_id)")
        for table in ("g1m_contract_errors", "g1m_observation_context"):
            self._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1M row'); END""")
            self._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1M row'); END""")


def capture_observation_refined(self, source) -> bool:
    review_id = str(source["review_id"])
    trade_id = int(source["trade_id"])
    captured_ts = float(source["captured_ts"])

    # A prospective T0 cannot be admitted if its outcome was already known by T0.
    # Historical replays normally resolve after capture and remain backfill; only
    # a resolution timestamp <= capture is a true temporal-contract violation.
    with self._lock:
        replay = self._conn.execute(
            "SELECT resolved_ts FROM decision_replays WHERE review_id=?",
            (review_id,),
        ).fetchone()
    if replay is not None and float(replay["resolved_ts"]) <= captured_ts + 1e-9:
        self._error(
            code="DECISION_AFTER_OUTCOME",
            detail="decision replay was resolved at or before T0",
            critical=True,
            review_id=review_id,
            trade_id=trade_id,
        )
        return False

    inserted = _ORIGINAL_CAPTURE(self, source)
    if not inserted:
        return False

    # Freeze cohort identity now. Never read mutable current trade metadata later
    # when attributing prospective evidence.
    with self._lock, self._conn:
        obs = self._conn.execute(
            "SELECT observation_id FROM g1m_management_observations WHERE review_id=?",
            (review_id,),
        ).fetchone()
        trade = self._conn.execute(
            "SELECT instrument,direction,setup FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        if obs is not None:
            context = {
                "observation_id": str(obs["observation_id"]),
                "trade_id": trade_id,
                "instrument": trade["instrument"] if trade is not None else None,
                "direction": trade["direction"] if trade is not None else None,
                "setup": trade["setup"] if trade is not None else None,
                "frozen_at_t0_capture": True,
                "refinement_version": REFINEMENT_VERSION,
            }
            raw = _json(context)
            self._conn.execute(
                "INSERT OR IGNORE INTO g1m_observation_context("
                "observation_id,instrument,direction,setup,context_json,context_sha256,created_ts)"
                " VALUES(?,?,?,?,?,?,?)",
                (context["observation_id"], context["instrument"], context["direction"],
                 context["setup"], raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                 time.time()),
            )
    return True


def cohorts_refined(self) -> dict:
    with self._lock:
        rows = self._conn.execute("""
            SELECT g.production_policy,g.origin,g.policy_version,c.instrument,c.direction,c.setup,
                   COUNT(*) raw_n,COUNT(DISTINCT g.trade_id) trade_n,
                   AVG(o.mva_vs_hold_r) mean_mva_vs_hold_r
            FROM g1m_management_observations g
            LEFT JOIN g1m_observation_context c USING(observation_id)
            LEFT JOIN g1m_policy_outcomes o
              ON o.observation_id=g.observation_id AND o.policy_name='PRODUCTION_POLICY'
            GROUP BY g.production_policy,g.origin,g.policy_version,
                     c.instrument,c.direction,c.setup
            ORDER BY raw_n DESC
        """).fetchall()
    return {
        "contract_version": self.status()["g1m_contract_version"],
        "context_contract": REFINEMENT_VERSION,
        "items": [dict(row) for row in rows],
    }


def create_research_cut(self, *, cutoff_ts: float | None = None) -> dict:
    cutoff = float(cutoff_ts if cutoff_ts is not None else time.time())
    with self._lock:
        rows = self._conn.execute("""
            SELECT g.observation_id,g.trade_id,g.t0_payload_sha256,
                   z.resolution_sha256,z.resolved_ts
            FROM g1m_management_observations g
            JOIN g1m_resolutions z USING(observation_id)
            WHERE g.policy_edge_eligible=1
              AND g.captured_ts<=?
              AND z.resolved_ts<=?
            ORDER BY g.captured_ts,g.observation_id
        """, (cutoff, cutoff)).fetchall()
    members = [
        {
            "observation_id": str(row["observation_id"]),
            "trade_id": int(row["trade_id"]),
            "t0_sha256": str(row["t0_payload_sha256"]),
            "resolution_sha256": str(row["resolution_sha256"]),
        }
        for row in rows
    ]
    trade_ids = {row["trade_id"] for row in members}
    # Under the published v1 dependency contract, all decisions from one trade
    # share total weight 1, therefore the exact aggregate effective N is trades N.
    effective_n = float(len(trade_ids))
    source_sha = _sha(members)
    cut_payload = {
        "contract_version": G1M_RESEARCH_CUT_VERSION,
        "cutoff_ts": cutoff,
        "source_sha256": source_sha,
        "source_ids": [row["observation_id"] for row in members],
        "raw_n": len(members),
        "unique_trade_n": len(trade_ids),
        "effective_n": effective_n,
    }
    cut_id = "g1m-cut-" + _sha(cut_payload)[:28]
    with self._lock, self._conn:
        existing = self._conn.execute(
            "SELECT source_sha256 FROM g1m_research_cuts WHERE cut_id=?",
            (cut_id,),
        ).fetchone()
        if existing is not None and str(existing["source_sha256"]) != source_sha:
            raise ValueError("immutable G1M cut collision")
        self._conn.execute(
            "INSERT OR IGNORE INTO g1m_research_cuts("
            "cut_id,cutoff_ts,contract_version,source_ids_json,source_sha256,"
            "raw_n,unique_trade_n,effective_n,created_ts) VALUES(?,?,?,?,?,?,?,?,?)",
            (cut_id, cutoff, G1M_RESEARCH_CUT_VERSION,
             _json(cut_payload["source_ids"]), source_sha, len(members),
             len(trade_ids), effective_n, time.time()),
        )
    return {"cut_id": cut_id, **cut_payload}


def research_cuts(self, limit: int = 50) -> dict:
    with self._lock:
        rows = self._conn.execute(
            "SELECT * FROM g1m_research_cuts ORDER BY cutoff_ts DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return {
        "contract_version": G1M_RESEARCH_CUT_VERSION,
        "items": [dict(row) for row in rows],
    }


def status_refined(self) -> dict:
    body = _ORIGINAL_STATUS(self)
    body["integrity_refinement_version"] = REFINEMENT_VERSION
    body["dependency_groups"] = body.get("unique_trades", 0)
    body["research_cut_count"] = int(self._conn.execute(
        "SELECT COUNT(*) FROM g1m_research_cuts").fetchone()[0])
    return body


def install_g1_management_refinement() -> None:
    if getattr(ManagementEdgeRuntime, "_g1m_refinement_version", None) == REFINEMENT_VERSION:
        return
    ManagementEdgeRuntime._ensure_tables = ensure_tables_refined
    ManagementEdgeRuntime._capture_observation = capture_observation_refined
    ManagementEdgeRuntime.cohorts = cohorts_refined
    ManagementEdgeRuntime.create_research_cut = create_research_cut
    ManagementEdgeRuntime.research_cuts = research_cuts
    ManagementEdgeRuntime.status = status_refined
    ManagementEdgeRuntime._g1m_refinement_version = REFINEMENT_VERSION
