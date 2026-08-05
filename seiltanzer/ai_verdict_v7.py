"""Verdict v7: explicit manual degraded actions and strategy-aware HOLD wording."""
from __future__ import annotations

from datetime import datetime

from . import ai_verdict_v6 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
Слабая delayed/proxy цепочка не является абсолютным запретом активного решения.
В режиме degraded_manual разрешены CLOSE_10/25/50 и EXIT для ручного исполнения,
если delayed option family не единственный аргумент, есть независимые live-tape/
order-flow подтверждения и улучшение CVaR существенно относительно потери Expected.
HOLD описывай как отсутствие внепланового вмешательства, а не прогноз роста.
Различай запланированные стратегией ордера лестницы и новые внеплановые ордера.
Показывай экономический разрыв политик, следующий рубеж, его цену и долю остатка.
"""


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


def _price(value) -> str:
    value = _num(value)
    if value is None:
        return "—"
    if abs(value) >= 100:
        return f"{value:,.2f}".replace(",", " ")
    if abs(value) >= 10:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _time(value) -> str:
    value = _num(value)
    if value is None:
        return "время не указано"
    try:
        return datetime.fromtimestamp(value).strftime("%d.%m.%Y %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "время не указано"


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


def _insert_before(lines: list[str], before: str, header: str,
                   body: list[str]) -> None:
    if any(line.startswith(header) for line in lines):
        return
    index = next(
        (i for i, line in enumerate(lines) if line.startswith(before)),
        len(lines),
    )
    lines[index:index] = [header, *body, ""]


def _active_top(manager: dict) -> list[str]:
    gate = manager.get("gate") or {}
    rec = manager.get("recommendation") or {}
    overlay = gate.get("degraded_authority_overlay") or {}
    selected = overlay.get("selected") or {}
    evidence = overlay.get("evidence") or {}
    policy = rec.get("policy") or gate.get("policy") or selected.get("policy") or "—"
    action = rec.get("execution_action_ru") or rec.get("action_ru") or policy
    return [
        f"**ДЕЙСТВИЕ СЕЙЧАС** — {action}. ВЫПОЛНИТЬ ВРУЧНУЮ.",
        (
            "Режим: пониженный авторитет данных. Слабая delayed/proxy цепочка "
            "не блокирует рекомендацию, но автоматическое исполнение запрещено."
        ),
        (
            f"Расчётное действие: {action}; оно подтверждено для ручного "
            "исполнения в degraded-authority режиме."
        ),
        (
            f"Компромисс против HOLD: Expected {_r(selected.get('expected_delta_vs_hold_r'))}; "
            f"улучшение CVaR10 {_r(selected.get('cvar_gain_vs_hold_r'))}; "
            f"живых независимых семей {evidence.get('live_adverse_count', 0)}, "
            f"всего семей {evidence.get('total_adverse_count', 0)}."
        ),
        (
            "Delayed option-proxy не является единственным основанием. Решение "
            "усиливает стандартный менеджмент только когда снижение хвостового "
            "риска оправдывает потерю Expected."
        ),
    ]


def _hold_top(manager: dict) -> list[str]:
    policies = manager.get("policies") or {}
    hold = policies.get("HOLD") or {}
    gate = manager.get("gate") or {}
    stability = manager.get("stability") or {}
    authority = gate.get("authority_stability") or {}
    source_count = (authority.get("winner_counts") or {}).get("HOLD", 0)
    economic = manager.get("economic_indifference") or {}
    nearest = economic.get("nearest_active_policy") or {}
    next_step = manager.get("strategy_next_step") or {}
    fraction = _num(next_step.get("close_fraction_of_current_remainder"))
    next_line = (
        f"Следующая ступень стратегии: {_r(next_step.get('next_rung_r'))} → "
        f"цена {_price(next_step.get('next_rung_price'))}; закроет примерно "
        f"{_pct(fraction)} текущего остатка."
        if next_step.get("next_rung_r") is not None else
        "Новых ступеней лестницы впереди нет."
    )
    return [
        (
            "**ДЕЙСТВИЕ СЕЙЧАС** — НЕ СОВЕРШАТЬ ВНЕПЛАНОВЫХ РУЧНЫХ "
            "ИЗМЕНЕНИЙ. СОХРАНИТЬ ТЕКУЩИЙ СТОП/БУ И ПРЕДУСМОТРЕННЫЕ "
            "СТРАТЕГИЕЙ ОРДЕРА ЛЕСТНИЦЫ."
        ),
        (
            "Смысл HOLD: не найдено достаточного основания вмешиваться сильнее "
            "стратегии. Это не прогноз обязательного продолжения роста."
        ),
        (
            f"Expected net HOLD {_r(hold.get('expected_final_r'))}; CVaR10 "
            f"{_r(hold.get('cvar10_r'))}; устойчивость "
            f"{stability.get('selected_count', 0)}/{stability.get('checks', 0)}; "
            f"источники {source_count}/{authority.get('checks', 0)}."
        ),
        (
            f"Ближайшая активная альтернатива {nearest.get('policy', '—')}: "
            f"разрыв Expected {_r(nearest.get('expected_delta_vs_hold_r'))}; "
            f"изменение CVaR10 {_r(nearest.get('cvar_gain_vs_hold_r'))}. "
            "При малом разрыве HOLD является наименее вмешивающимся tie-break."
        ),
        next_line,
        (
            "Индикаторный трейлинг не моделируется. В количественной модели "
            "учтены текущий стоп/БУ и лестница фиксаций."
        ),
    ]


def _economic_lines(manager: dict) -> list[str]:
    economic = manager.get("economic_indifference") or {}
    nearest = economic.get("nearest_active_policy") or {}
    exit_row = economic.get("exit_comparison") or {}
    lines = [
        f"Зона безразличия Expected: {_r(economic.get('indifference_band_r'))}.",
        (
            f"Ближайшая активная политика: {nearest.get('policy', '—')}; "
            f"Expected против HOLD {_r(nearest.get('expected_delta_vs_hold_r'))}; "
            f"CVaR10 против HOLD {_r(nearest.get('cvar_gain_vs_hold_r'))}."
        ),
        (
            f"Полный EXIT: Expected против HOLD "
            f"{_r(exit_row.get('expected_delta_vs_hold_r'))}; CVaR10 против HOLD "
            f"{_r(exit_row.get('cvar_gain_vs_hold_r'))}."
        ),
    ]
    if economic.get("policies_economically_close"):
        lines.append(
            "Политики экономически близки по Expected. Поэтому HOLD не считается "
            "сильным направленным сигналом, а active risk-overlay может выбрать "
            "сокращение при существенном выигрыше CVaR и живых подтверждениях."
        )
    return lines


def _degraded_rules(manager: dict) -> list[str]:
    reqs = ((manager.get("decision_requirements") or {})
            .get("degraded_manual_policies") or {})
    labels = {
        "CLOSE_10": "закрытие 10%",
        "CLOSE_25": "закрытие 25%",
        "CLOSE_50": "закрытие 50%",
        "EXIT": "полный выход",
    }
    lines = [
        "Низкая надёжность больше не является абсолютным запретом. Она переводит "
        "CLOSE/EXIT в ручной degraded-authority режим.",
        "Delayed option_distribution может дать максимум одну семью и никогда не "
        "разрешает действие без live-tape/order-flow/strategy-filter подтверждения.",
    ]
    for policy in ("CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        row = reqs.get(policy) or {}
        if not row:
            continue
        lines.append(
            f"{policy} ({labels[policy]}): допустимая потеря Expected до "
            f"{_r(row.get('max_expected_sacrifice_r'))}; минимальное улучшение "
            f"CVaR10 {_r(row.get('min_cvar_gain_r'))}; семей всего/live не меньше "
            f"{row.get('min_total_adverse_families')}/"
            f"{row.get('min_live_adverse_families')}; support local/source "
            f"{_pct(row.get('min_local_support'))}/"
            f"{_pct(row.get('min_source_support'))}."
        )
    lines.append(
        "Аварийный EXIT разрешён и при более слабой source-поддержке, если HOLD "
        "нарушает hard CVaR floor минимум на 0.15R и есть живое плюс ещё одно "
        "независимое подтверждение."
    )
    return lines


def _audit_lines(manager: dict) -> list[str]:
    audit = manager.get("input_audit") or {}
    rows = audit.get("rows") or {}
    labels = {
        "instrument_price": "Цена инструмента",
        "option_proxy_price": "Цена option-proxy",
        "option_chain": "Опционная цепочка",
        "volatility_indices": "Индексы волатильности",
        "atr_regime_vrp": "ATR, режим и VRP",
        "levels_and_orderflow": "Уровни и order-flow",
        "cross_asset_correlation": "Межрыночные корреляции",
        "oi_gex_strike_landscape": "OI/GEX и strike landscape",
        "strategy_filters": "Фильтры сетапа",
        "ladder_and_breakeven": "Лестница и БУ",
    }
    result = [
        f"Снимок аудита: {audit.get('snapshot_utc') or 'время не указано'}. "
        f"Доступно {audit.get('available_count', 0)}/{audit.get('total_count', 0)} групп."
    ]
    for key, label in labels.items():
        row = rows.get(key) or {}
        details = ["есть" if row.get("available") else "нет"]
        if row.get("symbol"):
            details.append(f"символ {row.get('symbol')}")
        if _num(row.get("value")) is not None:
            details.append(f"значение {_price(row.get('value'))}")
        if row.get("status"):
            details.append(f"статус {row.get('status')}")
        if row.get("source"):
            details.append(f"источник {row.get('source')}")
        if _num(row.get("age_sec")) is not None:
            details.append(f"возраст {_num(row.get('age_sec')):.1f} сек")
        result.append(f"{label}: " + "; ".join(details) + f"; роль {row.get('role', '—')}.")
        if key == "volatility_indices":
            for item in row.get("items") or []:
                result.append(
                    f"  {item.get('symbol')}: {_price(item.get('value'))}; "
                    f"{item.get('status') or 'статус —'}; "
                    f"возраст {_num(item.get('age_sec')):.1f} сек."
                    if _num(item.get("age_sec")) is not None else
                    f"  {item.get('symbol')}: {_price(item.get('value'))}; "
                    f"{item.get('status') or 'статус —'}."
                )
    result.append(
        "Наличие значения не означает равный голос: optimizer, gate и context-only "
        "роли остаются раздельными."
    )
    return result


def render_policy_report(snapshot: dict) -> str:
    manager = snapshot.get("policy_manager") or {}
    gate = manager.get("gate") or {}
    rec = manager.get("recommendation") or {}
    lines = _BASE_RENDER(snapshot).splitlines()

    status = gate.get("status")
    if status == "confirmed_degraded_manual":
        first_section = next(
            (i for i in range(1, len(lines)) if lines[i].startswith("**")),
            len(lines),
        )
        lines = _active_top(manager) + [""] + lines[first_section:]
        selected = rec.get("policy") or gate.get("policy")
        if selected == "EXIT":
            after = [
                "После ручного полного выхода позиция закрыта; дальнейший стоп/БУ "
                "и ступени этой сделки не применяются."
            ]
        else:
            after = [
                f"После ручного {selected} сохранить текущий стоп/БУ и "
                "предусмотренные стратегией ступени для оставшегося объёма."
            ]
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", after)
    elif status == "confirmed_hold" and rec.get("policy") == "HOLD":
        first_section = next(
            (i for i in range(1, len(lines)) if lines[i].startswith("**")),
            len(lines),
        )
        lines = _hold_top(manager) + [""] + lines[first_section:]
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", [
            "Нового внепланового исполнения нет. Сохранить текущий стоп/БУ и "
            "предусмотренные стратегией ордера лестницы."
        ])

    _insert_before(
        lines, "**ПОЧЕМУ ВЫБРАНО**", "**ЭКОНОМИЧЕСКАЯ БЛИЗОСТЬ ПОЛИТИК** —",
        _economic_lines(manager),
    )
    _replace_section(
        lines, "**ЧТО ДОЛЖНО ИЗМЕНИТЬ РЕШЕНИЕ**", _degraded_rules(manager)
    )
    _replace_section(
        lines, "**КАКИЕ ДАННЫЕ РЕАЛЬНО УЧТЕНЫ**", _audit_lines(manager)
    )
    return "\n".join(lines).strip()


# request_verdict resolves these globals in compatibility layers.
for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._base,
):
    module.SYSTEM_PROMPT = SYSTEM_PROMPT
    module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
