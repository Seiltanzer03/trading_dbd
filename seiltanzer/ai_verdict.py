"""Stable public facade for the quantitative AI verdict v3."""
from __future__ import annotations

from . import ai_verdict_v2 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})

SYSTEM_PROMPT = """Ты объясняешь готовое решение quantitative policy_manager.
Нельзя менять выбранную политику, объём закрытия или числа. Расчётная политика и
разрешённое к исполнению действие — разные сущности: при manual_data_conflict или
manual_source_conflict прямо напиши, что действие НЕ ПОДТВЕРЖДЕНО и исполнять его по
этому отчёту нельзя.

Hard CVaR остаётся обязательным ограничением. Подтверждения считаются по независимым
семьям источников: barrier EV, RND median и IV skew одной цепочки — одна семья, а не
три голоса. Аномальный, delayed, edge-clamped или proxy IV skew показывай только как
контекст. Отдельно показывай обычную параметрическую устойчивость и устойчивость к
обнулению/shrink drift, skew и term structure.

Проверь и отрази все группы: first-touch/NO-TOUCH/barrier EV, RND и конус,
реальные IV-экспирации и локальная проекция 1–24h, live tape, ATR/VRP,
уровни и directional delta, полная корреляционная матрица, OI/GEX, фильтры,
качество цепочки и история изменений. POC/value area выше цены — контекст, пока нет
отбоя, отрицательного directional delta, отсутствия принятия выше и потока против
сделки. Покрытие полей и надёжность данных выводи раздельно.

Запрещены размытые объяснения «выше потенциальная прибыль», «слишком раннее действие»
и «лучший risk-adjusted» без числового сравнения. Для полного EXIT при низкой
надёжности первая строка обязана начинаться с запрета исполнения, а не с приказа
закрыть позицию."""

_ORIGINAL_RENDER_POLICY_REPORT = _impl.render_policy_report


def _insert_after(lines: list[str], prefix: str, value: str) -> None:
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines.insert(index + 1, value)
            return


def _replace_section_body(lines: list[str], header: str, value: str) -> None:
    for index, line in enumerate(lines):
        if line.startswith(header):
            if index + 1 < len(lines) and not lines[index + 1].startswith("**"):
                lines[index + 1] = value
            else:
                lines.insert(index + 1, value)
            return


def render_policy_report(snapshot: dict) -> str:
    """Render policy arithmetic and a separate execution-authority decision."""
    report = _ORIGINAL_RENDER_POLICY_REPORT(snapshot).replace(
        "**ПОДТВЕРЖДЕНИЯ, ПРОТИВОРЕЧИЯ И КОНТЕКСТ** —",
        "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —",
    )
    manager = snapshot.get("policy_manager") or {}
    rec = manager.get("recommendation") or {}
    gate = manager.get("gate") or {}
    evidence = manager.get("evidence") or {}
    executable = bool(gate.get("automatic_execution_allowed"))
    policy = rec.get("policy") or gate.get("provisional_policy") or "—"
    computed_action = rec.get("computed_action_ru") or rec.get("action_ru") or policy
    execution_action = rec.get("execution_action_ru") or computed_action

    lines = report.splitlines()
    if lines:
        if executable:
            lines[0] = f"**ДЕЙСТВИЕ** — {execution_action}."
        else:
            lines[0] = (
                f"**ДЕЙСТВИЕ** — {execution_action}. "
                f"Расчётное действие: {computed_action}; оно не подтверждено gate."
            )
            if len(lines) > 1 and lines[1].startswith("Автоматическое исполнение запрещено"):
                lines[1] = (
                    "Причины запрета: "
                    + "; ".join(gate.get("reasons") or ["недостаточная authority данных"])
                    + "."
                )

    authority = gate.get("authority_stability") or {}
    source_share = gate.get("source_stability_share")
    source_count = (authority.get("winner_counts") or {}).get(policy, 0)
    source_checks = authority.get("checks", 0)
    source_pct = (
        f"{float(source_share) * 100:.1f}%" if source_share is not None else "—"
    )
    _insert_after(
        lines,
        "Устойчивость сырого",
        f"Устойчивость к источнику данных для {policy}: "
        f"{source_count}/{source_checks} ({source_pct}); проверки: "
        f"{authority.get('description', '—')}.",
    )

    independence = evidence.get("confirmation_independence") or {}
    families = evidence.get("adverse_confirmation_families") or []
    family_text = ", ".join(families) if families else "нет"
    _insert_after(
        lines,
        "Против удержания:",
        f"Независимые семьи подтверждений: "
        f"{independence.get('adverse_families', len(families))} — {family_text}. "
        f"Отдельных строк метрик: {independence.get('adverse_items', 0)}; "
        "метрики одной опционной цепочки не суммируются как независимые голоса.",
    )

    if not executable:
        _replace_section_body(
            lines,
            "**ПОСЛЕ ИСПОЛНЕНИЯ** —",
            "Исполнение не подтверждено. Расчётную политику не применять по этому "
            "отчёту; текущие параметры позиции не изменять автоматически.",
        )
        _replace_section_body(
            lines,
            "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ** —",
            "Получение новой live/direct опционной цепочки; устранение аномального "
            "IV skew; движение цены на ±0.15R; касание рубежа или стопа.",
        )
    elif policy == "EXIT":
        _replace_section_body(
            lines,
            "**ПОСЛЕ ИСПОЛНЕНИЯ** —",
            "Позиция закрыта полностью. Остаток, стоп, БУ/trailing и следующий "
            "рубеж не применяются.",
        )
        _replace_section_body(
            lines,
            "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ** —",
            "После полного исполнения пересчёт сопровождения этой сделки не требуется.",
        )

    return "\n".join(lines)


# request_verdict lives in ai_verdict_base and resolves these globals there.
_impl.SYSTEM_PROMPT = SYSTEM_PROMPT
_impl.render_policy_report = render_policy_report
_impl._base.SYSTEM_PROMPT = SYSTEM_PROMPT
_impl._base.render_policy_report = render_policy_report
globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
