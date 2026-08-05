"""Policy manager v11: acknowledgement-aware decision continuity."""
from __future__ import annotations

from copy import deepcopy

from . import ai_policy_v10 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RESOLVE = _impl.resolve_management_sequence


def resolve_management_sequence(policy_result: dict,
                                previous_state: dict | None,
                                *, trade_id: int,
                                captured_ts: float | None = None) -> dict:
    """Keep a not-executed decision active; consume an executed one once."""
    previous = deepcopy(previous_state) if isinstance(previous_state, dict) else previous_state
    ack_status = previous.get("execution_status") if isinstance(previous, dict) else None

    if ack_status == "not_executed":
        # The user explicitly says the broker action has not happened.  Keep the
        # same decision_id active instead of issuing another CLOSE percentage.
        previous["execution_status"] = "pending_execution"
        result = _BASE_RESOLVE(
            policy_result, previous, trade_id=trade_id, captured_ts=captured_ts
        )
        if result.get("decision_id") == previous.get("decision_id"):
            result["continuity"] = "continue_same_pending_decision"
            result["last_ack_status"] = "not_executed"
            result["not_executed_at"] = previous_state.get("not_executed_at")
        return result

    return _BASE_RESOLVE(
        policy_result, previous, trade_id=trade_id, captured_ts=captured_ts
    )


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl._impl._base,
):
    module.resolve_management_sequence = resolve_management_sequence

globals()["resolve_management_sequence"] = resolve_management_sequence
