"""Verdict v15: distinguish the nearest ladder rung from the final take."""
from __future__ import annotations

import re
from typing import Any

from . import ai_verdict_v14 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
_GEOMETRY_RE = re.compile(
    r"Рубеж раньше стопа: (.+?)\. Стоп раньше рубежа: (.+?)\."
)
_FINAL_TAKE_MARKER = "По опционной barrier-модели финальный тейк"


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _r(value: Any) -> str:
    number = _num(value)
    return "—" if number is None else f"{number:+.3f}R"


def _pct(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "—"
    if 0.0 < abs(number) < 0.001:
        return f"{number * 100:.3f}%"
    return f"{number * 100:.1f}%"


def _clarify_geometry(text: str, manager: dict) -> str:
    """Clarify that scenario geometry targets the next rung, not final take."""
    geometry = manager.get("scenario_geometry") or {}
    inputs = manager.get("inputs") or {}
    next_rung = _num(geometry.get("next_rung_r"))
    final_take = _num(inputs.get("T"))

    match = _GEOMETRY_RE.search(text)
    if not match:
        return text

    rung_probability, stop_probability = match.groups()
    uses_intermediate_rung = (
        next_rung is not None
        and final_take is not None
        and next_rung < final_take - 1e-6
    )
    if uses_intermediate_rung:
        replacement = (
            f"Ближайшая ступень {_r(next_rung)} раньше стопа: {rung_probability}. "
            f"Стоп раньше ближайшей ступени: {stop_probability}."
        )
    else:
        target = next_rung if next_rung is not None else final_take
        replacement = (
            f"Финальный тейк {_r(target)} раньше стопа: {rung_probability}. "
            f"Стоп раньше финального тейка: {stop_probability}."
        )

    text = text[:match.start()] + replacement + text[match.end():]
    if not uses_intermediate_rung or _FINAL_TAKE_MARKER in text:
        return text

    barrier = (manager.get("evidence") or {}).get("option_barrier") or {}
    p_take = _num(barrier.get("p_take"))
    p_stop = _num(barrier.get("p_stop"))
    no_touch = _num(barrier.get("no_touch"))
    if p_take is None or final_take is None:
        return text

    parts = [
        f"{_FINAL_TAKE_MARKER} {_r(final_take)} раньше стопа: {_pct(p_take)}"
    ]
    if p_stop is not None:
        parts.append(f"стоп раньше финального тейка: {_pct(p_stop)}")
    if no_touch is not None:
        parts.append(f"ни один барьер не достигнут: {_pct(no_touch)}")
    final_line = "; ".join(parts) + "."
    return text.replace(replacement, replacement + "\n" + final_line, 1)


def render_policy_report(snapshot: dict) -> str:
    text = _BASE_RENDER(snapshot)
    manager = snapshot.get("policy_manager") or {}
    return _clarify_geometry(text, manager)


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
