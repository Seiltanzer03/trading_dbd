"""Stable public facade for the quantitative AI verdict v4."""
from __future__ import annotations

from . import ai_verdict_v4 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

SYSTEM_PROMPT = (
    _impl.SYSTEM_PROMPT.replace("Не меняй числа", "Нельзя менять числа", 1)
    + "\nОбязательно проверь: локальная проекция 1–24h; полная корреляционная "
      "матрица; раздел РАСЧЁТ ПОЛИТИК. Запрещена размытая формулировка "
      "«слишком раннее действие» без числового обоснования."
)
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


def _is_executable(gate: dict, rec: dict) -> bool:
    for source in (gate, rec):
        value = source.get("automatic_execution_allowed")
        if value is not None:
            return bool(value)
    return gate.get("status") in {"confirmed", "downgraded_within_feasible_set"}


def _section(lines: list[str], header: str) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if line.startswith(header)), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    return start, end


def _replace_section(lines: list[str], header: str, body: list[str]) -> None:
    bounds = _section(lines, header)
    if bounds is None:
        return
    start, end = bounds
    lines[start + 1:end] = body + [""]


def _insert_after(lines: list[str], prefixes: tuple[str, ...], value: str) -> None:
    if value in lines:
        return
    for index, line in enumerate(lines):
        if any(line.startswith(prefix) for prefix in prefixes):
            lines.insert(index + 1, value)
            return


def _append_section(lines: list[str], header: str, value: str) -> None:
    bounds = _section(lines, header)
    if bounds is None or value in lines:
        return
    start, end = bounds
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, value)


def render_policy_report(snapshot: dict) -> str:
    """Render v4 with a plain working action and legacy snapshot support."""
    manager = snapshot.get("policy_manager") or {}
    rec = manager.get("recommendation") or {}
    gate = manager.get("gate") or {}
    evidence = manager.get("evidence") or {}
    geometry = manager.get("scenario_geometry") or {}
    tradeoff = manager.get("risk_tradeoff") or {}
    selected = rec.get("policy") or gate.get("provisional_policy") or gate.get("raw_policy") or "—"
    computed = rec.get("computed_action_ru") or rec.get("action_ru") or selected
    execution = rec.get("execution_action_ru") or computed
    executable = _is_executable(gate, rec)

    lines = _ORIGINAL_RENDER_POLICY_REPORT(snapshot).splitlines()
    next_header = next(
        (i for i in range(1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    body = lines[next_header:]
    if executable:
        lines = [f"**ДЕЙСТВИЕ СЕЙЧАС** — {execution}.", ""] + body
    else:
        reasons = gate.get("reasons") or ["gate не подтвердил изменение позиции"]
        lines = [
            "**ДЕЙСТВИЕ СЕЙЧАС** — НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ; "
            "ПРОДОЛЖАТЬ ТЕКУЩЕЕ СОПРОВОЖДЕНИЕ. "
            "НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ. "
            f"Расчётное действие: {computed}; оно не подтверждено.",
            "Причины запрета: " + "; ".join(reasons) + ".",
            "",
        ] + body

    # Snapshots created before v4 have the previous geometry schema.
    legacy_hour = (geometry.get("no_event_empirical") or {}).get("60m") or {}
    if legacy_hour and not geometry.get("no_event_windows"):
        n = int(_num(geometry.get("scenario_count")) or legacy_hour.get("scenarios") or 0)
        event_minutes = _num(geometry.get("expected_event_minutes"))
        _replace_section(lines, "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ**", [
            f"Один набор из {n or '—'} путей для всех политик. "
            f"P рубежа раньше стопа {_pct(geometry.get('p_next_rung_before_stop'))}; "
            f"P стопа раньше рубежа {_pct(geometry.get('p_stop_before_next_rung'))}.",
            f"За 60 минут событие произошло в {legacy_hour.get('events', 0)} из "
            f"{legacy_hour.get('scenarios', n)} сценариев; оценка NO-EVENT "
            f"{legacy_hour.get('display', '—')}.",
            "Ожидаемое время до ближайшего рубежа или стопа: "
            + (f"{event_minutes:.1f} мин." if event_minutes is not None else "не определено."),
        ])

    old_delta = _num(tradeoff.get("expected_cost_vs_hold_r"))
    if tradeoff.get("expected_delta_vs_hold_r") is None and old_delta is not None:
        _append_section(
            lines,
            "**ПОЧЕМУ ВЫБРАНО**",
            f"Цена защиты относительно HOLD: {_r(old_delta)}; "
            f"улучшение CVaR10: {_r(tradeoff.get('cvar_improvement_vs_hold_r'))}.",
        )

    authority = gate.get("authority_stability") or {}
    if authority and not any(line.startswith("Устойчивость к источнику данных") for line in lines):
        count = (authority.get("winner_counts") or {}).get(selected, 0)
        _insert_after(
            lines,
            ("Устойчивость сырого", "Параметрическая устойчивость сырого"),
            f"Устойчивость к источнику данных для {selected}: "
            f"{count}/{authority.get('checks', 0)} ({_pct(gate.get('source_stability_share'))}); "
            f"проверки: {authority.get('description', '—')}.",
        )

    if not any(line.startswith("Независимые семьи подтверждений:") for line in lines):
        independence = evidence.get("confirmation_independence") or {}
        families = evidence.get("adverse_confirmation_families") or []
        _insert_after(
            lines,
            ("Против удержания:", "Однонаправленные семьи против удержания:"),
            f"Независимые семьи подтверждений: "
            f"{independence.get('adverse_families', len(families))} — "
            f"{', '.join(families) if families else 'нет'}. "
            f"Отдельных строк метрик: {independence.get('adverse_items', 0)}; "
            "смешанные семьи не дают голоса против удержания.",
        )

    if not executable:
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", [
            "Исполнение не подтверждено. Никакого нового исполнения: сохранить "
            "действующие стоп, БУ/trailing и лестницу частичных фиксаций."
        ])
    elif selected == "EXIT":
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", [
            "Позиция закрыта полностью; остаток, стоп, БУ/trailing и следующий "
            "рубеж не применяются."
        ])

    return "\n".join(
        line.replace("Надёжность:", "Надёжность расчёта:")
        for line in lines
    ).strip()


for _module in (_impl, _impl._impl, _impl._impl._base):
    _module.SYSTEM_PROMPT = SYSTEM_PROMPT
    _module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
