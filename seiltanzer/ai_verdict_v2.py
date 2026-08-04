"""Verdict facade with a deterministic hard-CVaR report."""
from __future__ import annotations

from . import ai_verdict_base as _base

globals().update({
    name: value for name, value in vars(_base).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})

SYSTEM_PROMPT = """Ты объясняешь готовое решение quantitative policy_manager.
Нельзя менять выбранную политику, объём закрытия или числа. Hard CVaR — обязательное
ограничение: недопустимая политика не может быть восстановлена confirmation gate.
Отдельно показывай сырой и финальный выбор, их устойчивость, общую геометрию путей,
цену защиты по Expected R и улучшение CVaR10. IV skew является подтверждением только
при покрытии обоих крыльев ±5% реальными страйками. POC/value area выше цены — контекст,
пока нет отбоя, отрицательного directional delta, отсутствия принятия выше и adverse flow.
Покрытие полей и надёжность данных выводи раздельно. Не используй размытые фразы."""


def _r(value) -> str:
    value = _base._num(value)
    return "—" if value is None else f"{value:+.3f}R"


def _p(value) -> str:
    value = _base._num(value)
    return "—" if value is None else f"{value * 100:.1f}%"


def _item(item: dict) -> str:
    fields = []
    for key, value in item.items():
        if key in {"metric", "context_only"}:
            continue
        fields.append(f"{key}={value}")
    return f"{item.get('metric', 'метрика')}: " + ", ".join(fields)


def render_policy_report(snapshot: dict) -> str:
    manager = snapshot.get("policy_manager") or {}
    rec = manager.get("recommendation") or {}
    policies = manager.get("policies") or {}
    rule = manager.get("selection_rule") or {}
    gate = manager.get("gate") or {}
    geometry = manager.get("scenario_geometry") or {}
    raw_stability = manager.get("raw_optimizer_stability") or {}
    final_stability = manager.get("stability") or {}
    tradeoff = manager.get("risk_tradeoff") or {}
    evidence = manager.get("evidence") or {}
    coverage = (manager.get("metric_coverage") or {}).get("summary") or {}
    inputs = manager.get("inputs") or {}
    raw = rec.get("raw_optimizer_policy") or gate.get("raw_policy") or rec.get("policy")
    selected = rec.get("policy") or raw or "HOLD"
    floor = _base._num(rule.get("cvar_floor_r"))
    eligible = rule.get("eligible") or []
    ineligible = rule.get("ineligible") or {}

    lines = [f"**ДЕЙСТВИЕ** — {rec.get('action_ru', selected)}."]
    if not rec.get("automatic_execution_allowed", True):
        lines.append("Автоматическое исполнение запрещено: gate зафиксировал конфликт модели.")

    lines += ["", "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** —"]
    lines.append(
        f"Один набор из {geometry.get('scenario_count', '—')} путей для всех политик. "
        f"P рубежа раньше стопа {_p(geometry.get('p_next_rung_before_stop'))}; "
        f"P стопа раньше рубежа {_p(geometry.get('p_stop_before_next_rung'))}."
    )
    hour = (geometry.get("no_event_empirical") or {}).get("60m") or {}
    if hour:
        lines.append(
            f"За 60 минут событие произошло в {hour.get('events', 0)} из "
            f"{hour.get('scenarios', '—')} сценариев; оценка NO-EVENT {hour.get('display', '—')}."
        )
    event_minutes = _base._num(geometry.get("expected_event_minutes"))
    lines.append("Ожидаемое время до ближайшего рубежа или стопа: "
                 + (f"{event_minutes:.1f} мин." if event_minutes is not None else "не определено."))

    lines += ["", "**РАСЧЁТ ПОЛИТИК** —"]
    for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        m = policies.get(name) or {}
        lines.append(
            f"{name}: Expected {_r(m.get('expected_final_r'))}; "
            f"медиана {_r(m.get('median_final_r'))}; CVaR10 {_r(m.get('cvar10_r'))}; "
            f"P прибыли {_p(m.get('p_final_profit'))}; "
            f"P отдачи 0.25R {_p(m.get('p_giveback_0_25_from_now'))}; "
            f"P отдачи 0.50R {_p(m.get('p_giveback_0_50_from_now'))}."
        )

    lines += ["", "**ПОЧЕМУ ВЫБРАНО** —"]
    lines.append(f"Сырой выбор: {raw}. Допустимые по CVaR10 ≥ {_r(floor)}: "
                 + (", ".join(eligible) if eligible else "нет") + ".")
    if "HOLD" in ineligible:
        hc = ineligible["HOLD"].get("cvar10_r")
        lines.append(f"HOLD исключён: CVaR10 {_r(hc)} ниже допустимого {_r(floor)}; "
                     "confirmation gate не имеет права вернуть HOLD.")
    selected_metric = policies.get(selected) or {}
    alternatives = [n for n in eligible if n != selected]
    if alternatives:
        best_alt = max(alternatives, key=lambda n: policies[n].get("expected_final_r", -999))
        lines.append(f"{selected} выбран среди допустимых: Expected {_r(selected_metric.get('expected_final_r'))} "
                     f"против {best_alt} {_r(policies[best_alt].get('expected_final_r'))}.")
    lines.append(
        f"Цена защиты относительно HOLD: {_r(tradeoff.get('expected_cost_vs_hold_r'))}; "
        f"улучшение CVaR10: {_r(tradeoff.get('cvar_improvement_vs_hold_r'))}."
    )
    lines.append(
        f"Устойчивость сырого {raw}: {raw_stability.get('selected_count', 0)}/"
        f"{raw_stability.get('checks', 0)} ({_p(raw_stability.get('selected_share'))}). "
        f"Устойчивость финального {selected}: {final_stability.get('selected_count', 0)}/"
        f"{final_stability.get('checks', 0)} ({_p(final_stability.get('selected_share'))})."
    )
    lines.append(f"Результат gate: {gate.get('status', rec.get('gate_status', '—'))}."
                 + (" Причины: " + "; ".join(gate.get("reasons") or []) + "."
                    if gate.get("reasons") else ""))

    lines += ["", "**ПОДТВЕРЖДЕНИЯ, ПРОТИВОРЕЧИЯ И КОНТЕКСТ** —"]
    adverse = evidence.get("adverse_confirmations") or []
    supportive = evidence.get("supportive_contradictions") or []
    context = evidence.get("context_observations") or []
    lines.append("Против удержания: " + ("; ".join(_item(x) for x in adverse[:6])
                                          if adverse else "нет независимых подтверждений") + ".")
    lines.append("В пользу удержания: " + ("; ".join(_item(x) for x in supportive[:6])
                                           if supportive else "нет независимых подтверждений") + ".")
    if context:
        lines.append("Контекст без самостоятельного веса: "
                     + "; ".join(_item(x) for x in context[:6]) + ".")
    flags = evidence.get("uncertainty_flags") or []
    if flags:
        lines.append("Ограничения: " + "; ".join(_item(x) for x in flags[:6]) + ".")

    attribution = manager.get("counterfactual_attribution") or {}
    lines += ["", "**РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ** —"]
    if attribution.get("available"):
        parts = [f"{x['component']} {x['delta_expected_r']:+.3f}R"
                 for x in attribution.get("sequential_contributions") or []]
        lines.append(f"Expected HOLD изменился на {attribution.get('total_change_r', 0):+.3f}R: "
                     + "; ".join(parts) + ".")
    else:
        lines.append("Предыдущего совместимого снимка ещё нет.")

    lines += ["", "**ПОСЛЕ ИСПОЛНЕНИЯ** —"]
    lines.append(f"Оставить {float(rec.get('remaining_fraction') or 0) * 100:.0f}% текущего остатка. "
                 f"{rec.get('remaining_management')}. Следующий рубеж: {_r(rec.get('next_rung_r'))}.")

    cancel = manager.get("cancellation_boundary") or {}
    lines += ["", "**ГРАНИЦА ОТМЕНЫ** —"]
    if cancel.get("available") and cancel.get("hold_switch"):
        sw = cancel["hold_switch"]
        lines.append(f"До исполнения пересчитать при r={sw.get('r'):+.3f}R; "
                     f"там расчёт переключается на HOLD, barrier EV={sw.get('barrier_ev_r'):+.3f}R.")
    else:
        lines.append(cancel.get("reason") or "На проверенной сетке переход не найден.")

    lines += ["", "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ** —"]
    lines.append("Новая опционная цепочка; движение цены на ±0.15R; касание рубежа, стопа или границы отмены.")

    reliability = _base._at(evidence, "data_quality", "reliability", default={}) or {}
    age = _base._num(inputs.get("chain_age_sec"))
    lines += ["", "**КАЧЕСТВО ДАННЫХ** —"]
    lines.append(
        f"Покрытие данных: {coverage.get('available_groups', 0)}/{coverage.get('total_groups', 0)}. "
        f"Надёжность расчёта: {reliability.get('level', 'не определена')}. "
        f"Цепочка: {inputs.get('chain_status')}; возраст "
        + (f"{age / 60:.1f} мин; " if age is not None else "неизвестен; ")
        + f"proxy={inputs.get('proxy_quality')}. Причины: "
        + "; ".join(reliability.get("reasons") or ["нет отмеченных ограничений"]) + "."
    )
    return "\n".join(lines)


def _validate_model_report(content: str, snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    required = ("ДЕЙСТВИЕ", "ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ", "РАСЧЁТ ПОЛИТИК",
                "ПОЧЕМУ ВЫБРАНО", "ПОДТВЕРЖДЕНИЯ", "РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ",
                "ПОСЛЕ ИСПОЛНЕНИЯ", "ГРАНИЦА ОТМЕНЫ", "СЛЕДУЮЩИЙ ПЕРЕСЧЁТ",
                "КАЧЕСТВО ДАННЫХ")
    violations = [f"нет раздела {x}" for x in required if x not in content.upper()]
    action = _base._at(manager, "recommendation", "action_ru")
    if action and action not in content.upper():
        violations.append("изменено рассчитанное действие")
    selected = _base._at(manager, "recommendation", "policy")
    eligible = _base._at(manager, "selection_rule", "eligible", default=[]) or []
    if selected and eligible and selected not in eligible:
        violations.append("финальная политика нарушает CVaR feasible set")
    stability = manager.get("stability") or {}
    if selected and float(stability.get("selected_share") or 0.0) <= 0.0:
        violations.append("финальная политика имеет устойчивость 0%")
    for name, metrics in (manager.get("policies") or {}).items():
        if name not in content:
            violations.append(f"нет политики {name}")
        for field in ("expected_final_r", "cvar10_r"):
            value = _base._num(metrics.get(field))
            if value is not None and f"{value:+.3f}" not in content:
                violations.append(f"изменено или пропущено {name}.{field}")
    return violations


_base.SYSTEM_PROMPT = SYSTEM_PROMPT
_base.render_policy_report = render_policy_report
_base._validate_model_report = _validate_model_report
globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
globals()["_validate_model_report"] = _validate_model_report
