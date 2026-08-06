"""Verdict v13: expose the option-implied center used by the optimizer."""
from __future__ import annotations

from typing import Any

from . import ai_verdict_v12 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
Опционный центр объясняй строго как две связанные величины одной семьи
option_distribution: raw RND mean к горизонту и robust forward после shrink/cap,
который уже входит в drift_R, Expected и CVaR. Не называй их гарантированной или
фундаментальной справедливой ценой. Если raw mean отклонён plausibility-check,
показывай его только как контекст и не приписывай ему влияние на решение.
Mean, median, mode, skew и barrier EV нельзя считать независимыми голосами.
"""


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _r(value: Any) -> str:
    value = _num(value)
    return "—" if value is None else f"{value:+.3f}R"


def _section(lines: list[str], header: str) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if line.startswith(header)), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    return start, end


def _center_line(manager: dict) -> str | None:
    center = manager.get("option_center") or (
        ((manager.get("evidence") or {}).get("cone_rnd") or {}).get("option_center")
    ) or {}
    if not center.get("available"):
        return None
    raw = center.get("raw_mean_r")
    raw_gap = center.get("raw_gap_r")
    robust = center.get("robust_forward_r")
    robust_gap = center.get("robust_gap_r")
    if center.get("raw_mean_accepted"):
        authority = (
            "сырой mean прошёл plausibility-check; в Expected/CVaR вошёл не он "
            "напрямую, а уменьшенный и ограниченный robust drift"
        )
    elif center.get("raw_rejected_gap_r") is not None:
        authority = (
            "сырой mean отклонён plausibility-check и оставлен только контекстом; "
            "в Expected/CVaR он не вошёл"
        )
    else:
        authority = "опционный центр не получил направленного авторитета"
    return (
        f"Опционный центр: raw RND mean H {_r(raw)} (разрыв от текущего r "
        f"{_r(raw_gap)}); robust forward {_r(robust)} (использованный drift "
        f"{_r(robust_gap)}; source={center.get('source') or '—'}). {authority}. "
        "Mean, median, mode, skew и barrier EV считаются одной семьёй "
        "option_distribution, без двойного голоса."
    )


def render_policy_report(snapshot: dict) -> str:
    text = _BASE_RENDER(snapshot)
    manager = snapshot.get("policy_manager") or {}
    line = _center_line(manager)
    if not line or line in text:
        return text
    lines = text.splitlines()
    bounds = _section(lines, "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ**")
    if bounds is None:
        lines.extend(["", "**ОПЦИОННЫЙ ЦЕНТР** —", line])
    else:
        _, end = bounds
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, line)
    return "\n".join(lines).strip()


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._base,
):
    module.SYSTEM_PROMPT = SYSTEM_PROMPT
    module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
