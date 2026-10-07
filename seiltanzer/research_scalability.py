"""G.1E.2 bounded research presentation and heavy-scan isolation.

The immutable source ledgers and model mathematics stay authoritative.  This layer
replaces request-time full-history scans with indexed/materialized summaries and
prevents G.1C refit attempts from scanning the whole dataset when the minimum Q
sample cannot possibly be met.
"""
from __future__ import annotations

import copy
import json
import math
import time
import types
from collections import Counter
from typing import Any

from . import g1_intelligence_nonblocking as _nb
from . import g1_shadow_runtime as _g1c
from . import g1_shadow_refinement as _g1c_integrity
from .g1_q_evidence_runtime import (
    G1B1_STAGE, Q_EVIDENCE_CONTRACT_VERSION, Q_CAPABILITY_CONTRACT_VERSION,
    Q_CAPTURE_ATTEMPT_CONTRACT_VERSION, Q_CAPTURE_POLICY_VERSION,
)
from .g1_dataset_runtime import G1_DATASET_CONTRACT_VERSION
from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION
from .option_q_adapter import OPTION_Q_CONTRACT_VERSION, EXPIRY_CLOCK_VERSION
from .config import INSTRUMENTS


SCALABILITY_CONTRACT_VERSION = "g1e2-research-scalability-v1"
LIGHT_STATUS_VERSION = "bounded-materialized-status-v1"

_ORIGINAL_MAYBE_REFIT = _g1c._maybe_refit


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value)) if value is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _light_q_status(self) -> dict:
    with self._lock:
        aggregate = self._conn.execute("""
            SELECT COUNT(*) attempts,
                   SUM(CASE WHEN observation_created=1 THEN 1 ELSE 0 END) captured,
                   MAX(attempt_ts) last_attempt,
                   MAX(CASE WHEN observation_created=1 THEN attempt_ts END) last_success
            FROM g1_q_capture_attempts WHERE attempt_origin='background_collector'
        """).fetchone()
        states = self._conn.execute("""
            SELECT p.resolution_status,COUNT(*) n
            FROM g1_q_capture_attempts q JOIN passive_market_observations p
              ON p.observation_id=q.created_observation_id
            WHERE q.attempt_origin='background_collector' AND q.observation_created=1
            GROUP BY p.resolution_status
        """).fetchall()
        blockers = self._conn.execute("""
            SELECT blocker_code,COUNT(*) n FROM g1_q_capture_attempts
            WHERE attempt_origin='background_collector' AND blocker_code IS NOT NULL
            GROUP BY blocker_code ORDER BY n DESC LIMIT 12
        """).fetchall()
        relation = self._conn.execute("""
            SELECT relation,COUNT(*) n FROM g1_q_capture_attempts
            WHERE attempt_origin='background_collector' AND observation_created=1
            GROUP BY relation
        """).fetchall()
        provider = self._conn.execute("""
            SELECT COALESCE(provider,'UNKNOWN') provider,COUNT(*) n FROM g1_q_capture_attempts
            WHERE attempt_origin='background_collector' AND observation_created=1
            GROUP BY COALESCE(provider,'UNKNOWN')
        """).fetchall()
        eligible = self._conn.execute("""
            SELECT COUNT(*) raw_n,COUNT(DISTINCT dependency_group_id) effective_n
            FROM g1_dataset_membership WHERE dataset_contract_version=? AND q_to_p_eligible=1
        """, (G1_DATASET_CONTRACT_VERSION,)).fetchone()
    state_counts = {str(r["resolution_status"]): int(r["n"]) for r in states}
    attempts = int(aggregate["attempts"] or 0)
    captured = int(aggregate["captured"] or 0)
    resolved = state_counts.get("resolved", 0)
    effective = int(eligible["effective_n"] or 0)
    if effective < 30:
        evidence = "INSUFFICIENT"
    elif effective < 100:
        evidence = "EARLY"
    else:
        evidence = "PROVISIONAL"
    return {
        "g1_stage": G1B1_STAGE,
        "q_evidence_contract_version": Q_EVIDENCE_CONTRACT_VERSION,
        "capability_contract_version": Q_CAPABILITY_CONTRACT_VERSION,
        "capture_attempt_contract_version": Q_CAPTURE_ATTEMPT_CONTRACT_VERSION,
        "capture_policy_version": Q_CAPTURE_POLICY_VERSION,
        "dataset_contract_version": G1_DATASET_CONTRACT_VERSION,
        "option_q_contract_version": OPTION_Q_CONTRACT_VERSION,
        "expiry_clock_version": EXPIRY_CLOCK_VERSION,
        "measurement_runtime_version": MEASUREMENT_RUNTIME_VERSION,
        "generated_ts": time.time(), "prospective_only": True,
        "configured_instrument_n": sum(bool(INSTRUMENTS[c].options_proxy) for c in INSTRUMENTS),
        "total_instrument_n": len(INSTRUMENTS),
        "capture_attempt_n": attempts, "successful_q_capture_n": captured,
        "unresolved_q_capture_n": max(0, captured-resolved),
        "resolved_q_observation_n": resolved,
        "q_to_p_eligible_n": int(eligible["raw_n"] or 0),
        "g1b_q_metrics_eligible_n": 0,
        "unique_q_anchor_n": effective, "effective_q_n": effective,
        "relation_counts": {str(r["relation"]): int(r["n"]) for r in relation},
        "provider_counts": {str(r["provider"]): int(r["n"]) for r in provider},
        "top_blockers": {str(r["blocker_code"]): int(r["n"]) for r in blockers},
        "last_attempt_ts": aggregate["last_attempt"],
        "last_successful_q_capture_ts": aggregate["last_success"],
        "evidence_status": evidence,
        "implemented": True, "configured": True,
        "provider_available": captured > 0, "runtime_validated": captured > 0,
        "data_available": captured > 0, "prospective_capture_observed": attempts > 0,
        "resolved_evidence_available": resolved > 0,
        "measurement_ready": int(eligible["raw_n"] or 0) > 0,
        "authority": "research_only", "production_authority": False,
        "calibrator_fitted": False, "calibrator_registry_writes": False,
        "g1_training_allowed": False, "physical_probability_published": False,
        "promotion_allowed": False, "production_replacement_allowed": False,
        "sample_count_auto_promotion": False,
        "materialized_status": True,
        "materialization_contract_version": LIGHT_STATUS_VERSION,
    }


def _threshold_status(stats: dict, family: str) -> dict:
    return _g1c._threshold_status(stats, family)


def _light_g1c_status(self) -> dict:
    q = _light_q_status(self)
    with self._lock:
        eligible_rows = self._conn.execute("""
            SELECT p.forecast_json,p.outcome_json,p.captured_ts,p.target_ts,
                   p.instrument,g.dependency_group_id,g.base_cohort_id,g.base_cohort_json
            FROM g1_dataset_membership g JOIN passive_market_observations p USING(observation_id)
            WHERE g.dataset_contract_version=? AND g.q_to_p_eligible=1
              AND g.forecast_eval_eligible=1
              AND NOT EXISTS(SELECT 1 FROM g1_contract_errors e
                  WHERE e.dataset_contract_version=g.dataset_contract_version
                    AND e.observation_id=g.observation_id AND e.error_type='SOURCE_MUTATED')
            ORDER BY p.captured_ts,p.observation_id
        """, (G1_DATASET_CONTRACT_VERSION,)).fetchall()
        model_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_shadow_models").fetchone()[0])
        pred_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_shadow_predictions").fetchone()[0])
        fit_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_fit_runs").fetchone()[0])
        error_n = int(self._conn.execute("SELECT COUNT(*) FROM g1c_contract_errors").fetchone()[0])
        critical_n = _g1c_integrity._critical_error_count(self)
    # Use the same labels, CDF interpolation and dependence clock as G.1C.
    # Read only eligible rows and the small projection needed by its statistics.
    clean_rows = []
    for record in eligible_rows:
        row = dict(record)
        row["forecast"] = _loads(row.pop("forecast_json"), {})
        row["outcome"] = _loads(row.pop("outcome_json"), {})
        row["base_cohort"] = _loads(row.pop("base_cohort_json"), {})
        probability = _g1c._g1b._q_up_probability(row)
        direction = _g1c._g1b._future_direction(row)
        if probability is None or direction is None or not math.isfinite(probability):
            continue
        row.update(raw_q=probability, outcome_y=direction)
        clean_rows.append(row)
    stats = _g1c._stats(self, clean_rows)
    readiness = _g1c_integrity._scope_fit_readiness(self, clean_rows)
    blockers = Counter()
    for item in readiness.values():
        blockers.update(item["blockers"])
    g1d = _g1c._g1d_status(stats, critical_contract_errors=critical_n)
    return {
        "g1_stage": _g1c.G1C_STAGE, "g1c_contract_version": _g1c.G1C_CONTRACT_VERSION,
        "fit_threshold_contract_version": _g1c.G1C_FIT_THRESHOLD_VERSION,
        "generated_ts": time.time(), "q_captured": q["successful_q_capture_n"],
        "q_resolved": q["resolved_q_observation_n"], "q_eligible": stats["raw_n"],
        "effective_q_n": stats["effective_n"], "positive_n": stats["positive_n"],
        "negative_n": stats["negative_n"], "unique_q_n": stats["unique_q_n"],
        "fit_readiness": readiness, "fit_run_n": fit_n, "frozen_model_n": model_n,
        "prospective_shadow_prediction_n": pred_n,
        "ready_for_g1d": g1d["ready"],
        "g1d_readiness": g1d,
        "top_fit_blockers": dict(blockers.most_common()), "contract_error_n": error_n,
        "critical_contract_error_n": critical_n,
        "critical_contract_error_types": sorted(_g1c_integrity._CRITICAL_ERRORS),
        "q_semantic_pooling": "separated_by_q_relation_and_proxy_transform",
        "refinement_contract_version": _g1c_integrity.REFINEMENT_VERSION,
        "calibrator_fitted": model_n > 0,
        "shadow_model_fitting_allowed": any(item["ready"] for item in readiness.values()),
        "production_model_training_allowed": False, "oos_validated": False,
        "edge_claim": False, "physical_probability_published": False,
        "production_authority": False, "production_replacement_allowed": False,
        "promotion_allowed": False, "sample_count_auto_promotion": False,
        "authority": "research_only", "materialized_status": True,
        "materialization_contract_version": LIGHT_STATUS_VERSION,
    }


def _light_passive_status(self) -> dict:
    with self._lock:
        rows = self._conn.execute(
            "SELECT resolution_status,COUNT(*) n FROM passive_market_observations GROUP BY resolution_status"
        ).fetchall()
        counts = {str(r["resolution_status"]): int(r["n"]) for r in rows}
        total = sum(counts.values())
        current = int(self._conn.execute(
            "SELECT COUNT(*) FROM passive_market_observations WHERE feature_contract_version=?",
            (_pl.PASSIVE_SCHEMA_VERSION,)).fetchone()[0])
        latest = {str(r["instrument"]): float(r["ts"] or 0) for r in self._conn.execute(
            "SELECT instrument,MAX(captured_ts) ts FROM passive_market_observations GROUP BY instrument")}
        page_count = int(self._conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(self._conn.execute("PRAGMA page_size").fetchone()[0])
    g1s = getattr(self, "_g1s_runtime", None)
    g1s_status = g1s.status() if g1s is not None else {}
    effective = max((int(x.get("effective_n") or 0) for x in g1s_status.get("horizons", [])), default=0)
    return {
        "current_contract_version": _pl.PASSIVE_SCHEMA_VERSION,
        "version": _pl.PASSIVE_SCHEMA_VERSION,
        "collector_status": "degraded" if self._last_step_error else "running",
        "last_step_ts": self._last_step_ts, "last_skip_reason": self._last_skip_reason,
        "last_successful_capture_ts": self._last_successful_capture_ts,
        "last_successful_bar_capture_ts": self._last_successful_bar_capture_ts,
        "last_successful_resolution_ts": self._last_successful_resolution_ts,
        "latest_error": self._last_step_error, "supported_instruments": list(INSTRUMENTS),
        "last_observation_per_instrument": latest,
        "pending_resolutions": counts.get("pending",0),
        "resolved_observations": counts.get("resolved",0), "resolution_counts": counts,
        "raw_n": total, "current_f32_n": current, "pristine_f32_n": g1s_status.get("resolved",0),
        "fixed_horizon_raw_n": g1s_status.get("observations",0),
        "option_native_raw_n": _light_q_status(self)["successful_q_capture_n"],
        "terminal_q_eligible_n": _light_q_status(self)["successful_q_capture_n"],
        "terminal_q_resolved_n": _light_q_status(self)["resolved_q_observation_n"],
        "current_contract_effective_n": effective, "evidence_eligible_n": current,
        "effective_n": effective, "database_size_bytes": page_count*page_size,
        "budget": self.budget, "g1_training_allowed": False,
        "active_trade_required": False, "authority": "research_only",
        "promotion_allowed": False, "materialized_status": True,
        "materialization_contract_version": LIGHT_STATUS_VERSION,
    }


def _light_passive_calibration(self) -> dict:
    g1s = getattr(self, "_g1s_runtime", None)
    status = g1s.status() if g1s is not None else {"horizons": []}
    q = _light_q_status(self)
    raw = sum(int(h.get("raw_resolved") or 0) for h in status.get("horizons", []))
    effective = sum(int(h.get("effective_n") or 0) for h in status.get("horizons", []))
    return {
        "version": "passive-q-calibration-materialized-v1", "dataset": "passive_market",
        "probability_semantics": {"input": "risk_neutral_Q_terminal",
                                  "output": "physical_P_shadow",
                                  "physical_probability_published": False},
        "raw_n": raw, "terminal_q_eligible_n": q["q_to_p_eligible_n"],
        "fixed_horizon_raw_n": raw, "effective_n": effective,
        "short_horizon": status, "g1_training_allowed": False,
        "sample_count_auto_promotion": False,
        "evidence_status": "EARLY" if effective >= 30 else "INSUFFICIENT",
        "authority": "shadow", "promotion_allowed": False,
        "materialized_status": True,
    }


def _light_passive_edge(self, real_report: dict | None = None) -> dict:
    g1s = getattr(self, "_g1s_runtime", None)
    management = getattr(self, "_g1m_runtime", None)
    horizons = g1s.status().get("horizons", []) if g1s is not None else []
    return {
        "version": "three-way-edge-report-materialized-v1",
        "market_forecast_edge": {"dataset": "g1s_short_horizon",
                                 "horizons": horizons, "evidence": "research_only"},
        "virtual_management_edge": {"status": "deferred_from_request_path"},
        "real_management_edge": management.edge() if management is not None else {
            "dataset": "real_user_trade", "evidence": "INSUFFICIENT"},
        "datasets_mixed": False, "promotion_allowed": False,
        "materialized_status": True,
    }


def _gated_maybe_refit(self, now: float) -> None:
    # The smallest G.1C family needs 60 eligible Q outcomes. Avoid an expensive
    # immutable-cut/full-history build when this necessary condition is impossible.
    with self._lock:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM g1_dataset_membership WHERE dataset_contract_version=? "
            "AND q_to_p_eligible=1", (G1_DATASET_CONTRACT_VERSION,)).fetchone()
    eligible = int(row[0] or 0)
    if eligible < min(v["raw_n"] for v in _g1c.FIT_THRESHOLDS.values()):
        self._g1c_last_refit_result = {
            "status": "SKIPPED_FAST_GATE", "q_eligible_n": eligible,
            "required_raw_n": 60, "scalability_contract_version": SCALABILITY_CONTRACT_VERSION,
        }
        self._g1c_last_refit_error = None
        return
    return _ORIGINAL_MAYBE_REFIT(self, now)


def _light_intelligence_warm(runtime) -> None:
    g1s = getattr(runtime.engine, "short_horizon", None)
    passive = runtime.passive
    short = g1s.status() if g1s is not None else {}
    q = _light_q_status(passive)
    g1c = _light_g1c_status(passive)
    storage = runtime.storage.status(engine=None) if runtime.storage is not None else None
    horizon_items = short.get("horizons", [])
    forecast_n = sum(int(h.get("raw_resolved") or 0) for h in horizon_items)
    forecast_eff = sum(int(h.get("effective_n") or 0) for h in horizon_items)
    maturity = "EARLY" if forecast_n else "COLLECTING"
    base = _nb._fallback_status(runtime)
    base.update({
        "maturity_state": maturity,
        "headline": ("Быстрый контур получает завершённые 15–240m outcomes; option Q созревает отдельно."
                     if forecast_n else "Быстрый контур собирает первые короткие outcomes."),
        "experience": {"forecast_eval_n": forecast_n, "forecast_effective_n": forecast_eff,
                       "q_attempts": q["capture_attempt_n"],
                       "q_captured": q["successful_q_capture_n"],
                       "q_resolved": q["resolved_q_observation_n"],
                       "q_clean_eligible": q["q_to_p_eligible_n"],
                       "q_effective_n": q["effective_q_n"]},
        "models": {"platt": g1c["fit_readiness"]["platt"],
                   "beta": g1c["fit_readiness"]["beta"],
                   "isotonic": g1c["fit_readiness"]["isotonic"],
                   "frozen_model_n": g1c["frozen_model_n"],
                   "prospective_prediction_n": g1c["prospective_shadow_prediction_n"]},
        "evidence": {"dataset_status": maturity, "baseline_status": maturity,
                     "q_status": q["evidence_status"],
                     "ready_for_g1d": g1c["ready_for_g1d"],
                     "g1d": g1c["g1d_readiness"]},
        "data_quality": {"excluded_n": 0, "primary_reasons": {},
                         "top_q_blockers": q["top_blockers"]},
        "storage": storage, "short_horizon": short,
        "q_maturity_audit": g1s.q_audit(limit=1000) if g1s is not None else {},
        "presentation_state": "LIVE_MATERIALIZED",
        "scalability_contract_version": SCALABILITY_CONTRACT_VERSION,
    })
    pipeline = {
        "contract_version": "g1-intelligence-cockpit-v1",
        "presentation_state": "LIVE_MATERIALIZED",
        "funnel": [{"name":"ATTEMPTS","n":q["capture_attempt_n"]},
                   {"name":"CAPTURED","n":q["successful_q_capture_n"]},
                   {"name":"RESOLVED","n":q["resolved_q_observation_n"]},
                   {"name":"Q→P ELIGIBLE","n":q["q_to_p_eligible_n"]},
                   {"name":"EFFECTIVE Q N","n":q["effective_q_n"]}],
        "short_horizon": short, "q_blockers": q["top_blockers"],
    }
    quality = {"contract_version":"g1-intelligence-cockpit-v1",
               "presentation_state":"LIVE_MATERIALIZED",
               "short_horizon": short,
               "presentation_note":"G.1S baselines are materialized from frozen short-horizon outcomes."}
    calibration = {"contract_version":"g1-intelligence-cockpit-v1",
                   "presentation_state":"LIVE_MATERIALIZED", "status":g1c,
                   "models":passive.g1c_models(limit=50), "cohorts":{"items":[]},
                   "predictions":passive.g1c_predictions(limit=50),
                   "research_only":True,"production_used":False}
    _nb._store_value(runtime, "panel_status", base)
    _nb._store_value(runtime, "panel_pipeline", pipeline)
    _nb._store_value(runtime, "panel_quality", quality)
    _nb._store_value(runtime, "panel_calibration", calibration)
    runtime._g1e_last_warm_ts = time.time()


def install_research_scalability(app) -> None:
    if getattr(app.state, "research_scalability_installed", False):
        return
    passive = app.state.engine.passive
    passive._legacy_request_status = passive.status
    passive._legacy_request_calibration = passive.calibration_report
    passive._legacy_request_edge = passive.edge_report
    passive._legacy_g1_q_status = passive.g1_q_status
    passive._legacy_g1c_status = passive.g1c_status
    passive.status = types.MethodType(_light_passive_status, passive)
    passive.calibration_report = types.MethodType(_light_passive_calibration, passive)
    passive.edge_report = types.MethodType(_light_passive_edge, passive)
    passive.g1_q_status = types.MethodType(_light_q_status, passive)
    passive.g1c_status = types.MethodType(_light_g1c_status, passive)

    storage = getattr(app.state, "storage", None)
    if storage is not None:
        original_storage_status = storage.status
        def bounded_storage_status(self, *, engine=None):
            result = original_storage_status(engine=None)
            result["research_health_decoupled"] = True
            result["scalability_contract_version"] = SCALABILITY_CONTRACT_VERSION
            return result
        storage.status = types.MethodType(bounded_storage_status, storage)

    _g1c._maybe_refit = _gated_maybe_refit
    _nb._warm_live = _light_intelligence_warm
    app.state.research_scalability_installed = True
