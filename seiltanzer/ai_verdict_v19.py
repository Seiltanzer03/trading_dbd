"""Verdict v19: structured report integrity over the established v18 decision.

V19 changes presentation only. It activates only for the current structured
snapshot contract (root metric coverage + trade geometry + net CVaR floor), so
legacy snapshots and LLM-only responses retain their established contract.
"""
from __future__ import annotations

from typing import Any
import re

from . import ai_verdict_v18 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
_BASE_REQUEST = _impl.request_verdict
REPORT_VERSION = "ai-verdict-v19-structured-integrity"
_MATERIAL_DELTA_EPS = 1e-5


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _text(value: Any) -> str:
    return "—" if value is None or value == "" else str(value)


def _r(value: Any) -> str:
    value = _number(value)
    return "—" if value is None else f"{value:+.3f}R"


def _pct(value: Any) -> str:
    value = _number(value)
    if value is None:
        return "—"
    if 0 < abs(value) < 0.001:
        return f"{value * 100:.3f}%"
    return f"{value * 100:.1f}%"


def _score(value: Any) -> str:
    value = _number(value)
    return "—" if value is None else f"{value:+.3f}"


def _fraction_pct(value: Any) -> str:
    value = _number(value)
    return "—" if value is None else f"{value * 100:.1f}%"


def _count_ratio(count: Any, total: Any, share: Any = None) -> str:
    k = _number(count)
    n = _number(total)
    if k is None or n is None or n <= 0:
        return "UNAVAILABLE"
    return f"{int(k)}/{int(n)} ({_pct(share)})"


def _prob(value: Any, count: Any, total: Any) -> str:
    probability = _number(value)
    n_value = _number(total)
    k_value = _number(count)
    if probability is None:
        return "—"
    if n_value is None or n_value <= 0 or k_value is None:
        return _pct(probability)
    n = int(n_value)
    k = int(k_value)
    if k == 0:
        return f"0 наблюдений из {n} (<{100 / n:.2f}%)"
    return f"{probability * 100:.1f}% ({k}/{n})"


def _structured_contract(snapshot: dict) -> bool:
    manager = snapshot.get("policy_manager") or {}
    root_coverage = snapshot.get("metric_coverage") or {}
    risk = manager.get("risk_constraint") or {}
    return bool(
        isinstance(root_coverage, dict)
        and (root_coverage.get("summary") or {}).get("total_groups")
        and isinstance(snapshot.get("trade_geometry"), dict)
        and snapshot.get("trade_geometry")
        and risk.get("net_cvar_floor_r") is not None
    )


def _section(lines: list[str], header: str) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if line.startswith(header)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("**")), len(lines))
    return start, end


def _replace_section(lines: list[str], header: str, body: list[str]) -> None:
    bounds = _section(lines, header)
    if bounds is None:
        return
    start, end = bounds
    lines[start + 1:end] = [*body, ""]


def _replace_take_stop_body(lines: list[str], body: list[str]) -> None:
    start = next((i for i, line in enumerate(lines) if line.startswith("**TAKE vs STOP/BE")), None)
    if start is None:
        return
    end = start + 1
    while end < len(lines) and lines[end].strip() and not lines[end].startswith("**"):
        end += 1
    lines[start + 1:end] = body


def _plan_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    arbiter = manager.get("management_arbiter") or {}
    shadow = manager.get("shadow_policy_contract") or {}
    rec = manager.get("recommendation") or {}
    authority = decision.get("authority") or arbiter.get("winner") or "—"
    production = decision.get("policy") or rec.get("policy") or "—"
    unified = manager.get("unified_edge_ensemble") or {}
    model_policy = unified.get("selected_policy") or decision.get("model_policy") or shadow.get("new_candidate_policy") or rec.get("raw_optimizer_policy") or production
    continuity = decision.get("continuity") or "none (первый разбор)"
    ranking_line = (
        f"Единый ансамбль выбрал {unified.get('selected_policy') or '—'}: веса компонентов ранжируют допустимые действия; hard-risk/CVaR и ограничения исполнения обязательны. Балл не является Expected или исторической прибылью."
        if unified.get("applied", unified.get("available")) else
        f"Выбор ансамбля не применён: {unified.get('operational_guard_reason') or 'нет допустимого варианта'}. Действует {production} после проверок исполнения."
        if unified else
        f"Диагностические баллы: стратегия {_score(arbiter.get('strategy_score'))}; overlay до бонуса {_score(arbiter.get('ai_score_before_priority'))}; после бонуса {_score(arbiter.get('ai_score_after_priority'))}. Баллы и бонус не определяют победителя: подтверждённый overlay получает приоритет по правилу."
    )
    return [
        f"Авторитет плана: {authority}; production policy: {production}; shadow/model candidate: {model_policy}.",
        f"Статус исполнения: {_text(decision.get('execution_status'))}; continuity={continuity}.",
        f"Новая доля закрытия текущего остатка: {_fraction_pct(decision.get('incremental_close_fraction'))}; остаток после действия: {_fraction_pct(decision.get('remaining_fraction_after_action'))}.",
        ranking_line,
        ("Единый выбор учитывает все опубликованные компоненты; после обязательных риск-проверок второй параллельной команды нет."
         if unified else "Приоритет ИИ действует только после evidence/CVaR/stress gate; после выбора арбитра второй параллельной команды нет; production authority этим отчётом не расширяется."),
        *_position_economics_lines(snapshot),
    ]


def _position_economics_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    position = snapshot.get("position_state") or {}
    economics = manager.get("position_economics") or {}
    before = _number(decision.get("remaining_fraction_before_action"))
    if before is None:
        before = _number(position.get("remaining_position_fraction"))
    after = _number(decision.get("remaining_fraction_after_action"))
    fraction = _number(decision.get("incremental_close_fraction"))
    lines = ["Expected, медиана и CVaR таблицы политик даны на единицу текущего остатка; R отсчитывается от исходного риска сделки. P прибыли относится к будущему результату остатка, а не всей частично закрытой сделки."]
    if before is not None and fraction is not None:
        lines.append(f"Объём относительно исходной позиции: до {_pct(before)}; закрыть {_pct(before * fraction)}; после {_pct(after)}.")
    selected = decision.get("policy") or (manager.get("recommendation") or {}).get("policy")
    row = (economics.get("policies") or {}).get(selected) or {}
    unified = manager.get("unified_edge_ensemble") or {}
    primary = _primary_economics(snapshot)
    if primary:
        bank = unified.get("scenario_bank") or {}
        lines.append(f"Основная экономика: единый сравнительный банк {bank.get('bank_id') or '—'}; источник {bank.get('source') or '—'}. Контрольная bridge-модель и независимый gate показаны отдельно.")
        realized = _number(economics.get("realized_r_weighted"))
        expected, cvar = _number(primary.get("expected_net_r")), _number(primary.get("cvar10_net_r"))
        if before is not None and realized is not None and expected is not None and cvar is not None:
            delta, gain = _number(primary.get("delta_expected_r")), _number(primary.get("delta_cvar_r"))
            row = {"expected_total_r": realized + before * expected,
                   "cvar10_total_r": realized + before * cvar,
                   "expected_delta_total_r": before * delta if delta is not None else None,
                   "cvar_gain_total_r": before * gain if gain is not None else None}
        else:
            row = {}
            lines.append(f"Экономика {selected} на единицу текущего остатка: Expected {_r(expected)}; CVaR10 {_r(cvar)}. Результат всей сделки недоступен: отсутствует остаток или фактический зафиксированный результат.")
    if row:
        lines.append(f"Польза {selected} против HOLD для всей сделки: Expected {_r(row.get('expected_delta_total_r'))}; CVaR10 {_r(row.get('cvar_gain_total_r'))}.")
        lines.append(f"Вся сделка при {selected}: Expected {_r(row.get('expected_total_r'))}; CVaR10 {_r(row.get('cvar10_total_r'))}; зафиксировано {_r(economics.get('realized_r_weighted'))}. Исторические издержки исполненных закрытий не учтены; будущие издержки включены в модель.")
        if economics.get("realized_price_basis") not in {"user_supplied_broker_fill", "NOT_APPLICABLE"}:
            lines.append("Зафиксированный результат использует оценку/неуточнённые цены закрытий; точность результата всей сделки зависит от фактических цен исполнения у брокера.")
    previous = decision.get("previous_executed_reduction") or {}
    if previous:
        lines.append(f"Последнее подтверждённое сокращение: {previous.get('policy')}; остаток {_pct(previous.get('remaining_before'))} → {_pct(previous.get('remaining_after'))} исходной позиции; decision_id={previous.get('decision_id')}.")
        if decision.get("repeat_reduction"):
            lines.append("Это новое сокращение обновлённого остатка по новому расчёту; прежнее исполнение не повторяется. Дополнительное сокращение требует актуального gate.")
    repeat_gate = manager.get("repeat_intervention_gate") or {}
    if repeat_gate.get("status") == "deferred_no_material_change":
        lines.append(f"Повторный {repeat_gate.get('candidate_policy')} отложен: предыдущее сокращение учтено, существенного нового основания нет. Сейчас HOLD для остатка. Нужны ухудшение на 0.15R, усиление расчётной пользы или новые независимые подтверждения; новое время котировки не считается подтверждением.")
    elif repeat_gate.get("allowed"):
        lines.append("Основания нового сокращения после исполнения: " + ", ".join(repeat_gate.get("reasons") or []) + ".")
    return lines


def _primary_economics(snapshot: dict) -> dict:
    manager = snapshot.get("policy_manager") or {}
    unified = manager.get("unified_edge_ensemble") or {}
    if not unified.get("applied", unified.get("available")) or not unified.get("shared_scenario_bank"):
        return {}
    production = (manager.get("management_decision") or {}).get("policy")
    identity = unified.get("selected_candidate_id")
    return next((row for row in unified.get("candidates") or []
                 if row.get("candidate_id") == identity and row.get("policy") == production), {})


def _terminal_cancellation_lines(snapshot: dict) -> list[str] | None:
    """Keep cancellation text aligned with the authoritative terminal action."""
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    event = decision.get("strategy_terminal_event")
    policy = decision.get("policy")
    if not event and policy != "HOLD" and decision.get("execution_status") == "pending_execution":
        boundary = manager.get("cancellation_boundary") or {}
        switch = boundary.get("hold_switch") or {}
        return [
            f"Граница базового net-оптимизатора: r={_r(switch.get('r'))} → HOLD. Это не граница отмены итогового {policy} от risk-overlay.",
            "Итоговую команду подтверждает или заменяет новый арбитражный пересчёт с актуальными evidence/CVaR/stress проверками. Изменение остатка, стопа или тейка делает старое исполнение недопустимым; нужен новый разбор.",
        ]
    if not event or policy == "HOLD":
        return None
    return [
        f"Для {policy} по терминальному событию стратегии {event} граница отмены не применяется. "
        "До подтверждения ручного исполнения команда остаётся действующей; если позиция уже "
        "закрыта у брокера, нужно подтвердить исполнение в терминале."
    ]


def _trade_geometry_lines(snapshot: dict) -> list[str]:
    g = snapshot.get("trade_geometry") or {}
    position = snapshot.get("position_state") or {}
    return [
        f"Цена сейчас: {_text(g.get('current'))}; ENTRY: {_text(g.get('entry'))}.",
        f"Исходный STOP: {_text(g.get('original_stop'))}.",
        f"Активный риск-барьер: {_text(g.get('active_risk_barrier'))} · {_text(g.get('active_risk_barrier_type'))}.",
        f"FINAL TAKE: {_text(g.get('final_take'))}.",
        f"CURRENT R: {_r(g.get('current_r'))}; R до активного barrier: {_r(g.get('r_to_active_stop'))}; R до FINAL TAKE: {_r(g.get('r_to_final_take'))}.",
        f"Остаток позиции: {_pct(position.get('remaining_position_fraction'))}; уже зафиксировано: {_pct(position.get('realized_position_fraction'))}.",
    ]


def _take_stop_lines(snapshot: dict) -> list[str]:
    g = snapshot.get("trade_geometry") or {}
    take = _number(g.get("take_first")); stop = _number(g.get("stop_or_be_first")); no_touch = _number(g.get("no_touch")); p50 = _number(g.get("p50_resolution_minutes"))
    if take is None or stop is None or no_touch is None:
        return [
            "Authoritative execution-MC TAKE vs active STOP: UNAVAILABLE.",
            "Причина: insufficient authoritative execution-MC data; ноль не подставляется.",
            "Ниже scenario-path geometry относится к отдельному контракту ближайшей ступени и не подменяет вероятность FINAL TAKE.",
            "Risk-neutral Q и physical calibrated P shadow публикуются отдельно.",
        ]
    p50_text = f"{p50:.0f} мин" if p50 is not None else "за горизонтом / не определена"
    return [
        f"TAKE раньше активного risk barrier: {_pct(take)}; STOP/BE раньше TAKE: {_pct(stop)}; NO TOUCH: {_pct(no_touch)}.",
        f"P50 развязки: {p50_text}. Risk-neutral Q и physical calibrated P shadow публикуются отдельно.",
    ]


def _scenario_geometry_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    g = manager.get("scenario_geometry") or {}
    n_value = _number(g.get("scenario_count"))
    n = int(n_value) if n_value is not None and n_value > 0 else None
    rung = _number(g.get("next_rung_r"))
    lines = [
        f"Один набор из {n if n is not None else '—'} путей. "
        f"Ближайшая ступень {_r(rung)} раньше стопа: "
        f"{_prob(g.get('p_next_rung_before_stop'), g.get('rung_first_count'), n)}. "
        f"Стоп раньше ближайшей ступени: "
        f"{_prob(g.get('p_stop_before_next_rung'), g.get('stop_first_count'), n)}."
    ]
    barrier = ((manager.get("evidence") or {}).get("option_barrier") or {})
    p_take = _number(barrier.get("p_take")); p_stop = _number(barrier.get("p_stop")); no_touch = _number(barrier.get("no_touch")); final_take = _number((manager.get("inputs") or {}).get("T"))
    if p_take is not None and final_take is not None:
        pieces = [f"По опционной barrier-модели финальный тейк {_r(final_take)} раньше стопа: {_pct(p_take)}"]
        if p_stop is not None: pieces.append(f"стоп раньше финального тейка: {_pct(p_stop)}")
        if no_touch is not None: pieces.append(f"ни один барьер не достигнут: {_pct(no_touch)}")
        lines.append("; ".join(pieces) + ".")
    lines.append(
        "За полный горизонт ни рубеж, ни стоп не достигнуты: "
        + _prob(g.get("p_unresolved_full_horizon"), g.get("unresolved_count"), n)
        + (f"; горизонт {float(g.get('full_horizon_minutes')):.0f} мин."
           if _number(g.get("full_horizon_minutes")) is not None else ".")
    )
    hour = (g.get("no_event_windows") or {}).get("60m") or {}
    if hour:
        events = _number(hour.get("events")); scenarios = _number(hour.get("scenarios"))
        event_text = (
            f"{int(events)} из {int(scenarios)}"
            if events is not None and scenarios is not None else "UNAVAILABLE"
        )
        lines.append(
            f"За первые 60 минут событие произошло в {event_text} сценариев; "
            f"NO-EVENT {_prob(hour.get('no_event_probability'), hour.get('no_event_count'), hour.get('scenarios'))}."
        )
    mean_event = _number(g.get("mean_event_minutes_given_resolved"))
    if mean_event is not None:
        resolved = _number(g.get("resolved_count"))
        resolved_text = (
            f"{int(resolved)}/{n}" if resolved is not None and n is not None else "UNAVAILABLE"
        )
        lines.append(
            f"Среднее время до события только среди разрешившихся сценариев: "
            f"{mean_event:.1f} мин. ({resolved_text})."
        )
    return lines


def _degraded_evidence_summary(gate: dict) -> tuple[dict, float | None, float | None, float | None]:
    overlay = gate.get("degraded_authority_overlay") or {}
    evidence = overlay.get("evidence") or {}
    return (
        evidence,
        _number(evidence.get("total_adverse_count")),
        _number(evidence.get("live_adverse_count")),
        _number(evidence.get("observed_adverse_item_count")),
    )


def _risk_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    risk = manager.get("risk_constraint") or {}
    rule = manager.get("selection_rule") or {}
    rec = manager.get("recommendation") or {}
    policies = manager.get("policies") or {}
    raw = rec.get("raw_optimizer_policy") or (manager.get("gate") or {}).get("raw_policy") or rec.get("policy") or "—"
    gross = _number(risk.get("gross_cvar_floor_r")); gross = _number(risk.get("cvar_floor_r")) if gross is None else gross
    deferred = _number(risk.get("unavoidable_deferred_cost_r")); deferred = _number((manager.get("execution_cost_model") or {}).get("deferred_full_close_r")) if deferred is None else deferred
    net = _number(rule.get("cvar_floor_r")); chosen_cvar = _number((policies.get(raw) or {}).get("cvar10_r"))
    eligible_value = rule.get("eligible") if "eligible" in rule else None
    eligible = list(eligible_value or []) if eligible_value is not None else None
    arithmetic_ok = bool(chosen_cvar is not None and net is not None and chosen_cvar >= net - 1e-12)
    eligible_text = "UNAVAILABLE" if eligible is None else (", ".join(eligible) if eligible else "нет")
    lines = [
        (f"Базовый quant-выбор: {raw}. Допустимы по NET CVaR в базовом расчёте: {eligible_text}."
         if manager.get('unified_edge_ensemble') else f"Расчётный выбор: {raw}. Допустимы по NET CVaR: {eligible_text}."),
        f"Gross strategy CVaR floor: {_r(gross)}.",
        f"Unavoidable deferred close cost: {_r(deferred)}.",
        f"Net selection floor: {_r(net)}.",
    ]
    if chosen_cvar is not None and net is not None:
        symbol = ">=" if arithmetic_ok else "<"
        eligible_membership = eligible is not None and raw in eligible
        state = "ELIGIBLE" if arithmetic_ok and eligible_membership else "INELIGIBLE"
        lines.append(f"{raw} CVaR10 net: {_r(chosen_cvar)} {symbol} {_r(net)} → {state}.")
    lines.append(f"Источник gross floor: {_text(risk.get('source'))}. Правило: {_text(risk.get('rule'))}.")

    indifference = _number(rule.get("indifference_band_r"))
    best_expected = _number(rule.get("best_expected_r"))
    raw_expected = _number((policies.get(raw) or {}).get("expected_final_r"))
    if indifference is not None and best_expected is not None and raw_expected is not None:
        gap = max(0.0, best_expected - raw_expected)
        soft = rule.get('combined_edge_soft_weight') or {}
        if soft.get('applied'):
            lines.append(
                f"Лучший исходный Expected {_r(best_expected)}; Expected {raw} {_r(raw_expected)}; разрыв {_r(gap)}. "
                f"Выбор использует мягкое ранжирование edge с зоной безразличия {_r(indifference)}. "
                "Поправка ранжирования не является дополнительным Expected или прибылью.")
        else:
            lines.append(
            f"Зона безразличия Expected: {_r(indifference)}. Лучший Expected {_r(best_expected)}; "
            f"{raw} отстаёт на {_r(gap)}. При разрыве не больше зоны выбирается "
            "наименее вмешивающаяся допустимая политика."
            )

    integrity = snapshot.get("report_integrity") or {}
    tradeoff = manager.get("risk_tradeoff") or integrity.get("risk_tradeoff") or {}; delta = _number(tradeoff.get("expected_delta_vs_hold_r"))
    if delta is not None:
        label = tradeoff.get("expected_delta_label") or "расчётное преимущество над HOLD"
        lines.append(f"{label}: {_r(delta)}; улучшение CVaR10 относительно HOLD: {_r(tradeoff.get('cvar_improvement_vs_hold_r'))}.")
    raw_stability = manager.get("raw_optimizer_stability") or integrity.get("raw_optimizer_stability") or {}
    final_stability = manager.get("stability") or integrity.get("stability") or {}
    selected = rec.get("policy") or raw
    lines.append(
        f"Параметрическая устойчивость сырого {raw}: "
        f"{_count_ratio(raw_stability.get('selected_count'), raw_stability.get('checks'), raw_stability.get('selected_share'))}. "
        f"Финального {selected}: "
        f"{_count_ratio(final_stability.get('selected_count'), final_stability.get('checks'), final_stability.get('selected_share'))}."
    )
    gate = manager.get("gate") or {}
    authority = gate.get("authority_stability") or {}
    source_checks = _number(authority.get("checks"))
    winner_counts = authority.get("winner_counts") or {}
    source_count = _number(winner_counts.get(selected)) if isinstance(winner_counts, dict) else None
    if source_checks is None or source_checks <= 0 or source_count is None:
        lines.append(
            f"Устойчивость к источнику данных для {selected}: UNAVAILABLE "
            "(authority-stability audit не опубликован в snapshot)."
        )
    else:
        lines.append(
            f"Устойчивость к источнику данных для {selected}: "
            f"{int(source_count)}/{int(source_checks)} ({_pct(source_count / source_checks)}); это доля побед при смене источников, а не допустимость overlay."
        )
    overlay = gate.get("degraded_authority_overlay") or {}
    selected_overlay = overlay.get("selected") or {}
    support = selected_overlay.get("support") or {}
    requirements = selected_overlay.get("requirements") or {}
    if selected_overlay:
        lines.append(f"Кандидат deterministic risk-overlay: {selected_overlay.get('policy')}; базовый оптимизатор: {raw}; итоговая команда: {(manager.get('management_decision') or {}).get('policy') or selected}.")
        lines.append(f"Overlay против HOLD на единицу остатка: Expected {_r(selected_overlay.get('expected_delta_vs_hold_r'))}; улучшение CVaR10 {_r(selected_overlay.get('cvar_gain_vs_hold_r'))}; требуется минимум {_r(requirements.get('min_cvar_gain_r'))}.")
        local = support.get("local_support", selected_overlay.get("local_support"))
        source = support.get("source_support", selected_overlay.get("source_support"))
        lines.append(f"Поддержка gate ({support.get('basis') or 'тип не опубликован'}): параметрическая {_pct(local)}, минимум {_pct(requirements.get('min_local_support'))}; источники {_pct(source)}, минимум {_pct(requirements.get('min_source_support'))}. Допустимость в стрессах не означает статистически доказанного преимущества.")
        alternatives = overlay.get("candidate_summary") or {}
        for name, row in alternatives.items():
            if name == selected_overlay.get("policy"):
                continue
            failures = row.get("failed") or []
            failure_labels = {
                "expected_and_cvar": "экономика/CVaR", "total_adverse": "число независимых семей",
                "live_adverse": "число живых семей", "local_support": "параметрические стрессы",
                "source_support": "проверки источников", "not_option_only": "независимое живое подтверждение",
            }
            failures = [failure_labels.get(code, code) for code in failures]
            result = "допустим, но уступил выбранному по utility" if row.get("qualified") else "не прошёл: " + ", ".join(failures)
            lines.append(f"Почему не {name}: {result}; нужны семьи {_text(row.get('required_families'))}, живые {_text(row.get('required_live_families'))}; utility {_score(row.get('utility'))}.")
        lines.append("Utility overlay учитывает Expected, CVaR10, вероятность отдачи 0.50R и долю сокращения; это заданный критерий управления риском, а не доказанный исторический перевес.")

    evidence_summary, total_families, live_families, observed_items = _degraded_evidence_summary(gate)
    if gate.get("status") == "confirmed_degraded_manual":
        families = evidence_summary.get("adverse_families") or []
        family_text = ", ".join(str(item) for item in families) if families else "детали не опубликованы"
        if total_families is not None or live_families is not None:
            total_text = "—" if total_families is None else str(int(total_families))
            live_text = "—" if live_families is None else str(int(live_families))
            item_text = "—" if observed_items is None else str(int(observed_items))
            lines.append(
                f"Degraded-manual подтверждение: независимых adverse families {total_text}; "
                f"live families {live_text}; наблюдаемых adverse metric rows {item_text}; "
                f"семьи: {family_text}."
            )
        else:
            lines.append(
                "Degraded-manual подтверждение сохранено gate, но детальный evidence summary "
                "не опубликован в snapshot; отсутствие деталей после compaction не трактуется как 0."
            )

    decision = manager.get("management_decision") or {}
    auto = gate.get("automatic_execution_allowed") if "automatic_execution_allowed" in gate else None
    manual_pending = bool(
        decision.get("execution_status") == "pending_execution"
        and decision.get("manual_execution_required") is True
        and decision.get("policy") not in (None, "", "HOLD")
    )
    if (manager.get("repeat_intervention_gate") or {}).get("status") == "deferred_no_material_change":
        work_action = "HOLD для обновлённого остатка; повторное вмешательство отложено до существенного нового основания"
    elif manual_pending:
        instruction = (
            decision.get("instruction_ru")
            or rec.get("execution_action_ru")
            or f"{decision.get('policy')} вручную"
        )
        work_action = f"{instruction} (только вручную; автоматическое исполнение запрещено)"
    elif (
        gate.get("status") == "confirmed_degraded_manual"
        and gate.get("working_action_confirmed")
        and selected != "HOLD"
    ):
        work_action = f"{selected} вручную; автоматическое исполнение запрещено"
    elif auto is True:
        work_action = selected
    elif auto is False:
        work_action = "не менять позицию по этому отчёту"
    else:
        work_action = "UNAVAILABLE; fail-safe — не менять позицию по этому отчёту"
    lines.append(f"Итог gate: {_text(gate.get('status'))}. Рабочее действие: {work_action}.")
    return lines


def _quality_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    root = snapshot.get("metric_coverage") or {}
    coverage = root.get("summary") or root
    available_value = _number(coverage.get("available_groups"))
    total_value = _number(coverage.get("total_groups"))
    ratio = _number(coverage.get("coverage_ratio"))
    if ratio is None and available_value is not None and total_value is not None and total_value > 0:
        ratio = available_value / total_value
    coverage_text = (
        f"{int(available_value)}/{int(total_value)}"
        if available_value is not None and total_value is not None else "UNAVAILABLE"
    )
    audit = manager.get("input_audit") or {}
    audit_available = _number(audit.get("available_count"))
    audit_total = _number(audit.get("total_count"))
    audit_text = (
        f"{int(audit_available)}/{int(audit_total)}"
        if audit_available is not None and audit_total is not None else "UNAVAILABLE"
    )
    evidence = manager.get("evidence") or {}
    from .management_contract import decision_reliability
    reliability = decision_reliability(snapshot)
    inputs = manager.get("inputs") or {}
    scope = manager.get("management_model_scope") or {}
    reasons = reliability.get("reasons")
    if isinstance(reasons, list):
        reason_text = "; ".join(str(item) for item in reasons) if reasons else "существенные ограничения не отмечены"
    else:
        reason_text = "не опубликованы"
    lines = [
        f"Покрытие decision metrics: {coverage_text} ({_pct(ratio)}). Input audit: {audit_text}.",
        f"Надёжность расчёта: {_text(reliability.get('level'))}. Цепочка: {_text(inputs.get('chain_status'))}; proxy={_text(inputs.get('proxy_quality'))}.",
        f"Причины: {reason_text}.",
    ]
    availability = snapshot.get("metric_availability_contract") or {}
    if availability:
        lines.append(
            f"Availability contract: {availability.get('contract_version', '—')}; "
            f"missing_is_zero={str(bool(availability.get('missing_is_zero'))).lower()}; "
            f"fabrication_allowed={str(bool(availability.get('fabrication_allowed'))).lower()}."
        )
    if scope.get("indicator_trailing_modelled") is False:
        lines.append("Область модели менеджмента: лестница и БУ учтены; индикаторный трейлинг исключён из Expected/CVaR.")
    lines.append("Наличие значения не означает равный голос: optimizer, gate, context-only и shadow роли остаются раздельными.")
    return lines


def _material_change_lines(manager: dict, key: str) -> list[str]:
    rows = (manager.get("state_change_attribution") or {}).get(key) or []; material = []
    for row in rows:
        delta = _number(row.get("delta"))
        if delta is None or abs(delta) < _MATERIAL_DELTA_EPS: continue
        material.append((abs(delta), row, delta))
    material.sort(key=lambda item: item[0], reverse=True)
    if not material: return ["Материального изменения относительно reference нет."]
    return [f"{row.get('metric')}: {delta:+.5f} vs {row.get('reference')}." for _, row, delta in material[:4]]


def _metric_audit_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}; evidence = manager.get("evidence") or {}; state = manager.get("option_derivative_state") or evidence.get("option_derivative_state") or {}; metrics = state.get("metrics") or {}
    order = ("p_take", "p_stop", "p_no_touch", "barrier_ev", "bop", "q10", "q50", "q90", "width", "h_take", "h_stop", "hazard_log_ratio", "iv", "rv", "vrp", "skew", "term_slope", "gex_force", "gex_stiffness", "distance_to_zero_gamma")
    lines = ["Текущие значения и производные разделены: отсутствующее значение не приравнивается к нулю; fallback/proxy должен быть явно помечен источником и качеством."]
    for name in order:
        row = metrics.get(name)
        if not isinstance(row, dict): continue
        value = _number(row.get("value")); slope = _number(row.get("slope")); acceleration = _number(row.get("acceleration")); current = "UNAVAILABLE" if value is None else f"{value:.6g} {row.get('value_units') or ''}".strip(); derivative = f"slope={slope:.6g} {row.get('slope_units') or ''}".strip() if slope is not None else "slope=UNAVAILABLE"
        if acceleration is not None: derivative += f"; acceleration={acceleration:.6g}"
        lines.append(f"{name}: current={current}; {derivative}; N={_text(row.get('sample_count'))}; span={_text(row.get('time_span_minutes'))}m; confidence={_pct(row.get('confidence'))}; source_quality={_pct(row.get('source_quality'))}.")
    if len(lines) == 1: lines.append("Option derivative metric workspace: UNAVAILABLE; нулевые значения не подставлены.")
    return lines


def _ede_context_lines(snapshot: dict) -> list[str]:
    context = snapshot.get("ede_causal_context") or {}
    if not context:
        return ["EDE causal context отсутствует в этом snapshot."]
    lines = list(context.get("context_lines_ru") or [])
    lines.append(
        f"DATA_MATURITY={context.get('data_maturity', 'INSUFFICIENT_DATA')}; "
        f"EDGE_MATURITY={context.get('edge_maturity', 'INSUFFICIENT_DATA')}.")
    available = [name for name, row in (context.get("families") or {}).items()
                 if row.get("available")]
    lines.append("Доступные causal families: " + (", ".join(available) if available else "нет") + ".")
    authority = context.get("authority") or {}
    lines.append(
        "Authority: production_directional_authority=false; auto_promotion=false; "
        f"may_trigger_exit_or_close={str(bool(authority.get('may_trigger_exit_or_close'))).lower()}.")
    return lines


def _repair_degraded_manual_summary(lines: list[str], snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    gate = manager.get("gate") or {}
    if gate.get("status") != "confirmed_degraded_manual":
        return lines
    evidence, total_families, live_families, observed_items = _degraded_evidence_summary(gate)
    if total_families is None and live_families is None and observed_items is None:
        return lines
    families = evidence.get("adverse_families") or []
    family_text = ", ".join(str(item) for item in families) if families else "детали не опубликованы"
    repaired = []
    for line in lines:
        if "Независимые семьи подтверждений:" in line:
            total_text = "—" if total_families is None else str(int(total_families))
            live_text = "—" if live_families is None else str(int(live_families))
            line = (
                f"Независимые семьи подтверждений: {total_text}; live: {live_text}; "
                f"семьи: {family_text}."
            )
        elif "Отдельных строк метрик:" in line:
            item_text = "—" if observed_items is None else str(int(observed_items))
            line = f"Отдельных adverse строк метрик: {item_text}."
        elif line.startswith("Однонаправленные семьи против удержания:"):
            line = f"Однонаправленные семьи против удержания по observed gate: {family_text}."
        elif line.startswith("Метрики против удержания:") and "нет." in line:
            metrics = evidence.get("observed_metrics") or []
            names = ", ".join(str(row.get("metric")) for row in metrics if isinstance(row, dict))
            count = "—" if observed_items is None else str(int(observed_items))
            line = f"Метрики против удержания: observed gate сохранил {count} строк; {names or 'детали не опубликованы в компактном снимке'}. Значения и пороги не восстановлены из отсутствующих данных."
        elif line.startswith("Однонаправленные семьи в пользу удержания:") and "supportive_families" not in evidence:
            line = "Семьи в пользу удержания: детали не опубликованы в компактном снимке."
        elif line.startswith("Смешанные семьи,") and "mixed_families" not in evidence:
            line = "Смешанные семьи: детали не опубликованы в компактном снимке."
        repaired.append(line)
    return repaired


def normalize_structured_report(text: str, snapshot: dict) -> str:
    if not _structured_contract(snapshot): return text
    lines = text.splitlines()
    _replace_section(lines, "**ЕДИНЫЙ ПЛАН МЕНЕДЖМЕНТА**", _plan_lines(snapshot)); _replace_section(lines, "**ГЕОМЕТРИЯ СДЕЛКИ**", _trade_geometry_lines(snapshot)); _replace_take_stop_body(lines, _take_stop_lines(snapshot)); _replace_section(lines, "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ**", _scenario_geometry_lines(snapshot)); _replace_section(lines, "**ПОЧЕМУ ВЫБРАНО**", _risk_lines(snapshot)); _replace_section(lines, "**КАЧЕСТВО ДАННЫХ**", _quality_lines(snapshot))
    terminal_cancellation = _terminal_cancellation_lines(snapshot)
    if terminal_cancellation is not None:
        _replace_section(lines, "**ГРАНИЦА ОТМЕНЫ**", terminal_cancellation)
    manager = snapshot.get("policy_manager") or {}; _replace_section(lines, "**ЧТО УЛУЧШИЛОСЬ**", _material_change_lines(manager, "what_improved")); _replace_section(lines, "**ЧТО УХУДШИЛОСЬ**", _material_change_lines(manager, "what_deteriorated"))
    lines = _repair_degraded_manual_summary(lines, snapshot)
    lines = [line.replace("AI priority bonus", "диагностический бонус (не определяет выбор)") for line in lines]
    decision = manager.get("management_decision") or {}
    if decision.get("execution_status") == "pending_execution" and decision.get("policy") in {"CLOSE_10", "CLOSE_25", "CLOSE_50"}:
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", [
            "Подтвердите действие только после исполнения у брокера: терминал уменьшит остаток и учтёт зафиксированный результат. Повторное подтверждение того же decision_id не уменьшает позицию второй раз.",
            "Новое сокращение возможно после нового расчёта для обновлённого остатка и прохождения актуального gate; стандартный стоп/БУ и лестница продолжаются для остатка.",
        ])
    lines = [line.replace("Shadow metrics:", "Derived shadow scenario distribution:") for line in lines]
    bounds = _section(lines, "**РАСЧЁТ ПОЛИТИК**")
    if bounds is not None:
        start, _ = bounds
        label = ("Контрольная базовая bridge-модель: общие пути только для HOLD/CLOSE/EXIT. Её Expected отличается от единого сравнительного банка; этот раздел не задаёт итоговую экономику ансамбля."
                 if manager.get("unified_edge_ensemble") else
                 "Base production policy distribution (common execution-MC paths):")
        if start + 1 >= len(lines) or lines[start + 1] != label: lines.insert(start + 1, label)
        if manager.get("unified_edge_ensemble"):
            lines = [line for line in lines if line != "Base production policy distribution (common execution-MC paths):"]
    if not any(line.startswith("**EDE CAUSAL MARKET CONTEXT**") for line in lines): lines.extend(["", "**EDE CAUSAL MARKET CONTEXT** —", *_ede_context_lines(snapshot)])
    if not any(line.startswith("**FULL METRIC AUDIT**") for line in lines): lines.extend(["", "**FULL METRIC AUDIT** —", *_metric_audit_lines(snapshot)])
    from .mathematical_edge import render_math_edge
    math_section = render_math_edge(manager.get('mathematical_edge') or {},
        manager.get('combined_edge_soft_weight'),
        (manager.get('selection_rule') or {}).get('combined_edge_soft_weight'))
    unified = manager.get("unified_edge_ensemble") or {}
    if unified:
        primary = _primary_economics(snapshot)
        if primary:
            _replace_section(lines, "**ПРОВЕРЕННЫЙ ВЫВОД**", [
                f"Действующий план: {decision.get('policy')}; основная модельная экономика единого сравнения на единицу остатка: Expected {_r(primary.get('expected_net_r'))}; CVaR10 {_r(primary.get('cvar10_net_r'))}.",
                "Общий сравнительный банк использует опубликованную модель исполнения; он не воспроизводит скрытые bridge-события базового контроля. Исходные ограничения источников, риска и независимого допуска остаются обязательными. Исполнение у брокера требует отдельного подтверждения.",
            ])
        risk_bounds = _section(lines, "**ПОЧЕМУ ВЫБРАНО**")
        if risk_bounds is not None:
            lines.insert(risk_bounds[0] + 1, "Ниже — проверка базового контроля до ансамбля. Итоговый выбор и его экономика приведены в едином плане и единой таблице кандидатов.")
        close_bounds = _section(lines, "**ЭКОНОМИЧЕСКАЯ БЛИЗОСТЬ ПОЛИТИК**")
        if close_bounds is not None:
            label = "Сравнение базового bridge-контроля, отдельно от основной экономики единого банка."
            if lines[close_bounds[0] + 1:close_bounds[0] + 2] != [label]:
                lines.insert(close_bounds[0] + 1, label)
        math_component = next((row for row in unified.get("components") or []
                               if row.get("component_id") == "mathematical_edge"), {})
        math_section = re.sub(
            r"Мягкий вес базовых политик [^\n]*?общий лимит 40%\.",
            f"Единый ансамбль: математический edge {_pct(math_component.get('nominal_weight'))} номинально → {_pct(math_component.get('effective_weight'))} фактически для допустимых действий.",
            math_section)
    _replace_section(lines, '**МАТЕМАТИЧЕСКИЙ EDGE**', math_section.strip().splitlines()[1:])
    if not any(line.startswith('**МАТЕМАТИЧЕСКИЙ EDGE**') for line in lines):
        lines.extend(['', *math_section.strip().splitlines()])
    unified_lines = render_unified_ensemble_lines(unified)
    if unified_lines:
        _replace_section(lines, "**ЕДИНЫЙ ВЫБОР ДЕЙСТВИЯ**", unified_lines[1:])
        if not any(line.startswith("**ЕДИНЫЙ ВЫБОР ДЕЙСТВИЯ**") for line in lines):
            lines.extend(["", *unified_lines])
    return "\n".join(lines).strip()


def normalize_final_report(text: str, snapshot: dict) -> str:
    return normalize_structured_report(text, snapshot)


def render_policy_report(snapshot: dict) -> str:
    return normalize_structured_report(_BASE_RENDER(snapshot), snapshot)


def request_verdict(snapshot: dict) -> dict:
    result = _BASE_REQUEST(snapshot)
    if not isinstance(result, dict) or not isinstance(result.get("verdict"), str) or not _structured_contract(snapshot): return result
    result = dict(result)
    result["verdict"] = render_policy_report(snapshot) if result.get("model") == "deterministic-policy-fallback" else normalize_structured_report(result["verdict"], snapshot)
    result["report_version"] = REPORT_VERSION
    return result


def _chain(root):
    seen = set(); current = root
    while current is not None and id(current) not in seen:
        seen.add(id(current)); yield current; current = getattr(current, "_impl", None)


for module in _chain(_impl): module.render_policy_report = render_policy_report

globals()["render_policy_report"] = render_policy_report
globals()["request_verdict"] = request_verdict
