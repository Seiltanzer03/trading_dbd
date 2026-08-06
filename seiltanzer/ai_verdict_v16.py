"""Verdict v16: normalize the final API report after model/fallback rendering.

The previous layer only wrapped ``render_policy_report``. ``request_verdict``
can retain an older renderer reference, so the final ``result['verdict']`` must
also be normalized immediately before it is returned by the API.
"""
from __future__ import annotations

import re
from typing import Any

from . import ai_verdict_v15 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
_BASE_REQUEST = _impl.request_verdict
_SOURCE_STABILITY_RE = re.compile(
    r"(Устойчивость к источнику данных для\s+([A-Z0-9_]+):\s*)"
    r"(\d+)/(\d+)\s*\(([\d.,]+)%\)"
)
_FINAL_TAKE_MARKER = "По опционной barrier-модели финальный тейк"


def _number(value: Any) -> float | None:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _at(value: Any, *path: str):
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_number(root: dict, paths: tuple[tuple[str, ...], ...]) -> float | None:
    for path in paths:
        value = _number(_at(root, *path))
        if value is not None:
            return value
    return None


def _r(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:+.3f}R"


def _pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if 0.0 < abs(number) < 0.001:
        return f"{number * 100:.3f}%"
    return f"{number * 100:.1f}%"


def _fix_source_stability(text: str) -> str:
    """Make the printed percentage arithmetically equal to count/checks."""
    def replace(match: re.Match[str]) -> str:
        prefix, _policy, count_raw, checks_raw, _old_pct = match.groups()
        count = int(count_raw)
        checks = int(checks_raw)
        share = count / checks if checks > 0 else 0.0
        return f"{prefix}{count}/{checks} ({share * 100:.1f}%)"

    return _SOURCE_STABILITY_RE.sub(replace, text)


def _geometry_values(snapshot: dict) -> tuple[float | None, float | None]:
    manager = snapshot.get("policy_manager") or {}
    # Only use values produced by the scenario engine. A recommendation-only
    # fixture may contain a next rung without any geometry/final-take model;
    # treating that as the final take makes repeated normalization non-idempotent.
    next_rung = _first_number(manager, (
        ("scenario_geometry", "next_rung_r"),
        ("strategy_next_step", "next_rung_r"),
    ))
    final_take = _first_number(manager, (
        ("inputs", "T"),
        ("policy_inputs", "T"),
    ))
    if final_take is None:
        final_take = _first_number(snapshot, (
            ("tick", "prob", "T"),
            ("tick", "cone", "T"),
            ("tick", "market", "T"),
        ))
    return next_rung, final_take


def _barrier_values(snapshot: dict) -> tuple[float | None, float | None, float | None]:
    manager = snapshot.get("policy_manager") or {}
    roots = [
        (manager.get("evidence") or {}).get("option_barrier") or {},
        manager.get("option_barrier") or {},
        _at(snapshot, "tick", "prob") or {},
        _at(snapshot, "tick", "cone") or {},
    ]
    take_paths = (("p_take",), ("p_take_first_touch",), ("take_touch",))
    stop_paths = (("p_stop",), ("p_stop_first_touch",), ("stop_touch",))
    no_touch_paths = (("no_touch",), ("p_no_touch",), ("no_touch_probability",))

    def from_roots(paths):
        for root in roots:
            value = _first_number(root, paths)
            if value is not None:
                return value
        return None

    return from_roots(take_paths), from_roots(stop_paths), from_roots(no_touch_paths)


def _clarify_geometry_final(text: str, snapshot: dict) -> str:
    """Relabel only the geometry sentence, without parsing decimal values."""
    if "Рубеж раньше стопа:" not in text or "Стоп раньше рубежа:" not in text:
        return text

    next_rung, final_take = _geometry_values(snapshot)
    if next_rung is None and final_take is None:
        return text

    intermediate = (
        next_rung is not None
        and final_take is not None
        and next_rung < final_take - 1e-6
    )
    lines = text.splitlines()
    geometry_index = next(
        (
            index for index, line in enumerate(lines)
            if "Рубеж раньше стопа:" in line and "Стоп раньше рубежа:" in line
        ),
        None,
    )
    if geometry_index is None:
        return text

    line = lines[geometry_index]
    if intermediate:
        line = line.replace(
            "Рубеж раньше стопа:",
            f"Ближайшая ступень {_r(next_rung)} раньше стопа:",
            1,
        ).replace(
            "Стоп раньше рубежа:",
            "Стоп раньше ближайшей ступени:",
            1,
        )
    else:
        target = final_take if final_take is not None else next_rung
        line = line.replace(
            "Рубеж раньше стопа:",
            f"Финальный тейк {_r(target)} раньше стопа:",
            1,
        ).replace(
            "Стоп раньше рубежа:",
            "Стоп раньше финального тейка:",
            1,
        )
    lines[geometry_index] = line

    if intermediate and _FINAL_TAKE_MARKER not in text:
        p_take, p_stop, no_touch = _barrier_values(snapshot)
        if p_take is not None and final_take is not None:
            parts = [
                f"{_FINAL_TAKE_MARKER} {_r(final_take)} раньше стопа: {_pct(p_take)}"
            ]
            if p_stop is not None:
                parts.append(f"стоп раньше финального тейка: {_pct(p_stop)}")
            if no_touch is not None:
                parts.append(f"ни один барьер не достигнут: {_pct(no_touch)}")
            lines.insert(geometry_index + 1, "; ".join(parts) + ".")

    return "\n".join(lines)


def normalize_final_report(text: str, snapshot: dict) -> str:
    text = _clarify_geometry_final(text, snapshot)
    return _fix_source_stability(text)


def render_policy_report(snapshot: dict) -> str:
    return normalize_final_report(_BASE_RENDER(snapshot), snapshot)


def request_verdict(snapshot: dict) -> dict:
    result = _BASE_REQUEST(snapshot)
    if not isinstance(result, dict) or not isinstance(result.get("verdict"), str):
        return result
    result = dict(result)
    # The deterministic fallback must be byte-for-byte identical to the public
    # renderer. This also prevents a second normalization pass from changing it.
    if result.get("model") == "deterministic-policy-fallback":
        result["verdict"] = render_policy_report(snapshot)
    else:
        result["verdict"] = normalize_final_report(result["verdict"], snapshot)
    return result


globals()["render_policy_report"] = render_policy_report
globals()["request_verdict"] = request_verdict
