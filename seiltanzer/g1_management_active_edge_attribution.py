"""Prospective decision-edge attribution for the active high-risk research edge.

The report asks a concrete management question: when the frozen active edge
supported the open position, did HOLD beat EXIT/reduction afterwards; and when it
opposed the position, did EXIT/reduction beat HOLD? Repeated reviews of one trade
are dependency-weighted so one trade contributes total weight one per local
horizon/group. The report is observational research only and never changes the
production action, score, size, execution or promotion authority.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from statistics import median
from typing import Any

from .g1_management_local_runtime import LOCAL_HORIZONS, ManagementLocalRuntime


ATTRIBUTION_VERSION = "g1m-active-edge-decision-attribution-v1"
MATURITY_THRESHOLDS = {
    "EARLY": 30,
    "RESEARCH": 75,
    "PROVISIONAL": 150,
}
MIN_GROUP_RAW = 3
MAX_GROUP_ROWS = 80
_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        out = json.loads(str(value)) if value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return out if isinstance(out, dict) else {}


def _nonnegative_int(value: Any) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, out)


def _q(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    position = min(1.0, max(0.0, q)) * (len(xs) - 1)
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return xs[lo]
    fraction = position - lo
    return xs[lo] * (1.0 - fraction) + xs[hi] * fraction


def _maturity(effective_n: int) -> str:
    if effective_n >= MATURITY_THRESHOLDS["PROVISIONAL"]:
        return "PROVISIONAL"
    if effective_n >= MATURITY_THRESHOLDS["RESEARCH"]:
        return "RESEARCH"
    if effective_n >= MATURITY_THRESHOLDS["EARLY"]:
        return "EARLY"
    return "INSUFFICIENT"


def _vote_ratio(supporting: int, opposing: int) -> float | None:
    total = int(supporting) + int(opposing)
    return ((int(supporting) - int(opposing)) / total) if total > 0 else None


def _vote_bucket(ratio: float | None) -> str:
    if ratio is None:
        return "NO_DIRECTIONAL_MATCH"
    if ratio <= -0.50:
        return "STRONG_OPPOSE"
    if ratio < 0.0:
        return "WEAK_OPPOSE"
    if ratio == 0.0:
        return "BALANCED"
    if ratio < 0.50:
        return "WEAK_SUPPORT"
    return "STRONG_SUPPORT"


def _aligned_delta(policies: dict[str, float], net_vote: int,
                   hold_alternative: str) -> float | None:
    hold = _finite(policies.get("HOLD"))
    alternative = _finite(policies.get(hold_alternative))
    if hold is None or alternative is None or int(net_vote) == 0:
        return None
    # Positive always means the edge-aligned management choice won afterwards.
    return (hold - alternative) if int(net_vote) > 0 else (alternative - hold)


def _trade_weighted_metric(records: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    by_trade: dict[int, list[float]] = defaultdict(list)
    raw_values = []
    for row in records:
        value = _finite(row.get(metric))
        if value is None:
            continue
        by_trade[int(row["trade_id"])].append(value)
        raw_values.append(value)
    trade_values = [sum(values) / len(values) for values in by_trade.values() if values]
    effective_n = len(trade_values)
    return {
        "raw_n": len(raw_values),
        "unique_trades": effective_n,
        "effective_n": effective_n,
        "dependency_contract": "one_trade_total_weight_one_per_local_horizon_bucket",
        "mean_aligned_delta_r": (
            sum(trade_values) / len(trade_values) if trade_values else None),
        "median_aligned_delta_r": median(trade_values) if trade_values else None,
        "worst_decile_aligned_delta_r": _q(trade_values, 0.10),
        "positive_rate": (
            sum(value > 1e-12 for value in trade_values) / effective_n
            if effective_n else None),
        "negative_rate": (
            sum(value < -1e-12 for value in trade_values) / effective_n
            if effective_n else None),
        "tie_rate": (
            sum(abs(value) <= 1e-12 for value in trade_values) / effective_n
            if effective_n else None),
        "maturity": _maturity(effective_n),
        "effective_trades_to_early": max(0, MATURITY_THRESHOLDS["EARLY"] - effective_n),
        "edge_claim_allowed": False,
    }


def _variant_counts(context: dict[str, Any], variant: str) -> tuple[int, int, int]:
    supporting = _nonnegative_int(context.get("supporting_position_n"))
    opposing = _nonnegative_int(context.get("opposing_position_n"))
    strict_supporting = _nonnegative_int(context.get("strict_supporting_position_n"))
    strict_opposing = _nonnegative_int(context.get("strict_opposing_position_n"))
    if variant == "STRICT_REFERENCE":
        return strict_supporting, strict_opposing, strict_supporting - strict_opposing
    if variant == "HIGH_RISK_ONLY":
        risk_supporting = _nonnegative_int(
            context.get("high_risk_only_supporting_position_n",
                        max(0, supporting - strict_supporting)))
        risk_opposing = _nonnegative_int(
            context.get("high_risk_only_opposing_position_n",
                        max(0, opposing - strict_opposing)))
        return risk_supporting, risk_opposing, risk_supporting - risk_opposing
    return supporting, opposing, supporting - opposing


def _decorate_window(row: dict[str, Any]) -> dict[str, Any]:
    context = _loads(row.get("active_edge_context_json"))
    policies = dict(row.get("policies") or {})
    output = {**row, "active_edge_context": context, "policies": policies}
    for variant in ("ALL_ACTIVE", "STRICT_REFERENCE", "HIGH_RISK_ONLY"):
        supporting, opposing, net_vote = _variant_counts(context, variant)
        prefix = variant.lower()
        ratio = _vote_ratio(supporting, opposing)
        output[f"{prefix}_supporting_n"] = supporting
        output[f"{prefix}_opposing_n"] = opposing
        output[f"{prefix}_net_vote"] = net_vote
        output[f"{prefix}_vote_ratio"] = ratio
        output[f"{prefix}_vote_bucket"] = _vote_bucket(ratio)
        output[f"{prefix}_hold_vs_exit_aligned_r"] = _aligned_delta(
            policies, net_vote, "EXIT")
        output[f"{prefix}_hold_vs_close50_aligned_r"] = _aligned_delta(
            policies, net_vote, "CLOSE_50")
        output[f"{prefix}_hold_vs_close25_aligned_r"] = _aligned_delta(
            policies, net_vote, "CLOSE_25")
    return output


def _window_records(runtime: ManagementLocalRuntime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read resolved prospective windows joined to the immutable active-edge T0."""
    with runtime._lock:
        coverage = runtime._conn.execute("""
            SELECT COUNT(*) AS sidecar_observation_n,
                   COUNT(DISTINCT CASE WHEN available=1 THEN review_id END) AS available_observation_n
            FROM g1m_active_edge_t0
        """).fetchone()
        rows = [dict(row) for row in runtime._conn.execute("""
            SELECT w.window_id,w.horizon_minutes,w.trade_id,w.observation_id,
                   w.evidence_eligible,w.origin,ae.context_json AS active_edge_context_json,
                   p.policy_name,p.terminal_r
            FROM g1m_local_windows w
            JOIN g1m_local_outcomes o USING(window_id)
            JOIN g1m_active_edge_t0 ae USING(observation_id)
            JOIN g1m_local_policy_outcomes p USING(window_id)
            WHERE w.evidence_eligible=1 AND ae.available=1
              AND p.policy_name IN ('HOLD','EXIT','CLOSE_25','CLOSE_50')
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
            "active_edge_context_json": row["active_edge_context_json"],
            "policies": {},
        })
        target["policies"][str(row["policy_name"])] = float(row["terminal_r"])

    windows = [_decorate_window(row) for row in pivot.values()]
    return windows, {
        "sidecar_observation_n": int(coverage["sidecar_observation_n"] or 0),
        "available_sidecar_observation_n": int(coverage["available_observation_n"] or 0),
        "resolved_prospective_window_n": len(windows),
        "resolved_unique_trade_n": len({int(row["trade_id"]) for row in windows}),
    }


def _variant_report(windows: list[dict[str, Any]], variant: str,
                    local_horizon: int) -> dict[str, Any]:
    prefix = variant.lower()
    rows = [row for row in windows
            if int(row["horizon_minutes"]) == int(local_horizon)
            and int(row.get(f"{prefix}_net_vote") or 0) != 0]
    return {
        "variant": variant,
        "local_horizon_minutes": int(local_horizon),
        "directional_window_n": len(rows),
        "mean_abs_vote_ratio": (
            sum(abs(float(row[f"{prefix}_vote_ratio"])) for row in rows
                if row.get(f"{prefix}_vote_ratio") is not None) / len(rows)
            if rows else None),
        "hold_vs_exit": _trade_weighted_metric(
            rows, f"{prefix}_hold_vs_exit_aligned_r"),
        "hold_vs_close50": _trade_weighted_metric(
            rows, f"{prefix}_hold_vs_close50_aligned_r"),
        "hold_vs_close25": _trade_weighted_metric(
            rows, f"{prefix}_hold_vs_close25_aligned_r"),
        "production_authority": False,
    }


def _bucket_reports(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for horizon in LOCAL_HORIZONS:
        horizon_rows = [row for row in windows if int(row["horizon_minutes"]) == int(horizon)]
        for bucket in (
            "STRONG_OPPOSE", "WEAK_OPPOSE", "BALANCED",
            "WEAK_SUPPORT", "STRONG_SUPPORT",
        ):
            rows = [row for row in horizon_rows
                    if row.get("all_active_vote_bucket") == bucket]
            if not rows:
                continue
            output.append({
                "local_horizon_minutes": int(horizon),
                "vote_bucket": bucket,
                "raw_window_n": len(rows),
                "hold_vs_exit": _trade_weighted_metric(
                    rows, "all_active_hold_vs_exit_aligned_r"),
                "hold_vs_close50": _trade_weighted_metric(
                    rows, "all_active_hold_vs_close50_aligned_r"),
                "post_selection_descriptive_only": True,
            })
    return output


def _group_records(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for window in windows:
        context = window.get("active_edge_context") or {}
        groups = context.get("matched_groups") or []
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            net_vote = int(group.get("net_vote") or 0)
            if net_vote == 0:
                continue
            records.append({
                "trade_id": int(window["trade_id"]),
                "observation_id": str(window["observation_id"]),
                "local_horizon_minutes": int(window["horizon_minutes"]),
                "target_id": str(group.get("target_id") or "UNKNOWN"),
                "target_family": str(group.get("target_family") or "OTHER"),
                "signal_horizon_minutes": int(group.get("signal_horizon_minutes") or 0),
                "matched_n": _nonnegative_int(group.get("matched_n")),
                "net_vote": net_vote,
                "vote_ratio": _finite(group.get("net_vote_ratio")),
                "hold_vs_exit_aligned_r": _aligned_delta(
                    window["policies"], net_vote, "EXIT"),
                "hold_vs_close50_aligned_r": _aligned_delta(
                    window["policies"], net_vote, "CLOSE_50"),
            })
    return records


def _group_reports(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in _group_records(windows):
        key = (
            int(row["local_horizon_minutes"]),
            str(row["target_family"]),
            str(row["target_id"]),
            int(row["signal_horizon_minutes"]),
        )
        grouped[key].append(row)
    output = []
    for (local_horizon, family, target, signal_horizon), rows in grouped.items():
        if len(rows) < MIN_GROUP_RAW:
            continue
        output.append({
            "local_horizon_minutes": local_horizon,
            "target_family": family,
            "target_id": target,
            "signal_horizon_minutes": signal_horizon,
            "raw_window_n": len(rows),
            "hold_vs_exit": _trade_weighted_metric(rows, "hold_vs_exit_aligned_r"),
            "hold_vs_close50": _trade_weighted_metric(rows, "hold_vs_close50_aligned_r"),
            "post_selection_descriptive_only": True,
            "edge_claim_allowed": False,
        })
    output.sort(key=lambda row: (
        -int((row["hold_vs_exit"] or {}).get("effective_n") or 0),
        -abs(float((row["hold_vs_exit"] or {}).get("mean_aligned_delta_r") or 0.0)),
        int(row["local_horizon_minutes"]),
        str(row["target_family"]),
    ))
    return output[:MAX_GROUP_ROWS]


def build_active_edge_decision_attribution(
    windows: list[dict[str, Any]], coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = dict(coverage or {})
    variants = []
    for horizon in LOCAL_HORIZONS:
        for variant in ("ALL_ACTIVE", "STRICT_REFERENCE", "HIGH_RISK_ONLY"):
            variants.append(_variant_report(windows, variant, horizon))

    all_active_effective = max(
        (int(row["hold_vs_exit"]["effective_n"])
         for row in variants if row["variant"] == "ALL_ACTIVE"),
        default=0,
    )
    if not windows:
        verdict = "AWAITING_RESOLVED_POST_ACTIVATION_WINDOWS"
    elif all_active_effective < MATURITY_THRESHOLDS["EARLY"]:
        verdict = "INSUFFICIENT_DECISION_EDGE_DATA"
    else:
        verdict = "DESCRIPTIVE_DECISION_EDGE_AVAILABLE_NOT_VALIDATED"

    return {
        "contract_version": ATTRIBUTION_VERSION,
        "question": (
            "When active edge supports the open position, does HOLD outperform "
            "EXIT/reduction; when it opposes, does EXIT/reduction outperform HOLD?"
        ),
        "alignment_metric": (
            "positive R means the management action aligned with the frozen edge "
            "beat its opposite action over the same immutable local path"
        ),
        "coverage": {
            **coverage,
            "attributed_window_n": len(windows),
            "attributed_unique_trade_n": len({int(row["trade_id"]) for row in windows}),
        },
        "maturity_contract": {
            "thresholds_effective_trades": MATURITY_THRESHOLDS,
            "overall_all_active_effective_n": all_active_effective,
            "overall": _maturity(all_active_effective),
            "automatic_weight_fit": False,
            "automatic_weight_activation": False,
        },
        "variants": variants,
        "vote_buckets": _bucket_reports(windows),
        "target_horizon_attribution": _group_reports(windows),
        "strict_vs_high_risk_interpretation": {
            "STRICT_REFERENCE": "old strict-reference subset only",
            "HIGH_RISK_ONLY": "active signals added by the lowered gate after removing strict subset",
            "ALL_ACTIVE": "all matching active structured signals",
        },
        "verdict": verdict,
        "decision_weight_applied": False,
        "research_only": True,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "automatic_execution": False,
        "may_trigger_exit_or_close": False,
        "edge_claim_allowed": False,
    }


def active_edge_decision_attribution(runtime: ManagementLocalRuntime) -> dict[str, Any]:
    try:
        windows, coverage = _window_records(runtime)
    except Exception as exc:
        return {
            "contract_version": ATTRIBUTION_VERSION,
            "verdict": "ATTRIBUTION_DATA_UNAVAILABLE",
            "reason": f"{type(exc).__name__}: {str(exc)[:240]}",
            "decision_weight_applied": False,
            "research_only": True,
            "production_authority": False,
            "auto_promotion": False,
            "automatic_execution": False,
            "edge_claim_allowed": False,
        }
    return build_active_edge_decision_attribution(windows, coverage)


def install_g1_management_active_edge_attribution() -> None:
    global _INSTALLED
    if _INSTALLED or getattr(
            ManagementLocalRuntime, "_active_edge_attribution_version", None
    ) == ATTRIBUTION_VERSION:
        return
    _INSTALLED = True
    original_edge = ManagementLocalRuntime.edge

    def edge(self) -> dict:
        body = original_edge(self)
        body["active_edge_decision_attribution"] = active_edge_decision_attribution(self)
        return body

    ManagementLocalRuntime.active_edge_decision_attribution = active_edge_decision_attribution
    ManagementLocalRuntime.edge = edge
    ManagementLocalRuntime._active_edge_attribution_version = ATTRIBUTION_VERSION
