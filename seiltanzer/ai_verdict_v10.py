"""Verdict v10: compatibility wording for the unified stateful plan."""
from __future__ import annotations

from . import ai_verdict_v9 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report


def render_policy_report(snapshot: dict) -> str:
    text = _BASE_RENDER(snapshot)
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    rec = manager.get("recommendation") or {}
    is_hold = (
        decision.get("execution_status") == "strategy_active"
        or (
            not decision
            and rec.get("policy") == "HOLD"
            and (manager.get("gate") or {}).get("status") == "confirmed_hold"
        )
    )
    if is_hold and "HOLD подтверждён" not in "\n".join(text.splitlines()[:8]):
        lines = text.splitlines()
        lines.insert(
            1,
            "Статус: HOLD подтверждён как единый план без внепланового "
            "исполнения; предусмотренные стратегией ордера сохраняются.",
        )
        text = "\n".join(lines)
    return text


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._base,
):
    module.render_policy_report = render_policy_report

globals()["render_policy_report"] = render_policy_report
