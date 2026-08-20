"""Conservative completion of the G.1S prospective OOS evidence contract.

This is intentionally the last G.1S monkey-patch layer.  It does not change
production trading authority.  It makes dependency-adjusted metrics primary,
restores the serious 1000/400 evidence gate from the specification, and ensures
that every evaluated prediction/model/baseline was knowable before the relevant
future outcome.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from typing import Any

from . import g1_short_horizon_runtime as _runtime_module
from .g1_short_horizon_metrics_refinement import (
    DEPENDENCY_GROUP_VERSION,
    PATH_METRICS_VERSION,
)
from .g1_short_horizon_runtime import ShortHorizonRuntime, _finite


EVIDENCE_COMPLETION_VERSION = "g1s-evidence-completion-v1"
OOS_WEIGHTING_VERSION = "g1s-dependency-weighted-oos-v2"
BASELINE_CAUSALITY_VERSION = "g1s-causal-baselines-v1"
PROSPECTIVE_TIMING_VERSION = "g1s-prospective-timing-v2"

SERIOUS_OOS_REQUIRED = {
    "raw_resolved": 1000,
    "effective_n": 400,
    "positive_n": 120,
    "negative_n": 120,
    "temporal_blocks": 20,
}


def _clip(value: float) -> float:
    return max(1e-9, min(1.0 - 1e-9, float(value)))


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ret15(row: dict[str, Any]) -> float | None:
    if "frozen_ret_15m" in row:
        return _finite(row.get("frozen_ret_15m"))
    features = _loads(row.get("frozen_features_json"))
    intraday = features.get("g1s_intraday")
    if isinstance(intraday, dict):
        value = _finite(intraday.get("ret_15m"))
        if value is not None:
            return value
    for key in ("ret_15m", "return_15m"):
        value = _finite(features.get(key))
        if value is not None:
            return value
    return None


def _weighted_mean(values: list[float], weights: list[float]) -> float | None:
    den = sum(weights)
    if not values or den <= 0.0:
        return None
    return sum(v*w for v, w in zip(values, weights)) / den


def _weighted_brier(ps: list[float], ys: list[int], weights: list[float]) -> float | None:
    return _weighted_mean([(p-y)**2 for p, y in zip(ps, ys)], weights)


def _weighted_logloss(ps: list[float], ys: list[int], weights: list[float]) -> float | None:
    losses: list[float] = []
    for p, y in zip(ps, ys):
        p = _clip(p)
        losses.append(-(y*math.log(p) + (1-y)*math.log(1-p)))
    return _weighted_mean(losses, weights)


def _weighted_ece(ps: list[float], ys: list[int], weights: list[float], bins: int = 10) -> tuple[float | None, list[dict[str, Any]]]:
    den = sum(weights)
    if not ps or den <= 0.0:
        return None, []
    reliability: list[dict[str, Any]] = []
    ece = 0.0
    for index in range(bins):
        lo = index / bins
        hi = (index+1) / bins
        members = [i for i, p in enumerate(ps)
                   if (lo <= p < hi) or (index == bins-1 and p == 1.0)]
        if not members:
            continue
        w = [weights[i] for i in members]
        bin_weight = sum(w)
        mean_p = sum(weights[i]*ps[i] for i in members) / bin_weight
        observed = sum(weights[i]*ys[i] for i in members) / bin_weight
        ece += (bin_weight/den) * abs(mean_p-observed)
        reliability.append({
            "lo": lo,
            "hi": hi,
            "weight": bin_weight,
            "mean_probability": mean_p,
            "observed_rate": observed,
            "absolute_gap": abs(mean_p-observed),
        })
    return ece, reliability


def _weighted_balanced_accuracy(ps: list[float], ys: list[int], weights: list[float]) -> float | None:
    pos_den = sum(w for y, w in zip(ys, weights) if y == 1)
    neg_den = sum(w for y, w in zip(ys, weights) if y == 0)
    if pos_den <= 0.0 or neg_den <= 0.0:
        return None
    tpr = sum(w for p, y, w in zip(ps, ys, weights) if y == 1 and p >= 0.5) / pos_den
    tnr = sum(w for p, y, w in zip(ps, ys, weights) if y == 0 and p < 0.5) / neg_den
    return 0.5*(tpr+tnr)


def _weighted_roc_auc(ps: list[float], ys: list[int], weights: list[float]) -> float | None:
    pos_total = sum(w for y, w in zip(ys, weights) if y == 1)
    neg_total = sum(w for y, w in zip(ys, weights) if y == 0)
    if pos_total <= 0.0 or neg_total <= 0.0:
        return None
    ranked = sorted(zip(ps, ys, weights), key=lambda item: item[0])
    concordant = 0.0
    cum_neg = 0.0
    index = 0
    while index < len(ranked):
        score = ranked[index][0]
        tie_pos = tie_neg = 0.0
        end = index
        while end < len(ranked) and ranked[end][0] == score:
            _, y, w = ranked[end]
            if y == 1:
                tie_pos += w
            else:
                tie_neg += w
            end += 1
        concordant += tie_pos*(cum_neg + 0.5*tie_neg)
        cum_neg += tie_neg
        index = end
    return concordant/(pos_total*neg_total)


def _weighted_pr_auc(ps: list[float], ys: list[int], weights: list[float]) -> float | None:
    total_pos = sum(w for y, w in zip(ys, weights) if y == 1)
    if total_pos <= 0.0:
        return None
    ranked = sorted(zip(ps, ys, weights), key=lambda item: item[0], reverse=True)
    tp = fp = previous_recall = area = 0.0
    index = 0
    while index < len(ranked):
        score = ranked[index][0]
        tie_tp = tie_fp = 0.0
        end = index
        while end < len(ranked) and ranked[end][0] == score:
            _, y, w = ranked[end]
            if y == 1:
                tie_tp += w
            else:
                tie_fp += w
            end += 1
        tp += tie_tp
        fp += tie_fp
        recall = tp/total_pos
        precision = tp/max(tp+fp, 1e-12)
        area += max(0.0, recall-previous_recall)*precision
        previous_recall = recall
        index = end
    return area


def _dependency_weights(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]]) -> tuple[list[float], int]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[runtime._dependency_key(row)].append(index)
    weights = [0.0]*len(rows)
    for members in groups.values():
        member_weight = 1.0/len(members)
        for index in members:
            weights[index] = member_weight
    return weights, len(groups)


def _safe_eval_rows(runtime: ShortHorizonRuntime) -> dict[str, list[dict[str, Any]]]:
    """Return only genuinely prospective, pre-outcome model predictions."""
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT p.model_id,p.p_up,p.created_ts AS prediction_created_ts,
                   g.observation_id,g.instrument,g.horizon_minutes,g.captured_ts,
                   g.target_ts,g.market_regime,
                   CASE WHEN json_valid(g.frozen_features_json) THEN COALESCE(
                        json_extract(g.frozen_features_json,'$.g1s_intraday.ret_15m'),
                        json_extract(g.frozen_features_json,'$.ret_15m'),
                        json_extract(g.frozen_features_json,'$.return_15m'))
                        ELSE NULL END AS frozen_ret_15m,
                   r.direction_label,r.resolved_ts,
                   m.feature_set,m.model_family,m.created_ts AS model_created_ts,
                   m.training_cutoff_ts
            FROM g1s_shadow_predictions p
            JOIN g1s_observations g USING(observation_id)
            JOIN g1s_resolutions r USING(observation_id)
            JOIN g1s_models m USING(model_id)
            WHERE p.production_used=0 AND g.oos_eligible=1
              AND r.direction_label!='FLAT'
            ORDER BY g.captured_ts,g.observation_id,p.model_id
        """).fetchall()
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        captured = _finite(row.get("captured_ts"))
        target = _finite(row.get("target_ts"))
        resolved = _finite(row.get("resolved_ts"))
        model_created = _finite(row.get("model_created_ts"))
        cutoff = _finite(row.get("training_cutoff_ts"))
        predicted = _finite(row.get("prediction_created_ts"))
        p_up = _finite(row.get("p_up"))
        if None in (captured, target, resolved, model_created, cutoff, predicted, p_up):
            continue
        # Model must have existed by T0, its fit cut must be strictly earlier,
        # and the shadow prediction must be frozen before the future horizon ends.
        if model_created > captured + 1e-6:
            continue
        if cutoff >= captured - 1e-9:
            continue
        if predicted >= target - 1e-9:
            continue
        if resolved < target - 1e-6:
            continue
        by_model[str(row["model_id"])].append(row)
    return by_model


def _causal_baselines(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Construct baselines using only labels resolved before each prediction T0."""
    if not rows:
        return {"constant_0_5": [], "chronological_base_rate": [],
                "naive_resolved_persistence": [], "fixed_momentum_15m": []}
    ordered = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
    resolution_events = sorted(ordered, key=lambda row: (float(row["resolved_ts"]), float(row["captured_ts"])))
    visible_up = visible_n = 0
    latest_visible: dict[str, Any] | None = None
    event_index = 0
    base_rate: list[float] = []
    persistence: list[float] = []
    momentum: list[float] = []
    for row in ordered:
        captured = float(row["captured_ts"])
        while event_index < len(resolution_events):
            event = resolution_events[event_index]
            if float(event["resolved_ts"]) >= captured - 1e-9:
                break
            # Future/corrupt capture ordering is never admitted as prior evidence.
            if float(event["captured_ts"]) < captured - 1e-9:
                y_event = 1 if str(event["direction_label"]) == "UP" else 0
                visible_up += y_event
                visible_n += 1
                latest_visible = event
            event_index += 1
        base_rate.append(0.5 if visible_n < 20 else visible_up/visible_n)
        if latest_visible is None:
            persistence.append(0.5)
        else:
            persistence.append(0.55 if str(latest_visible["direction_label"]) == "UP" else 0.45)
        ret15 = _ret15(row)
        momentum.append(0.5 if ret15 is None or abs(ret15) < 1e-12 else (0.55 if ret15 > 0 else 0.45))
    return {
        "constant_0_5": [0.5]*len(ordered),
        "chronological_base_rate": base_rate,
        "naive_resolved_persistence": persistence,
        "fixed_momentum_15m": momentum,
    }


def _candidate_blockers(rows: list[dict[str, Any]], effective_n: int) -> tuple[dict[str, int], list[str]]:
    positive = sum(str(row["direction_label"]) == "UP" for row in rows)
    negative = sum(str(row["direction_label"]) == "DOWN" for row in rows)
    days = len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"]))) for row in rows})
    regimes = len({str(row.get("market_regime") or "UNKNOWN") for row in rows})
    observed = {
        "raw_resolved": len(rows),
        "effective_n": int(effective_n),
        "positive_n": int(positive),
        "negative_n": int(negative),
        "temporal_blocks": int(days),
        "volatility_regime_count": int(regimes),
    }
    blockers = [
        f"INSUFFICIENT_{key.upper()}"
        for key, required in SERIOUS_OOS_REQUIRED.items()
        if observed[key] < int(required)
    ]
    if regimes < 2:
        blockers.append("INSUFFICIENT_VOLATILITY_REGIME_DIVERSITY")
    return observed, blockers


def _evaluate_model(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "contract_version": OOS_WEIGHTING_VERSION,
            "raw_n": 0,
            "effective_n": 0,
            "candidate_blockers": ["NO_SAFE_PROSPECTIVE_ROWS"],
            "verdict": "INSUFFICIENT",
        }
    rows = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
    ps = [float(row["p_up"]) for row in rows]
    ys = [1 if str(row["direction_label"]) == "UP" else 0 for row in rows]
    weights, group_n = _dependency_weights(runtime, rows)
    ece, reliability = _weighted_ece(ps, ys, weights)
    baselines_p = _causal_baselines(rows)
    baselines: dict[str, dict[str, Any]] = {}
    for name, values in baselines_p.items():
        baseline_ece, _ = _weighted_ece(values, ys, weights)
        baselines[name] = {
            "brier": _weighted_brier(values, ys, weights),
            "log_loss": _weighted_logloss(values, ys, weights),
            "ece": baseline_ece,
        }
    model_brier = _weighted_brier(ps, ys, weights)
    model_log = _weighted_logloss(ps, ys, weights)
    observed, blockers = _candidate_blockers(rows, group_n)
    baseline_briers = [item["brier"] for item in baselines.values() if item["brier"] is not None]
    baseline_logs = [item["log_loss"] for item in baselines.values() if item["log_loss"] is not None]
    if blockers:
        verdict = "INSUFFICIENT"
    elif (model_brier is not None and model_log is not None and baseline_briers and baseline_logs
          and model_brier < min(baseline_briers) and model_log < min(baseline_logs)):
        verdict = "YES"
    else:
        verdict = "NO"
    return {
        "contract_version": OOS_WEIGHTING_VERSION,
        "baseline_causality_contract_version": BASELINE_CAUSALITY_VERSION,
        "prospective_timing_contract_version": PROSPECTIVE_TIMING_VERSION,
        "metric_weighting": "dependency_group_total_weight_one",
        "raw_n": len(rows),
        "effective_n": group_n,
        "weight_sum": sum(weights),
        "positive_n": observed["positive_n"],
        "negative_n": observed["negative_n"],
        "temporal_blocks": observed["temporal_blocks"],
        "volatility_regime_count": observed["volatility_regime_count"],
        "oos_candidate_required": dict(SERIOUS_OOS_REQUIRED),
        "candidate_blockers": blockers,
        "brier": model_brier,
        "log_loss": model_log,
        "ece": ece,
        "reliability": reliability,
        "balanced_accuracy": _weighted_balanced_accuracy(ps, ys, weights),
        "roc_auc_secondary": _weighted_roc_auc(ps, ys, weights),
        "pr_auc_secondary": _weighted_pr_auc(ps, ys, weights),
        "baselines": baselines,
        "verdict": verdict,
        "dependency_group_total_weight_one": True,
        "model_must_exist_by_t0": True,
        "training_cutoff_strictly_before_t0": True,
        "prediction_must_precede_target": True,
        "chronological_baselines_use_only_pre_t0_resolutions": True,
    }


def _install_effectiveness(previous_effectiveness):
    def effectiveness(runtime: ShortHorizonRuntime) -> dict[str, Any]:
        report = previous_effectiveness(runtime)
        safe_by_model = _safe_eval_rows(runtime)
        for item in report.get("items", []):
            model_id = str(item.get("model_id") or "")
            authoritative = _evaluate_model(runtime, safe_by_model.get(model_id, []))
            old_oos = item.get("oos")
            if isinstance(old_oos, dict):
                item["descriptive_unweighted_oos"] = dict(old_oos)
            item["oos"] = authoritative
            item["dependency_adjusted_oos"] = authoritative
            item["does_model_beat_baseline_oos"] = authoritative["verdict"]
            item["oos_candidate_blockers"] = authoritative.get("candidate_blockers", [])
            item["effectiveness_integrity_version"] = EVIDENCE_COMPLETION_VERSION
        verdicts = [str(item.get("does_model_beat_baseline_oos")) for item in report.get("items", [])]
        report["does_model_beat_baseline_oos"] = (
            "YES" if "YES" in verdicts else ("NO" if "NO" in verdicts else "INSUFFICIENT")
        )
        report["oos_candidate_required"] = dict(SERIOUS_OOS_REQUIRED)
        report["oos_metric_weighting"] = "dependency_group_total_weight_one"
        report["baseline_causality_contract_version"] = BASELINE_CAUSALITY_VERSION
        report["prospective_timing_contract_version"] = PROSPECTIVE_TIMING_VERSION
        report["evidence_completion_version"] = EVIDENCE_COMPLETION_VERSION
        report["oos_validated"] = False
        report["edge_claim_allowed"] = False
        report["production_authority"] = False
        return report
    return effectiveness


def _install_status(previous_status):
    def status(runtime: ShortHorizonRuntime) -> dict[str, Any]:
        report = previous_status(runtime)
        report["oos_candidate_required"] = dict(SERIOUS_OOS_REQUIRED)
        report["path_metrics_contract_version"] = PATH_METRICS_VERSION
        report["dependency_group_contract_version"] = DEPENDENCY_GROUP_VERSION
        report["evidence_completion_version"] = EVIDENCE_COMPLETION_VERSION
        report["prospective_timing_contract_version"] = PROSPECTIVE_TIMING_VERSION
        report["baseline_causality_contract_version"] = BASELINE_CAUSALITY_VERSION
        report["production_authority"] = False
        report["auto_promotion"] = False
        report["edge_claim_allowed"] = False
        return report
    return status


def install_g1_short_horizon_evidence_completion() -> None:
    if getattr(ShortHorizonRuntime, "_evidence_completion_version", None) == EVIDENCE_COMPLETION_VERSION:
        return
    # Mutate the shared dict in place so previously imported bounded status code
    # sees the same serious candidate gate without requiring a request-time scan.
    _runtime_module.OOS_CANDIDATE_REQUIRED.clear()
    _runtime_module.OOS_CANDIDATE_REQUIRED.update(SERIOUS_OOS_REQUIRED)

    previous_effectiveness = ShortHorizonRuntime.effectiveness
    previous_status = ShortHorizonRuntime.status
    completed_effectiveness = _install_effectiveness(previous_effectiveness)
    ShortHorizonRuntime.effectiveness = completed_effectiveness
    ShortHorizonRuntime.prospective_oos = completed_effectiveness
    ShortHorizonRuntime.status = _install_status(previous_status)
    ShortHorizonRuntime._evidence_completion_version = EVIDENCE_COMPLETION_VERSION
