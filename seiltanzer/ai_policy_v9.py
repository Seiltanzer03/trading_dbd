"""Policy manager v9: one stateful management plan with AI-first arbitration.

The strategy baseline and AI risk overlay are not exposed as concurrent commands.
A deterministic arbiter selects one authority.  The chosen decision is linked to
its predecessor, so an executed reduction is never silently repeated on the next
review.  Broker execution remains manual and is explicitly acknowledged through
journal state.
"""
from __future__ import annotations

import time
from typing import Any

from . import ai_policy_v8 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_ANALYZE = _impl.analyze_policies
_POLICY_RANK = {
    "HOLD": 0,
    "CLOSE_10": 1,
    "CLOSE_25": 2,
    "CLOSE_50": 3,
    "EXIT": 4,
}
_AI_PRIORITY_BONUS_R = 0.015


def _confirmed_active(gate: dict, policy: str) -> bool:
    if policy == "HOLD":
        return False
    if gate.get("status") == "confirmed_degraded_manual":
        return bool(gate.get("working_action_confirmed"))
    return bool(
        gate.get("automatic_execution_allowed")
        or gate.get("execution_policy") == policy
        or (
            gate.get("working_action_confirmed")
            and gate.get("status") in {
                "confirmed", "downgraded_within_feasible_set",
                "confirmed_manual", "confirmed_active",
            }
        )
    )


def _arbiter(policy_result: dict) -> dict:
    gate = policy_result.get("gate") or {}
    rec = policy_result.get("recommendation") or {}
    policies = policy_result.get("policies") or {}
    model_policy = gate.get("policy") or rec.get("policy") or "HOLD"
    hold = policies.get("HOLD") or {}
    candidate = policies.get(model_policy) or hold
    overlay = gate.get("degraded_authority_overlay") or {}
    selected_overlay = overlay.get("selected") or {}
    ai_active = _confirmed_active(gate, model_policy)

    hold_expected = _float(hold.get("expected_final_r"))
    hold_cvar = _float(hold.get("cvar10_r"))
    candidate_expected = _float(candidate.get("expected_final_r"))
    candidate_cvar = _float(candidate.get("cvar10_r"))
    strategy_score = hold_expected + 0.35 * hold_cvar
    ai_score_without_priority = candidate_expected + 0.35 * candidate_cvar
    ai_score = ai_score_without_priority + (_AI_PRIORITY_BONUS_R if ai_active else 0.0)

    if ai_active:
        winner = "AI"
        effective_policy = model_policy
        reason = (
            "подтверждённый AI risk-overlay имеет приоритет над стандартным "
            "менеджментом; стратегия применяется только к остатку после действия"
        )
    else:
        winner = "STRATEGY"
        effective_policy = "HOLD"
        reason = (
            "активный AI risk-overlay не прошёл условия; действует стандартный "
            "стоп/БУ и лестница"
        )
    return {
        "winner": winner,
        "effective_policy": effective_policy,
        "model_policy": model_policy,
        "ai_priority_bonus_r": _AI_PRIORITY_BONUS_R,
        "strategy_score": round(strategy_score, 4),
        "ai_score_before_priority": round(ai_score_without_priority, 4),
        "ai_score_after_priority": round(ai_score, 4),
        "expected_delta_vs_hold_r": round(candidate_expected - hold_expected, 4),
        "cvar_gain_vs_hold_r": round(candidate_cvar - hold_cvar, 4),
        "overlay_qualified": bool(selected_overlay),
        "reason": reason,
        "single_authority": True,
    }


def _previous_summary(previous: dict | None) -> dict | None:
    if not isinstance(previous, dict) or not previous:
        return None
    return {
        "decision_id": previous.get("decision_id"),
        "policy": previous.get("policy"),
        "model_policy": previous.get("model_policy"),
        "authority": previous.get("authority"),
        "execution_status": previous.get("execution_status"),
        "issued_at": previous.get("issued_at"),
        "executed_at": previous.get("executed_at"),
        "last_executed_policy": previous.get("last_executed_policy"),
        "executed_action_count": int(previous.get("executed_action_count") or 0),
    }


def resolve_management_sequence(policy_result: dict,
                                previous_state: dict | None,
                                *, trade_id: int,
                                captured_ts: float | None = None) -> dict:
    """Resolve one sequential action and prevent accidental repeated reductions."""
    now = float(captured_ts or time.time())
    arbiter = policy_result.get("management_arbiter") or _arbiter(policy_result)
    gate = policy_result.get("gate") or {}
    rec = policy_result.get("recommendation") or {}
    model_policy = arbiter.get("model_policy") or rec.get("policy") or "HOLD"
    effective_policy = arbiter.get("effective_policy") or "HOLD"
    previous = previous_state if isinstance(previous_state, dict) else {}
    previous_status = previous.get("execution_status")
    previous_policy = previous.get("policy") or "HOLD"
    previous_rank = _POLICY_RANK.get(previous_policy, 0)
    current_rank = _POLICY_RANK.get(effective_policy, 0)
    last_executed_policy = previous.get("last_executed_policy")
    executed_count = int(previous.get("executed_action_count") or 0)

    base = {
        "trade_id": int(trade_id),
        "model_policy": model_policy,
        "arbiter_winner": arbiter.get("winner"),
        "arbiter_reason": arbiter.get("reason"),
        "ai_priority_bonus_r": arbiter.get("ai_priority_bonus_r"),
        "previous": _previous_summary(previous),
        "single_authority": True,
        "automatic_broker_execution": False,
        "last_executed_policy": last_executed_policy,
        "last_executed_decision_id": previous.get("last_executed_decision_id"),
        "executed_action_count": executed_count,
    }

    if effective_policy == "HOLD":
        decision_id = f"T{trade_id}-{int(now * 1000)}-STRATEGY"
        return {
            **base,
            "decision_id": decision_id,
            "issued_at": now,
            "authority": "STRATEGY",
            "policy": "HOLD",
            "execution_status": "strategy_active",
            "manual_execution_required": False,
            "incremental_close_fraction": 0.0,
            "remaining_fraction_after_action": 1.0,
            "continuity": (
                "active_ai_plan_cancelled_by_current_arbiter"
                if previous_status == "pending_execution" else
                "strategy_continues"
            ),
            "supersedes_decision_id": (
                previous.get("decision_id")
                if previous_status == "pending_execution" else None
            ),
            "instruction_ru": (
                "НЕ СОВЕРШАТЬ ВНЕПЛАНОВЫХ РУЧНЫХ ИЗМЕНЕНИЙ; "
                "ВЕСТИ ОСТАТОК ПО СТОПУ/БУ И ЛЕСТНИЦЕ СТРАТЕГИИ"
            ),
        }

    fraction = float(POLICY_FRACTIONS[effective_policy])
    same_pending = bool(
        previous_status == "pending_execution"
        and previous_policy == effective_policy
        and previous.get("decision_id")
    )
    if same_pending:
        return {
            **base,
            **previous,
            "model_policy": model_policy,
            "arbiter_winner": "AI",
            "arbiter_reason": arbiter.get("reason"),
            "previous": _previous_summary(previous),
            "continuity": "continue_same_pending_decision",
            "single_authority": True,
            "automatic_broker_execution": False,
        }

    same_already_executed = bool(
        previous_status == "executed"
        and previous_policy == effective_policy
    )
    weaker_after_execution = bool(
        previous_status == "executed"
        and current_rank <= previous_rank
    )
    if same_already_executed or weaker_after_execution:
        return {
            **base,
            "decision_id": f"T{trade_id}-{int(now * 1000)}-CONTINUE",
            "issued_at": now,
            "authority": "AI_CONTINUITY",
            "policy": "HOLD",
            "model_policy": model_policy,
            "execution_status": "executed_continuation",
            "manual_execution_required": False,
            "incremental_close_fraction": 0.0,
            "remaining_fraction_after_action": 1.0,
            "continuity": "previous_ai_action_already_executed_no_repeat",
            "supersedes_decision_id": None,
            "instruction_ru": (
                f"ПРЕДЫДУЩЕЕ РЕШЕНИЕ ИИ {previous_policy} УЖЕ ВЫПОЛНЕНО; "
                "НЕ ПОВТОРЯТЬ СОКРАЩЕНИЕ, ВЕСТИ ОСТАТОК ПО ЕДИНОМУ ПЛАНУ"
            ),
        }

    decision_id = f"T{trade_id}-{int(now * 1000)}-{effective_policy}"
    action_ru = {
        "CLOSE_10": "ЗАКРЫТЬ 10% ТЕКУЩЕГО ОСТАТКА",
        "CLOSE_25": "ЗАКРЫТЬ 25% ТЕКУЩЕГО ОСТАТКА",
        "CLOSE_50": "ЗАКРЫТЬ 50% ТЕКУЩЕГО ОСТАТКА",
        "EXIT": "ЗАКРЫТЬ ВЕСЬ ТЕКУЩИЙ ОСТАТОК",
    }[effective_policy]
    return {
        **base,
        "decision_id": decision_id,
        "issued_at": now,
        "authority": "AI_OVERRIDE",
        "policy": effective_policy,
        "execution_status": "pending_execution",
        "manual_execution_required": True,
        "incremental_close_fraction": fraction,
        "remaining_fraction_after_action": round(1.0 - fraction, 4),
        "continuity": (
            "escalates_previous_executed_ai_plan"
            if previous_status == "executed" and current_rank > previous_rank else
            "new_ai_override"
        ),
        "supersedes_decision_id": (
            previous.get("decision_id")
            if previous_status == "pending_execution" else None
        ),
        "instruction_ru": action_ru + " СЕЙЧАС; ПОСЛЕ ВЫПОЛНЕНИЯ ПОДТВЕРДИТЬ В ТЕРМИНАЛЕ",
    }


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    result["management_arbiter"] = _arbiter(result)
    result["version"] = "quant-policy-v9-stateful-ai-first-arbiter"
    return result


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl._base,
):
    module.analyze_policies = analyze_policies

globals()["analyze_policies"] = analyze_policies
globals()["resolve_management_sequence"] = resolve_management_sequence
