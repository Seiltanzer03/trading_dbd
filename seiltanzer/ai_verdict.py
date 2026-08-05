"""Stable public facade for the quantitative AI verdict v4."""
from __future__ import annotations

from . import ai_verdict_v4 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_ORIGINAL_RENDER_POLICY_REPORT = _impl.render_policy_report


def _insert_after(lines: list[str], prefix: str, value: str) -> None:
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines.insert(index + 1, value)
            return


def _replace_section_body(lines: list[str], header: str, value: str) -> None:
    for index, line in enumerate(lines):
        if line.startswith(header):
            if index + 1 < len(lines) and not lines[index + 1].startswith("**"):
                lines[index + 1] = value
            else:
                lines.insert(index + 1, value)
            return


def render_policy_report(snapshot: dict) -> str:
    """Keep legacy contract phrases while preserving the clearer v4 meaning."""
    report = _ORIGINAL_RENDER_POLICY_REPORT(snapshot)
    report = report.replace("расчётное действие:", "Расчётное действие:")
    manager = snapshot.get("policy_manager") or {}
    gate = manager.get("gate") or {}
    evidence = manager.get("evidence") or {}
    lines = report.splitlines()

    if not gate.get("automatic_execution_allowed"):
        _replace_section_body(
            lines,
            "**ПОСЛЕ ИСПОЛНЕНИЯ** —",
            "Исполнение не подтверждено. Никакого нового исполнения: сохранить "
            "действующие стоп, БУ/trailing и лестницу частичных фиксаций.",
        )

    if "Независимые семьи подтверждений:" not in report:
        families = evidence.get("adverse_confirmation_families") or []
        text = ", ".join(families) if families else "нет"
        value = (
            f"Независимые семьи подтверждений: {len(families)} — {text}. "
            "Смешанные семьи не дают голоса против удержания."
        )
        inserted = False
        for prefix in ("Однонаправленные семьи против удержания:", "Против удержания:"):
            before = len(lines)
            _insert_after(lines, prefix, value)
            if len(lines) > before:
                inserted = True
                break
        if not inserted:
            _insert_after(lines, "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —", value)

    return "\n".join(lines)


_impl.render_policy_report = render_policy_report
_impl._impl.render_policy_report = render_policy_report
_impl._impl._base.render_policy_report = render_policy_report
globals()["render_policy_report"] = render_policy_report
