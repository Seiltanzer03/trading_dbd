"""G.1-M.1: short-horizon management feedback, separate from terminal edge.

Each frozen G.1-M decision is replayed only on the observed path available through
15/30/60/120 trading minutes.  The same authoritative decision counterfactual
replay is reused; no alternate execution simulator is introduced.  Pre-existing
G.1-M prospective decisions are exposed descriptively, but only windows frozen
after G.1-M.1 activation are evidence-eligible.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from typing import Any

from .decision_research import POLICY_FRACTIONS, counterfactual_replay
from . import passive_learning as _pl


G1M_LOCAL_CONTRACT_VERSION = "g1m-local-feedback-v1"
G1M_LOCAL_WINDOW_VERSION = "g1m-local-window-v1"
G1M_LOCAL_OUTCOME_VERSION = "g1m-local-outcome-v1"
LOCAL_HORIZONS = (15, 30, 60, 120)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value)) if value is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class ManagementLocalRuntime:
    def __init__(self, engine):
        self.engine = engine
        self.management = engine.management
        self._conn = engine.passive._conn
        self._lock = engine.passive._lock
        self._ensure_tables()
        self.activation_ts = self._activation_ts()
        self._last_error: str | None = None
        self._last_step_ts: float | None = None

    def _ensure_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_local_activation(
                    id INTEGER PRIMARY KEY CHECK(id=1),activation_ts REAL NOT NULL,
                    contract_version TEXT NOT NULL)""")
            self._conn.execute(
                "INSERT OR IGNORE INTO g1m_local_activation(id,activation_ts,contract_version) "
                "VALUES(1,?,?)", (time.time(), G1M_LOCAL_CONTRACT_VERSION))
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_local_windows(
                    window_id TEXT PRIMARY KEY,
                    observation_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    trade_id INTEGER NOT NULL,
                    instrument TEXT NOT NULL,
                    captured_ts REAL NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    target_ts REAL NOT NULL,
                    origin TEXT NOT NULL,
                    evidence_eligible INTEGER NOT NULL,
                    window_json TEXT NOT NULL,
                    window_sha256 TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    UNIQUE(observation_id,horizon_minutes))""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1m_local_due "
                "ON g1m_local_windows(target_ts,observation_id)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_local_outcomes(
                    window_id TEXT PRIMARY KEY,
                    observation_id TEXT NOT NULL,
                    resolved_ts REAL NOT NULL,
                    path_end_ts REAL NOT NULL,
                    path_point_n INTEGER NOT NULL,
                    production_policy TEXT NOT NULL,
                    realized_best_action TEXT NOT NULL,
                    production_local_r REAL NOT NULL,
                    hold_local_r REAL NOT NULL,
                    original_plan_local_r REAL NOT NULL,
                    production_mva_vs_hold_r REAL NOT NULL,
                    production_regret_r REAL NOT NULL,
                    mfe_r REAL,
                    mae_r REAL,
                    outcome_json TEXT NOT NULL,
                    outcome_sha256 TEXT NOT NULL,
                    created_ts REAL NOT NULL)""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1m_local_outcome_observation "
                "ON g1m_local_outcomes(observation_id,resolved_ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_local_policy_outcomes(
                    window_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    terminal_r REAL NOT NULL,
                    mva_vs_hold_r REAL NOT NULL,
                    regret_r REAL NOT NULL,
                    outcome_json TEXT NOT NULL,
                    PRIMARY KEY(window_id,policy_name))""")
            for table in ("g1m_local_activation", "g1m_local_windows",
                          "g1m_local_outcomes", "g1m_local_policy_outcomes"):
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1M local row'); END""")
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1M local row'); END""")

    def _activation_ts(self) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT activation_ts FROM g1m_local_activation WHERE id=1").fetchone()
        return float(row[0])

    def materialize_windows(self, limit: int = 500) -> int:
        with self._lock:
            rows = self._conn.execute("""
                SELECT g.observation_id,g.review_id,g.trade_id,g.captured_ts,g.origin,
                       g.policy_edge_eligible,c.instrument
                FROM g1m_management_observations g
                JOIN g1m_observation_context c USING(observation_id)
                ORDER BY g.captured_ts,g.observation_id LIMIT ?
            """, (max(1, min(int(limit), 5000)),)).fetchall()
        created = 0
        for row in rows:
            instrument = str(row["instrument"] or "")
            if not instrument:
                continue
            source_was_prospective = bool(int(row["policy_edge_eligible"] or 0))
            live_after_activation = float(row["captured_ts"]) >= self.activation_ts - 1e-9
            origin = "LIVE_PROSPECTIVE" if live_after_activation else (
                "PREEXISTING_PROSPECTIVE_DESCRIPTIVE" if source_was_prospective
                else "RESEARCH_BACKFILL")
            eligible = source_was_prospective and live_after_activation
            for horizon in LOCAL_HORIZONS:
                try:
                    target = _pl._advance_trading_time(
                        instrument, float(row["captured_ts"]), horizon)
                except Exception:
                    target = float(row["captured_ts"]) + horizon * 60.0
                payload = {
                    "contract_version": G1M_LOCAL_WINDOW_VERSION,
                    "observation_id": str(row["observation_id"]),
                    "review_id": str(row["review_id"]), "trade_id": int(row["trade_id"]),
                    "instrument": instrument, "captured_ts": float(row["captured_ts"]),
                    "horizon_minutes": horizon, "target_ts": target,
                    "origin": origin, "evidence_eligible": eligible,
                    "time_basis": "trading_minutes",
                }
                window_id = "g1ml-" + _sha(payload)[:30]
                raw = _json(payload)
                with self._lock, self._conn:
                    cur = self._conn.execute(
                        "INSERT OR IGNORE INTO g1m_local_windows(window_id,observation_id,"
                        "review_id,trade_id,instrument,captured_ts,horizon_minutes,target_ts,origin,"
                        "evidence_eligible,window_json,window_sha256,created_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (window_id, row["observation_id"], row["review_id"], int(row["trade_id"]),
                         instrument, float(row["captured_ts"]), horizon, target, origin,
                         int(eligible), raw, hashlib.sha256(raw.encode()).hexdigest(), time.time()))
                    created += int(cur.rowcount > 0)
        return created

    def _path_to_target(self, review_id: str, captured: float, target: float) -> list[dict] | None:
        with self._lock:
            points = [dict(r) for r in self._conn.execute(
                "SELECT ts,r,price FROM decision_path_points WHERE review_id=? "
                "AND ts>=? AND ts<=? ORDER BY ts",
                (review_id, captured-1e-9, target+1e-9)).fetchall()]
            next_point = self._conn.execute(
                "SELECT ts,r,price FROM decision_path_points WHERE review_id=? AND ts>? "
                "ORDER BY ts LIMIT 1", (review_id, target)).fetchone()
        if not points:
            return None
        last_ts = float(points[-1]["ts"])
        # Terminal local outcome must be observed close to the frozen horizon. We do
        # not silently use a price minutes/hours before target or a post-target point.
        if target - last_ts > 90.0:
            return None
        # A next point proves collection continued through the horizon. It is never
        # fed into replay, so post-target market data cannot affect the outcome.
        if last_ts < target - 1e-6 and next_point is None:
            return None
        return points

    def resolve_due(self, *, now: float | None = None, limit: int = 500) -> int:
        now = float(now or time.time())
        with self._lock:
            windows = self._conn.execute("""
                SELECT w.* FROM g1m_local_windows w
                LEFT JOIN g1m_local_outcomes o USING(window_id)
                WHERE o.window_id IS NULL AND w.target_ts<=?
                ORDER BY w.target_ts LIMIT ?
            """, (now, max(1, min(int(limit), 5000)))).fetchall()
        resolved = 0
        for window in windows:
            with self._lock:
                source = self._conn.execute(
                    "SELECT snapshot_json,snapshot_sha256 FROM decision_snapshots WHERE review_id=?",
                    (window["review_id"],)).fetchone()
            if source is None:
                continue
            snapshot_raw = str(source["snapshot_json"])
            if hashlib.sha256(snapshot_raw.encode()).hexdigest() != str(source["snapshot_sha256"]):
                self._last_error = "decision snapshot SHA mismatch"
                continue
            snapshot = _loads(snapshot_raw, {})
            points = self._path_to_target(
                str(window["review_id"]), float(window["captured_ts"]), float(window["target_ts"]))
            if not points:
                continue
            try:
                replay = counterfactual_replay(snapshot, points)
                standard = replay.get("policies") or {}
                if not all(name in standard for name in POLICY_FRACTIONS):
                    continue
                original_future = self.management._original_plan_outcome(snapshot, points)
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
                continue
            with self._lock:
                obs = self._conn.execute(
                    "SELECT realized_before_r,remaining_before,production_policy "
                    "FROM g1m_management_observations WHERE observation_id=?",
                    (window["observation_id"],)).fetchone()
            if obs is None:
                continue
            realized_before = float(obs["realized_before_r"])
            remaining = float(obs["remaining_before"])
            production = str(obs["production_policy"])
            values = {name: float(standard[name]["net_realized_r"])
                      for name in POLICY_FRACTIONS}
            values["ORIGINAL_PLAN"] = realized_before + remaining * float(original_future)
            values["PRODUCTION_POLICY"] = values[production]
            hold = values["HOLD"]
            best_name = max((*POLICY_FRACTIONS.keys(), "ORIGINAL_PLAN"), key=lambda n: values[n])
            best = values[best_name]
            outcome_payload = {
                "contract_version": G1M_LOCAL_OUTCOME_VERSION,
                "window_id": str(window["window_id"]),
                "observation_id": str(window["observation_id"]),
                "horizon_minutes": int(window["horizon_minutes"]),
                "target_ts": float(window["target_ts"]),
                "path_end_ts": float(points[-1]["ts"]),
                "path_point_n": len(points),
                "production_policy": production,
                "realized_best_action": best_name,
                "production_local_r": values[production],
                "hold_local_r": hold,
                "original_plan_local_r": values["ORIGINAL_PLAN"],
                "production_mva_vs_hold_r": values[production] - hold,
                "production_regret_r": best - values[production],
                "mfe_r": _finite(replay.get("mfe_r")), "mae_r": _finite(replay.get("mae_r")),
                "evidence_eligible": bool(window["evidence_eligible"]),
                "semantics": "LOCAL_DECISION_QUALITY_NOT_TERMINAL_MANAGEMENT_EDGE",
                "source": "authoritative_counterfactual_replay_truncated_at_frozen_horizon",
            }
            raw = _json(outcome_payload)
            with self._lock, self._conn:
                for name, terminal in values.items():
                    policy_payload = {
                        "contract_version": G1M_LOCAL_OUTCOME_VERSION,
                        "window_id": str(window["window_id"]), "policy": name,
                        "terminal_r": terminal, "mva_vs_hold_r": terminal-hold,
                        "regret_r": best-terminal,
                    }
                    self._conn.execute(
                        "INSERT OR IGNORE INTO g1m_local_policy_outcomes(window_id,policy_name,"
                        "terminal_r,mva_vs_hold_r,regret_r,outcome_json) VALUES(?,?,?,?,?,?)",
                        (window["window_id"], name, terminal, terminal-hold, best-terminal,
                         _json(policy_payload)))
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1m_local_outcomes(window_id,observation_id,resolved_ts,"
                    "path_end_ts,path_point_n,production_policy,realized_best_action,"
                    "production_local_r,hold_local_r,original_plan_local_r,"
                    "production_mva_vs_hold_r,production_regret_r,mfe_r,mae_r,"
                    "outcome_json,outcome_sha256,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (window["window_id"], window["observation_id"], now, float(points[-1]["ts"]),
                     len(points), production, best_name, values[production], hold,
                     values["ORIGINAL_PLAN"], values[production]-hold,
                     best-values[production], _finite(replay.get("mfe_r")),
                     _finite(replay.get("mae_r")), raw, hashlib.sha256(raw.encode()).hexdigest(),
                     time.time()))
            resolved += 1
        return resolved

    def outcomes(self, *, limit: int = 200) -> dict:
        with self._lock:
            rows = self._conn.execute("""
                SELECT w.horizon_minutes,w.origin,w.evidence_eligible,w.target_ts,
                       o.* FROM g1m_local_outcomes o JOIN g1m_local_windows w USING(window_id)
                ORDER BY o.resolved_ts DESC LIMIT ?
            """, (max(1, min(int(limit), 1000)),)).fetchall()
        return {"contract_version": G1M_LOCAL_CONTRACT_VERSION,
                "semantics": "LOCAL_DECISION_QUALITY_NOT_TERMINAL_MANAGEMENT_EDGE",
                "items": [dict(r) for r in rows]}

    def edge(self) -> dict:
        with self._lock:
            rows = [dict(r) for r in self._conn.execute("""
                SELECT w.horizon_minutes,w.observation_id,w.trade_id,
                       o.production_mva_vs_hold_r,o.production_regret_r
                FROM g1m_local_windows w JOIN g1m_local_outcomes o USING(window_id)
                WHERE w.evidence_eligible=1 ORDER BY w.captured_ts
            """).fetchall()]
        by_horizon = defaultdict(list)
        for row in rows:
            by_horizon[int(row["horizon_minutes"])].append(row)
        items = []
        for horizon in LOCAL_HORIZONS:
            group = by_horizon[horizon]
            values = [float(r["production_mva_vs_hold_r"]) for r in group]
            trades = {int(r["trade_id"]) for r in group}
            items.append({
                "horizon_minutes": horizon, "raw_n": len(group),
                "unique_trades": len(trades), "effective_n": len(trades),
                "mean_mva_vs_hold_r": sum(values)/len(values) if values else None,
                "positive_n": sum(v > 1e-12 for v in values),
                "negative_n": sum(v < -1e-12 for v in values),
                "edge_claim_allowed": False,
            })
        return {"contract_version": G1M_LOCAL_CONTRACT_VERSION, "items": items,
                "terminal_edge_separate": True, "production_authority": False,
                "edge_claim_allowed": False}

    def status(self) -> dict:
        with self._lock:
            windows = int(self._conn.execute("SELECT COUNT(*) FROM g1m_local_windows").fetchone()[0])
            resolved = int(self._conn.execute("SELECT COUNT(*) FROM g1m_local_outcomes").fetchone()[0])
            eligible = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1m_local_windows WHERE evidence_eligible=1").fetchone()[0])
            eligible_resolved = int(self._conn.execute("""
                SELECT COUNT(*) FROM g1m_local_windows w JOIN g1m_local_outcomes o USING(window_id)
                WHERE w.evidence_eligible=1""").fetchone()[0])
        return {"contract_version": G1M_LOCAL_CONTRACT_VERSION, "windows": windows,
                "resolved": resolved, "pending": max(0, windows-resolved),
                "evidence_eligible": eligible, "eligible_resolved": eligible_resolved,
                "last_step_ts": self._last_step_ts, "last_error": self._last_error,
                "authority": {"research_only": True, "production_authority": False,
                              "auto_execution_allowed": False,
                              "policy_promotion_allowed": False,
                              "edge_claim_allowed": False}}

    def step(self) -> dict:
        try:
            windows = self.materialize_windows()
            resolved = self.resolve_due()
            self._last_error = None
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            windows = resolved = 0
        self._last_step_ts = time.time()
        return {"windows_created": windows, "resolved": resolved,
                "error": self._last_error}
