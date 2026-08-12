"""Verdict v19: structured report integrity over the established v18 decision.

V19 does not change policy selection, execution authority, simulations or model
scores.  It replaces only report sections that v15-v18 could mis-render by
parsing already-formatted numbers or by reading a compacted duplicate field.
"""
from __future__ import annotations

from typing import Any

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


def _prob(value: Any, count: Any, total: Any) -> str:
    probability = _number(value)
    n = int(_number(total) or 0)
    k = int(_number(count) or 0)
    if probability is None:
        return "—"
    if k == 0 and n:
        return f"0 наблюдений из {n} (<{100 / n:.2f}%)"
    return f"{probability * 100:.1f}% ({k}/{n})" if n else _pct(probability)


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
    lines[start + 1:end] = [*body, ""]


def _plan_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    arbiter = manager.get("management_arbiter") or {}
    shadow = manager.get("shadow_policy_contract") or {}
    rec = manager.get("recommendation") or {}
    authority = decision.get("authority") or arbiter.get("winner") or "STRATEGY"
    production = decision.get("policy") or rec.get("policy") or "—"
    model_policy = (decision.get("model_policy") or shadow.get("new_candidate_policy")
                    or rec.get("raw_optimizer_policy") or production)
    return [
        f"Авторитет плана: {authority}; production policy: {production}; "
        f"shadow/model candidate: {model_policy}.",
        f"Статус исполнения: {decision.get('execution_status', '—')}; "
        f"continuity={decision.get('continuity', '—')}.",
        f"Новая доля закрытия текущего остатка: "
        f"{float(_number(decision.get('incremental_close_fraction')) or 0.0) * 100:.1f}%; "
        f"остаток после действия: "
        f"{float(_number(decision.get('remaining_fraction_after_action')) or 1.0) * 100:.1f}%.",
        (
            f"Арбитражный счёт: стратегия {float(_number(arbiter.get('strategy_score')) or 0):+.3f}; "
            f"ИИ до приоритета {float(_number(arbiter.get('ai_score_before_priority')) or 0):+.3f}; "
            f"ИИ после приоритета {float(_number(arbiter.get('ai_score_after_priority')) or 0):+.3f}."
        ),
        "Приоритет ИИ действует только после evidence/CVaR/stress gate; "
        "production authority этим отчётом не расширяется.",
    ]


def _trade_geometry_lines(snapshot: dict) -> list[str]:
    g = snapshot.get("trade_geometry") or {}
    position = snapshot.get("position_state") or {}
    return [
        f"Цена сейчас: {g.get('current')}; ENTRY: {g.get('entry')}.",
        f"Исходный STOP: {g.get('original_stop')}.",
        f"Активный риск-барьер: {g.get('active_risk_barrier')} · "
        f"{g.get('active_risk_barrier_type')}.",
        f"FINAL TAKE: {g.get('final_take')}.",
        f"CURRENT R: {_r(g.get('current_r'))}; "
        f"R до активного barrier: {_r(g.get('r_to_active_stop'))}; "
        f"R до FINAL TAKE: {_r(g.get('r_to_final_take'))}.",
        f"Остаток позиции: {_pct(position.get('remaining_position_fraction'))}; "
        f"уже зафиксировано: {_pct(position.get('realized_position_fraction'))}.",
    ]


def _take_stop_lines(snapshot: dict) -> list[str]:
    g = snapshot.get("trade_geometry") or {}
    take = _number(g.get("take_first"))
    stop = _number(g.get("stop_or_be_first"))
    no_touch = _number(g.get("no_touch"))
    p50 = _number(g.get("p50_resolution_minutes"))
    if take is None or stop is None or no_touch is None:
        return [
            "Authoritative execution-MC TAKE vs active STOP: UNAVAILABLE.",
            "Причина: insufficient authoritative execution-MC data.",
            "Ниже scenario-path geometry относится к отдельному контракту "
            "ближайшей ступени и не подменяет вероятность FINAL TAKE.",
            "Risk-neutral Q и physical calibrated P shadow публикуются отдельно.",
        ]
    p50_text = f"{p50:.0f} мин" if p50 is not None else "за горизонтом / не определена"
    return [
        f"TAKE раньше активного risk barrier: {_pct(take)}; "
        f"STOP/BE раньше TAKE: {_pct(stop)}; NO TOUCH: {_pct(no_touch)}.",
        f"P50 развязки: {p50_text}. Risk-neutral Q и physical calibrated P shadow "
        "публикуются отдельно.",
    ]


def _scenario_geometry_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    g = manager.get("scenario_geometry") or {}
    n = int(_number(g.get("scenario_count")) or 0)
    rung = _number(g.get("next_rung_r"))
    lines = [
        f"Один набор из {n or '—'} путей. "
        f"Ближайшая ступень {_r(rung)} раньше стопа: "
        f"{_prob(g.get('p_next_rung_before_stop'), g.get('rung_first_count'), n)}. "
        f"Стоп раньше ближайшей ступени: "
        f"{_prob(g.get('p_stop_before_next_rung'), g.get('stop_first_count'), n)}."
    ]
    barrier = ((manager.get("evidence") or {}).get("option_barrier") or {})
    p_take = _number(barrier.get("p_take"))
    p_stop = _number(barrier.get("p_stop"))
    no_touch = _number(barrier.get("no_touch"))
    final_take = _number((manager.get("inputs") or {}).get("T"))
    if p_take is not None and final_take is not None:
        pieces = [f"По опционной barrier-модели финальный тейк {_r(final_take)} раньше стопа: {_pct(p_take)}"]
        if p_stop is not None:
            pieces.append(f"стоп раньше финального тейка: {_pct(p_stop)}")
        if no_touch is not None:
            pieces.append(f"ни один барьер не достигнут: {_pct(no_touch)}")
        lines.append("; ".join(pieces) + ".")
    lines.append(
        "За полный горизонт ни рубеж, ни стоп не достигнуты: "
        f"{_prob(g.get('p_unresolved_full_horizon'), g.get('unresolved_count'), n)}"
        + (f"; горизонт {float(g.get('full_horizon_minutes')):.0f} мин."
           if _number(g.get("full_horizon_minutes")) is not None else ".")
    )
    hour = (g.get("no_event_windows") or {}).get("60m") or {}
    if hour:
        lines.append(
            f"За первые 60 минут событие произошло в {hour.get('events', 0)} из "
            f"{hour.get('scenarios', n)} сценариев; NO-EVENT "
            f"{_prob(hour.get('no_event_probability'), hour.get('no_event_count'), hour.get('scenarios', n))}."
        )
    mean_event = _number(g.get("mean_event_minutes_given_resolved"))
    if mean_event is not None:
        lines.append(
            f"Среднее время до события только среди разрешившихся сценариев: "
            f"{mean_event:.1f} мин. ({int(_number(g.get('resolved_count')) or 0)}/{n})."
        )
    return lines


def _risk_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    risk = manager.get("risk_constraint") or {}
    rule = manager.get("selection_rule") or {}
    rec = manager.get("recommendation") or {}
    policies = manager.get("policies") or {}
    raw = rec.get("raw_optimizer_policy") or (manager.get("gate") or {}).get("raw_policy") or rec.get("policy") or "—"
    gross = _number(risk.get("gross_cvar_floor_r"))
    if gross is None:
        gross = _number(risk.get("cvar_floor_r"))
    deferred = _number(risk.get("unavoidable_deferred_cost_r"))
    if deferred is None:
        deferred = _number((manager.get("execution_cost_model") or {}).get("deferred_full_close_r"))
    net = _number(rule.get("cvar_floor_r"))
    chosen_cvar = _number((policies.get(raw) or {}).get("cvar10_r"))
    eligible = list(rule.get("eligible") or [])
    arithmetic_ok = bool(chosen_cvar is not None and net is not None and chosen_cvar >= net - 1e-12)
    lines = [
        f"Расчётный выбор: {raw}. Допустимы по NET CVaR: "
        f"{', '.join(eligible) if eligible else 'нет'}.",
        f"Gross strategy CVaR floor: {_r(gross)}.",
        f"Unavoidable deferred close cost: {_r(deferred)}.",
        f"Net selection floor: {_r(net)}.",
    ]
    if chosen_cvar is not None and net is not None:
        symbol = ">=" if arithmetic_ok else "<"
        state = "ELIGIBLE" if arithmetic_ok and raw in eligible else "INELIGIBLE"
        lines.append(
            f"{raw} CVaR10 net: {_r(chosen_cvar)} {symbol} {_r(net)} → {state}."
        )
    lines.append(
        f"Источник gross floor: {risk.get('source', 'не указан')}. "
        f"Правило: {risk.get('rule', '—')}."
    )
    tradeoff = manager.get("risk_tradeoff") or {}
    delta = _number(tradeoff.get("expected_delta_vs_hold_r"))
    if delta is not None:
        lines.append(
            f"Расчётное преимущество над HOLD: {_r(delta)}; улучшение CVaR10: "
            f"{_r(tradeoff.get('cvar_improvement_vs_hold_r'))}."
        )
    raw_stability = manager.get("raw_optimizer_stability") or {}
    final_stability = manager.get("stability") or {}
    selected = rec.get("policy") or raw
    lines.append(
        f"Параметрическая устойчивость сырого {raw}: "
        f"{raw_stability.get('selected_count', 0)}/{raw_stability.get('checks', 0)} "
        f"({_pct(raw_stability.get('selected_share'))}). "
        f"Финального {selected}: {final_stability.get('selected_count', 0)}/"
        f"{final_stability.get('checks', 0)} ({_pct(final_stability.get('selected_share'))})."
    )
    authority = (manager.get("gate") or {}).get("authority_stability") or {}
    source_count = (authority.get("winner_counts") or {}).get(selected, 0)
    lines.append(
        f"Устойчивость к источнику данных для {selected}: {source_count}/"
        f"{authority.get('checks', 0)} ({_pct((manager.get('gate') or {}).get('source_stability_share'))})."
    )
    lines.append(
        f"Итог gate: {(manager.get('gate') or {}).get('status', '—')}. "
        f"Рабочее действие: {selected if (manager.get('gate') or {}).get('automatic_execution_allowed') else 'не менять позицию по этому отчёту'}."
    )
    return lines


def _quality_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    root = snapshot.get("metric_coverage") or {}
    coverage = root.get("summary") or root
    available = int(_number(coverage.get("available_groups")) or 0)
    total = int(_number(coverage.get("total_groups")) or 0)
    ratio = _number(coverage.get("coverage_ratio"))
    audit = manager.get("input_audit") or {}
    evidence = manager.get("evidence") or {}
    reliability = ((evidence.get("data_quality") or {}).get("reliability") or
                   coverage.get("reliability") or {})
    inputs = manager.get("inputs") or {}
    return [
        f"Покрытие decision metrics: {available}/{total} "
        f"({_pct(ratio) if ratio is not None else '—'}). "
        f"Input audit: {audit.get('available_count', 0)}/{audit.get('total_count', 0)}.",
        f"Надёжность расчёта: {reliability.get('level', 'не определена')}. "
        f"Цепочка: {inputs.get('chain_status')}; "
        f"proxy={inputs.get('proxy_quality')}.",
        "Причины: " + "; ".join(
            reliability.get("reasons") or ["существенные ограничения не отмечены"]
        ) + ".",
        "Наличие значения не означает равный голос: optimizer, gate, context-only "
        "и shadow роли остаются раздельными.",
    ]


def _material_change_lines(manager: dict, key: str) -> list[str]:
    attribution = manager.get("state_change_attribution") or {}
    rows = attribution.get(key) or []
    material = []
    for row in rows:
        delta = _number(row.get("delta"))
        if delta is None or abs(delta) < _MATERIAL_DELTA_EPS:
            continue
        material.append((abs(delta), row, delta))
    material.sort(key=lambda item: item[0], reverse=True)
    if not material:
        return ["Материального изменения относительно reference нет."]
    return [
        f"{row.get('metric')}: {delta:+.5f} vs {row.get('reference')}."
        for _, row, delta in material[:4]
    ]


def _metric_audit_lines(snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    evidence = manager.get("evidence") or {}
    state = manager.get("option_derivative_state") or evidence.get("option_derivative_state") or {}
    metrics = state.get("metrics") or {}
    order = (
        "p_take", "p_stop", "p_no_touch", "barrier_ev", "bop", "q10", "q50", "q90",
        "width", "h_take", "h_stop", "hazard_log_ratio", "iv", "rv", "vrp", "skew",
        "term_slope", "gex_force", "gex_stiffness", "distance_to_zero_gamma",
    )
    lines = [
        "Текущие значения и производные разделены: derivative=UNAVAILABLE не означает, "
        "что current value отсутствует."
    ]
    for name in order:
        row = metrics.get(name)
        if not isinstance(row, dict):
            continue
        value = _number(row.get("value"))
        slope = _number(row.get("slope"))
        acceleration = _number(row.get("acceleration"))
        current = "—" if value is None else f"{value:.6g} {row.get('value_units') or ''}".strip()
        derivative = (
            f"slope={slope:.6g} {row.get('slope_units') or ''}".strip()
            if slope is not None else "slope=UNAVAILABLE"
        )
        if acceleration is not None:
            derivative += f"; acceleration={acceleration:.6g}"
        lines.append(
            f"{name}: current={current}; {derivative}; N={row.get('sample_count', '—')}; "
            f"span={row.get('time_span_minutes', '—')}m; "
            f"confidence={_pct(row.get('confidence'))}; source_quality={_pct(row.get('source_quality'))}."
        )
    if len(lines) == 1:
        lines.append("Option derivative metric workspace отсутствует в этом snapshot.")
    return lines


def normalize_final_report(text: str, snapshot: dict) -> str:
    lines = text.splitlines()
    _replace_section(lines, "**ЕДИНЫЙ ПЛАН МЕНЕДЖМЕНТА**", _plan_lines(snapshot))
    _replace_section(lines, "**ГЕОМЕТРИЯ СДЕЛКИ**", _trade_geometry_lines(snapshot))
    _replace_section(lines, "**TAKE vs STOP/BE", _take_stop_lines(snapshot))
    _replace_section(lines, "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ**", _scenario_geometry_lines(snapshot))
    _replace_section(lines, "**ПОЧЕМУ ВЫБРАНО**", _risk_lines(snapshot))
    _replace_section(lines, "**КАЧЕСТВО ДАННЫХ**", _quality_lines(snapshot))
    manager = snapshot.get("policy_manager") or {}
    _replace_section(lines, "**ЧТО УЛУЧШИЛОСЬ**", _material_change_lines(manager, "what_improved"))
    _replace_section(lines, "**ЧТО УХУДШИЛОСЬ**", _material_change_lines(manager, "what_deteriorated"))
    lines = [
        line.replace("Shadow metrics:", "Derived shadow scenario distribution:")
        for line in lines
    ]
    # The base policy table and derived scenario values are intentionally labelled
    # as different evaluation layers so equal-looking metrics are not conflated.
    bounds = _section(lines, "**РАСЧЁТ ПОЛИТИК**")
    if bounds is not None:
        start, _ = bounds
        label = "Base production policy distribution (common execution-MC paths):"
        if start + 1 >= len(lines) or lines[start + 1] != label:
            lines.insert(start + 1, label)
    if not any(line.startswith("**FULL METRIC AUDIT**") for line in lines):
        lines.extend(["", "**FULL METRIC AUDIT** —", *_metric_audit_lines(snapshot)])
    return "\n".join(lines).strip()


def render_policy_report(snapshot: dict) -> str:
    return normalize_final_report(_BASE_RENDER(snapshot), snapshot)


def request_verdict(snapshot: dict) -> dict:
    result = _BASE_REQUEST(snapshot)
    if not isinstance(result, dict) or not isinstance(result.get("verdict"), str):
        return result
    result = dict(result)
    result["verdict"] = normalize_final_report(result["verdict"], snapshot)
    if result.get("model") == "deterministic-policy-fallback":
        result["report_version"] = REPORT_VERSION
    return result


def _chain(root):
    seen = set()
    current = root
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "_impl", None)


for module in _chain(_impl):
    module.render_policy_report = render_policy_report

globals()["render_policy_report"] = render_policy_report
globals()["request_verdict"] = request_verdict
