"""Repair AI Verdict report semantics without changing policy math.

This guard exists at the presentation/snapshot boundary only. It keeps the
stateful active stop and its R-distance on one canonical geometry source, and it
prevents byte-budget compaction markers from being rendered as fake zero data.
Expected R, CVaR, policy selection, Active Edge and execution authority are not
modified here.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable

_INSTALLED = False


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def repair_snapshot_geometry(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep every displayed R-distance on one live trade geometry source.

    Stateful active-stop price, FINAL TAKE and CURRENT R must agree. If current
    market geometry is unavailable, distances are unavailable too; stale -1R/0R
    fallbacks must never masquerade as live distances.
    """
    geometry = snapshot.get("trade_geometry")
    if not isinstance(geometry, dict):
        return snapshot

    entry = _number(geometry.get("entry"))
    original_stop = _number(geometry.get("original_stop"))
    if entry is None or original_stop is None:
        geometry["r_to_active_stop"] = None
        geometry["r_to_final_take"] = None
        return snapshot

    risk = abs(entry - original_stop)
    if risk <= 0.0:
        geometry["r_to_active_stop"] = None
        geometry["r_to_final_take"] = None
        return snapshot

    if original_stop < entry:
        sign = 1.0
    elif original_stop > entry:
        sign = -1.0
    else:
        geometry["r_to_active_stop"] = None
        geometry["r_to_final_take"] = None
        return snapshot

    current_r = _number(geometry.get("current_r"))
    current_price = _number(geometry.get("current"))
    if current_r is None and current_price is not None:
        current_r = sign * (current_price - entry) / risk
        geometry["current_r"] = round(current_r, 6)

    if current_r is None:
        geometry["r_to_active_stop"] = None
        geometry["r_to_final_take"] = None
        return snapshot

    active_stop = _number(geometry.get("active_risk_barrier"))
    if active_stop is None:
        geometry["r_to_active_stop"] = None
    else:
        active_stop_r = sign * (active_stop - entry) / risk
        geometry["r_to_active_stop"] = round(current_r - active_stop_r, 4)

    final_take = _number(geometry.get("final_take"))
    if final_take is None:
        geometry["r_to_final_take"] = None
    else:
        final_take_r = sign * (final_take - entry) / risk
        geometry["r_to_final_take"] = round(final_take_r - current_r, 4)
    return snapshot


def _compacted(snapshot: dict[str, Any]) -> bool:
    budget = snapshot.get("snapshot_budget") or {}
    return bool(budget.get("report_integrity_degraded")) or (
        str(budget.get("degrade_reason") or "") == "BASE_REPORT_INTEGRITY_BYTE_BUDGET"
    )


def _section_bounds(lines: list[str], title: str) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if line.startswith(title)), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("**"):
            end = i
            break
    return start, end


def _replace_section_body(lines: list[str], title: str, body: list[str]) -> None:
    bounds = _section_bounds(lines, title)
    if bounds is None:
        return
    start, end = bounds
    replacement = list(body)
    if end < len(lines) and (not replacement or replacement[-1] != ""):
        replacement.append("")
    lines[start + 1:end] = replacement


def _repair_source_stability(lines: list[str], snapshot: dict[str, Any]) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    gate = manager.get("gate") or {}
    authority = gate.get("authority_stability") or {}
    checks = _number(authority.get("checks"))
    winner_counts = authority.get("winner_counts") or {}
    rec = manager.get("recommendation") or {}
    selected = str(rec.get("policy") or gate.get("policy") or "HOLD")
    count = _number(winner_counts.get(selected)) if isinstance(winner_counts, dict) else None
    if checks is None or checks <= 0 or count is None:
        return lines
    share = max(0.0, min(1.0, count / checks))
    prefix = f"Устойчивость к источнику данных для {selected}:"
    replacement = f"{prefix} {int(count)}/{int(checks)} ({share * 100:.1f}%)."
    return [replacement if line.startswith(prefix) else line for line in lines]


def repair_report_semantics(text: str, snapshot: dict[str, Any]) -> str:
    """Remove contradictions introduced by compact snapshots/report labels."""
    lines = text.splitlines()
    manager = snapshot.get("policy_manager") or {}
    scenario = manager.get("scenario_geometry") or {}
    scenario_count = _number(scenario.get("scenario_count"))
    has_scenario_geometry = scenario_count is not None and scenario_count > 0
    compacted = _compacted(snapshot)

    common_label = "Base production policy distribution (common execution-MC paths):"
    compact_label = (
        "Base production policy distribution (policy outcomes preserved; "
        "detailed common-path geometry unavailable in compact snapshot):"
    )
    if not has_scenario_geometry:
        lines = [compact_label if line == common_label else line for line in lines]
        _replace_section_body(
            lines,
            "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ**",
            [
                "Детальная scenario-path geometry: UNAVAILABLE в этом snapshot; "
                "нулевые вероятности не подставляются.",
                "Policy Expected/median/CVaR ниже сохранены отдельно и не являются "
                "вероятностью FINAL TAKE vs active STOP/BE.",
            ],
        )

    if compacted:
        repaired: list[str] = []
        for line in lines:
            line = line.replace(
                "Снимок аудита: время не указано. Доступно 0/0 групп.",
                "Детальный input audit: COMPACTED по byte-budget; это не означает отсутствие данных.",
            )
            line = line.replace(
                "Доступно 0/0 групп.",
                "Детальный input audit: COMPACTED по byte-budget; это не означает отсутствие данных.",
            )
            line = line.replace(
                "Input audit: UNAVAILABLE.",
                "Input audit: COMPACTED (детали удалены из snapshot по byte-budget, не исходные данные).",
            )
            if "correlation_regime_shift" in line and "[bounded]" in line:
                line = (
                    "Ограничения: correlation_regime_shift: детальные пары COMPACTED по byte-budget; "
                    "сам regime-shift сохранён как uncertainty/regime gate."
                )
            repaired.append(line)
        lines = repaired

    cleaned: list[str] = []
    for line in lines:
        if line.strip().startswith("[bounded]:"):
            continue
        if "[bounded]" in line:
            line = line.replace("[bounded]", "DETAIL_COMPACTED")
        cleaned.append(line)
    lines = cleaned

    lines = _repair_source_stability(lines, snapshot)

    authority = (snapshot.get("ede_causal_context") or {}).get("authority") or {}
    if authority.get("production_directional_authority") is False:
        lines = [
            line.replace(
                "IV/GEX/skew подтверждают удержание как causal context.",
                "IV/GEX/skew дают supportive causal context для удержания; "
                "это не самостоятельное production-подтверждение.",
            )
            for line in lines
        ]

    decision = manager.get("management_decision") or {}
    if (
        decision.get("authority") == "STRATEGY"
        and decision.get("policy") == "EXIT"
        and decision.get("strategy_terminal_event") == "FINAL_TAKE_REACHED"
    ):
        for index, line in enumerate(lines):
            if line.startswith("**ДЕЙСТВИЕ СЕЙЧАС**"):
                lines[index] = (
                    "**ДЕЙСТВИЕ СЕЙЧАС** — FINAL TAKE ДОСТИГНУТ/ПЕРЕСЕЧЁН: "
                    "ЗАКРЫТЬ ВЕСЬ ТЕКУЩИЙ ОСТАТОК ПО СТРАТЕГИИ. ИСПОЛНЕНИЕ РУЧНОЕ."
                )
            elif line.startswith("Арбитр:"):
                lines[index] = (
                    "Арбитр: STRATEGY → EXIT. Причина: достигнут/пересечён FINAL TAKE; "
                    "терминальное правило стратегии выше AI risk-overlay."
                )
            elif re.search(r"Рабочее действие: .*не менять позицию", line):
                lines[index] = re.sub(
                    r"Рабочее действие: .*?(?=\.$)",
                    "Рабочее действие: закрыть весь текущий остаток по стратегии",
                    line,
                )

    audit_bounds = _section_bounds(lines, "**FULL METRIC AUDIT**")
    clarification = (
        "p_take/p_stop ниже относятся к option-distribution/derivative audit; "
        "это НЕ authoritative execution-MC вероятность FINAL TAKE vs active STOP/BE."
    )
    if audit_bounds is not None and clarification not in lines:
        start, _ = audit_bounds
        lines.insert(start + 1, clarification)

    return "\n".join(lines).strip()


def install_ai_report_semantics_guard() -> None:
    """Patch API snapshot/report references captured before app creation."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import ai_verdict_v19 as v19
    from . import app as app_module

    original_build = app_module.build_snapshot
    original_normalize: Callable[[str, dict[str, Any]], str] = v19.normalize_structured_report

    def guarded_build_snapshot(engine: Any) -> dict[str, Any]:
        snapshot = original_build(engine)
        return repair_snapshot_geometry(snapshot)

    def guarded_normalize(text: str, snapshot: dict[str, Any]) -> str:
        return repair_report_semantics(original_normalize(text, snapshot), snapshot)

    app_module.build_snapshot = guarded_build_snapshot
    v19.normalize_structured_report = guarded_normalize
    _INSTALLED = True
