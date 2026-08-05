"""Verdict v11: stateful reports with strict legacy snapshot compatibility."""
from __future__ import annotations

from . import ai_verdict_v10 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_STATEFUL_RENDER = _impl.render_policy_report
# v10 -> v9 -> v7; v7._BASE_RENDER is the established v6 report contract.
_LEGACY_RENDER = _impl._impl._impl._BASE_RENDER


def render_policy_report(snapshot: dict) -> str:
    manager = snapshot.get("policy_manager") or {}
    if manager.get("management_decision"):
        return _STATEFUL_RENDER(snapshot)

    text = _LEGACY_RENDER(snapshot)
    rec = manager.get("recommendation") or {}
    gate = manager.get("gate") or {}
    is_hold = (
        rec.get("policy") == "HOLD"
        and gate.get("status") in {
            "confirmed_hold", "hold_no_reduction_evidence"
        }
    )
    if is_hold and "HOLD подтверждён" not in "\n".join(text.splitlines()[:8]):
        lines = text.splitlines()
        lines.insert(
            1,
            "Статус: HOLD подтверждён; нового внепланового исполнения нет.",
        )
        text = "\n".join(lines)
    return text


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._base,
):
    module.render_policy_report = render_policy_report

globals()["render_policy_report"] = render_policy_report
