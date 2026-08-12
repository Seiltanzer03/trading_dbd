"""Final evidence-integrity layer for G.1S effectiveness metrics.

The base metrics refinement is descriptive and useful, but overlapping short-horizon
observations must not receive independent weight in an OOS claim and a real-trade
relevance row must never use a prediction written after that trade was opened.
This layer keeps all learned components research-only while making those two
boundaries explicit and deterministic.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from .g1_short_horizon_baseline_refinement import _momentum_probability
from .g1_short_horizon_metrics_refinement import (
    OOS_CONTEXT_MIN_EFFECTIVE_N,
    _effectiveness as _base_effectiveness,
)
from .g1_short_horizon_runtime import ShortHorizonRuntime, _finite


INTEGRITY_VERSION = "g1s-effectiveness-integrity-v1"
DEPENDENCY_WEIGHT_VERSION = "g1s-oos-dependency-weight-v1"
TRADE_VALIDATION_VERSION = "g1s-trade-preentry-validation-v1"


def _clip(value: float) -> float:
    return max(1e-9, min(1.0 - 1e-9, float(value)))


def _weighted_brier(ps: list[float], ys: list[int], ws: list[float]) -> float | None:
    den = sum(ws)
    if not ps or den <= 0:
        return None
    return sum(w * (p-y) ** 2 for p, y, w in zip(ps, ys, ws)) / den


def _weighted_logloss(ps: list[float], ys: list[int], ws: list[float]) -> float | None:
    den = sum(ws)
    if not ps or den <= 0:
        return None
    total = 0.0
    for p, y, w in zip(ps, ys, ws):
        p = _clip(p)
        total += w * (-(y * math.log(p) + (1-y) * math.log(1-p)))
    return total / den


def _model_eval_rows(runtime: ShortHorizonRuntime) -> dict[str, list[dict[str, Any]]]:
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT p.model_id,p.p_up,p.created_ts AS prediction_created_ts,
                   g.observation_id,g.instrument,g.horizon_minutes,g.captured_ts,
                   g.market_regime,g.frozen_features_json,g.frozen_forecast_json,
                   r.direction_label,m.feature_set,m.model_family
            FROM g1s_shadow_predictions p
            JOIN g1s_observations g USING(observation_id)
            JOIN g1s_resolutions r USING(observation_id)
            JOIN g1s_models m USING(model_id)
            WHERE p.production_used=0 AND r.direction_label!='FLAT'
            ORDER BY g.captured_ts,g.observation_id,p.model_id
        """).fetchall()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[str(row["model_id"])].append(dict(row))
    return out


def _dependency_weights(runtime: ShortHorizonRuntime,
                        rows: list[dict[str, Any]]) -> tuple[list[float], int]:
    """Give every overlapping temporal group total weight one for this model."""
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[runtime._dependency_key(row)].append(index)
    weights = [0.0] * len(rows)
    for members in groups.values():
        weight = 1.0 / len(members)
        for index in members:
            weights[index] = weight
    return weights, len(groups)


def _preentry_trade_metrics(runtime: ShortHorizonRuntime, model_id: str) -> dict[str, Any]:
    """Use only model/prediction artifacts already persisted before trade entry."""
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT DISTINCT l.trade_id,l.horizon_minutes,t.opened_at,t.direction,t.result_r,
                   r.direction_label,p.p_up,p.created_ts AS prediction_created_ts,
                   m.created_ts AS model_created_ts,m.training_cutoff_ts
            FROM g1s_trade_links l
            JOIN trades t ON t.id=l.trade_id
            JOIN g1s_shadow_predictions p
              ON p.observation_id=l.observation_id AND p.model_id=?
            JOIN g1s_models m ON m.model_id=p.model_id
            JOIN g1s_resolutions r ON r.observation_id=l.observation_id
            WHERE p.production_used=0 AND r.direction_label!='FLAT'
              AND p.created_ts<=t.opened_at
              AND m.created_ts<=t.opened_at
              AND m.training_cutoff_ts<t.opened_at
            ORDER BY t.opened_at,l.trade_id,l.horizon_minutes
        """, (model_id,)).fetchall()
    ps: list[float] = []
    ys: list[int] = []
    winning_ps: list[float] = []
    nonwinning_ps: list[float] = []
    trade_ids: set[int] = set()
    for row in rows:
        direction = str(row["direction"] or "").lower()
        if direction not in {"long", "short"}:
            continue
        p_up = _finite(row["p_up"])
        if p_up is None:
            continue
        p_trade = p_up if direction == "long" else 1.0-p_up
        expected_label = "UP" if direction == "long" else "DOWN"
        y = 1 if str(row["direction_label"]) == expected_label else 0
        ps.append(float(p_trade)); ys.append(y)
        trade_ids.add(int(row["trade_id"]))
        result_r = _finite(row["result_r"])
        if result_r is not None:
            (winning_ps if result_r > 0 else nonwinning_ps).append(float(p_trade))
    brier = None if not ps else sum((p-y)**2 for p, y in zip(ps, ys))/len(ps)
    baseline = None if not ys else sum((0.5-y)**2 for y in ys)/len(ys)
    return {
        "contract_version": TRADE_VALIDATION_VERSION,
        "raw_n": len(ps),
        "unique_trade_n": len(trade_ids),
        "brier_move_with_trade_direction": brier,
        "baseline_0_5_brier": baseline,
        "delta_brier_vs_0_5": None if brier is None or baseline is None else baseline-brier,
        "mean_p_move_with_trade_on_winning_trades": (
            sum(winning_ps)/len(winning_ps) if winning_ps else None),
        "mean_p_move_with_trade_on_nonwinning_trades": (
            sum(nonwinning_ps)/len(nonwinning_ps) if nonwinning_ps else None),
        "prediction_must_precede_trade_entry": True,
        "model_must_precede_trade_entry": True,
        "real_trades_are_validation_not_training": True,
    }


def _dependency_adjusted_for_model(runtime: ShortHorizonRuntime,
                                   rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"raw_n": 0, "effective_n": 0, "verdict": "INSUFFICIENT"}
    ps = [float(row["p_up"]) for row in rows]
    ys = [1 if str(row["direction_label"]) == "UP" else 0 for row in rows]
    weights, group_n = _dependency_weights(runtime, rows)

    # Chronological base-rate uses only labels already seen earlier in this
    # evaluation stream.  It is intentionally simple and parameter-free.
    base_rate: list[float] = []
    seen_up = 0
    seen_n = 0
    for y in ys:
        base_rate.append(0.5 if seen_n < 20 else seen_up/seen_n)
        seen_up += y
        seen_n += 1
    momentum = [_momentum_probability(row) for row in rows]
    half = [0.5] * len(rows)

    model_brier = _weighted_brier(ps, ys, weights)
    model_log = _weighted_logloss(ps, ys, weights)
    baselines = {
        "constant_0_5": {
            "brier": _weighted_brier(half, ys, weights),
            "log_loss": _weighted_logloss(half, ys, weights),
        },
        "chronological_base_rate": {
            "brier": _weighted_brier(base_rate, ys, weights),
            "log_loss": _weighted_logloss(base_rate, ys, weights),
        },
        "fixed_momentum_15m": {
            "brier": _weighted_brier(momentum, ys, weights),
            "log_loss": _weighted_logloss(momentum, ys, weights),
        },
    }
    valid_briers = [v["brier"] for v in baselines.values() if v["brier"] is not None]
    valid_logs = [v["log_loss"] for v in baselines.values() if v["log_loss"] is not None]
    if group_n < OOS_CONTEXT_MIN_EFFECTIVE_N:
        verdict = "INSUFFICIENT"
    elif (model_brier is not None and model_log is not None and valid_briers and valid_logs
          and model_brier < min(valid_briers) and model_log < min(valid_logs)):
        verdict = "YES"
    else:
        verdict = "NO"
    return {
        "contract_version": DEPENDENCY_WEIGHT_VERSION,
        "raw_n": len(rows),
        "effective_n": group_n,
        "weight_sum": sum(weights),
        "model_brier": model_brier,
        "model_log_loss": model_log,
        "baselines": baselines,
        "verdict": verdict,
        "dependency_group_total_weight_one": True,
    }


def _effectiveness_integrity(runtime: ShortHorizonRuntime) -> dict:
    report = _base_effectiveness(runtime)
    rows_by_model = _model_eval_rows(runtime)
    for item in report.get("items", []):
        model_id = str(item.get("model_id") or "")
        dep = _dependency_adjusted_for_model(runtime, rows_by_model.get(model_id, []))
        item["dependency_adjusted_oos"] = dep
        item["does_model_beat_baseline_oos"] = dep["verdict"]
        item.setdefault("oos", {})["effective_n"] = dep["effective_n"]
        item["trade_validation"] = _preentry_trade_metrics(runtime, model_id)
        # Replace the earlier broad "linked observation" counters with the
        # strict pre-entry model/prediction validation contract.
        item["trade_aligned_n"] = item["trade_validation"]["raw_n"]
        item["trade_aligned_delta_brier_vs_0_5"] = item["trade_validation"]["delta_brier_vs_0_5"]
        item["effectiveness_integrity_version"] = INTEGRITY_VERSION
    report["dependency_weight_contract_version"] = DEPENDENCY_WEIGHT_VERSION
    report["trade_validation_contract_version"] = TRADE_VALIDATION_VERSION
    report["effectiveness_integrity_version"] = INTEGRITY_VERSION
    report["oos_validated"] = False
    report["edge_claim_allowed"] = False
    report["production_authority"] = False
    return report


def install_g1_short_horizon_metrics_integrity() -> None:
    if getattr(ShortHorizonRuntime, "_metrics_integrity_version", None) == INTEGRITY_VERSION:
        return
    ShortHorizonRuntime.effectiveness = _effectiveness_integrity
    ShortHorizonRuntime.prospective_oos = _effectiveness_integrity
    ShortHorizonRuntime._metrics_integrity_version = INTEGRITY_VERSION
