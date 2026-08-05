"""Verdict v6: actionable HOLD, exact price triggers and input provenance."""
from __future__ import annotations

from datetime import datetime

from . import ai_verdict_v5 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
HOLD — это подтверждённое отсутствие нового ордера, а не неподтверждённое исполнение.
Низкая надёжность данных запрещает активное CLOSE/EXIT, но не отменяет устойчивый HOLD.
Для ±0.15R всегда показывай r и цену. Показывай время следующей попытки опроса
цепочки и не обещай live/direct качество. Индикаторный трейлинг не моделируется:
не включай его в Expected/CVaR и не выдавай по нему инструкций.
Разделяй данные на optimizer, gate и context-only.
"""


def _num(value):
    try:
        out = float(value)
        return out if out == out else None
    except (TypeError, ValueError):
        return None


def _fmt_r(value) -> str:
    value = _num(value)
    return "—" if value is None else f"{value:+.3f}R"


def _fmt_price(value) -> str:
    value = _num(value)
    if value is None:
        return "—"
    if abs(value) >= 100:
        return f"{value:,.2f}".replace(",", " ")
    if abs(value) >= 10:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _fmt_local_time(value: str | None) -> str:
    if not value:
        return "не определено"
    try:
        dt = datetime.fromisoformat(value)
        zone = dt.tzname() or ""
        return dt.strftime("%d.%m.%Y %H:%M:%S") + (f" {zone}" if zone else "")
    except (TypeError, ValueError):
        return value


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


def _insert_section_before(lines: list[str], before_header: str,
                           header: str, body: list[str]) -> None:
    if any(line.startswith(header) for line in lines):
        return
    index = next(
        (i for i, line in enumerate(lines) if line.startswith(before_header)),
        len(lines),
    )
    lines[index:index] = [header, *body, ""]


def _hold_top(manager: dict) -> list[str]:
    rec = manager.get("recommendation") or {}
    gate = manager.get("gate") or {}
    policies = manager.get("policies") or {}
    hold = policies.get("HOLD") or {}
    stability = manager.get("stability") or {}
    source = gate.get("authority_stability") or {}
    source_count = (source.get("winner_counts") or {}).get("HOLD", 0)
    adverse = (manager.get("evidence") or {}).get(
        "adverse_confirmation_families") or []
    reliability = (((manager.get("evidence") or {}).get("data_quality") or {})
                   .get("reliability") or {})
    return [
        "**ДЕЙСТВИЕ СЕЙЧАС** — УДЕРЖИВАТЬ ТЕКУЩИЙ ОСТАТОК; "
        "НЕ СОКРАЩАТЬ ПОЗИЦИЮ И НЕ ВЫСТАВЛЯТЬ НОВЫХ ОРДЕРОВ.",
        "Статус: HOLD подтверждён как отсутствие нового исполнения — "
        "его не требуется «исполнять автоматически».",
        (
            f"Основание: Expected net HOLD {_fmt_r(hold.get('expected_final_r'))}; "
            f"CVaR10 {_fmt_r(hold.get('cvar10_r'))}; параметрическая устойчивость "
            f"{stability.get('selected_count', 0)}/{stability.get('checks', 0)}; "
            f"устойчивость к источникам {source_count}/{source.get('checks', 0)}; "
            f"независимых аргументов за сокращение {len(adverse)}."
        ),
        (
            f"Ограничение данных: надёжность {reliability.get('level', 'не определена')}. "
            "Это запрещает новое активное CLOSE/EXIT, но не отменяет подтверждённый HOLD."
        ),
        (
            "Сопровождение в модели: текущий стоп/БУ и лестница фиксаций. "
            "Индикаторный трейлинг не моделируется и не влияет на Expected/CVaR."
        ),
    ]


def _trigger_lines(manager: dict) -> list[str]:
    triggers = manager.get("recalculation_triggers") or {}
    lower = triggers.get("minus_0_15_r") or {}
    upper = triggers.get("plus_0_15_r") or {}
    chain = triggers.get("chain_refresh") or {}
    seconds = _num(chain.get("seconds_until_attempt"))
    if chain.get("overdue"):
        countdown = "опрос уже должен выполняться при ближайшем цикле"
    elif seconds is not None:
        countdown = f"примерно через {max(0, round(seconds / 60))} мин"
    else:
        countdown = "время ожидания не определено"
    return [
        (
            f"Движение вниз на 0.15R: r={_fmt_r(lower.get('r'))} → "
            f"цена {_fmt_price(lower.get('price'))}."
        ),
        (
            f"Движение вверх на 0.15R: r={_fmt_r(upper.get('r'))} → "
            f"цена {_fmt_price(upper.get('price'))}."
        ),
        (
            "Следующая попытка обновления опционной цепочки: "
            f"{_fmt_local_time(chain.get('next_attempt_local'))} "
            f"({countdown}; интервал опроса "
            f"{round((_num(chain.get('poll_interval_sec')) or 600) / 60)} мин)."
        ),
        (
            "Текущий/ожидаемый источник: "
            f"{chain.get('current_source') or 'не указан'}, статус "
            f"{chain.get('current_status') or 'не указан'}. Следующий опрос не "
            "гарантирует live/direct: источник может снова вернуть delayed/proxy данные."
        ),
        (
            "Внеплановый пересчёт: касание следующего рубежа, стопа или БУ; "
            "изменение модели издержек."
        ),
    ]


def _audit_lines(manager: dict) -> list[str]:
    audit = manager.get("input_audit") or {}
    rows = audit.get("rows") or {}
    labels = {
        "instrument_price": "Цена инструмента",
        "option_proxy_price": "Цена option-proxy",
        "option_chain": "Опционная цепочка",
        "volatility_indices": "VIX/GVZ и другие индексы волатильности",
        "atr_regime_vrp": "ATR, режим и VRP",
        "levels_and_orderflow": "Уровни и order-flow",
        "cross_asset_correlation": "Межрыночные корреляции",
        "oi_gex_strike_landscape": "OI/GEX и strike landscape",
        "strategy_filters": "Фильтры сетапа",
        "ladder_and_breakeven": "Лестница и БУ",
    }
    role_ru = {
        "optimizer_and_geometry": "входит в оптимизатор и геометрию",
        "option_moneyness_mapping": "переносит moneyness опционного proxy",
        "option_anchor_optimizer_and_evidence": "опционный якорь оптимизатора и evidence",
        "strategy_filters_and_context_when_applicable": "фильтры/контекст, когда применимо",
        "evidence_and_regime_context": "evidence и режимный контекст",
        "evidence_gate": "участвует в gate подтверждений",
        "uncertainty_and_regime_gate": "участвует в gate неопределённости",
        "context_only": "только контекст, без самостоятельного голоса",
        "evidence_gate_when_setup_uses_filter": "gate только для применимых фильтров сетапа",
        "strategy_baseline_optimizer": "базовый менеджмент в оптимизаторе",
    }
    result = [
        f"Доступно {audit.get('available_count', 0)}/{audit.get('total_count', 0)} "
        "групп аудита. Доступность не означает одинаковый вес."
    ]
    for key, label in labels.items():
        row = rows.get(key) or {}
        state = "есть" if row.get("available") else "нет"
        details = []
        if row.get("status"):
            details.append(f"статус {row.get('status')}")
        if row.get("source"):
            details.append(f"источник {row.get('source')}")
        role = role_ru.get(row.get("role"), row.get("role") or "роль не задана")
        suffix = "; " + ", ".join(details) if details else ""
        result.append(f"{label}: {state}{suffix}; роль — {role}.")
    result.append(
        "Важно: отображаемые котировки не суммируются как равные голоса. "
        "Живая цена и option mapping двигают расчёт; VIX/GVZ, корреляции и "
        "OI/GEX ограничивают или объясняют его."
    )
    return result


def render_policy_report(snapshot: dict) -> str:
    manager = snapshot.get("policy_manager") or {}
    gate = manager.get("gate") or {}
    rec = manager.get("recommendation") or {}
    report = _BASE_RENDER(snapshot)
    lines = report.splitlines()

    confirmed_hold = bool(
        (rec.get("policy") == "HOLD")
        and gate.get("working_action_confirmed")
        and gate.get("status") == "confirmed_hold"
    )
    if confirmed_hold:
        first_section = next(
            (i for i in range(1, len(lines)) if lines[i].startswith("**")),
            len(lines),
        )
        lines = _hold_top(manager) + [""] + lines[first_section:]
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", [
            "Нового исполнения нет. Продолжать сопровождение текущего остатка "
            "по действующему стопу/БУ и лестнице фиксаций. Индикаторный "
            "трейлинг находится вне количественной модели ИИ."
        ])
        _replace_section(lines, "**ГРАНИЦА ОТМЕНЫ**", [
            "Для HOLD нет границы отмены до исполнения, потому что нового "
            "исполнения нет. Решение пересматривается на контрольных ценах ниже, "
            "при новом снимке цепочки или событии стратегии."
        ])

    _replace_section(lines, "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ**", _trigger_lines(manager))
    _insert_section_before(
        lines, "**КАЧЕСТВО ДАННЫХ**", "**КАКИЕ ДАННЫЕ РЕАЛЬНО УЧТЕНЫ** —",
        _audit_lines(manager),
    )

    scope = manager.get("management_model_scope") or {}
    if scope and not any("Индикаторный трейлинг исключён" in line for line in lines):
        bounds = _section(lines, "**КАЧЕСТВО ДАННЫХ**")
        if bounds:
            _, end = bounds
            insert_at = end
            while insert_at > 0 and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines.insert(
                insert_at,
                "Область модели менеджмента: лестница и БУ учтены; "
                "индикаторный трейлинг исключён из Expected/CVaR.",
            )

    replacements = {
        "active stop/BE/trailing": "current stop/BE",
        "стоп, БУ/trailing и лестницу": "стоп/БУ и лестницу",
        "стоп, БУ/trailing": "стоп/БУ",
        "касание рубежа, стопа, БУ/trailing": "касание рубежа, стопа или БУ",
        "остаток тралом": "остаток по ручному сопровождению вне модели ИИ",
    }
    text = "\n".join(lines)
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.strip()


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._base,
):
    module.SYSTEM_PROMPT = SYSTEM_PROMPT
    module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
