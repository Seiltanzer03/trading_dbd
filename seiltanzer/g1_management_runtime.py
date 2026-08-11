"""Phase G.1-M prospective management-edge measurement.

This layer never changes the production policy.  It consumes the already-frozen
``decision_snapshots`` and authoritative realized-path ``decision_replays`` and
adds immutable attribution: policy edge, execution/compliance edge, dependency
weights and OOS-readiness evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from statistics import median
from typing import Any

from .decision_research import POLICY_FRACTIONS
from .execution_simulator import ExecutionSpec, replay_execution_path


G1M_STAGE = "G.1-M"
G1M_CONTRACT_VERSION = "g1m-management-edge-v1"
G1M_OBSERVATION_VERSION = "g1m-management-observation-v1"
G1M_COUNTERFACTUAL_VERSION = "g1m-counterfactual-policy-v1"
G1M_ATTRIBUTION_VERSION = "g1m-execution-attribution-v1"
G1M_WEIGHT_VERSION = "g1m-trade-dependency-weight-v1"
G1M_READINESS_VERSION = "g1m-oos-readiness-v1"
G1M_RESEARCH_CUT_VERSION = "g1m-research-cut-v1"

ACTION_SET = ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
COMPARATORS = (*ACTION_SET, "ORIGINAL_PLAN", "PRODUCTION_POLICY")
READINESS_REQUIRED = {
    "raw_observations": 200,
    "unique_trades": 100,
    "effective_n": 80,
    "temporal_periods": 3,
    "positive_mva": 20,
    "negative_mva": 20,
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else default
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _at(value: Any, *path: str, default=None):
    current = value
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = min(max(q, 0.0), 1.0) * (len(xs) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    f = pos - lo
    return xs[lo] * (1.0 - f) + xs[hi] * f


def _cvar10(values: list[float]) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    n = max(1, int(math.ceil(len(xs) * 0.10)))
    return sum(xs[:n]) / n


class ManagementEdgeRuntime:
    """Materializes G.1-M evidence from frozen production decision records."""

    def __init__(self, engine):
        self.engine = engine
        # Reuse the passive connection/lock. Storage durability already configures
        # this connection with WAL/FULL synchronous/busy_timeout.
        self._conn = engine.passive._conn
        self._lock = engine.passive._lock
        self._ensure_tables()
        self.activation_ts = self._activation_ts()

    # ---------------------------------------------------------------- schema

    def _ensure_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_runtime_activation (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    activation_ts REAL NOT NULL,
                    contract_version TEXT NOT NULL
                )""")
            self._conn.execute(
                "INSERT OR IGNORE INTO g1m_runtime_activation(id,activation_ts,contract_version) "
                "VALUES(1,?,?)", (time.time(), G1M_CONTRACT_VERSION))
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_management_observations (
                    observation_id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL UNIQUE,
                    trade_id INTEGER NOT NULL,
                    captured_ts REAL NOT NULL,
                    origin TEXT NOT NULL,
                    production_policy TEXT NOT NULL,
                    policy_version TEXT,
                    snapshot_sha256 TEXT NOT NULL,
                    t0_payload_sha256 TEXT NOT NULL,
                    current_price REAL,
                    current_r REAL,
                    remaining_before REAL NOT NULL,
                    realized_before_r REAL NOT NULL,
                    entry REAL,
                    original_stop REAL,
                    active_stop REAL,
                    take_price REAL,
                    measurement_eligible INTEGER NOT NULL,
                    policy_edge_eligible INTEGER NOT NULL,
                    execution_edge_eligible INTEGER NOT NULL,
                    exclusion_reason TEXT,
                    observation_json TEXT NOT NULL,
                    created_ts REAL NOT NULL
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1m_obs_trade_ts "
                "ON g1m_management_observations(trade_id,captured_ts)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1m_obs_eligibility "
                "ON g1m_management_observations(policy_edge_eligible,captured_ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_counterfactual_policies (
                    observation_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    immediate_close_fraction REAL,
                    continuation_contract TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    policy_sha256 TEXT NOT NULL,
                    PRIMARY KEY(observation_id,policy_name)
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_t0_policy_metrics (
                    observation_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    expected_r REAL,
                    median_r REAL,
                    cvar10_r REAL,
                    p_loss REAL,
                    metrics_json TEXT NOT NULL,
                    PRIMARY KEY(observation_id,policy_name)
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_resolutions (
                    observation_id TEXT PRIMARY KEY,
                    review_id TEXT NOT NULL UNIQUE,
                    trade_id INTEGER NOT NULL,
                    resolved_ts REAL NOT NULL,
                    resolution_kind TEXT NOT NULL,
                    source_replay_version TEXT NOT NULL,
                    source_snapshot_sha256 TEXT NOT NULL,
                    realized_best_action TEXT,
                    production_regret_r REAL,
                    resolution_json TEXT NOT NULL,
                    resolution_sha256 TEXT NOT NULL
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_policy_outcomes (
                    observation_id TEXT NOT NULL,
                    policy_name TEXT NOT NULL,
                    terminal_r REAL NOT NULL,
                    management_incremental_r REAL NOT NULL,
                    mva_vs_hold_r REAL,
                    mva_vs_original_plan_r REAL,
                    mva_vs_exit_r REAL,
                    regret_r REAL,
                    loss_avoided_vs_hold_r REAL,
                    upside_sacrificed_vs_hold_r REAL,
                    outcome_json TEXT NOT NULL,
                    PRIMARY KEY(observation_id,policy_name)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1m_outcome_policy "
                "ON g1m_policy_outcomes(policy_name,observation_id)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_execution_attribution (
                    observation_id TEXT PRIMARY KEY,
                    decision_id TEXT,
                    recommendation_status TEXT NOT NULL,
                    compliance_state TEXT NOT NULL,
                    production_policy TEXT NOT NULL,
                    actual_policy TEXT,
                    production_terminal_r REAL,
                    actual_terminal_r REAL,
                    compliance_delta_r REAL,
                    attribution_json TEXT NOT NULL,
                    attribution_sha256 TEXT NOT NULL
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_contract_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_id TEXT,
                    review_id TEXT,
                    trade_id INTEGER,
                    ts REAL NOT NULL,
                    error_code TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    critical INTEGER NOT NULL DEFAULT 0
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1m_error_code_ts "
                "ON g1m_contract_errors(error_code,ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1m_research_cuts (
                    cut_id TEXT PRIMARY KEY,
                    cutoff_ts REAL NOT NULL,
                    contract_version TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    raw_n INTEGER NOT NULL,
                    unique_trade_n INTEGER NOT NULL,
                    effective_n REAL NOT NULL,
                    created_ts REAL NOT NULL
                )""")
            for table in (
                "g1m_runtime_activation", "g1m_management_observations",
                "g1m_counterfactual_policies", "g1m_t0_policy_metrics",
                "g1m_resolutions", "g1m_policy_outcomes",
                "g1m_execution_attribution", "g1m_research_cuts",
            ):
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1M row'); END""")
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1M row'); END""")

    def _activation_ts(self) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT activation_ts FROM g1m_runtime_activation WHERE id=1"
            ).fetchone()
        return float(row[0])

    # --------------------------------------------------------------- helpers

    def _error(self, *, code: str, detail: str, critical: bool = False,
               observation_id: str | None = None, review_id: str | None = None,
               trade_id: int | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO g1m_contract_errors(observation_id,review_id,trade_id,ts,"
                "error_code,detail,critical) VALUES(?,?,?,?,?,?,?)",
                (observation_id, review_id, trade_id, time.time(), code,
                 str(detail)[:2000], int(bool(critical))))

    @staticmethod
    def _position(snapshot: dict) -> dict:
        return snapshot.get("position_state") or {}

    @staticmethod
    def _policy_metrics(snapshot: dict, policy: str) -> dict:
        row = ((_at(snapshot, "policy_manager", "policies", default={}) or {})
               .get(policy) or {})
        expected = _finite(row.get("expected_net_r"))
        if expected is None:
            expected = _finite(row.get("expected_final_r"))
        median_r = _finite(row.get("median_net_r"))
        if median_r is None:
            median_r = _finite(row.get("median_r"))
        cvar = _finite(row.get("cvar10_net_r"))
        if cvar is None:
            cvar = _finite(row.get("cvar10_r"))
        p_loss = _finite(row.get("p_loss"))
        return {
            "expected_r": expected, "median_r": median_r,
            "cvar10_r": cvar, "p_loss": p_loss,
            "source": "frozen_policy_manager" if row else "not_available",
            "raw": row,
        }

    def _freeze_policies(self, observation_id: str, snapshot: dict,
                         production_policy: str) -> None:
        rows: list[tuple[str, float | None, str, dict]] = []
        for policy in ACTION_SET:
            rows.append((
                policy, POLICY_FRACTIONS[policy],
                "NO_CONTINUATION" if policy == "EXIT"
                else "CURRENT_MANAGED_CONTINUATION_V1",
                {"policy": policy, "immediate_close_fraction": POLICY_FRACTIONS[policy],
                 "continuation": "none" if policy == "EXIT"
                 else "authoritative_execution_simulator_current_state"},
            ))
        rows.append((
            "ORIGINAL_PLAN", 0.0, "ORIGINAL_STOP_TAKE_NO_NEW_MANAGEMENT_V1",
            {"policy": "ORIGINAL_PLAN", "immediate_close_fraction": 0.0,
             "continuation": "original_stop_take_without_future_be_or_ladder"},
        ))
        rows.append((
            "PRODUCTION_POLICY", POLICY_FRACTIONS.get(production_policy),
            f"ALIAS:{production_policy}",
            {"policy": "PRODUCTION_POLICY", "alias": production_policy},
        ))
        with self._lock, self._conn:
            for name, fraction, continuation, payload in rows:
                raw = _json(payload)
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1m_counterfactual_policies("
                    "observation_id,policy_name,immediate_close_fraction,"
                    "continuation_contract,policy_json,policy_sha256) VALUES(?,?,?,?,?,?)",
                    (observation_id, name, fraction, continuation, raw, _sha_text(raw)))
                metric_policy = production_policy if name == "PRODUCTION_POLICY" else name
                metrics = (self._policy_metrics(snapshot, metric_policy)
                           if metric_policy in ACTION_SET else {
                               "expected_r": None, "median_r": None,
                               "cvar10_r": None, "p_loss": None,
                               "source": "not_computed_for_original_plan", "raw": {}})
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1m_t0_policy_metrics("
                    "observation_id,policy_name,expected_r,median_r,cvar10_r,p_loss,metrics_json)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (observation_id, name, metrics["expected_r"], metrics["median_r"],
                     metrics["cvar10_r"], metrics["p_loss"], _json(metrics)))

    # ---------------------------------------------------------- T0 capture

    def _capture_observation(self, source: sqlite3.Row) -> bool:
        review_id = str(source["review_id"])
        trade_id = int(source["trade_id"])
        captured = float(source["captured_ts"])
        raw_snapshot = str(source["snapshot_json"])
        source_sha = str(source["snapshot_sha256"])
        if _sha_text(raw_snapshot) != source_sha:
            self._error(code="T0_HASH_MISMATCH", detail="decision snapshot sha mismatch",
                        critical=True, review_id=review_id, trade_id=trade_id)
            return False
        snapshot = _loads(raw_snapshot, {})
        if not isinstance(snapshot, dict):
            self._error(code="INVALID_T0_SNAPSHOT", detail="snapshot is not an object",
                        critical=True, review_id=review_id, trade_id=trade_id)
            return False
        production = str(source["production_policy"] or "HOLD")
        if production not in ACTION_SET:
            self._error(code="UNSUPPORTED_PRODUCTION_POLICY", detail=production,
                        critical=True, review_id=review_id, trade_id=trade_id)
            return False
        position = self._position(snapshot)
        remaining = _finite(position.get("remaining_position_fraction"))
        realized = _finite(position.get("realized_r_weighted"))
        current_r = _finite(_at(snapshot, "observation", "position", "r"))
        current_price = _finite(_at(snapshot, "observation", "exact_levels", "current"))
        manager = snapshot.get("policy_manager") or {}
        inputs = manager.get("inputs") or {}
        take_r = _finite(inputs.get("T"))
        decision = manager.get("management_decision") or {}
        origin = ("TEST" if snapshot.get("demo") else
                  "LIVE_PROSPECTIVE" if captured >= self.activation_ts - 1e-9
                  else "RESEARCH_BACKFILL")
        exclusion = None
        if remaining is None or not (0.0 <= remaining <= 1.0):
            exclusion = "INVALID_REMAINING_FRACTION"
        elif current_r is None:
            exclusion = "MISSING_T0_R"
        elif current_price is None and origin == "LIVE_PROSPECTIVE":
            exclusion = "MISSING_T0_PRICE"
        elif take_r is None:
            exclusion = "MISSING_EXECUTION_INPUTS"
        elif origin != "LIVE_PROSPECTIVE":
            exclusion = "NON_PROSPECTIVE_ORIGIN"
        measurement_eligible = exclusion is None
        payload = {
            "contract_version": G1M_OBSERVATION_VERSION,
            "review_id": review_id, "trade_id": trade_id,
            "captured_ts": captured, "origin": origin,
            "production_policy": production,
            "policy_version": source["policy_version"],
            "snapshot_sha256": source_sha,
            "current_price": current_price, "current_r": current_r,
            "remaining_before": remaining,
            "realized_before_r": realized or 0.0,
            "active_stop": _finite(position.get("active_stop_price")),
            "original_stop": _finite(position.get("original_stop")),
            "take_r": take_r,
            "decision_id": decision.get("decision_id"),
            "measurement_eligible": measurement_eligible,
            "exclusion_reason": exclusion,
        }
        t0_sha = _sha_text(_json(payload))
        observation_id = "g1m-" + hashlib.sha256(
            f"{review_id}|{source_sha}|{G1M_OBSERVATION_VERSION}".encode()
        ).hexdigest()[:30]
        # Frozen geometry from management_decisions is preferred because it was
        # registered atomically with the AI review. Snapshot-derived fields remain
        # the source for T0 R/price and position state.
        with self._lock:
            decision_row = self._conn.execute(
                "SELECT entry,original_stop,take_price,status FROM management_decisions "
                "WHERE review_id=? ORDER BY created_ts DESC,rowid DESC LIMIT 1",
                (review_id,)).fetchone()
        entry = _finite(decision_row["entry"]) if decision_row else None
        original_stop = (_finite(decision_row["original_stop"]) if decision_row
                         else _finite(position.get("original_stop")))
        take_price = _finite(decision_row["take_price"]) if decision_row else None
        execution_eligible = bool(measurement_eligible and decision_row is not None)
        observation_json = _json({**payload, "entry": entry,
                                  "original_stop": original_stop,
                                  "take_price": take_price})
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO g1m_management_observations("
                "observation_id,review_id,trade_id,captured_ts,origin,production_policy,"
                "policy_version,snapshot_sha256,t0_payload_sha256,current_price,current_r,"
                "remaining_before,realized_before_r,entry,original_stop,active_stop,take_price,"
                "measurement_eligible,policy_edge_eligible,execution_edge_eligible,"
                "exclusion_reason,observation_json,created_ts)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (observation_id, review_id, trade_id, captured, origin, production,
                 source["policy_version"], source_sha, t0_sha, current_price, current_r,
                 remaining if remaining is not None else -1.0, realized or 0.0,
                 entry, original_stop, _finite(position.get("active_stop_price")), take_price,
                 int(measurement_eligible), int(measurement_eligible), int(execution_eligible),
                 exclusion, observation_json, time.time()))
        self._freeze_policies(observation_id, snapshot, production)
        return True

    def capture_new(self, limit: int = 200) -> int:
        with self._lock:
            rows = self._conn.execute("""
                SELECT d.* FROM decision_snapshots d
                LEFT JOIN g1m_management_observations g ON g.review_id=d.review_id
                WHERE g.review_id IS NULL
                ORDER BY d.captured_ts ASC LIMIT ?
            """, (max(1, min(int(limit), 2000)),)).fetchall()
        captured = 0
        for row in rows:
            try:
                captured += int(self._capture_observation(row))
            except Exception as exc:
                self._error(code="CAPTURE_EXCEPTION", detail=str(exc), critical=True,
                            review_id=str(row["review_id"]), trade_id=int(row["trade_id"]))
        return captured

    # ------------------------------------------------------------ resolution

    @staticmethod
    def _original_plan_outcome(snapshot: dict, points: list[dict]) -> float:
        inputs = _at(snapshot, "policy_manager", "inputs", default={}) or {}
        current_r = _finite(inputs.get("r0"))
        take_r = _finite(inputs.get("T"))
        if current_r is None or take_r is None:
            raise ValueError("snapshot lacks original-plan execution inputs")
        values = [float(p["r"]) for p in points]
        if not values or abs(values[0] - current_r) > 1e-7:
            values.insert(0, current_r)
        spec = ExecutionSpec.from_values(
            current_r=current_r,
            max_r=current_r,
            take_r=take_r,
            rungs=(),
            rung_fraction_original=0.0,
            be_after_r=max(take_r + 1.0, 1_000_000.0),
            stop_r=-1.0,
        )
        return float(replay_execution_path(values, spec).outcome_r)

    def _resolve_observation(self, obs: sqlite3.Row, replay_row: sqlite3.Row) -> bool:
        observation_id = str(obs["observation_id"])
        review_id = str(obs["review_id"])
        with self._lock:
            source = self._conn.execute(
                "SELECT snapshot_json,snapshot_sha256 FROM decision_snapshots WHERE review_id=?",
                (review_id,)).fetchone()
        if source is None or str(source["snapshot_sha256"]) != str(obs["snapshot_sha256"]):
            self._error(code="T0_HASH_MISMATCH", detail="source snapshot identity changed",
                        critical=True, observation_id=observation_id,
                        review_id=review_id, trade_id=int(obs["trade_id"]))
            return False
        if _sha_text(str(source["snapshot_json"])) != str(obs["snapshot_sha256"]):
            self._error(code="T0_HASH_MISMATCH", detail="source snapshot bytes changed",
                        critical=True, observation_id=observation_id,
                        review_id=review_id, trade_id=int(obs["trade_id"]))
            return False
        snapshot = _loads(source["snapshot_json"], {})
        replay = _loads(replay_row["replay_json"], {})
        standard = replay.get("policies") or {}
        if not all(name in standard for name in ACTION_SET):
            self._error(code="COUNTERFACTUAL_NOT_FROZEN",
                        detail="source replay lacks fixed action set", critical=True,
                        observation_id=observation_id, review_id=review_id,
                        trade_id=int(obs["trade_id"]))
            return False
        with self._lock:
            points = [dict(row) for row in self._conn.execute(
                "SELECT ts,r,price FROM decision_path_points WHERE review_id=? "
                "ORDER BY ts", (review_id,)).fetchall()]
        original_future = self._original_plan_outcome(snapshot, points)
        realized_before = float(obs["realized_before_r"])
        remaining = float(obs["remaining_before"])
        original_terminal = realized_before + remaining * original_future
        values: dict[str, float] = {
            name: float(standard[name]["net_realized_r"]) for name in ACTION_SET
        }
        values["ORIGINAL_PLAN"] = original_terminal
        production = str(obs["production_policy"])
        values["PRODUCTION_POLICY"] = values[production]
        hold = values["HOLD"]
        original = values["ORIGINAL_PLAN"]
        exit_now = values["EXIT"]
        best_name = max((*ACTION_SET, "ORIGINAL_PLAN"), key=lambda name: values[name])
        best_value = values[best_name]
        with self._lock, self._conn:
            for name in COMPARATORS:
                terminal = values[name]
                outcome = {
                    "contract_version": G1M_COUNTERFACTUAL_VERSION,
                    "policy": name,
                    "terminal_r": terminal,
                    "management_incremental_r": terminal - realized_before,
                    "mva_vs_hold_r": terminal - hold,
                    "mva_vs_original_plan_r": terminal - original,
                    "mva_vs_exit_r": terminal - exit_now,
                    "regret_r": best_value - terminal,
                    "loss_avoided_vs_hold_r": max(0.0, terminal - hold) if hold < 0 else 0.0,
                    "upside_sacrificed_vs_hold_r": max(0.0, hold - terminal),
                    "source": "authoritative_decision_replay"
                    if name not in {"ORIGINAL_PLAN", "PRODUCTION_POLICY"}
                    else "g1m_original_plan_replay" if name == "ORIGINAL_PLAN"
                    else f"alias:{production}",
                }
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1m_policy_outcomes("
                    "observation_id,policy_name,terminal_r,management_incremental_r,"
                    "mva_vs_hold_r,mva_vs_original_plan_r,mva_vs_exit_r,regret_r,"
                    "loss_avoided_vs_hold_r,upside_sacrificed_vs_hold_r,outcome_json)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (observation_id, name, terminal, terminal - realized_before,
                     terminal - hold, terminal - original, terminal - exit_now,
                     best_value - terminal,
                     max(0.0, terminal - hold) if hold < 0 else 0.0,
                     max(0.0, hold - terminal), _json(outcome)))
            resolution = {
                "contract_version": G1M_CONTRACT_VERSION,
                "observation_id": observation_id,
                "review_id": review_id,
                "resolution_kind": replay_row["resolution_kind"],
                "source_replay_version": replay_row["replay_version"],
                "realized_best_action": best_name,
                "production_policy": production,
                "production_regret_r": best_value - values[production],
                "causal_claim": False,
            }
            raw_resolution = _json(resolution)
            self._conn.execute(
                "INSERT OR IGNORE INTO g1m_resolutions("
                "observation_id,review_id,trade_id,resolved_ts,resolution_kind,"
                "source_replay_version,source_snapshot_sha256,realized_best_action,"
                "production_regret_r,resolution_json,resolution_sha256)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (observation_id, review_id, int(obs["trade_id"]),
                 float(replay_row["resolved_ts"]), str(replay_row["resolution_kind"]),
                 str(replay_row["replay_version"]), str(obs["snapshot_sha256"]),
                 best_name, best_value - values[production], raw_resolution,
                 _sha_text(raw_resolution)))
        self._write_execution_attribution(obs, values)
        return True

    def _write_execution_attribution(self, obs: sqlite3.Row,
                                     values: dict[str, float]) -> None:
        observation_id = str(obs["observation_id"])
        review_id = str(obs["review_id"])
        production = str(obs["production_policy"])
        with self._lock:
            row = self._conn.execute(
                "SELECT decision_id,status FROM management_decisions WHERE review_id=? "
                "ORDER BY created_ts DESC,rowid DESC LIMIT 1", (review_id,)).fetchone()
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
        payload = {
            "contract_version": G1M_ATTRIBUTION_VERSION,
            "decision_id": decision_id,
            "recommendation_status": status,
            "compliance_state": compliance,
            "production_policy": production,
            "actual_policy": actual,
            "production_terminal_r": production_result,
            "actual_terminal_r": actual_result,
            "compliance_delta_r": (production_result - actual_result
                                   if production_result is not None
                                   and actual_result is not None else None),
            "interpretation": "policy and user execution are separate",
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
                 production_result, actual_result, payload["compliance_delta_r"],
                 raw, _sha_text(raw)))

    def resolve_new(self, limit: int = 200) -> int:
        with self._lock:
            rows = self._conn.execute("""
                SELECT g.*,r.resolved_ts,r.resolution_kind,r.replay_version,r.replay_json
                FROM g1m_management_observations g
                JOIN decision_replays r ON r.review_id=g.review_id
                LEFT JOIN g1m_resolutions z ON z.observation_id=g.observation_id
                WHERE z.observation_id IS NULL
                ORDER BY r.resolved_ts ASC LIMIT ?
            """, (max(1, min(int(limit), 2000)),)).fetchall()
        resolved = 0
        for row in rows:
            try:
                resolved += int(self._resolve_observation(row, row))
            except Exception as exc:
                self._error(code="RESOLUTION_EXCEPTION", detail=str(exc), critical=True,
                            observation_id=str(row["observation_id"]),
                            review_id=str(row["review_id"]), trade_id=int(row["trade_id"]))
        return resolved

    def step(self) -> dict:
        return {"captured": self.capture_new(), "resolved": self.resolve_new()}

    # ---------------------------------------------------------- aggregation

    def _eligible_production_rows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("""
                SELECT g.observation_id,g.trade_id,g.captured_ts,g.production_policy,
                       o.terminal_r,o.mva_vs_hold_r,o.mva_vs_original_plan_r,o.mva_vs_exit_r,
                       o.regret_r,o.loss_avoided_vs_hold_r,o.upside_sacrificed_vs_hold_r,
                       a.compliance_state,a.actual_policy
                FROM g1m_management_observations g
                JOIN g1m_policy_outcomes o
                  ON o.observation_id=g.observation_id AND o.policy_name='PRODUCTION_POLICY'
                LEFT JOIN g1m_execution_attribution a ON a.observation_id=g.observation_id
                WHERE g.policy_edge_eligible=1
                ORDER BY g.captured_ts
            """).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _dependency_weights(rows: list[dict]) -> dict[str, float]:
        by_trade: dict[int, list[str]] = defaultdict(list)
        for row in rows:
            by_trade[int(row["trade_id"])].append(str(row["observation_id"]))
        out: dict[str, float] = {}
        for ids in by_trade.values():
            weight = 1.0 / len(ids)
            for oid in ids:
                out[oid] = weight
        return out

    def status(self) -> dict:
        with self._lock:
            counts = self._conn.execute("""
                SELECT COUNT(*) raw_n,
                       SUM(CASE WHEN measurement_eligible=1 THEN 1 ELSE 0 END) measurement_n,
                       SUM(CASE WHEN policy_edge_eligible=1 THEN 1 ELSE 0 END) policy_n,
                       SUM(CASE WHEN execution_edge_eligible=1 THEN 1 ELSE 0 END) execution_n,
                       COUNT(DISTINCT trade_id) trade_n
                FROM g1m_management_observations
            """).fetchone()
            resolved_n = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1m_resolutions").fetchone()[0])
            critical_errors = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1m_contract_errors WHERE critical=1").fetchone()[0])
        rows = self._eligible_production_rows()
        weights = self._dependency_weights(rows)
        eligible_trade_n = len({int(r["trade_id"]) for r in rows})
        effective_n = sum(weights.values())
        mvas = [float(r["mva_vs_hold_r"]) for r in rows
                if r.get("mva_vs_hold_r") is not None]
        periods = {time.strftime("%Y-%m", time.gmtime(float(r["captured_ts"]))) for r in rows}
        observed = {
            "raw_observations": len(rows),
            "unique_trades": eligible_trade_n,
            "effective_n": int(math.floor(effective_n + 1e-12)),
            "temporal_periods": len(periods),
            "positive_mva": sum(v > 1e-12 for v in mvas),
            "negative_mva": sum(v < -1e-12 for v in mvas),
        }
        blockers = [key for key, req in READINESS_REQUIRED.items()
                    if observed.get(key, 0) < req]
        if critical_errors:
            blockers.append("CRITICAL_CONTRACT_ERRORS")
        ready = not blockers
        raw_n = int(counts["raw_n"] or 0)
        if raw_n == 0:
            evidence_status = "NO_EVIDENCE"
        elif resolved_n == 0:
            evidence_status = "COLLECTING"
        elif ready:
            evidence_status = "READY_FOR_OOS"
        elif len(rows) < 20:
            evidence_status = "INSUFFICIENT_EVIDENCE"
        else:
            evidence_status = "DESCRIPTIVE_ONLY"
        return {
            "g1_stage": G1M_STAGE,
            "g1m_contract_version": G1M_CONTRACT_VERSION,
            "activation_ts": self.activation_ts,
            "evidence_status": evidence_status,
            "observations": raw_n,
            "measurement_eligible": int(counts["measurement_n"] or 0),
            "policy_edge_eligible": int(counts["policy_n"] or 0),
            "execution_edge_eligible": int(counts["execution_n"] or 0),
            "unique_trades_all": int(counts["trade_n"] or 0),
            "resolved": resolved_n,
            "pending": max(0, raw_n - resolved_n),
            "prospective_resolved": len(rows),
            "unique_trades": eligible_trade_n,
            "effective_n": effective_n,
            "dependency_weight_contract": G1M_WEIGHT_VERSION,
            "readiness_contract_version": G1M_READINESS_VERSION,
            "readiness_required": dict(READINESS_REQUIRED),
            "readiness_observed": observed,
            "readiness_blockers": blockers,
            "ready_for_oos": ready,
            "critical_errors": critical_errors,
            "authority": {
                "research_only": True,
                "production_authority": False,
                "auto_execution_allowed": False,
                "policy_promotion_allowed": False,
                "oos_validated": False,
                "edge_claim_allowed": False,
            },
        }

    @staticmethod
    def _summary(values: list[float]) -> dict:
        return {
            "n": len(values),
            "mean": sum(values) / len(values) if values else None,
            "median": median(values) if values else None,
            "q10": _quantile(values, 0.10),
            "q25": _quantile(values, 0.25),
            "q75": _quantile(values, 0.75),
            "q90": _quantile(values, 0.90),
            "cvar10": _cvar10(values),
        }

    def edge(self) -> dict:
        rows = self._eligible_production_rows()
        weights = self._dependency_weights(rows)
        mva = [float(r["mva_vs_hold_r"]) for r in rows if r.get("mva_vs_hold_r") is not None]
        weighted_num = sum(float(r["mva_vs_hold_r"]) * weights[str(r["observation_id"])]
                           for r in rows if r.get("mva_vs_hold_r") is not None)
        weighted_den = sum(weights[str(r["observation_id"])] for r in rows
                           if r.get("mva_vs_hold_r") is not None)
        return {
            "contract_version": G1M_CONTRACT_VERSION,
            "comparator": "HOLD",
            "raw": self._summary(mva),
            "dependency_adjusted_mean_mva_r": (
                weighted_num / weighted_den if weighted_den > 0 else None),
            "unique_trades": len({int(r["trade_id"]) for r in rows}),
            "effective_n": sum(weights.values()),
            "win_vs_hold_rate": (sum(v > 0 for v in mva) / len(mva) if mva else None),
            "loss_vs_hold_rate": (sum(v < 0 for v in mva) / len(mva) if mva else None),
            "downside_saved_r": sum(float(r["loss_avoided_vs_hold_r"] or 0) for r in rows),
            "upside_sacrificed_r": sum(float(r["upside_sacrificed_vs_hold_r"] or 0) for r in rows),
            "causal_claim": False,
            "edge_claim_allowed": False,
        }

    def policies(self) -> dict:
        out = []
        with self._lock:
            for policy in COMPARATORS:
                rows = [dict(r) for r in self._conn.execute("""
                    SELECT g.trade_id,o.* FROM g1m_policy_outcomes o
                    JOIN g1m_management_observations g USING(observation_id)
                    WHERE o.policy_name=? AND g.policy_edge_eligible=1
                """, (policy,)).fetchall()]
                deltas = [float(r["mva_vs_hold_r"]) for r in rows]
                out.append({
                    "policy": policy,
                    "raw_n": len(rows),
                    "unique_trades": len({int(r["trade_id"]) for r in rows}),
                    "mva_vs_hold": self._summary(deltas),
                    "win_vs_hold_rate": sum(v > 0 for v in deltas) / len(deltas) if deltas else None,
                    "terminal_r": self._summary([float(r["terminal_r"]) for r in rows]),
                })
        return {"contract_version": G1M_CONTRACT_VERSION, "items": out}

    def observations(self, *, resolved: bool | None = None, limit: int = 100) -> dict:
        where = ""
        if resolved is True:
            where = "WHERE z.observation_id IS NOT NULL"
        elif resolved is False:
            where = "WHERE z.observation_id IS NULL"
        with self._lock:
            rows = self._conn.execute(f"""
                SELECT g.*,z.resolved_ts,z.resolution_kind,z.realized_best_action,
                       z.production_regret_r,a.compliance_state,a.actual_policy
                FROM g1m_management_observations g
                LEFT JOIN g1m_resolutions z USING(observation_id)
                LEFT JOIN g1m_execution_attribution a USING(observation_id)
                {where}
                ORDER BY g.captured_ts DESC LIMIT ?
            """, (max(1, min(int(limit), 500)),)).fetchall()
        return {"contract_version": G1M_CONTRACT_VERSION,
                "items": [dict(row) for row in rows]}

    def decision(self, observation_id: str) -> dict | None:
        with self._lock:
            obs = self._conn.execute(
                "SELECT * FROM g1m_management_observations WHERE observation_id=?",
                (observation_id,)).fetchone()
            if obs is None:
                return None
            policies = [dict(r) for r in self._conn.execute(
                "SELECT * FROM g1m_counterfactual_policies WHERE observation_id=? "
                "ORDER BY policy_name", (observation_id,)).fetchall()]
            metrics = [dict(r) for r in self._conn.execute(
                "SELECT * FROM g1m_t0_policy_metrics WHERE observation_id=? "
                "ORDER BY policy_name", (observation_id,)).fetchall()]
            outcomes = [dict(r) for r in self._conn.execute(
                "SELECT * FROM g1m_policy_outcomes WHERE observation_id=? "
                "ORDER BY policy_name", (observation_id,)).fetchall()]
            attribution = self._conn.execute(
                "SELECT * FROM g1m_execution_attribution WHERE observation_id=?",
                (observation_id,)).fetchone()
        return {
            "contract_version": G1M_CONTRACT_VERSION,
            "observation": dict(obs),
            "frozen_policies": policies,
            "t0_metrics": metrics,
            "realized_outcomes": outcomes,
            "execution_attribution": dict(attribution) if attribution else None,
        }

    def cohorts(self) -> dict:
        with self._lock:
            rows = self._conn.execute("""
                SELECT g.production_policy,g.origin,g.policy_version,t.instrument,
                       COUNT(*) raw_n,COUNT(DISTINCT g.trade_id) trade_n,
                       AVG(o.mva_vs_hold_r) mean_mva_vs_hold_r
                FROM g1m_management_observations g
                LEFT JOIN trades t ON t.id=g.trade_id
                LEFT JOIN g1m_policy_outcomes o
                  ON o.observation_id=g.observation_id AND o.policy_name='PRODUCTION_POLICY'
                GROUP BY g.production_policy,g.origin,g.policy_version,t.instrument
                ORDER BY raw_n DESC
            """).fetchall()
        return {"contract_version": G1M_CONTRACT_VERSION,
                "items": [dict(r) for r in rows]}

    def close(self) -> None:
        # Connection is owned by PassiveLearningEngine.
        return None
