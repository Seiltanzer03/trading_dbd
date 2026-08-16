"""Attach the active high-risk research edge to AI Verdict snapshots.

This is decision-support context for the user's manual trading workflow. It does
not execute orders or replace the deterministic management authority. Reports are
small files materialized off-host; request time performs only bounded JSON reads
and current-T0 rule matching.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "ai-active-high-risk-edge-context-v1"
POLICY_VERSION = "g1s-manual-trader-high-risk-edge-policy-v1"
MAX_REPORT_AGE_SEC = 8 * 60 * 60
MAX_SIGNALS = 12
_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _research_dir(engine: Any) -> Path:
    data_dir = Path(getattr(getattr(engine, "settings", None), "data_dir", "."))
    return data_dir / "research"


def _load_report(path: Path, snapshot_ts: float) -> dict[str, Any] | None:
    try:
        stat = path.stat()
        if stat.st_size <= 0 or stat.st_size > 8_000_000:
            return None
        # File publication time is the earliest instant at which this report may
        # influence a live snapshot. A later snapshot may use it; an older one may not.
        if stat.st_mtime > snapshot_ts + 1.0:
            return None
        age = snapshot_ts-stat.st_mtime
        if age < -1.0 or age > MAX_REPORT_AGE_SEC:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if payload.get("edge_policy") != POLICY_VERSION:
            return None
        if payload.get("production_authority") is not False:
            return None
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _structured_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for horizon in report.get("horizons") or []:
        for target in horizon.get("targets") or []:
            for candidate in target.get("candidates") or []:
                if candidate.get("status") != "DISCOVERY_SIGNAL":
                    continue
                row = dict(candidate)
                row["source"] = "STRUCTURED"
                output.append(row)
    return output


def _ml_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**candidate, "source": "ML"}
        for candidate in report.get("candidates") or []
        if candidate.get("status") == "ML_DISCOVERY_SIGNAL"
    ]


def _current_values(engine: Any, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        from .edge_discovery.ai_context import (
            _latest_frozen_context,
            canonical_current_feature_map,
        )
        instrument = str((snapshot.get("strategy") or {}).get("instrument") or "")
        if not instrument:
            return {}
        frozen = _latest_frozen_context(engine, snapshot)
        return canonical_current_feature_map(frozen, instrument)
    except Exception:
        return {}


def _conditions_match(values: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> bool:
    conditions = candidate.get("conditions") or []
    if not conditions:
        return False
    try:
        from .edge_discovery.ai_context import _condition_matches_values
        return all(_condition_matches_values(values, item) for item in conditions)
    except Exception:
        return False


def _bias(candidate: dict[str, Any]) -> str:
    shift = candidate.get("prediction_shift") or {}
    interpretation = str(shift.get("interpretation") or "")
    bullish = {
        "MORE_UP", "MORE_UPSIDE_RETURN", "MORE_UPSIDE_EXCURSION",
        "LESS_DOWNSIDE_EXCURSION",
    }
    bearish = {
        "MORE_DOWN", "MORE_DOWNSIDE_RETURN", "LESS_UPSIDE_EXCURSION",
        "MORE_DOWNSIDE_EXCURSION",
    }
    if interpretation in bullish:
        return "BULLISH"
    if interpretation in bearish:
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


def build_active_edge_context(engine: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot_ts = _finite(snapshot.get("captured_ts")) or 0.0
    root = _research_dir(engine)
    structured_reports = []
    for horizon in (15, 30, 60, 120, 240):
        report = _load_report(root / f"active_structured_{horizon}m_latest.json", snapshot_ts)
        if report:
            structured_reports.append(report)
    ml_report = _load_report(root / "active_ml_latest.json", snapshot_ts)

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
                "risk_acceptance": "HIGH_FALSE_DISCOVERY_TOLERANCE",
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
                "risk_acceptance": "HIGH_FALSE_DISCOVERY_TOLERANCE",
            })

    rows.sort(key=lambda item: (
        item.get("conditions_match_current_t0") is not True,
        -float(_finite(item.get("primary_improvement")) or 0.0),
        int(item.get("horizon_minutes") or 0),
        str(item.get("candidate_id") or ""),
    ))
    rows = rows[:MAX_SIGNALS]
    supporting = sum(item.get("position_relation") == "SUPPORTS_POSITION" for item in rows)
    opposing = sum(item.get("position_relation") == "OPPOSES_POSITION" for item in rows)
    matched = sum(item.get("conditions_match_current_t0") is True for item in rows)
    return {
        "contract_version": CONTRACT_VERSION,
        "edge_policy": POLICY_VERSION,
        "available": bool(rows),
        "risk_acceptance": "HIGH_FALSE_DISCOVERY_TOLERANCE",
        "strict_reference_is_blocking": False,
        "matched_structured_signal_n": matched,
        "supporting_position_n": supporting,
        "opposing_position_n": opposing,
        "net_position_vote": supporting-opposing,
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
        snapshot["active_high_risk_edge_context"] = context
        ede = snapshot.get("ede_causal_context")
        if isinstance(ede, dict):
            ede["active_high_risk"] = context
            lines = ede.get("context_lines_ru")
            if isinstance(lines, list):
                if context["matched_structured_signal_n"]:
                    lines.append(
                        "Активный рискованный edge: совпало "
                        f"{context['matched_structured_signal_n']} ранних OOS-сигналов; "
                        f"за позицию {context['supporting_position_n']}, против "
                        f"{context['opposing_position_n']}. Строгий FDR здесь не блокирует."
                    )
                elif context["available"]:
                    lines.append(
                        "Есть активные ранние edge-кандидаты, но их structured-условия "
                        "с текущим T0 не совпали; ML используется только как контекст."
                    )
        manager = snapshot.get("policy_manager")
        if isinstance(manager, dict):
            evidence = manager.setdefault("evidence", {})
            if isinstance(evidence, dict):
                evidence["active_high_risk_edge"] = context
        return snapshot

    ai_verdict.build_snapshot = build_snapshot
