"""Integrity refinements for G.1S and G.1-M.1.

This module deliberately changes no production decision authority.  It closes
three research-integrity gaps found during pre-merge audit:

* local-management horizons fail closed if the trading-time clock cannot advance;
* Q maturity distinguishes failed capture attempts from captured observations
  that are actually due for resolution;
* trade relevance evaluates only frozen model predictions that existed before
  trade entry, while realised market direction remains explicitly descriptive.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
import types
from collections import defaultdict
from typing import Any

from . import passive_learning as _pl
from .g1_management_local_runtime import (
    G1M_LOCAL_CONTRACT_VERSION,
    G1M_LOCAL_WINDOW_VERSION,
    LOCAL_HORIZONS,
    ManagementLocalRuntime,
)
from .g1_short_horizon_runtime import (
    G1S_Q_AUDIT_VERSION,
    G1S_TRADE_RELEVANCE_VERSION,
    ShortHorizonRuntime,
    TRADE_LINK_MAX_AGE_SEC,
    _finite,
    _json,
)


REFINEMENT_VERSION = "g1-fast-learning-integrity-v2"
LOCAL_CLOCK_ERROR_VERSION = "g1m-local-clock-error-v1"
Q_AUDIT_REFINEMENT_VERSION = "g1s-q-resolution-audit-v2"
TRADE_RELEVANCE_REFINEMENT_VERSION = "g1s-trade-relevance-v2"


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _ensure_local_error_table(runtime: ManagementLocalRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1m_local_contract_errors(
                error_id TEXT PRIMARY KEY,
                observation_id TEXT,
                review_id TEXT,
                trade_id INTEGER,
                horizon_minutes INTEGER,
                ts REAL NOT NULL,
                code TEXT NOT NULL,
                detail TEXT NOT NULL,
                critical INTEGER NOT NULL DEFAULT 0,
                contract_version TEXT NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1m_local_error_code_ts "
            "ON g1m_local_contract_errors(code,ts)"
        )
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1m_local_contract_errors_immutable_update
            BEFORE UPDATE ON g1m_local_contract_errors
            BEGIN SELECT RAISE(ABORT,'immutable G1M local contract error'); END""")
        runtime._conn.execute("""
            CREATE TRIGGER IF NOT EXISTS g1m_local_contract_errors_immutable_delete
            BEFORE DELETE ON g1m_local_contract_errors
            BEGIN SELECT RAISE(ABORT,'immutable G1M local contract error'); END""")


def _record_local_clock_error(runtime: ManagementLocalRuntime, row, horizon: int,
                              exc: Exception) -> None:
    payload = {
        "contract_version": LOCAL_CLOCK_ERROR_VERSION,
        "observation_id": str(row["observation_id"]),
        "review_id": str(row["review_id"]),
        "trade_id": int(row["trade_id"]),
        "horizon_minutes": int(horizon),
        "code": "TRADING_TIME_ADVANCE_FAILED",
        "detail": f"{type(exc).__name__}: {str(exc)[:500]}",
    }
    error_id = "g1mlerr-" + _sha(payload)[:28]
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1m_local_contract_errors("
            "error_id,observation_id,review_id,trade_id,horizon_minutes,ts,code,detail,"
            "critical,contract_version) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (error_id, payload["observation_id"], payload["review_id"], payload["trade_id"],
             int(horizon), time.time(), payload["code"], payload["detail"], 1,
             LOCAL_CLOCK_ERROR_VERSION),
        )
    runtime._last_error = payload["detail"]


def _materialize_windows_fail_closed(self: ManagementLocalRuntime,
                                     limit: int = 500) -> int:
    """Freeze only true trading-time targets; never substitute wall-clock time."""
    _ensure_local_error_table(self)
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
                target = float(_pl._advance_trading_time(
                    instrument, float(row["captured_ts"]), int(horizon)))
            except Exception as exc:
                _record_local_clock_error(self, row, horizon, exc)
                continue
            if not math.isfinite(target) or target <= float(row["captured_ts"]):
                _record_local_clock_error(
                    self, row, horizon,
                    ValueError("trading-time target is non-finite or not after T0"),
                )
                continue
            payload = {
                "contract_version": G1M_LOCAL_WINDOW_VERSION,
                "observation_id": str(row["observation_id"]),
                "review_id": str(row["review_id"]),
                "trade_id": int(row["trade_id"]),
                "instrument": instrument,
                "captured_ts": float(row["captured_ts"]),
                "horizon_minutes": int(horizon),
                "target_ts": target,
                "origin": origin,
                "evidence_eligible": bool(eligible),
                "time_basis": "trading_minutes",
                "wall_clock_fallback_used": False,
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
                     instrument, float(row["captured_ts"]), int(horizon), target, origin,
                     int(eligible), raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                     time.time()),
                )
                created += int(cur.rowcount > 0)
    return created


def _direct_terminal_candidate(runtime: ShortHorizonRuntime, instrument: str,
                               target: float) -> tuple[float | None, str | None]:
    """Latest admissible direct observation at or before the frozen target."""
    with runtime._lock:
        bar = runtime._conn.execute("""
            SELECT bar_end_ts,quality FROM passive_market_bars
            WHERE instrument=? AND bar_end_ts<=? AND kind='direct'
              AND COALESCE(quality,0)>=0.90
            ORDER BY bar_end_ts DESC LIMIT 1
        """, (instrument, target + 1e-6)).fetchone()
        point = runtime._conn.execute("""
            SELECT ts,quality FROM passive_market_path
            WHERE instrument=? AND ts<=? AND kind='direct'
              AND COALESCE(quality,0)>=0.90
            ORDER BY ts DESC LIMIT 1
        """, (instrument, target + 1e-6)).fetchone()
    candidates: list[tuple[float, str]] = []
    if bar is not None:
        candidates.append((float(bar["bar_end_ts"]), "direct_1m_bar"))
    if point is not None:
        candidates.append((float(point["ts"]), "direct_path_point"))
    return max(candidates, default=(None, None), key=lambda x: -math.inf if x[0] is None else x[0])


def _q_audit_refined(self: ShortHorizonRuntime, *, now: float | None = None,
                     limit: int = 500) -> dict:
    """Audit captured Q maturity separately from pre-capture provider blockers."""
    now = float(now or time.time())
    with self._lock:
        rows = self._conn.execute("""
            SELECT q.attempt_id,q.attempt_ts,q.target_instrument,q.observation_created,
                   q.created_observation_id,q.blocker_code,q.requested_expiry_ts,
                   p.target_ts,p.resolution_status,p.instrument
            FROM g1_q_capture_attempts q
            LEFT JOIN passive_market_observations p
              ON p.observation_id=q.created_observation_id
            ORDER BY q.attempt_ts DESC LIMIT ?
        """, (max(1, min(int(limit), 5000)),)).fetchall()
    maturity = defaultdict(int)
    capture_blockers = defaultdict(int)
    items = []
    pending_targets = []
    captured_n = 0
    for row in rows:
        created = int(row["observation_created"] or 0) == 1 and bool(row["created_observation_id"])
        target = _finite(row["target_ts"])
        if not created:
            state = "CAPTURE_BLOCKED"
            capture_blockers[str(row["blocker_code"] or "UNKNOWN_CAPTURE_BLOCKER")] += 1
        else:
            captured_n += 1
            status = str(row["resolution_status"] or "pending")
            if status == "resolved":
                state = "RESOLVED"
            elif status != "pending":
                state = "RESOLUTION_BLOCKED"
            elif target is None:
                state = "CONTRACT_REJECTED"
            elif now <= target + float(_pl.MAX_GAP_SEC):
                state = "NOT_DUE_YET"
                pending_targets.append(target)
            else:
                instrument = str(row["instrument"] or row["target_instrument"] or "")
                candidate_ts, candidate_source = _direct_terminal_candidate(self, instrument, target)
                # The authoritative passive resolver requires data through the exact
                # frozen target. A merely later quote does not retroactively prove it.
                if candidate_ts is not None and candidate_ts >= target - 1e-6:
                    state = "DUE_BUT_NOT_RESOLVED"
                else:
                    state = "RESOLUTION_BLOCKED"
            maturity[state] += 1
        item = {
            "attempt_id": row["attempt_id"],
            "attempt_ts": row["attempt_ts"],
            "instrument": row["target_instrument"],
            "observation_id": row["created_observation_id"],
            "requested_expiry_ts": _finite(row["requested_expiry_ts"]),
            "target_ts": target,
            "resolution_status": row["resolution_status"],
            "blocker_code": row["blocker_code"],
            "audit_state": state,
        }
        if created and target is not None and state in {"DUE_BUT_NOT_RESOLVED", "RESOLUTION_BLOCKED"}:
            candidate_ts, candidate_source = _direct_terminal_candidate(
                self, str(row["instrument"] or row["target_instrument"] or ""), target)
            item["latest_admissible_terminal_candidate_ts"] = candidate_ts
            item["terminal_candidate_source"] = candidate_source
            item["terminal_gap_sec"] = None if candidate_ts is None else target - candidate_ts
        items.append(item)
    targets = sorted(pending_targets)
    return {
        "contract_version": G1S_Q_AUDIT_VERSION,
        "refinement_contract_version": Q_AUDIT_REFINEMENT_VERSION,
        "now": now,
        "attempt_n": len(rows),
        "captured_n": captured_n,
        "capture_blocked_n": len(rows) - captured_n,
        "capture_blockers": dict(capture_blockers),
        "counts": dict(maturity),
        "successful_capture_counts_only": True,
        "earliest_pending_target_ts": targets[0] if targets else None,
        "median_pending_target_ts": statistics.median(targets) if targets else None,
        "latest_pending_target_ts": targets[-1] if targets else None,
        "overdue_is_contract_failure": maturity.get("DUE_BUT_NOT_RESOLVED", 0) > 0,
        "items": items,
        "slow_q_semantics_unchanged": True,
    }


def _trade_relevance_refined(self: ShortHorizonRuntime) -> dict:
    """Evaluate only model predictions that were frozen before trade entry."""
    with self._lock:
        rows = self._conn.execute("""
            SELECT l.link_id,l.trade_id,l.observation_id,l.horizon_minutes,
                   l.forecast_age_sec,l.created_ts AS link_created_ts,
                   t.opened_at,t.instrument,t.direction,t.setup,t.result_r,t.status,
                   r.direction_label,r.terminal_log_return,
                   p.prediction_id,p.p_up,p.created_ts AS prediction_created_ts,
                   m.model_id,m.model_family,m.feature_set,m.created_ts AS model_created_ts,
                   m.training_cutoff_ts
            FROM g1s_trade_links l
            JOIN trades t ON t.id=l.trade_id
            LEFT JOIN g1s_resolutions r USING(observation_id)
            LEFT JOIN g1s_shadow_predictions p
              ON p.prediction_id=(
                SELECT p2.prediction_id FROM g1s_shadow_predictions p2
                JOIN g1s_models m2 ON m2.model_id=p2.model_id
                WHERE p2.observation_id=l.observation_id
                  AND p2.created_ts<=t.opened_at
                  AND m2.created_ts<=t.opened_at
                ORDER BY p2.created_ts DESC,p2.prediction_id DESC LIMIT 1
              )
            LEFT JOIN g1s_models m ON m.model_id=p.model_id
            ORDER BY t.opened_at,l.horizon_minutes
        """).fetchall()
    items = []
    brier_ps: list[float] = []
    brier_ys: list[int] = []
    trade_ids_with_prediction: set[int] = set()
    winner_ps: list[float] = []
    loser_ps: list[float] = []
    for row in rows:
        item = dict(row)
        market_label = row["direction_label"]
        trade_direction = "UP" if str(row["direction"]).lower() == "long" else "DOWN"
        if market_label in {"UP", "DOWN"}:
            market_aligned = market_label == trade_direction
        else:
            market_aligned = None
        item["market_move_aligned_with_trade_descriptive"] = market_aligned
        item["market_move_is_model_prediction"] = False
        p_up = _finite(row["p_up"])
        if p_up is not None and row["prediction_id"] is not None:
            p_trade = p_up if trade_direction == "UP" else 1.0 - p_up
            item["frozen_model_prediction_available_pre_entry"] = True
            item["p_move_with_trade_direction"] = p_trade
            item["model_direction_aligned_with_trade"] = p_trade > 0.5
            if market_aligned is not None:
                brier_ps.append(p_trade)
                brier_ys.append(1 if market_aligned else 0)
            trade_ids_with_prediction.add(int(row["trade_id"]))
            result_r = _finite(row["result_r"])
            if result_r is not None:
                (winner_ps if result_r > 0 else loser_ps).append(p_trade)
        else:
            item["frozen_model_prediction_available_pre_entry"] = False
            item["p_move_with_trade_direction"] = None
            item["model_direction_aligned_with_trade"] = None
        # Remove the old ambiguous field if a caller cached an older shape.
        item.pop("market_move_aligned_with_trade", None)
        items.append(item)
    brier = None
    baseline = None
    if brier_ps:
        brier = sum((p-y)**2 for p, y in zip(brier_ps, brier_ys)) / len(brier_ps)
        baseline = sum((0.5-y)**2 for y in brier_ys) / len(brier_ys)
    status = (
        "PROSPECTIVE_MODEL_EVALUATION_AVAILABLE" if brier_ps
        else "NO_PROSPECTIVE_MODEL_PREDICTIONS"
    )
    return {
        "contract_version": G1S_TRADE_RELEVANCE_VERSION,
        "refinement_contract_version": TRADE_RELEVANCE_REFINEMENT_VERSION,
        "max_pre_entry_forecast_age_sec": TRADE_LINK_MAX_AGE_SEC,
        "status": status,
        "items": items,
        "model_evaluable_n": len(brier_ps),
        "unique_trades_with_pre_entry_prediction": len(trade_ids_with_prediction),
        "brier_move_with_trade_direction": brier,
        "baseline_0_5_brier": baseline,
        "delta_brier_vs_0_5": None if brier is None or baseline is None else baseline-brier,
        "mean_p_move_with_trade_on_winning_trades": (
            sum(winner_ps)/len(winner_ps) if winner_ps else None),
        "mean_p_move_with_trade_on_nonwinning_trades": (
            sum(loser_ps)/len(loser_ps) if loser_ps else None),
        "real_trades_are_validation_not_training": True,
        "market_outcome_alignment_is_descriptive_not_forecast": True,
        "oos_validated": False,
        "edge_claim_allowed": False,
        "production_authority": False,
    }


def install_g1_fast_learning_integrity_refinement() -> None:
    if getattr(ShortHorizonRuntime, "_fast_learning_integrity_refinement", None) == REFINEMENT_VERSION:
        return

    original_local_ensure = ManagementLocalRuntime._ensure_tables
    def local_ensure(self):
        original_local_ensure(self)
        _ensure_local_error_table(self)
    ManagementLocalRuntime._ensure_tables = local_ensure
    ManagementLocalRuntime.materialize_windows = _materialize_windows_fail_closed

    ShortHorizonRuntime.q_audit = _q_audit_refined
    ShortHorizonRuntime.trade_relevance = _trade_relevance_refined

    # Some wrapped fitters historically returned the number of attempted INSERTs.
    # Normalize the public worker result to the actual immutable model-row delta.
    previous_fit = ShortHorizonRuntime.fit_if_ready
    def fit_count_actual(self, *args, **kwargs):
        with self._lock:
            before = int(self._conn.execute("SELECT COUNT(*) FROM g1s_models").fetchone()[0])
        previous_fit(self, *args, **kwargs)
        with self._lock:
            after = int(self._conn.execute("SELECT COUNT(*) FROM g1s_models").fetchone()[0])
        return max(0, after-before)
    ShortHorizonRuntime.fit_if_ready = fit_count_actual

    ShortHorizonRuntime._fast_learning_integrity_refinement = REFINEMENT_VERSION
    ManagementLocalRuntime._fast_learning_integrity_refinement = REFINEMENT_VERSION
