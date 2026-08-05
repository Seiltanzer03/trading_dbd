"""Verdict v5: explicit raw choice, rejected stress fallback and working action."""
from __future__ import annotations

from . import ai_verdict_v4_compat as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
Всегда разделяй три понятия: основной расчёт, stress-кандидат и рабочее действие.
Stress-кандидат, отклонённый gate, нельзя называть расчётным действием или рекомендацией.
Если основной выбор HOLD проходит CVaR и нет независимых подтверждений сокращения,
рабочее действие — сохранить текущее сопровождение, даже если отдельный stress fallback
предлагал более агрессивную политику.
"""


def _action_ru(policy: str | None) -> str:
    return {
        "HOLD": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
        "CLOSE_10": "ЗАКРЫТЬ 10% ПОЗИЦИИ СЕЙЧАС",
        "CLOSE_25": "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС",
        "CLOSE_50": "ЗАКРЫТЬ 50% ПОЗИЦИИ СЕЙЧАС",
        "EXIT": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
    }.get(policy or "", policy or "не определено")


def _is_executable(gate: dict, rec: dict) -> bool:
    explicit = gate.get("automatic_execution_allowed")
    if explicit is not None:
        return bool(explicit)
    explicit = rec.get("automatic_execution_allowed")
    if explicit is not None:
        return bool(explicit)
    return gate.get("status") in {"confirmed", "downgraded_within_feasible_set"}


def render_policy_report(snapshot: dict) -> str:
    report = _BASE_RENDER(snapshot)
    manager = snapshot.get("policy_manager") or {}
    gate = manager.get("gate") or {}
    rec = manager.get("recommendation") or {}
    executable = _is_executable(gate, rec)
    if executable:
        return report.replace(
            "hold_no_reduction_evidence",
            "HOLD сохранён: нет подтверждений сокращения",
        )

    raw = rec.get("raw_optimizer_policy") or gate.get("raw_policy") or "—"
    selected = rec.get("policy") or gate.get("provisional_policy") or raw
    rejected = gate.get("rejected_stress_fallback")
    reasons = gate.get("reasons") or ["gate не подтвердил изменение позиции"]

    lines = report.splitlines()
    next_header = next(
        (index for index in range(1, len(lines)) if lines[index].startswith("**")),
        len(lines),
    )
    body = lines[next_header:]
    top = [
        "**ДЕЙСТВИЕ СЕЙЧАС** — НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ; "
        "ПРОДОЛЖАТЬ ТЕКУЩЕЕ СОПРОВОЖДЕНИЕ. "
        "НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ.",
        f"Основной расчёт: {raw} — {_action_ru(raw)}.",
    ]
    if rejected:
        top.append(
            f"Отклонённый stress-кандидат: {rejected} — {_action_ru(rejected)}. "
            "Это не рекомендация: кандидат появился только в проверочных пересчётах "
            "и был запрещён единым CVaR/evidence gate."
        )
    elif selected != raw:
        top.append(
            f"Неподтверждённый gate-кандидат: {selected} — {_action_ru(selected)}. "
            "Не исполнять."
        )
    else:
        top.append(
            f"Расчётное действие: {_action_ru(selected)}; оно не подтверждено."
        )
    top.append("Почему не менять позицию: " + "; ".join(reasons) + ".")
    return "\n".join(top + [""] + body).replace(
        "hold_no_reduction_evidence",
        "HOLD сохранён: нет подтверждений сокращения",
    )


for module in (_impl, _impl._impl, _impl._impl._base):
    module.SYSTEM_PROMPT = SYSTEM_PROMPT
    module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
