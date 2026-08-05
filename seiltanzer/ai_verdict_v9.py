"""Verdict v9: one authoritative sequential management plan."""
from __future__ import annotations

from . import ai_verdict_v7 as _impl
from .ai_policy import resolve_management_sequence


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
_BASE_BUILD_SNAPSHOT = _impl.build_snapshot
_BASE_REQUEST_VERDICT = _impl.request_verdict
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
В policy_manager.management_decision находится единственный действующий план сделки.
Он имеет приоритет над отдельными строками strategy и risk-overlay. Не выдавай две
параллельные команды. Если execution_status=pending_execution — повтори точное
instruction_ru. Если continuity=continue_same_pending_decision, подчеркни, что это
прежнее действующее решение, а не дополнительное закрытие. Если strategy_active —
разрешены предусмотренные стратегией ступени, но запрещены новые внеплановые ордера.
Обязательно упомяни предыдущее решение и continuity.
"""


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


def _previous_text(decision: dict) -> str:
    previous = decision.get("previous") or {}
    if not previous:
        return "Предыдущего решения ИИ по этой сделке нет."
    return (
        f"Предыдущее решение: {previous.get('policy') or '—'}; "
        f"статус {previous.get('execution_status') or '—'}; "
        f"decision_id={previous.get('decision_id') or '—'}."
    )


def _management_top(manager: dict) -> list[str]:
    decision = manager.get("management_decision") or {}
    status = decision.get("execution_status")
    instruction = decision.get("instruction_ru") or "ПЛАН НЕ ОПРЕДЕЛЁН"
    arbiter = manager.get("management_arbiter") or {}
    previous = _previous_text(decision)
    continuity = decision.get("continuity")

    if status == "pending_execution":
        repeated = continuity == "continue_same_pending_decision"
        return [
            (
                f"**ДЕЙСТВИЕ СЕЙЧАС** — {instruction}."
                if not repeated else
                f"**ДЕЙСТВУЮЩЕЕ РЕШЕНИЕ ИИ** — {instruction}."
            ),
            (
                "Это прежнее действующее решение, а не команда закрыть ещё такой "
                "же объём повторно."
                if repeated else
                "Это единственная действующая команда менеджмента. AI risk-overlay "
                "имеет приоритет; стандартная стратегия применяется только к "
                "остатку после действия."
            ),
            (
                f"Статус: решение остаётся действующим до нового арбитражного "
                f"пересчёта. decision_id={decision.get('decision_id')}."
            ),
            previous,
            (
                f"Арбитр: {arbiter.get('winner', 'AI')} → "
                f"{arbiter.get('effective_policy', decision.get('policy'))}; "
                f"AI priority bonus {arbiter.get('ai_priority_bonus_r', 0):+.3f}R."
            ),
        ]

    if status == "executed_continuation":
        return [
            f"**ДЕЙСТВИЕ СЕЙЧАС** — {instruction}.",
            (
                "Прежнее активное решение ИИ уже считается выполненным в "
                "последовательности. Повторно закрывать тот же объём нельзя."
            ),
            previous,
            (
                "Единый следующий план: сопровождать фактический остаток по "
                "текущему стопу/БУ и ступеням, пока арбитр явно не выдаст новое "
                "усиление или EXIT."
            ),
        ]

    return [
        (
            "**ДЕЙСТВИЕ СЕЙЧАС** — HOLD ПОДТВЕРЖДЁН. НЕ ВЫСТАВЛЯТЬ "
            "НОВЫХ ОРДЕРОВ ВНЕ СТРАТЕГИИ; СОХРАНИТЬ ТЕКУЩИЙ СТОП/БУ "
            "И ПРЕДУСМОТРЕННЫЕ СТРАТЕГИЕЙ ОРДЕРА ЛЕСТНИЦЫ."
        ),
        (
            "Единственный действующий план — стандартный менеджмент. Это не "
            "прогноз обязательного продолжения движения, а решение не "
            "вмешиваться сильнее стратегии."
        ),
        previous,
        (
            f"Арбитр: {arbiter.get('winner', 'STRATEGY')} → HOLD. "
            f"Причина: {arbiter.get('reason', 'активный AI overlay не подтверждён')}."
        ),
    ]


def _plan_lines(manager: dict) -> list[str]:
    decision = manager.get("management_decision") or {}
    arbiter = manager.get("management_arbiter") or {}
    return [
        f"Авторитет плана: {decision.get('authority', '—')}; "
        f"политика действия: {decision.get('policy', '—')}; "
        f"модельный выбор: {decision.get('model_policy', '—')}.",
        f"Статус исполнения: {decision.get('execution_status', '—')}; "
        f"continuity={decision.get('continuity', '—')}.",
        f"Новая доля закрытия текущего остатка: "
        f"{float(decision.get('incremental_close_fraction') or 0) * 100:.1f}%; "
        f"остаток после действия: "
        f"{float(decision.get('remaining_fraction_after_action') or 0) * 100:.1f}%.",
        (
            f"Арбитражный счёт: стратегия {arbiter.get('strategy_score', 0):+.3f}; "
            f"ИИ до приоритета {arbiter.get('ai_score_before_priority', 0):+.3f}; "
            f"ИИ после приоритета {arbiter.get('ai_score_after_priority', 0):+.3f}."
        ),
        (
            "Приоритет ИИ действует только после прохождения evidence/CVaR/stress "
            "условий. После выбора арбитра второй параллельной команды нет."
        ),
    ]


def build_snapshot(engine) -> dict:
    snapshot = _BASE_BUILD_SNAPSHOT(engine)
    trade_id = snapshot.get("trade_id")
    if trade_id is None:
        return snapshot
    manager = snapshot.get("policy_manager") or {}
    previous_full = _impl._previous_full_snapshot(engine, int(trade_id))
    previous_decision = (
        ((previous_full or {}).get("policy_manager") or {}).get("management_decision")
    )
    decision = resolve_management_sequence(
        manager,
        previous_decision,
        trade_id=int(trade_id),
        captured_ts=snapshot.get("captured_ts"),
    )
    manager["management_decision"] = decision
    rec = manager.get("recommendation") or {}
    rec.update({
        "policy": decision.get("policy") or rec.get("policy"),
        "execution_action_ru": decision.get("instruction_ru"),
        "working_action_code": decision.get("execution_status"),
        "manual_execution_required": decision.get("manual_execution_required", False),
        "automatic_execution_allowed": False,
    })
    manager["recommendation"] = rec
    snapshot["policy_manager"] = manager
    return snapshot


def request_verdict(snapshot: dict) -> dict:
    result = _BASE_REQUEST_VERDICT(snapshot)
    decision = ((snapshot.get("policy_manager") or {}).get("management_decision") or {})
    result["management_decision"] = decision
    return result


def render_policy_report(snapshot: dict) -> str:
    manager = snapshot.get("policy_manager") or {}
    lines = _BASE_RENDER(snapshot).splitlines()
    first_section = next(
        (i for i in range(1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    lines = _management_top(manager) + [""] + lines[first_section:]
    _insert_before(
        lines,
        "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ**",
        "**ЕДИНЫЙ ПЛАН МЕНЕДЖМЕНТА** —",
        _plan_lines(manager),
    )

    decision = manager.get("management_decision") or {}
    status = decision.get("execution_status")
    if status == "pending_execution":
        policy = decision.get("policy")
        if decision.get("continuity") == "continue_same_pending_decision":
            body = [
                "Предыдущее решение остаётся действующим. Этот отчёт не создаёт "
                "дополнительного закрытия; после фактического выполнения вести "
                "оставшийся объём по единому плану."
            ]
        elif policy == "EXIT":
            body = [
                "После ручного полного выхода сопровождение этой сделки "
                "прекращается. Повторного EXIT по этому decision_id нет."
            ]
        else:
            body = [
                f"После ручного {policy} стратегический стоп/БУ и лестница "
                "продолжаются только для оставшегося объёма; повторять это "
                "сокращение как новый приказ нельзя."
            ]
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", body)
    elif status == "executed_continuation":
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", [
            "Предыдущее сокращение уже учтено в последовательности. Новых "
            "ордеров по этому отчёту нет; вести фактический остаток."
        ])
    else:
        _replace_section(lines, "**ПОСЛЕ ИСПОЛНЕНИЯ**", [
            "Нового внепланового исполнения нет. Продолжаются только текущий "
            "стоп/БУ и предусмотренные стратегией ступени."
        ])
    return "\n".join(lines).strip()


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._base,
):
    module.SYSTEM_PROMPT = SYSTEM_PROMPT
    module.build_snapshot = build_snapshot
    module.request_verdict = request_verdict
    module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["build_snapshot"] = build_snapshot
globals()["request_verdict"] = request_verdict
globals()["render_policy_report"] = render_policy_report
