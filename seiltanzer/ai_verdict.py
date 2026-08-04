"""Stable public facade for the quantitative AI verdict v2."""
from __future__ import annotations

from . import ai_verdict_v2 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})

SYSTEM_PROMPT = """Ты объясняешь готовое решение quantitative policy_manager.
Нельзя менять выбранную политику, объём закрытия или числа. Hard CVaR — обязательное
ограничение: недопустимая политика не может быть восстановлена confirmation gate.

Проверь и отрази все группы: first-touch/NO-TOUCH/barrier EV, RND и конус,
реальные IV-экспирации и локальная проекция 1–24h, live tape, ATR/VRP,
уровни и directional delta, полная корреляционная матрица, OI/GEX, фильтры,
качество цепочки и история изменений. IV skew является подтверждением только при
покрытии обоих крыльев ±5% реальными страйками. POC/value area выше цены — контекст,
пока нет отбоя, отрицательного directional delta, отсутствия принятия выше и потока
против сделки.

Отдельно показывай сырой и финальный выбор, их устойчивость, общую геометрию путей,
цену защиты по Expected R и улучшение CVaR10. Покрытие полей и надёжность данных
выводи раздельно. Запрещены размытые объяснения «выше потенциальная прибыль»,
«слишком раннее действие» и «лучший risk-adjusted» без числового сравнения.
Обязательные разделы включают РАСЧЁТ ПОЛИТИК и конкретное финальное действие."""

_ORIGINAL_RENDER_POLICY_REPORT = _impl.render_policy_report


def render_policy_report(snapshot: dict) -> str:
    """Render v2 report while preserving the established Russian section contract."""
    report = _ORIGINAL_RENDER_POLICY_REPORT(snapshot)
    return report.replace(
        "**ПОДТВЕРЖДЕНИЯ, ПРОТИВОРЕЧИЯ И КОНТЕКСТ** —",
        "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —",
    )


# request_verdict lives in ai_verdict_base and resolves these globals there.
_impl.SYSTEM_PROMPT = SYSTEM_PROMPT
_impl.render_policy_report = render_policy_report
_impl._base.SYSTEM_PROMPT = SYSTEM_PROMPT
_impl._base.render_policy_report = render_policy_report
globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
