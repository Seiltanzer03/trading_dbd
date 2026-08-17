"""Repair AI Verdict report semantics without changing policy math.

This guard exists at the presentation/snapshot boundary only. It keeps the
stateful active stop and its R-distance on one canonical geometry source, and it
prevents byte-budget compaction markers from being rendered as fake zero data.
Expected R, CVaR, policy selection, Active Edge and execution authority are not
modified here.
"""
from __future__ import annotations

import math
from typing import Any, Callable

_INSTALLED = False


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def repair_snapshot_geometry(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Make displayed active-stop price and R-distance use the same source.

    `trade_geometry.active_risk_barrier` is the stateful stop/BE price.  Older
    snapshots could combine that price with `first_touch_clock.risk_barrier_r`
    (or its -1R fallback), producing a +1R error after break-even was armed.
    Recompute only the displayed distance from entry/original-risk geometry.
    """
    geometry = snapshot.get("trade_geometry")
    if not isinstance(geometry, dict):
        return snapshot

    current_r = _number(geometry.get("current_r"))
    entry = _number(geometry.get("entry"))
    original_stop = _number(geometry.get("original_stop"))
    active_stop = _number(geometry.get("active_risk_barrier"))
    if None in (current_r, entry, original_stop, active_stop):
        return snapshot

    risk = abs(float(entry) - float(original_stop))
    if risk <= 0.0:
        return snapshot

    # Infer position-space sign from the original stop: long stops are below
    # entry, short stops above entry. The original stop is immutable trade risk.
    if float(original_stop) < float(entry):
        sign = 1.0
    elif float(original_stop) > float(entry):
        sign = -1.0
    else:
        return snapshot

    active_stop_r = sign * (float(active_stop) - float(entry)) / risk
    geometry["r_to_active_stop"] = round(float(current_r) - active_stop_r, 4)
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
    # Preserve one blank separator before the next section.
    replacement = list(body)
    if end < len(lines) and (not replacement or replacement[-1] != ""):
        replacement.append("")
    lines[start + 1:end] = replacement


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
            if "[bounded]" in line:
                if line.strip().startswith("[bounded]:"):
                    continue
                if "correlation_regime_shift" in line:
                    line = (
                        "Ограничения: correlation_regime_shift: детальные пары COMPACTED по byte-budget; "
                        "сам regime-shift сохранён как uncertainty/regime gate."
                    )
                else:
                    line = line.replace("[bounded]", "COMPACTED")
            repaired.append(line)
        lines = repaired

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
    # Existing v19 render/request function objects resolve this module-global at
    # call time, so both deterministic fallback and LLM-normalized output receive
    # exactly the same semantic repair.
    v19.normalize_structured_report = guarded_normalize
    _INSTALLED = True
