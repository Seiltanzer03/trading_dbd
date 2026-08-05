"""Clear Russian report for quantitative AI policy manager v4."""
from __future__ import annotations

from typing import Any

from . import ai_verdict_v2 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_ORIGINAL_RENDER_POLICY_REPORT = _impl.render_policy_report

SYSTEM_PROMPT = """Ты объясняешь готовый quantitative policy_manager v4.
Не меняй числа, CVaR-feasible set, расчётную политику или execution-authority.
Главное — сразу дать человеку однозначное рабочее действие.

Если automatic_execution_allowed=false, первая строка:
«НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ; продолжать текущее сопровождение».
Расчётный EXIT/CLOSE показывай отдельно как модельный кандидат, а не рекомендацию.

Объясняй:
- CVaR-порог из действующего стопа/БУ/trailing, а не из произвольной отдачи 0.80R;
- net Expected и CVaR после издержек;
- вероятность рубежа, стопа и отсутствия обоих событий за полный горизонт;
- среднее время события только среди разрешившихся сценариев;
- редкие вероятности с числом наблюдений;
- однонаправленные, смешанные и контекстные семейства доказательств;
- разницу между расчётным преимуществом и стоимостью защиты.
Не суммируй противоречащие метрики одной семьи как голос против удержания."""


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _at(value: Any, *path: str, default=None):
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _r(value: Any) -> str:
    value = _num(value)
    return "—" if value is None else f"{value:+.3f}R"


def _prob(value: Any, count: Any = None, total: Any = None) -> str:
    value = _num(value)
    count_n = int(count) if _num(count) is not None else None
    total_n = int(total) if _num(total) is not None else None
    if value is None:
        return "—"
    if count_n == 0 and total_n:
        return f"0 наблюдений из {total_n} (<{100 / total_n:.2f}%)"
    if 0.0 < value < 0.001:
        base = f"{value * 100:.3f}%"
    elif value < 0.01:
        base = f"{value * 100:.2f}%"
    else:
        base = f"{value * 100:.1f}%"
    if count_n is not None and total_n:
        base += f" ({count_n}/{total_n})"
    return base


def _item(item: dict) -> str:
    metric = item.get("metric", "метрика")
    fields = []
    for key, value in item.items():
        if key in {"metric", "context_only", "family"}:
            continue
        fields.append(f"{key}={value}")
    suffix = ", ".join(fields)
    return f"{metric}" + (f": {suffix}" if suffix else "")


def _status_ru(status: str | None) -> str:
    return {
        "confirmed": "подтверждено",
        "downgraded_within_feasible_set": "подтверждено с уменьшением объёма",
        "manual_source_conflict": "конфликт устойчивости источников",
        "manual_data_conflict": "данные недостаточно надёжны",
        "manual_conflict": "неразрешённый конфликт",
        "conflict": "не хватает подтверждений",
        "conflict_stability_fallback": "конфликт устойчивости",
    }.get(status or "", status or "не определён")


def render_policy_report(snapshot: dict) -> str:
    manager = snapshot.get("policy_manager") or {}
    rec = manager.get("recommendation") or {}
    gate = manager.get("gate") or {}
    policies = manager.get("policies") or {}
    geometry = manager.get("scenario_geometry") or {}
    rule = manager.get("selection_rule") or {}
    risk = manager.get("risk_constraint") or rule.get("risk_constraint") or {}
    costs = manager.get("execution_cost_model") or rule.get("execution_cost_model") or {}
    evidence = manager.get("evidence") or {}
    coverage = (manager.get("metric_coverage") or {}).get("summary") or {}
    tradeoff = manager.get("risk_tradeoff") or {}
    raw_stability = manager.get("raw_optimizer_stability") or {}
    final_stability = manager.get("stability") or {}
    attribution = manager.get("counterfactual_attribution") or {}
    cancellation = manager.get("cancellation_boundary") or {}
    inputs = manager.get("inputs") or {}

    executable = bool(gate.get("automatic_execution_allowed"))
    selected = rec.get("policy") or gate.get("provisional_policy") or "—"
    raw = rec.get("raw_optimizer_policy") or gate.get("raw_policy") or selected
    computed_action = rec.get("computed_action_ru") or rec.get("action_ru") or selected
    execution_action = rec.get("execution_action_ru") or computed_action

    lines = []
    if executable:
        lines.append(f"**ДЕЙСТВИЕ СЕЙЧАС** — {execution_action}.")
    else:
        lines.append(
            "**ДЕЙСТВИЕ СЕЙЧАС** — НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ; "
            "ПРОДОЛЖАТЬ ТЕКУЩЕЕ СОПРОВОЖДЕНИЕ."
        )
        lines.append(
            f"НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ. Расчётная политика — {selected}; "
            f"расчётное действие: {computed_action}. Это модельный кандидат, "
            "а не разрешённая рекомендация."
        )
        reasons = gate.get("reasons") or []
        lines.append(
            "Почему: " + ("; ".join(reasons) if reasons else "gate не подтвердил изменение позиции") + "."
        )

    lines += ["", "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** —"]
    n = int(_num(geometry.get("scenario_count")) or 0)
    rung_count = geometry.get("rung_first_count")
    stop_count = geometry.get("stop_first_count")
    unresolved_count = geometry.get("unresolved_count")
    lines.append(
        f"Один набор из {n or '—'} путей. Рубеж раньше стопа: "
        f"{_prob(geometry.get('p_next_rung_before_stop'), rung_count, n)}. "
        f"Стоп раньше рубежа: "
        f"{_prob(geometry.get('p_stop_before_next_rung'), stop_count, n)}."
    )
    lines.append(
        "За полный горизонт ни рубеж, ни стоп не достигнуты: "
        f"{_prob(geometry.get('p_unresolved_full_horizon'), unresolved_count, n)}"
        + (
            f"; горизонт {float(geometry.get('full_horizon_minutes')):.0f} мин."
            if _num(geometry.get("full_horizon_minutes")) is not None else "."
        )
    )
    hour = (geometry.get("no_event_windows") or {}).get("60m") or {}
    if hour:
        lines.append(
            f"За первые 60 минут событие произошло в {hour.get('events', 0)} из "
            f"{hour.get('scenarios', n)} сценариев; NO-EVENT "
            f"{_prob(hour.get('no_event_probability'), hour.get('no_event_count'), hour.get('scenarios', n))}."
        )
    mean_event = _num(geometry.get("mean_event_minutes_given_resolved"))
    resolved_count = int(_num(geometry.get("resolved_count")) or 0)
    lines.append(
        "Среднее время до события только среди разрешившихся сценариев: "
        + (f"{mean_event:.1f} мин. ({resolved_count}/{n})." if mean_event is not None and n else "не определено.")
    )

    lines += ["", "**РАСЧЁТ ПОЛИТИК** —"]
    for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        metric = policies.get(name) or {}
        gross = _num(metric.get("gross_expected_final_r"))
        cost = _num(metric.get("execution_cost_r"))
        gross_text = (
            f"; gross {_r(gross)}; издержки {_r(cost)}"
            if gross is not None or cost is not None else ""
        )
        lines.append(
            f"{name}: Expected net {_r(metric.get('expected_final_r'))}{gross_text}; "
            f"медиана net {_r(metric.get('median_final_r'))}; "
            f"CVaR10 net {_r(metric.get('cvar10_r'))}; "
            f"P прибыли {_prob(metric.get('p_final_profit'))}; "
            f"P отдачи 0.25R {_prob(metric.get('p_giveback_0_25_from_now'))}; "
            f"P отдачи 0.50R {_prob(metric.get('p_giveback_0_50_from_now'))}."
        )
    if costs:
        lines.append(
            "Модель издержек полного закрытия: сейчас "
            f"{_r(costs.get('immediate_full_close_r'))}, позднее "
            f"{_r(costs.get('deferred_full_close_r'))}. "
            f"Источник: {costs.get('source', '—')}; "
            f"{'это явно обозначенная резервная оценка' if costs.get('assumed') else 'использованы входные данные терминала'}."
        )

    lines += ["", "**ПОЧЕМУ ВЫБРАНО** —"]
    eligible = rule.get("eligible") or gate.get("eligible_policies") or []
    floor = risk.get("cvar_floor_r", rule.get("cvar_floor_r"))
    lines.append(
        f"Расчётный выбор: {raw}. CVaR10-порог {_r(floor)}. "
        f"Допустимы: {', '.join(eligible) if eligible else 'нет'}."
    )
    lines.append(
        "Источник порога: "
        f"{risk.get('source', 'не указан')}. Правило: {risk.get('rule', '—')}. "
        + (
            f"Явно заданная допустимая отдача: {_r(risk.get('max_giveback_r'))}."
            if risk.get("max_giveback_r") is not None
            else "Произвольный лимит отдачи прибыли не применялся."
        )
    )
    ineligible = rule.get("ineligible") or {}
    if "HOLD" in ineligible:
        lines.append(
            f"HOLD исключён по CVaR10: {_r((ineligible.get('HOLD') or {}).get('cvar10_r'))} "
            f"< {_r(floor)}."
        )
    elif policies.get("HOLD"):
        lines.append(
            f"HOLD проходит hard CVaR: {_r((policies.get('HOLD') or {}).get('cvar10_r'))} "
            f">= {_r(floor)}."
        )

    delta = _num(tradeoff.get("expected_delta_vs_hold_r"))
    delta_label = tradeoff.get("expected_delta_label")
    if delta is not None:
        lines.append(
            f"{delta_label or ('Расчётное преимущество над HOLD' if delta >= 0 else 'Стоимость защиты')}: "
            f"{_r(delta)}. Улучшение CVaR10 относительно HOLD: "
            f"{_r(tradeoff.get('cvar_improvement_vs_hold_r'))}."
        )

    lines.append(
        f"Параметрическая устойчивость сырого {raw}: "
        f"{raw_stability.get('selected_count', 0)}/{raw_stability.get('checks', 0)} "
        f"({_prob(raw_stability.get('selected_share'))}). "
        f"Финального {selected}: {final_stability.get('selected_count', 0)}/"
        f"{final_stability.get('checks', 0)} "
        f"({_prob(final_stability.get('selected_share'))})."
    )
    authority = gate.get("authority_stability") or {}
    source_count = (authority.get("winner_counts") or {}).get(selected, 0)
    lines.append(
        f"Устойчивость к источнику данных для {selected}: "
        f"{source_count}/{authority.get('checks', 0)} "
        f"({_prob(gate.get('source_stability_share'))})."
    )
    lines.append(
        f"Итог gate: {_status_ru(gate.get('status'))}. "
        f"Рабочее действие: {'исполнить ' + selected if executable else 'не менять позицию по этому отчёту'}."
    )

    lines += ["", "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —"]
    adverse = evidence.get("adverse_confirmations") or []
    supportive = evidence.get("supportive_contradictions") or []
    context = evidence.get("context_observations") or []
    mixed = evidence.get("mixed_confirmation_families") or []
    adverse_families = evidence.get("adverse_confirmation_families") or []
    supportive_families = evidence.get("supportive_confirmation_families") or []
    lines.append(
        "Однонаправленные семьи против удержания: "
        + (", ".join(adverse_families) if adverse_families else "нет") + "."
    )
    lines.append(
        "Однонаправленные семьи в пользу удержания: "
        + (", ".join(supportive_families) if supportive_families else "нет") + "."
    )
    lines.append(
        "Смешанные семьи, не дающие голоса gate: "
        + (", ".join(mixed) if mixed else "нет") + "."
    )
    lines.append(
        "Метрики против удержания: "
        + ("; ".join(_item(x) for x in adverse[:8]) if adverse else "нет") + "."
    )
    lines.append(
        "Метрики в пользу удержания: "
        + ("; ".join(_item(x) for x in supportive[:8]) if supportive else "нет") + "."
    )
    if context:
        lines.append(
            "Контекст без самостоятельного голоса: "
            + "; ".join(_item(x) for x in context[:10]) + "."
        )
    flags = evidence.get("uncertainty_flags") or []
    if flags:
        lines.append("Ограничения: " + "; ".join(_item(x) for x in flags[:8]) + ".")

    lines += ["", "**РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ** —"]
    if attribution.get("available"):
        parts = [
            f"{item.get('component')} {_r(item.get('delta_expected_r'))}"
            for item in attribution.get("sequential_contributions") or []
        ]
        lines.append(
            f"Expected HOLD изменился на {_r(attribution.get('total_change_r'))}: "
            + "; ".join(parts) + "."
        )
    else:
        lines.append("Предыдущего совместимого снимка для разложения нет.")

    lines += ["", "**ПОСЛЕ ИСПОЛНЕНИЯ** —"]
    if not executable:
        lines.append(
            "Никакого нового исполнения. Сохранить действующие стоп, БУ/trailing "
            "и лестницу частичных фиксаций."
        )
    elif selected == "EXIT":
        lines.append(
            "Позиция закрыта полностью; остаток, стоп, БУ/trailing и следующий рубеж не применяются."
        )
    else:
        lines.append(
            f"После действия оставить {float(rec.get('remaining_fraction') or 0) * 100:.0f}% "
            f"текущего остатка; {rec.get('remaining_management', 'вести по действующим правилам')}."
        )

    lines += ["", "**ГРАНИЦА ОТМЕНЫ** —"]
    if cancellation.get("available") and cancellation.get("hold_switch"):
        switch = cancellation["hold_switch"]
        lines.append(
            f"Пересчитать около r={float(switch.get('r')):+.3f}R: "
            "там net-оптимизатор переключается на HOLD."
        )
    else:
        lines.append(cancellation.get("reason") or "На проверенной сетке переход не найден.")

    lines += ["", "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ** —"]
    if executable and selected == "EXIT":
        lines.append("После подтверждённого полного выхода сопровождение этой сделки завершено.")
    else:
        lines.append(
            "Новая live/direct опционная цепочка; изменение цены на ±0.15R; "
            "касание рубежа, стопа, БУ/trailing или изменение execution-cost."
        )

    reliability = _at(evidence, "data_quality", "reliability", default={}) or {}
    age = _num(inputs.get("chain_age_sec"))
    lines += ["", "**КАЧЕСТВО ДАННЫХ** —"]
    lines.append(
        f"Покрытие: {coverage.get('available_groups', 0)}/{coverage.get('total_groups', 0)}. "
        f"Надёжность: {reliability.get('level', 'не определена')}. "
        f"Цепочка: {inputs.get('chain_status')}; возраст "
        + (f"{age / 60:.1f} мин; " if age is not None else "неизвестен; ")
        + f"proxy={inputs.get('proxy_quality')}. Причины: "
        + "; ".join(reliability.get("reasons") or ["существенные ограничения не отмечены"])
        + "."
    )
    return "\n".join(lines)


def _validate_model_report(content: str, snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    required = (
        "ДЕЙСТВИЕ", "ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ", "РАСЧЁТ ПОЛИТИК",
        "ПОЧЕМУ ВЫБРАНО", "ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ",
        "ПОСЛЕ ИСПОЛНЕНИЯ", "СЛЕДУЮЩИЙ ПЕРЕСЧЁТ", "КАЧЕСТВО ДАННЫХ",
    )
    upper = content.upper()
    violations = [f"нет раздела {name}" for name in required if name not in upper]
    gate = manager.get("gate") or {}
    rec = manager.get("recommendation") or {}
    if gate.get("automatic_execution_allowed"):
        action = rec.get("execution_action_ru") or rec.get("action_ru")
        if action and action.upper() not in upper:
            violations.append("изменено разрешённое действие")
    else:
        if "НИЧЕГО НЕ МЕНЯТЬ" not in upper:
            violations.append("нет ясного запрета изменения позиции")
    selected = rec.get("policy")
    eligible = (manager.get("selection_rule") or {}).get("eligible") or []
    if selected and eligible and selected not in eligible:
        violations.append("расчётная политика вне CVaR-feasible set")
    for name, metric in (manager.get("policies") or {}).items():
        if name not in content:
            violations.append(f"нет политики {name}")
        for field in ("expected_final_r", "cvar10_r"):
            value = _num(metric.get(field))
            if value is not None and f"{value:+.3f}" not in content:
                violations.append(f"изменено или пропущено {name}.{field}")
    return violations


_impl.SYSTEM_PROMPT = SYSTEM_PROMPT
_impl.render_policy_report = render_policy_report
_impl._validate_model_report = _validate_model_report
_impl._base.SYSTEM_PROMPT = SYSTEM_PROMPT
_impl._base.render_policy_report = render_policy_report
_impl._base._validate_model_report = _validate_model_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
globals()["_validate_model_report"] = _validate_model_report
