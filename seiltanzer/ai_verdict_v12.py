"""Verdict v12: persist manual acknowledgement into the next AI review."""
from __future__ import annotations

from copy import deepcopy

from . import ai_verdict_v11 as _impl
from .ai_decision_state import executed_ack_count, latest_ack
from .ai_policy import resolve_management_sequence
from .ai_verdict_v7 import _previous_full_snapshot
from .ai_verdict_v9 import (
    _BASE_BUILD_SNAPSHOT as _QUANT_BUILD_SNAPSHOT,
    _BASE_REQUEST_VERDICT,
)


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
Если management_decision.last_ack_status=not_executed, решение остаётся прежним и
не означает дополнительное сокращение. Если предыдущее решение отмечено executed,
не предлагай тот же CLOSE повторно: сопроводи остаток или выдай только явное новое
усиление плана.
"""


def _apply_ack(engine, trade_id: int,
               previous_decision: dict | None) -> dict | None:
    if not isinstance(previous_decision, dict) or not previous_decision.get("decision_id"):
        return previous_decision
    previous = deepcopy(previous_decision)
    ack = latest_ack(
        engine.journal, int(trade_id), str(previous["decision_id"])
    )
    if not ack:
        return previous

    previous["last_ack_status"] = ack.get("status")
    previous["acknowledged_at"] = ack.get("ts")
    if ack.get("status") == "executed":
        previous["execution_status"] = "executed"
        previous["executed_at"] = ack.get("ts")
        previous["last_executed_policy"] = previous.get("policy")
        previous["last_executed_decision_id"] = previous.get("decision_id")
        previous["executed_action_count"] = executed_ack_count(
            engine.journal, int(trade_id)
        )
    elif ack.get("status") == "not_executed":
        previous["execution_status"] = "not_executed"
        previous["not_executed_at"] = ack.get("ts")
    return previous


def _compact_decision(decision: dict) -> dict:
    """Drop duplicated nullable metadata while keeping the full live plan."""
    for key in (
        "arbiter_reason",
        "last_executed_policy",
        "last_executed_decision_id",
        "supersedes_decision_id",
        "previous",
    ):
        if decision.get(key) is None:
            decision.pop(key, None)
    return decision


def build_snapshot(engine) -> dict:
    """Build one plan after applying the latest user acknowledgement."""
    snapshot = _QUANT_BUILD_SNAPSHOT(engine)
    trade_id = snapshot.get("trade_id")
    if trade_id is None:
        return snapshot

    manager = snapshot.get("policy_manager") or {}
    previous_full = _previous_full_snapshot(engine, int(trade_id))
    previous_decision = (
        ((previous_full or {}).get("policy_manager") or {}).get("management_decision")
    )
    previous_decision = _apply_ack(engine, int(trade_id), previous_decision)
    decision = resolve_management_sequence(
        manager,
        previous_decision,
        trade_id=int(trade_id),
        captured_ts=snapshot.get("captured_ts"),
    )
    decision = _compact_decision(decision)
    manager["management_decision"] = decision
    recommendation = manager.get("recommendation") or {}
    recommendation.update({
        "policy": decision.get("policy") or recommendation.get("policy"),
        "execution_action_ru": decision.get("instruction_ru"),
        "working_action_code": decision.get("execution_status"),
        "manual_execution_required": decision.get(
            "manual_execution_required", False
        ),
        "automatic_execution_allowed": False,
    })
    manager["recommendation"] = recommendation
    snapshot["policy_manager"] = manager
    return snapshot


def request_verdict(snapshot: dict) -> dict:
    result = _BASE_REQUEST_VERDICT(snapshot)
    decision = (
        ((snapshot.get("policy_manager") or {}).get("management_decision")) or {}
    )
    result["management_decision"] = decision
    return result


def render_policy_report(snapshot: dict) -> str:
    text = _BASE_RENDER(snapshot)
    decision = (
        ((snapshot.get("policy_manager") or {}).get("management_decision")) or {}
    )
    if decision.get("last_ack_status") == "not_executed":
        lines = text.splitlines()
        marker = (
            "Подтверждение пользователя: предыдущее действие ещё НЕ ВЫПОЛНЕНО; "
            "тот же decision_id остаётся действующим и не создаёт дополнительное "
            "сокращение."
        )
        if marker not in lines:
            lines.insert(min(4, len(lines)), marker)
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
    module.SYSTEM_PROMPT = SYSTEM_PROMPT
    module.build_snapshot = build_snapshot
    module.request_verdict = request_verdict
    module.render_policy_report = render_policy_report

globals()["SYSTEM_PROMPT"] = SYSTEM_PROMPT
globals()["build_snapshot"] = build_snapshot
globals()["request_verdict"] = request_verdict
globals()["render_policy_report"] = render_policy_report
