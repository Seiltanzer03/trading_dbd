"""Stable public facade for the quantitative AI verdict v4."""
from __future__ import annotations

from . import ai_verdict_v4 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

SYSTEM_PROMPT = _impl.SYSTEM_PROMPT.replace(
    "Не меняй числа", "Нельзя менять числа", 1)
_ORIGINAL_RENDER_POLICY_REPORT = _impl.render_policy_report


def _num(value):
    try:
        out = float(value)
        return out if out == out else None
    except (TypeError, ValueError):
        return None


def _r(value) -> str:
    value = _num(value)
    return "—" if value is None else f"{value:+.3f}R"


def _pct(value) -> str:
    value = _num(value)
    return "—" if value is None else f"{value * 100:.1f}%"


def _infer_executable(gate: dict, recommendation: dict) -> bool:
    explicit = gate.get("automatic_execution_allowed")
    if explicit is not None:
        return bool(explicit)
    explicit = recommendation.get("automatic_execution_allowed")
    if explicit is not None:
        return bool(explicit)
    return gate.get("status") in {"confirmed", "downgraded_within_feasible_set"}


def _section_index(lines: list[str], header: str) -> int | None:
    return next((i for i, line in enumerate(lines) if line.startswith(header)), None)


def _replace_section(lines: list[str], header: str, body: list[str]) -> None:
    index = _section_index(lines, header)
    if index is None:
        return
    end = next(
        (i for i in range(index + 1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    lines[index + 1:end] = body + [""]


def _insert_after_prefix(lines: list[str], prefixes: tuple[str, ...], value: str) -> None:
    if value in lines:
        return
    for index, line in enumerate(lines):
        if any(line.startswith(prefix) for prefix in prefixes):
            lines.insert(index + 1, value)
            return


def _append_to_section(lines: list[str], header: str, value: str) -> None:
    if value in lines:
        return
    index = _section_index(lines, header)
    if index is None:
        return
    end = next(
        (i for i in range(index + 1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    insert_at = end
    while insert_at > index + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, value)


def render_policy_report(snapshot: dict) -> str:
    """Render v4 while retaining all established numeric report contracts."""
    report = _ORIGINAL_RENDER_POLICY_REPORT(snapshot)
    manager = snapshot.get("policy_manager") or {}
    recommendation = manager.get("recommendation") or {}
    gate = manager.get("gate") or {}
    evidence = manager.get("evidence") or {}
    geometry = manager.get("scenario_geometry") or {}
    tradeoff = manager.get("risk_tradeoff") or {}
    selected = (
        recommendation.get("policy")
        or gate.get("provisional_policy")
        or gate.get("raw_policy")
        or "—"
    )
    computed_action = (
        recommendation.get("computed_action_ru")
        or recommendation.get("action_ru")
        or selected
    )
    execution_action = (
        recommendation.get("execution_action_ru")
        or computed_action
    )
    executable = _infer_executable(gate, recommendation)
    lines = report.splitlines()

    # Rebuild only the top decision block. The first line is always actionable.
    next_header = next(
        (i for i in range(1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    body = lines[next_header:]
    if executable:
        first = f"**ДЕЙСТВИЕ СЕЙЧАС** — {execution_action}."
        lines = [first, ""] + body
    else:
        first = (
            "**ДЕЙСТВИЕ СЕЙЧАС** — НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ; "
            "ПРОДОЛЖАТЬ ТЕКУЩЕЕ СОПРОВОЖДЕНИЕ. "
            "НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ. "
            f"Расчётное действие: {computed_action}; оно не подтверждено."
        )
        reasons = gate.get("reasons") or ["gate не подтвердил изменение позиции"]
        lines = [first, "Причины запрета: " + "; ".join(reasons) + ".", ""] + body

    # Backward-compatible geometry for stored/fixture snapshots made before v4.
    legacy_hour = (geometry.get("no_event_empirical") or {}).get("60m") or {}
    if legacy_hour and not geometry.get("no_event_windows"):
        n = int(_num(geometry.get("scenario_count")) or legacy_hour.get("scenarios") or 0)
        geometry_body = [
            f"Один набор из {n or '—'} путей для всех политик. "
            f"P рубежа раньше стопа {_pct(geometry.get('p_next_rung_before_stop'))}; "
            f"P стопа раньше рубежа {_pct(geometry.get('p_stop_before_next_rung'))}.",
            f"За 60 минут событие произошло в {legacy_hour.get('events', 0)} из "
            f"{legacy_hour.get('scenarios', n)} сценариев; оценка NO-EVENT "
            f"{legacy_hour.get('display', '—')}.",
        ]
        event_minutes = _num(geometry.get("expected_event_minutes"))
        geometry_body.append(
            "Ожидаемое время до ближайшего рубежа или стопа: "
            + (f"{event_minutes:.1f} мин." if event_minutes is not None else "не определено.")
        )
        _replace_section(lines, "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ**", geometry_body)

    # Preserve the established name for old snapshots; v4 uses a directional label.
    old_delta = _num(tradeoff.get("expected_cost_vs_hold_r"))
    if tradeoff.get("expected_delta_vs_hold_r") is None and old_delta is not None:
        legacy_tradeoff = (
            f"Цена защиты относительно HOLD: {_r(old_delta)}; "
            f"улучшение CVaR10: {_r(tradeoff.get('cvar_improvement_vs_hold_r'))}."
        )
        _append_to_section(lines, "**ПОЧЕМУ ВЫБРАНО**", legacy_tradeoff)

    authority = gate.get("authority_stability") or {}
    if authority and not any(
        line.startswith("Устойчивость к источнику данных") for line in lines
    ):
        count = (authority.get("winner_counts") or {}).get(selected, 0)
        checks = authority.get("checks", 0)
        share = gate.get("source_stability_share")
        value = (
            f"Устойчивость к источнику данных для {selected}: {count}/{checks} "
            f"({_pct(share)}); проверки: {authority.get('description', '—')}."
        )
        _insert_after_prefix(
            lines,
            ("Устойчивость сырого", "Параметрическая устойчивость сырого"),
            value,
        )

    if not any(line.startswith("Независимые семьи подтверждений:") for line in lines):
        independence = evidence.get("confirmation_independence") or {}
        families = evidence.get("adverse_confirmation_families") or []
        family_text = ", ".join(families) if families else "нет"
        value = (
            f"Независимые семьи подтверждений: "
            f"{independence.get('adverse_families', len(families))} — {family_text}. "
            f"Отдельных строк метрик: {independence.get('adverse_items', 0)}; "
            "смешанные семьи не дают голоса против удержания."
        )
        _insert_after_prefix(
            lines,
            ("Против удержания:", "Однонаправленные семьи против удержания:"),
            value,
        )

    if not executable:
        _replace_section(
            lines,
            "**ПОСЛЕ ИСПОЛНЕНИЯ**",
            [
                "Исполнение не подтверждено. Никакого нового исполнения: "
                "сохранить действующие стоп, БУ/trailing и лестницу частичных фиксаций."
            ],
        )
    elif selected == "EXIT":
        _replace_section(
            lines,
            "**ПОСЛЕ ИСПОЛНЕНИЯ**",
            [
                "Позиция закрыта полностью; остаток, стоп, БУ/trailing и "
                "следующий рубеж не применяются."
            ],
        )

    lines = [
        line.replace("Надёжность:", "Надёжность расчёта:")
        for line in lines
    ]
    return "\n".join(lines).strip()


# request_verdict resolves these globals in the lower compatibility modules.
for _module in (_impl, _impl._impl, _impl._impl._base):
    _module.SYSTEM_PROMPT = SYSTEM_PROMPT
    _module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
