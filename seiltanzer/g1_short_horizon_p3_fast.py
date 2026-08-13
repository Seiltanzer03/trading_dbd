"""Exact fast runner for P3 path-geometry research.

The reference P3 target constructor is intentionally simple and independently
testable. This module precomputes source timestamps/T0 contexts once, then reuses
them across all five horizons. Target semantics, folds, baselines, models and
gates are unchanged.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from . import g1_short_horizon_p3_path_geometry as _p3
from .g1_short_horizon_historical_wf import BAR_SECONDS, _target_index, _weights


P3_FAST_CONTRACT = "g1s-p3-path-builder-precomputed-v1"


def _target_row_fast(source: dict[str, Any], context: dict[str, float],
                     horizon_minutes: int, times: list[float],
                     index: int) -> dict[str, Any] | None:
    bars = source["bars"]
    target_index = _target_index(times, int(index), int(horizon_minutes))
    if target_index is None:
        return None
    current = float(context["current_close"])
    future = bars[index + 1:target_index + 1]
    if len(future) < 2 or current <= 0:
        return None
    closes = np.asarray([current] + [float(bar["close"]) for bar in future], dtype=float)
    if np.any(closes <= 0):
        return None
    future_step_returns = np.diff(np.log(closes))
    if len(future_step_returns) < 2:
        return None
    high = max([current] + [float(bar["high"]) for bar in future])
    low = min([current] + [float(bar["low"]) for bar in future])
    if high <= 0 or low <= 0:
        return None
    return {
        **context,
        "target_ts": float(times[target_index]),
        "horizon_minutes": int(horizon_minutes),
        "future_steps_5m": len(future_step_returns),
        _p3.TARGET_FUTURE_RV: float(np.std(future_step_returns, ddof=0)),
        _p3.TARGET_MFE: max(0.0, float(math.log(high / current))),
        _p3.TARGET_MAE: max(0.0, float(-math.log(low / current))),
        "path_source": "real_yahoo_5m_ohlc",
        "historical_sampling_interval_sec": int(BAR_SECONDS),
    }


def _precompute_sources(sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for source in sources:
        instrument = str(source["instrument"])
        times = [float(bar["bar_end_ts"]) for bar in source["bars"]]
        result[instrument] = {
            "source": source,
            "times": times,
            "index_by_ts": {float(ts): index for index, ts in enumerate(times)},
            "contexts": _p3._pre_t0_context(source),
        }
    return result


def build_rows_fast(precomputed: dict[str, dict[str, Any]],
                    horizon_minutes: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in precomputed.values():
        source = item["source"]
        times = item["times"]
        index_by_ts = item["index_by_ts"]
        contexts = item["contexts"]
        for captured_ts in sorted(contexts):
            index = index_by_ts.get(float(captured_ts))
            if index is None:
                continue
            row = _target_row_fast(
                source, contexts[captured_ts], int(horizon_minutes), times, int(index))
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["instrument"])))
    return rows


def run_p3_path_geometry_fast(runtime) -> dict[str, Any]:
    source_set, sources = _p3._current_sources(runtime)
    started = time.time()
    precomputed = _precompute_sources(sources)
    results = []
    for horizon in _p3.HORIZONS:
        rows = build_rows_fast(precomputed, horizon)
        _weight, effective = _weights(rows)
        for target in _p3.TARGETS:
            evaluation = _p3.evaluate_target(rows, horizon, target)
            gate = _p3.winner_gate(evaluation, len(rows), effective)
            results.append({
                "target": target,
                "horizon_minutes": horizon,
                "raw_n": len(rows),
                "effective_n": effective,
                "historical_winner": gate["historical_winner"],
                "mae_relative_improvement": gate["mae_relative_improvement"],
                "rmse_relative_improvement": gate["rmse_relative_improvement"],
                "fold_joint_non_degrade_n": gate["fold_joint_non_degrade_observed"],
                "best_mae_baseline": gate["best_mae_baseline"],
                "best_rmse_baseline": gate["best_rmse_baseline"],
                "gate": gate,
            })
    return {
        "contract_version": _p3.P3_CONTRACT_VERSION,
        "builder_compute_contract": P3_FAST_CONTRACT,
        "feature_contract_version": _p3.P3_FEATURE_CONTRACT,
        "evidence_label": _p3.P3_EVIDENCE_LABEL,
        "source_set_sha256": source_set,
        "historical_sampling_interval": "5m",
        "live_path_sampling_interval": "1m_or_recorded_path",
        "historical_future_volatility_name": _p3.TARGET_FUTURE_RV,
        "historical_future_volatility_is_live_1m_metric": False,
        "mfe_mae_semantics_match_live_high_low_geometry": True,
        "path_resolution_matches_live": False,
        "run_count": len(results),
        "winner_count": sum(bool(row["historical_winner"]) for row in results),
        "results": results,
        "outer_test_used_for_model_selection": False,
        "fixed_model_family": _p3.P3_MODEL_FAMILY,
        "fixed_l2": _p3.P3_L2,
        "dependency_group_total_weight_one": True,
        "purge_target_overlap": True,
        "historical_options_used": False,
        "synthetic_option_history": False,
        "live_parity_ready": False,
        "auto_promotion": False,
        "production_authority": False,
        "duration_ms": (time.time() - started) * 1000.0,
    }
