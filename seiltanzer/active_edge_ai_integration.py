"""Attach active high-risk research edge to AI Verdict snapshots.

This is decision-support context for manual trading. Reports are materialized
off-host; request time performs bounded JSON reads and current-T0 rule matching.
The deterministic execution authority is not replaced.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .ai_verdict_budget_bridge import enforce_public_snapshot_budget
from .runtime_git_identity import runtime_git_sha


CONTRACT_VERSION = "ai-active-high-risk-edge-context-v1"
POLICY_VERSION = "g1s-manual-trader-high-risk-edge-policy-v1"
PUBLICATION_CONTRACT_VERSION = "active-edge-exact-sha-publication-v1"
MAX_REPORT_AGE_SEC = 8 * 60 * 60
MAX_SIGNALS = 8
MAX_MATCHED_GROUPS = 64
_INSTALLED = False

LEGACY_TO_CANONICAL_FEATURE_ID = {
    "asset": "regime.asset",
    "asset_family": "regime.asset_family",
    "session_utc": "regime.session_utc",
    "rv15_over_rv60": "vol.rv15_over_rv60",
    "trend_efficiency_60": "price.trend_efficiency_60",
    "cross_confirmation": "cross.confirmation",
    "family_breadth": "cross.family_breadth",
    "market_breadth": "cross.market_breadth",
    "range_60": "price.range_60",
    "cross_correlation": "cross.correlation",
    "cross_correlation_change": "cross.correlation_change",
}


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _research_dir(engine: Any) -> Path:
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research"


def _load_report(
    path: Path,
    snapshot_ts: float,
    expected_sha: str | None,
) -> dict[str, Any] | None:
    """Load only a fresh report published for this exact code generation."""
    expected = str(expected_sha or "").strip().lower()
    if len(expected) != 40:
        return None
    try:
        stat = path.stat()
        if stat.st_size <= 0 or stat.st_size > 8_000_000:
            return None
        if stat.st_mtime > snapshot_ts + 1.0:
            return None
        age = snapshot_ts - stat.st_mtime
        if age < -1.0 or age > MAX_REPORT_AGE_SEC:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if payload.get("edge_policy") != POLICY_VERSION:
            return None
        if payload.get("production_authority") is not False:
            return None
        if payload.get("publication_contract_version") != PUBLICATION_CONTRACT_VERSION:
            return None
        published_for = str(payload.get("published_for_sha") or "").strip().lower()
        if published_for != expected:
            return None
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _structured_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for horizon in report.get("horizons") or []:
        for target in horizon.get("targets") or []:
            for candidate in target.get("candidates") or []:
                if candidate.get("status") == "DISCOVERY_SIGNAL":
                    output.append(candidate)
    return output


def _ml_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [candidate for candidate in report.get("candidates") or []
            if candidate.get("status") == "ML_DISCOVERY_SIGNAL"]


def _current_values(engine: Any, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        from .edge_discovery.ai_context import (
            _latest_frozen_context,
            canonical_current_feature_map,
        )
        instrument = str((snapshot.get("strategy") or {}).get("instrument") or "")
        if not instrument:
            return {}
        return canonical_current_feature_map(
            _latest_frozen_context(engine, snapshot), instrument)
    except Exception:
        return {}


def _values_with_legacy_aliases(
    values: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    output = dict(values)
    for legacy, canonical in LEGACY_TO_CANONICAL_FEATURE_ID.items():
        row = values.get(canonical)
        if row is not None:
            output[legacy] = row
    breadth = values.get("cross.family_breadth") or {}
    if breadth.get("available"):
        numeric = _finite(breadth.get("value"))
        if numeric is not None:
            state = "POSITIVE" if numeric >= 0.60 else "NEGATIVE" if numeric <= 0.40 else "MIXED"
            output["family_breadth_state"] = {
                **breadth,
                "value": state,
                "available": True,
                "feature_id": "family_breadth_state",
            }
    return output


def _conditions_match(values: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> bool:
    conditions = candidate.get("conditions") or []
    if not conditions:
        return False
    try:
        from .edge_discovery.ai_context import _condition_matches_values
        aliased = _values_with_legacy_aliases(values)
        return all(_condition_matches_values(aliased, item) for item in conditions)
    except Exception:
        return False


def _bias(candidate: dict[str, Any]) -> str:
    shift = candidate.get("prediction_shift") or {}
    interpretation = str(shift.get("interpretation") or "")
    if interpretation in {
        "MORE_UP", "MORE_UPSIDE_RETURN", "MORE_UPSIDE_EXCURSION",
        "LESS_DOWNSIDE_EXCURSION",
    }:
        return "BULLISH"
    if interpretation in {
        "MORE_DOWN", "MORE_DOWNSIDE_RETURN", "LESS_UPSIDE_EXCURSION",
        "MORE_DOWNSIDE_EXCURSION",
    }:
        return "BEARISH"
    if shift.get("kind") == "MULTICLASS_PROBABILITY_SHIFT":
        strongest = str(shift.get("strongest_class") or "")
        value = _finite(shift.get("strongest_shift")) or 0.0
        if value > 0 and strongest == "UP_FIRST":
            return "BULLISH"
        if value > 0 and strongest == "DOWN_FIRST":
            return "BEARISH"
    return "NON_DIRECTIONAL"


def _relation(direction: str, bias: str) -> str:
    direction = direction.lower()
    if bias not in {"BULLISH", "BEARISH"}:
        return "NON_DIRECTIONAL"
    is_long = direction in {"long", "buy"}
    is_short = direction in {"short", "sell"}
    if not (is_long or is_short):
        return "UNKNOWN"
    supports = (is_long and bias == "BULLISH") or (is_short and bias == "BEARISH")
    return "SUPPORTS_POSITION" if supports else "OPPOSES_POSITION"


def _target_family(target_id: Any) -> str:
    target = str(target_id or "UNKNOWN").upper()
    if "FIRST_TOUCH" in target:
        return "PATH_FIRST_TOUCH"
    if "EXCURSION" in target or "MFE" in target or "MAE" in target:
        return "PATH_EXCURSION"
    if "VOL" in target or "IV" in target or "RV" in target:
        return "VOLATILITY"
    if "RETURN" in target:
        return "RETURN"
    if "DIRECTION" in target or target in {"UP", "DOWN"}:
        return "DIRECTION"
    return "OTHER"


def _vote_ratio(supporting: int, opposing: int) -> float | None:
    total = int(supporting) + int(opposing)
    return ((int(supporting) - int(opposing)) / total) if total > 0 else None


def _matched_groups(matched_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], dict[str, int | str]] = defaultdict(lambda: {
        "matched_n": 0,
        "supporting_n": 0,
        "opposing_n": 0,
        "strict_matched_n": 0,
        "strict_supporting_n": 0,
        "strict_opposing_n": 0,
    })
    for row in matched_rows:
        target = str(row.get("target_id") or "UNKNOWN")
        horizon = int(row.get("horizon_minutes") or 0)
        bucket = grouped[(target, horizon)]
        bucket["matched_n"] = int(bucket["matched_n"]) + 1
        relation = str(row.get("position_relation") or "")
        strict = bool(row.get("strict_reference_qualified"))
        if relation == "SUPPORTS_POSITION":
            bucket["supporting_n"] = int(bucket["supporting_n"]) + 1
        elif relation == "OPPOSES_POSITION":
            bucket["opposing_n"] = int(bucket["opposing_n"]) + 1
        if strict:
            bucket["strict_matched_n"] = int(bucket["strict_matched_n"]) + 1
            if relation == "SUPPORTS_POSITION":
                bucket["strict_supporting_n"] = int(bucket["strict_supporting_n"]) + 1
            elif relation == "OPPOSES_POSITION":
                bucket["strict_opposing_n"] = int(bucket["strict_opposing_n"]) + 1

    output = []
    for (target, horizon), counts in grouped.items():
        supporting = int(counts["supporting_n"])
        opposing = int(counts["opposing_n"])
        strict_supporting = int(counts["strict_supporting_n"])
        strict_opposing = int(counts["strict_opposing_n"])
        output.append({
            "target_id": target,
            "target_family": _target_family(target),
            "signal_horizon_minutes": horizon,
            "matched_n": int(counts["matched_n"]),
            "supporting_n": supporting,
            "opposing_n": opposing,
            "net_vote": supporting - opposing,
            "net_vote_ratio": _vote_ratio(supporting, opposing),
            "strict_matched_n": int(counts["strict_matched_n"]),
            "strict_supporting_n": strict_supporting,
            "strict_opposing_n": strict_opposing,
            "strict_net_vote": strict_supporting - strict_opposing,
            "strict_net_vote_ratio": _vote_ratio(strict_supporting, strict_opposing),
        })
    output.sort(key=lambda row: (
        -int(row["matched_n"]),
        str(row["target_family"]),
        int(row["signal_horizon_minutes"]),
        str(row["target_id"]),
    ))
    return output[:MAX_MATCHED_GROUPS]


def build_active_edge_context(engine: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot_ts = _finite(snapshot.get("captured_ts")) or 0.0
    root = _research_dir(engine)
    expected_sha = runtime_git_sha()
    structured_reports = [report for horizon in (15, 30, 60, 120, 240)
                          if (report := _load_report(
                              root / f"active_structured_{horizon}m_latest.json",
                              snapshot_ts,
                              expected_sha))]
    ml_report = _load_report(
        root / "active_ml_latest.json", snapshot_ts, expected_sha)
    values = _current_values(engine, snapshot)
    direction = str((snapshot.get("strategy") or {}).get("direction") or "")
    rows: list[dict[str, Any]] = []

    for report in structured_reports:
        for candidate in _structured_candidates(report):
            matched = _conditions_match(values, candidate)
            bias = _bias(candidate)
            rows.append({
                "candidate_id": candidate.get("candidate_id"),
                "source": "STRUCTURED",
                "target_id": candidate.get("target_id"),
                "horizon_minutes": candidate.get("horizon_minutes"),
                "primary_improvement": candidate.get("primary_improvement"),
                "q_value_diagnostic": candidate.get("q_value"),
                "fold_positive": candidate.get("fold_positive"),
                "strict_reference_qualified": bool(candidate.get("strict_reference_qualified")),
                "conditions_match_current_t0": matched,
                "prediction_shift": candidate.get("prediction_shift"),
                "market_bias": bias,
                "position_relation": _relation(direction, bias) if matched else "NOT_APPLICABLE",
            })
    if ml_report:
        for candidate in _ml_candidates(ml_report):
            rows.append({
                "candidate_id": candidate.get("candidate_id"),
                "source": "ML",
                "target_id": candidate.get("target_id"),
                "horizon_minutes": candidate.get("horizon_minutes"),
                "primary_improvement": candidate.get("primary_improvement"),
                "q_value_diagnostic": candidate.get("q_value"),
                "fold_positive": candidate.get("fold_positive"),
                "strict_reference_qualified": bool(candidate.get("strict_reference_qualified")),
                "conditions_match_current_t0": None,
                "market_bias": "NON_DIRECTIONAL_MODEL_CONFIRMATION",
                "position_relation": "CONTEXT_ONLY",
            })

    # Every active candidate participates in aggregate context. Only the most
    # relevant eight rows are serialized for explanation so the fixed snapshot
    # budget never turns into an accidental information-selection gate.
    all_rows = list(rows)
    matched_rows = [item for item in all_rows
                    if item.get("conditions_match_current_t0") is True]
    supporting = sum(item.get("position_relation") == "SUPPORTS_POSITION"
                     for item in matched_rows)
    opposing = sum(item.get("position_relation") == "OPPOSES_POSITION"
                   for item in matched_rows)
    matched = len(matched_rows)
    structured_n = sum(item.get("source") == "STRUCTURED" for item in all_rows)
    ml_n = sum(item.get("source") == "ML" for item in all_rows)
    strict_n = sum(bool(item.get("strict_reference_qualified")) for item in all_rows)
    strict_rows = [item for item in matched_rows if item.get("strict_reference_qualified")]
    matched_strict_n = len(strict_rows)
    strict_supporting = sum(item.get("position_relation") == "SUPPORTS_POSITION"
                            for item in strict_rows)
    strict_opposing = sum(item.get("position_relation") == "OPPOSES_POSITION"
                          for item in strict_rows)
    groups = _matched_groups(matched_rows)

    rows.sort(key=lambda item: (
        item.get("conditions_match_current_t0") is not True,
        -float(_finite(item.get("primary_improvement")) or 0.0),
        int(item.get("horizon_minutes") or 0),
        str(item.get("candidate_id") or ""),
    ))
    rows = rows[:MAX_SIGNALS]
    return {
        "contract_version": CONTRACT_VERSION,
        "edge_policy": POLICY_VERSION,
        "available": bool(all_rows),
        "risk_acceptance": "HIGH_FALSE_DISCOVERY_TOLERANCE",
        "strict_reference_is_blocking": False,
        "aggregate_scope": "ALL_ACTIVE_CANDIDATES_WITH_ALL_MATCHED_STRUCTURED_VOTES",
        "exact_sha_reports_only": True,
        "runtime_sha": expected_sha,
        "total_active_signal_n": len(all_rows),
        "structured_signal_n": structured_n,
        "ml_signal_n": ml_n,
        "strict_reference_signal_n": strict_n,
        "matched_strict_reference_signal_n": matched_strict_n,
        "matched_structured_signal_n": matched,
        "supporting_position_n": supporting,
        "opposing_position_n": opposing,
        "net_position_vote": supporting - opposing,
        "net_position_vote_ratio": _vote_ratio(supporting, opposing),
        "strict_supporting_position_n": strict_supporting,
        "strict_opposing_position_n": strict_opposing,
        "strict_net_position_vote": strict_supporting - strict_opposing,
        "strict_net_position_vote_ratio": _vote_ratio(strict_supporting, strict_opposing),
        "matched_groups": groups,
        "matched_group_n": len(groups),
        "serialized_signal_n": len(rows),
        "details_truncated": len(all_rows) > len(rows),
        "signals": rows,
        "role": "AI_VERDICT_AND_MANUAL_POSITION_CONTEXT",
        "automatic_execution": False,
        "auto_promotion": False,
    }


def install_active_edge_ai_integration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    from . import ai_verdict

    original = ai_verdict.build_snapshot

    def build_snapshot(engine):
        snapshot = original(engine)
        context = build_active_edge_context(engine, snapshot)
        ede = snapshot.get("ede_causal_context")
        if not isinstance(ede, dict):
            ede = {}
            snapshot["ede_causal_context"] = ede
        ede["active_high_risk"] = context
        lines = ede.get("context_lines_ru")
        if isinstance(lines, list):
            if context["matched_structured_signal_n"]:
                lines.append(
                    "Активный рискованный edge: совпало "
                    f"{context['matched_structured_signal_n']} ранних OOS-сигналов; "
                    f"за позицию {context['supporting_position_n']}, против "
                    f"{context['opposing_position_n']}. Строгий FDR не блокирует."
                )
            elif context["available"]:
                lines.append(
                    "Есть активные ранние edge-кандидаты, но их structured-условия "
                    "с текущим T0 не совпали; ML остаётся дополнительным контекстом."
                )
        manager = snapshot.get("policy_manager")
        if isinstance(manager, dict):
            evidence = manager.setdefault("evidence", {})
            if isinstance(evidence, dict):
                evidence["active_high_risk_edge"] = {
                    key: context[key] for key in (
                        "edge_policy", "available", "risk_acceptance", "aggregate_scope",
                        "exact_sha_reports_only", "runtime_sha",
                        "total_active_signal_n", "structured_signal_n", "ml_signal_n",
                        "strict_reference_signal_n", "matched_strict_reference_signal_n",
                        "matched_structured_signal_n", "supporting_position_n",
                        "opposing_position_n", "net_position_vote", "net_position_vote_ratio",
                        "strict_supporting_position_n", "strict_opposing_position_n",
                        "strict_net_position_vote", "strict_net_position_vote_ratio",
                        "matched_group_n", "serialized_signal_n", "details_truncated",
                    )
                }
        enforce_public_snapshot_budget(snapshot)
        return snapshot

    ai_verdict.build_snapshot = build_snapshot