"""Bounded prospective OOS evidence report for P3L volatility shadow models."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

import numpy as np

from .g1_short_horizon_historical_wf import _weights
from . import g1_short_horizon_p3_path_geometry as _p3
from .g1_short_horizon_p3_live_schema import (
    ASSET_FAMILY,
    P3L_CONTRACT_VERSION,
    P3L_EVIDENCE_LABEL,
    P3L_METRIC_MARGIN,
    P3L_PROGRESS_VERSION,
    P3L_REQUIRED_ROBUST_BLOCKS,
    P3L_SERIOUS_REQUIRED,
    ensure_p3l_tables,
)


def _metric_bundle(rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    y = np.asarray([float(row["future_realized_volatility_5m"]) for row in rows], dtype=float)
    prediction = np.asarray([float(row["predicted_volatility_5m"]) for row in rows], dtype=float)
    weights, _ = _weights(rows)
    model = _p3._metrics(y, prediction, weights)
    names = sorted(json.loads(rows[0]["baseline_predictions_json"])) if rows else []
    baselines = {}
    for name in names:
        values = np.asarray([
            float(json.loads(row["baseline_predictions_json"])[name]) for row in rows], dtype=float)
        baselines[name] = _p3._metrics(y, values, weights)
    return model, baselines


def _robust_blocks(rows: list[dict[str, Any]], blocks: int = 4) -> tuple[int, list[dict[str, Any]]]:
    if len(rows) < blocks:
        return 0, []
    ordered = sorted(rows, key=lambda row: (
        float(row["captured_ts"]), str(row["observation_id"])))
    reports = []; passed = 0
    for block_index, indices in enumerate(np.array_split(np.arange(len(ordered)), blocks), 1):
        group = [ordered[int(index)] for index in indices]
        if not group:
            continue
        model, baselines = _metric_bundle(group)
        mae_name, best_mae = _p3._best(baselines, "mae")
        rmse_name, best_rmse = _p3._best(baselines, "rmse")
        joint = model["mae"] <= best_mae and model["rmse"] <= best_rmse
        passed += int(joint)
        reports.append({
            "block": block_index, "n": len(group),
            "first_captured_ts": float(group[0]["captured_ts"]),
            "last_captured_ts": float(group[-1]["captured_ts"]),
            "model_mae": model["mae"], "best_mae_baseline": mae_name,
            "best_mae": best_mae, "model_rmse": model["rmse"],
            "best_rmse_baseline": rmse_name, "best_rmse": best_rmse,
            "joint_non_degrade": joint,
        })
    return passed, reports


def _instrument_heterogeneity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["instrument"])].append(row)
    out = []
    for instrument, group in sorted(grouped.items()):
        model, baselines = _metric_bundle(group)
        mae_name, best_mae = _p3._best(baselines, "mae")
        rmse_name, best_rmse = _p3._best(baselines, "rmse")
        out.append({
            "instrument": instrument, "n": len(group),
            "model_mae": model["mae"], "best_mae_baseline": mae_name,
            "mae_relative_improvement": (
                (best_mae-model["mae"])/best_mae if best_mae > _p3.EPS else None),
            "model_rmse": model["rmse"], "best_rmse_baseline": rmse_name,
            "rmse_relative_improvement": (
                (best_rmse-model["rmse"])/best_rmse if best_rmse > _p3.EPS else None),
            "descriptive_only": True,
        })
    return out


def _resolved_rows(runtime, horizon: int) -> list[dict[str, Any]]:
    with runtime._lock:
        return [dict(row) for row in runtime._conn.execute("""
            SELECT o.observation_id,o.instrument,o.horizon_minutes,o.captured_ts,o.target_ts,
                   p.predicted_volatility_5m,p.baseline_predictions_json,
                   r.future_realized_volatility_5m,
                   r.future_realized_volatility_1m_secondary,r.resolved_ts
            FROM g1s_volatility_observations o
            JOIN g1s_volatility_predictions p USING(observation_id)
            JOIN g1s_volatility_resolutions r USING(observation_id)
            WHERE o.horizon_minutes=? AND o.evidence_eligible=1
              AND r.resolution_status='RESOLVED'
            ORDER BY o.captured_ts,o.observation_id
        """, (int(horizon),)).fetchall()]


def refresh_p3l_progress(runtime) -> dict[int, dict[str, Any]]:
    ensure_p3l_tables(runtime)
    result = {}
    for horizon in _p3.HORIZONS:
        rows = _resolved_rows(runtime, int(horizon))
        if rows:
            model, baselines = _metric_bundle(rows)
            _, effective = _weights(rows)
            mae_name, best_mae = _p3._best(baselines, "mae")
            rmse_name, best_rmse = _p3._best(baselines, "rmse")
            mae_improvement = ((best_mae-model["mae"])/best_mae
                               if best_mae > _p3.EPS else None)
            rmse_improvement = ((best_rmse-model["rmse"])/best_rmse
                                if best_rmse > _p3.EPS else None)
            dates = {time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"])))
                     for row in rows}
            instruments = {str(row["instrument"]) for row in rows}
            families = {ASSET_FAMILY.get(instrument, "other") for instrument in instruments}
            robust_n, robust_report = _robust_blocks(rows)
            observed = {
                "raw_resolved": len(rows), "effective_n": int(effective),
                "temporal_blocks": len(dates), "instrument_count": len(instruments),
                "asset_family_count": len(families),
            }
            sample_gate = all(observed[name] >= required
                              for name, required in P3L_SERIOUS_REQUIRED.items())
            metric_gate = bool(
                mae_improvement is not None and rmse_improvement is not None
                and mae_improvement >= P3L_METRIC_MARGIN
                and rmse_improvement >= P3L_METRIC_MARGIN)
            robust_gate = robust_n >= P3L_REQUIRED_ROBUST_BLOCKS
            verdict = ("INSUFFICIENT" if not sample_gate else
                       "YES" if metric_gate and robust_gate else "NO")
            latest = max(float(row["resolved_ts"]) for row in rows)
            heterogeneity = _instrument_heterogeneity(rows)
        else:
            model = {"mae": None, "rmse": None}; baselines = {}
            mae_name = rmse_name = None; best_mae = best_rmse = None
            mae_improvement = rmse_improvement = None; robust_n = 0
            robust_report = []; heterogeneity = []
            observed = {name: 0 for name in P3L_SERIOUS_REQUIRED}
            verdict = "INSUFFICIENT"; latest = None
        with runtime._lock, runtime._conn:
            runtime._conn.execute("""
                INSERT INTO g1s_volatility_progress(
                    horizon_minutes,contract_version,raw_resolved,effective_n,temporal_blocks,
                    instrument_count,asset_family_count,robust_block_non_degrade_n,
                    model_mae,model_rmse,best_mae_baseline,best_mae,best_rmse_baseline,best_rmse,
                    mae_relative_improvement,rmse_relative_improvement,verdict,latest_resolved_ts,
                    updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(horizon_minutes) DO UPDATE SET
                    contract_version=excluded.contract_version,
                    raw_resolved=excluded.raw_resolved,effective_n=excluded.effective_n,
                    temporal_blocks=excluded.temporal_blocks,
                    instrument_count=excluded.instrument_count,
                    asset_family_count=excluded.asset_family_count,
                    robust_block_non_degrade_n=excluded.robust_block_non_degrade_n,
                    model_mae=excluded.model_mae,model_rmse=excluded.model_rmse,
                    best_mae_baseline=excluded.best_mae_baseline,best_mae=excluded.best_mae,
                    best_rmse_baseline=excluded.best_rmse_baseline,best_rmse=excluded.best_rmse,
                    mae_relative_improvement=excluded.mae_relative_improvement,
                    rmse_relative_improvement=excluded.rmse_relative_improvement,
                    verdict=excluded.verdict,latest_resolved_ts=excluded.latest_resolved_ts,
                    updated_ts=excluded.updated_ts
            """, (int(horizon), P3L_PROGRESS_VERSION,
                    int(observed["raw_resolved"]), int(observed["effective_n"]),
                    int(observed["temporal_blocks"]), int(observed["instrument_count"]),
                    int(observed["asset_family_count"]), int(robust_n),
                    model["mae"], model["rmse"], mae_name, best_mae,
                    rmse_name, best_rmse, mae_improvement, rmse_improvement,
                    verdict, latest, time.time()))
        result[int(horizon)] = {
            **observed,
            "robust_block_non_degrade_n": robust_n,
            "model": model, "baselines": baselines,
            "best_mae_baseline": mae_name, "best_rmse_baseline": rmse_name,
            "mae_relative_improvement": mae_improvement,
            "rmse_relative_improvement": rmse_improvement,
            "verdict": verdict,
            "robust_blocks": robust_report,
            "instrument_heterogeneity": heterogeneity,
        }
    return result


def p3l_evidence_report(runtime) -> dict[str, Any]:
    """Bounded report: reads only the five materialized progress rows."""
    ensure_p3l_tables(runtime)
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute(
            "SELECT * FROM g1s_volatility_progress ORDER BY horizon_minutes").fetchall()]
    return {
        "contract_version": P3L_CONTRACT_VERSION,
        "evidence_label": P3L_EVIDENCE_LABEL,
        "target": _p3.TARGET_FUTURE_RV,
        "horizons": rows,
        "serious_oos_required": dict(P3L_SERIOUS_REQUIRED),
        "metric_margin_required": P3L_METRIC_MARGIN,
        "robust_blocks_required": P3L_REQUIRED_ROBUST_BLOCKS,
        "instrument_heterogeneity_descriptive_only": True,
        "posthoc_instrument_selection_allowed": False,
        "request_time_full_history_scan": False,
        "edge_claim_allowed": False,
        "auto_promotion": False,
        "production_authority": False,
    }
