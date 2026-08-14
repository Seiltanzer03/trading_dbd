"""G.1-M.1 practical local management-edge report.

This is deliberately an analysis refinement over the existing immutable local
windows/outcomes.  It does not create another collector, simulator or policy.
The primary question is action-relative: which frozen management action was
better on the observed 15/30/60/120 minute path?

Core comparisons use every resolved immutable local window for descriptive
research, while maturity/edge evidence is computed only from windows already
marked ``evidence_eligible`` by the prospective G.1-M.1 contract.  Repeated
reviews of one trade are dependency-weighted by first averaging each comparison
inside the trade, so one trade contributes effective weight one per horizon.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from statistics import median
from typing import Any

from .g1_management_local_runtime import (
    G1M_LOCAL_CONTRACT_VERSION,
    LOCAL_HORIZONS,
    ManagementLocalRuntime,
)


EDGE_V2_VERSION = "g1m-local-management-edge-v2"
PAIRWISE_COMPARISONS = (
    ("HOLD", "EXIT"),
    ("HOLD", "CLOSE_50"),
    ("HOLD", "CLOSE_25"),
    ("CLOSE_25", "CLOSE_50"),
    ("PRODUCTION_POLICY", "HOLD"),
)
MATURITY_THRESHOLDS = {
    "EARLY": 30,
    "RESEARCH": 75,
    "PROVISIONAL": 150,
}
CONTEXT_MIN_RAW = 5
CONTEXT_SCAN_LIMIT = 5000


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _q(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    position = min(1.0, max(0.0, q)) * (len(xs) - 1)
    lo, hi = int(math.floor(position)), int(math.ceil(position))
    if lo == hi:
        return xs[lo]
    fraction = position - lo
    return xs[lo] * (1.0 - fraction) + xs[hi] * fraction


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _maturity(effective_n: int) -> str:
    if effective_n >= MATURITY_THRESHOLDS["PROVISIONAL"]:
        return "PROVISIONAL"
    if effective_n >= MATURITY_THRESHOLDS["RESEARCH"]:
        return "RESEARCH"
    if effective_n >= MATURITY_THRESHOLDS["EARLY"]:
        return "EARLY"
    return "INSUFFICIENT"


def _r_bucket(value: Any) -> str:
    current = _finite(value)
    if current is None:
        return "R_UNKNOWN"
    if current < -0.5:
        return "R_LT_-0.5"
    if current < 0.0:
        return "R_-0.5_TO_0"
    if current < 0.5:
        return "R_0_TO_0.5"
    if current < 1.0:
        return "R_0.5_TO_1"
    return "R_GE_1"


def _loads(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _context_labels(row: dict[str, Any]) -> dict[str, str]:
    labels = {
        "instrument": str(row.get("instrument") or "UNKNOWN"),
        "r_bucket": _r_bucket(row.get("current_r")),
        "production_policy": str(row.get("production_policy") or "UNKNOWN"),
    }
    context = _loads(row.get("context_json"))
    if not context:
        return labels

    market = context.get("market_context") or {}
    macro = market.get("macro") or {}
    if isinstance(macro, dict):
        regime = macro.get("regime") or macro.get("state") or macro.get("label")
        if regime is not None:
            labels["macro_regime"] = str(regime)

    cross = market.get("cross_asset") or {}
    if isinstance(cross, dict):
        confirmation = (
            cross.get("confirmation") or cross.get("confirm")
            or cross.get("state") or cross.get("relation")
        )
        if confirmation is not None:
            labels["cross_state"] = str(confirmation)

    derivatives = context.get("option_derivatives") or {}
    attribution = derivatives.get("option_state_attribution") or {}
    if isinstance(attribution, dict):
        positive = attribution.get("positive") or []
        negative = attribution.get("negative") or []
        if positive and not negative:
            labels["frozen_option_attribution"] = "POSITIVE"
        elif negative and not positive:
            labels["frozen_option_attribution"] = "NEGATIVE"
        elif positive or negative:
            labels["frozen_option_attribution"] = "MIXED"

    return labels


def _pairwise_rows(runtime: ManagementLocalRuntime) -> list[dict[str, Any]]:
    """Read policy outcomes once and pivot by immutable local window."""
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute("""
            SELECT w.window_id,w.horizon_minutes,w.trade_id,w.observation_id,
                   w.evidence_eligible,w.origin,g.production_policy,g.current_r,
                   COALESCE(c.instrument,w.instrument) AS instrument,
                   p.policy_name,p.terminal_r,p.regret_r,o.mfe_r,o.mae_r,
                   v2.context_json
            FROM g1m_local_windows w
            JOIN g1m_local_outcomes o USING(window_id)
            JOIN g1m_management_observations g USING(observation_id)
            LEFT JOIN g1m_observation_context c USING(observation_id)
            JOIN g1m_local_policy_outcomes p USING(window_id)
            LEFT JOIN g1m_t0_feature_context_v2 v2 USING(observation_id)
            ORDER BY w.captured_ts,w.window_id,p.policy_name
        """).fetchall()]

    pivot: dict[str, dict[str, Any]] = {}
    for row in rows:
        target = pivot.setdefault(str(row["window_id"]), {
            "window_id": str(row["window_id"]),
            "horizon_minutes": int(row["horizon_minutes"]),
            "trade_id": int(row["trade_id"]),
            "observation_id": str(row["observation_id"]),
            "evidence_eligible": bool(row["evidence_eligible"]),
            "origin": str(row["origin"]),
            "production_policy": str(row["production_policy"]),
            "current_r": row["current_r"],
            "instrument": str(row["instrument"] or "UNKNOWN"),
            "context_json": row["context_json"],
            "mfe_r": _finite(row["mfe_r"]),
            "mae_r": _finite(row["mae_r"]),
            "policies": {},
        })
        target["policies"][str(row["policy_name"])] = {
            "terminal_r": float(row["terminal_r"]),
            "regret_r": float(row["regret_r"]),
        }
    return list(pivot.values())


def _comparison_records(windows: list[dict[str, Any]], left: str, right: str,
                        *, horizon: int, prospective_only: bool) -> list[dict[str, Any]]:
    records = []
    for row in windows:
        if int(row["horizon_minutes"]) != int(horizon):
            continue
        if prospective_only and not row["evidence_eligible"]:
            continue
        policies = row["policies"]

        def policy(name: str) -> dict | None:
            if name == "PRODUCTION_POLICY":
                return policies.get(str(row["production_policy"]))
            return policies.get(name)

        lhs, rhs = policy(left), policy(right)
        if lhs is None or rhs is None:
            continue
        delta = float(lhs["terminal_r"]) - float(rhs["terminal_r"])
        records.append({
            **row,
            "delta_r": delta,
            "regret_reduction_r": float(rhs["regret_r"]) - float(lhs["regret_r"]),
        })
    return records


def _summarize(records: list[dict[str, Any]], *, maturity_from_sample: bool) -> dict:
    by_trade: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_trade[int(row["trade_id"])].append(row)
    trade_delta = [
        sum(float(item["delta_r"]) for item in group) / len(group)
        for group in by_trade.values()
    ]
    trade_regret = [
        sum(float(item["regret_reduction_r"]) for item in group) / len(group)
        for group in by_trade.values()
    ]
    mfe = [float(row["mfe_r"]) for row in records if row.get("mfe_r") is not None]
    mae = [float(row["mae_r"]) for row in records if row.get("mae_r") is not None]
    effective_n = len(by_trade)
    return {
        "raw_n": len(records),
        "unique_trades": effective_n,
        "effective_n": effective_n,
        "dependency_contract": "one_trade_total_weight_one_per_horizon",
        "mean_delta_r": _mean(trade_delta),
        "median_delta_r": median(trade_delta) if trade_delta else None,
        "worst_decile_delta_r": _q(trade_delta, 0.10),
        "mean_regret_reduction_r": _mean(trade_regret),
        "win_rate": (
            sum(value > 1e-12 for value in trade_delta) / len(trade_delta)
            if trade_delta else None
        ),
        "tie_rate": (
            sum(abs(value) <= 1e-12 for value in trade_delta) / len(trade_delta)
            if trade_delta else None
        ),
        "mean_mfe_r": _mean(mfe),
        "mean_mae_r": _mean(mae),
        "maturity": _maturity(effective_n) if maturity_from_sample else "DESCRIPTIVE_ONLY",
    }


def _context_map(records: list[dict[str, Any]]) -> dict[str, list[dict]]:
    """Bounded descriptive WHERE_IT_HELPS/HURTS map; never a promotion gate."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in records[-CONTEXT_SCAN_LIMIT:]:
        for dimension, value in _context_labels(row).items():
            groups[(dimension, value)].append(row)

    summaries = []
    for (dimension, value), group in groups.items():
        if len(group) < CONTEXT_MIN_RAW:
            continue
        summary = _summarize(group, maturity_from_sample=False)
        summaries.append({
            "dimension": dimension,
            "value": value,
            **summary,
            "post_selection_descriptive_only": True,
        })
    helps = sorted(
        (row for row in summaries if (row.get("mean_delta_r") or 0.0) > 0.0),
        key=lambda row: (float(row.get("mean_delta_r") or 0.0), int(row["raw_n"])),
        reverse=True,
    )[:10]
    hurts = sorted(
        (row for row in summaries if (row.get("mean_delta_r") or 0.0) < 0.0),
        key=lambda row: (float(row.get("mean_delta_r") or 0.0), -int(row["raw_n"])),
    )[:10]
    return {"WHERE_IT_HELPS": helps, "WHERE_IT_HURTS": hurts}


def management_edge_v2(self: ManagementLocalRuntime) -> dict:
    windows = _pairwise_rows(self)
    legacy_items = []
    prospective_policy_records: list[dict[str, Any]] = []
    pairwise = []

    for horizon in LOCAL_HORIZONS:
        actual_vs_hold = _comparison_records(
            windows, "PRODUCTION_POLICY", "HOLD", horizon=horizon,
            prospective_only=True)
        prospective_policy_records.extend(actual_vs_hold)
        actual_summary = _summarize(actual_vs_hold, maturity_from_sample=True)
        legacy_items.append({
            "horizon_minutes": horizon,
            "raw_n": actual_summary["raw_n"],
            "unique_trades": actual_summary["unique_trades"],
            "effective_n": actual_summary["effective_n"],
            "mean_mva_vs_hold_r": actual_summary["mean_delta_r"],
            "positive_n": sum(float(row["delta_r"]) > 1e-12 for row in actual_vs_hold),
            "negative_n": sum(float(row["delta_r"]) < -1e-12 for row in actual_vs_hold),
            "maturity": actual_summary["maturity"],
            "edge_claim_allowed": False,
        })

        for left, right in PAIRWISE_COMPARISONS:
            all_rows = _comparison_records(
                windows, left, right, horizon=horizon, prospective_only=False)
            eligible_rows = _comparison_records(
                windows, left, right, horizon=horizon, prospective_only=True)
            pairwise.append({
                "horizon_minutes": horizon,
                "left_action": left,
                "right_action": right,
                "delta_definition": "terminal_r(left)-terminal_r(right)",
                "descriptive_all": _summarize(all_rows, maturity_from_sample=False),
                "prospective": _summarize(eligible_rows, maturity_from_sample=True),
                "production_authority": False,
                "edge_claim_allowed": False,
            })

    contexts = _context_map(prospective_policy_records)
    prospective_effective = max((int(row["effective_n"]) for row in legacy_items), default=0)
    overall_maturity = _maturity(prospective_effective)
    if prospective_effective == 0:
        verdict = "INSUFFICIENT_MANAGEMENT_DATA"
    elif overall_maturity == "INSUFFICIENT":
        verdict = "INSUFFICIENT_MANAGEMENT_DATA"
    else:
        verdict = "DESCRIPTIVE_MANAGEMENT_EVIDENCE_AVAILABLE"

    return {
        "contract_version": EDGE_V2_VERSION,
        "local_contract_version": G1M_LOCAL_CONTRACT_VERSION,
        "semantics": "LOCAL_DECISION_QUALITY_NOT_TERMINAL_MANAGEMENT_EDGE",
        "dataset": {
            "resolved_windows": len(windows),
            "prospective_evidence_windows": sum(bool(row["evidence_eligible"]) for row in windows),
            "descriptive_rows_never_raise_prospective_maturity": True,
            "context_scan_limit": CONTEXT_SCAN_LIMIT,
        },
        "maturity_contract": {
            "thresholds_effective_trades": MATURITY_THRESHOLDS,
            "ROBUST": "REQUIRES_SEPARATE_PURGED_PROSPECTIVE_OOS_VALIDATION",
            "overall": overall_maturity,
        },
        "items": legacy_items,
        "pairwise": pairwise,
        "management_failure_regimes": contexts["WHERE_IT_HURTS"],
        "where_it_helps": contexts["WHERE_IT_HELPS"],
        "where_it_hurts": contexts["WHERE_IT_HURTS"],
        "management_shadow": {
            "activated": False,
            "reason": "REQUIRES_SEPARATELY_REVIEWED_RESEARCH_SIGNAL",
            "auto_activation": False,
        },
        "verdict": verdict,
        "terminal_edge_separate": True,
        "research_only": True,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
        "edge_claim_allowed": False,
    }


def install_g1_management_local_edge_v2() -> None:
    if getattr(ManagementLocalRuntime, "_local_edge_version", None) == EDGE_V2_VERSION:
        return
    ManagementLocalRuntime.edge = management_edge_v2
    ManagementLocalRuntime._local_edge_version = EDGE_V2_VERSION
