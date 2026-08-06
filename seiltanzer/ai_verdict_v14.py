"""Verdict v14: explain stateful distribution revaluation and its authority."""
from __future__ import annotations

from typing import Any

from . import ai_verdict_v13 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
Блок lattice_revaluation трактуй как динамику одной и той же семьи
option_distribution от входа к текущему моменту: вход, среднее, сейчас, переток
массы и взвешенный score. Он может усиливать, ослаблять или смешивать направление
опционной семьи, но не является отдельными независимыми голосами P(take), EV,
median и хвостов. confidence_weight уже учитывает качество источника, длину
истории и шум. INDICATIVE/SNAPSHOT mapping не игнорируй, но описывай как
пониженный вес, а не как равный LIVE mapping. Не дублируй этот вес второй раз.
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


def _pp(value: Any) -> str:
    value = _num(value)
    return "—" if value is None else f"{value * 100:+.1f} п.п."


def _pct(value: Any) -> str:
    value = _num(value)
    return "—" if value is None else f"{value * 100:.1f}%"


def _section(lines: list[str], header: str) -> tuple[int, int] | None:
    start = next((i for i, line in enumerate(lines) if line.startswith(header)), None)
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("**")),
        len(lines),
    )
    return start, end


def _revaluation_line(manager: dict) -> str | None:
    rev = manager.get("lattice_revaluation") or (
        (manager.get("evidence") or {}).get("lattice_revaluation")
    ) or {}
    if not rev.get("available"):
        return None
    entry = rev.get("entry") or {}
    average = rev.get("average") or {}
    current = rev.get("current") or {}
    delta = rev.get("change_from_entry") or {}
    score = rev.get("score") or {}
    source = rev.get("source_quality") or {}
    direction = {
        "improving": "улучшение",
        "deteriorating": "ухудшение",
        "neutral": "без материального сдвига",
    }.get(score.get("direction"), "без материального сдвига")
    weighted = _num(score.get("weighted"))
    confidence = _num(score.get("confidence_weight"))
    source_weight = _num(source.get("weight"))
    return (
        "Переоценка распределения: "
        f"P тейка вход/среднее/сейчас {_pct(entry.get('p_take'))} / "
        f"{_pct(average.get('p_take'))} / {_pct(current.get('p_take'))}; "
        f"barrier EV {_r(entry.get('barrier_ev_r'))} / "
        f"{_r(average.get('barrier_ev_r'))} / {_r(current.get('barrier_ev_r'))}. "
        f"К входу: P {_pp(delta.get('p_take'))}, EV {_r(delta.get('barrier_ev_r'))}, "
        f"центр {_r(delta.get('q50_r'))}. Взвешенный итог: {direction} "
        f"({weighted:+.3f}" if weighted is not None else "Переоценка распределения: итог —"
    ) + (
        f", доверие {confidence * 100:.0f}%, источник "
        f"{source.get('label') or source.get('mode') or '—'} ×{source_weight:.2f}). "
        "Это одна семья option_distribution, без нескольких независимых голосов."
        if weighted is not None and confidence is not None and source_weight is not None
        else "). Это одна семья option_distribution, без нескольких независимых голосов."
    )


def render_policy_report(snapshot: dict) -> str:
    text = _BASE_RENDER(snapshot)
    manager = snapshot.get("policy_manager") or {}
    line = _revaluation_line(manager)
    if not line or line in text:
        return text
    lines = text.splitlines()
    bounds = _section(lines, "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ**")
    if bounds is None:
        lines.extend(["", "**ПЕРЕОЦЕНКА РАСПРЕДЕЛЕНИЯ** —", line])
    else:
        _, end = bounds
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, line)
    return "\n".join(lines).strip()


def _chain(root):
    seen = set()
    current = root
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "_impl", None)


for module in _chain(_impl):
    module.SYSTEM_PROMPT = SYSTEM_PROMPT
    module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["render_policy_report"] = render_policy_report
