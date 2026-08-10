"""Verdict v17: deterministic state-change and shadow-policy attribution."""
from __future__ import annotations

from typing import Any

from . import ai_verdict_v16 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RENDER = _impl.render_policy_report
_BASE_REQUEST = _impl.request_verdict
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
option_derivative_state, interaction_state и derived_scenario_ensemble являются
одной option_distribution family и не считаются несколькими подтверждениями.
Числа, scenario weights, candidate policy и derivative thresholds уже рассчитаны
детерминированно; не пересчитывай и не придумывай их. Пока promotion_allowed=false,
shadow candidate не меняет ДЕЙСТВИЕ СЕЙЧАС. Обязательно прямо скажи, повлиял ли
derived state на production action, и перечисли сигналы с низкой confidence,
которые были проигнорированы.
"""


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _r(value: Any) -> str:
    value = _number(value)
    return "—" if value is None else f"{value:+.3f}R"


def _delta_lines(rows: list[dict], limit: int = 4) -> list[str]:
    return [
        f"{row.get('metric')}: изменение {row.get('delta'):+.5f} "
        f"от {row.get('reference')}."
        for row in rows[:limit]
        if _number(row.get("delta")) is not None
    ]


def _dynamic_block(manager: dict) -> list[str]:
    ensemble = manager.get("derived_scenario_ensemble") or {}
    attribution = manager.get("state_change_attribution") or {}
    shadow = manager.get("shadow_policy_contract") or {}
    if not ensemble:
        return []
    old = shadow.get("old_policy") or ensemble.get("old_policy") or "—"
    candidate = shadow.get("new_candidate_policy") or ensemble.get("candidate_policy") or "—"
    policies = ensemble.get("policies") or {}
    candidate_row = policies.get(candidate) or {}
    scenarios = ensemble.get("scenarios") or []
    supporting = [row for row in scenarios if row.get("winner") == candidate
                  and (_number(row.get("weight")) or 0.0) > 0.0]
    contradicting = [row for row in scenarios if row.get("winner") != candidate
                     and (_number(row.get("weight")) or 0.0) > 0.0]
    ignored = attribution.get("what_did_not_influence_low_confidence") or []
    thresholds = manager.get("derivative_switch_thresholds") or []
    improved = _delta_lines(attribution.get("what_improved") or [])
    deteriorated = _delta_lines(attribution.get("what_deteriorated") or [])

    why = deteriorated[:3] or improved[:3] or [
        "Достаточной истории для направленного сравнения пока нет."
    ]
    why.append(
        f"Production policy: {old}; shadow candidate: {candidate}. "
        f"{attribution.get('explicit_policy_effect') or 'Production action unchanged'}."
    )
    why.append(
        f"Shadow candidate metrics: Expected net {_r(candidate_row.get('expected_net_r'))}; "
        f"CVaR10 net {_r(candidate_row.get('cvar10_net_r'))}; "
        f"worst stress {_r(candidate_row.get('worst_stress_r'))}."
    )

    confirm = [
        f"{row['name']}: вес {float(row.get('weight') or 0) * 100:.1f}%, "
        f"winner {row.get('winner')}."
        for row in supporting[:4]
    ] or ["Нет material stress-сценария, отдельно поддерживающего shadow candidate."]
    oppose = [
        f"{row['name']}: вес {float(row.get('weight') or 0) * 100:.1f}%, "
        f"winner {row.get('winner')}."
        for row in contradicting[:4]
    ] or ["Material stress-сценарии не дали отдельного противоречия."]
    insufficient = [
        f"{row.get('metric')}: confidence {float(row.get('confidence') or 0) * 100:.0f}% — "
        f"{row.get('reason')}."
        for row in ignored[:5]
    ]
    validation = ensemble.get("validation_gate") or {}
    insufficient.append(
        "Shadow authority не повышен: "
        + str(validation.get("promotion_reason") or "manual OOS calibration required") + "."
    )
    insufficient.append(
        "GEX/OI geometry остаётся context-only: фактический знак dealer position не наблюдается."
    )
    switches = []
    for row in thresholds[:6]:
        raw = _number(row.get("raw_slope_threshold_per_minute"))
        raw_text = (
            f"; {row.get('metric')} slope {row.get('operator')} {raw:+.6f}/min"
            if raw is not None else ""
        )
        switches.append(
            f"{row.get('driver')}: bounded stress weight ≥ "
            f"{float(row.get('bounded_weight_threshold') or 0):.2f}{raw_text} → "
            f"candidate {row.get('candidate_policy')} "
            f"(deterministic stress reweighting, not LLM)."
        )
    switches = switches or [
        "В пределах откалиброванной stress-сетки derivative driver сам по себе "
        "не переключает policy."
    ]
    return [
        "**ПОЧЕМУ ИЗМЕНИЛОСЬ** —", *why, "",
        "**ЧТО ПОДТВЕРЖДАЕТ** —", *confirm, "",
        "**ЧТО ПРОТИВОРЕЧИТ** —", *oppose, "",
        "**ЧТО НЕ ИМЕЕТ ДОСТАТОЧНОГО ВЕСА** —", *insufficient, "",
        "**ЧТО ИЗМЕНИТ РЕШЕНИЕ · DERIVATIVE THRESHOLDS** —", *switches, "",
    ]


def render_policy_report(snapshot: dict) -> str:
    text = _BASE_RENDER(snapshot)
    manager = snapshot.get("policy_manager") or {}
    block = _dynamic_block(manager)
    if not block or "**ПОЧЕМУ ИЗМЕНИЛОСЬ**" in text:
        return text
    lines = text.splitlines()
    insert_at = next((index + 1 for index, line in enumerate(lines)
                      if line.startswith("**ДЕЙСТВИЕ")), 0)
    lines[insert_at:insert_at] = ["", *block]
    return "\n".join(lines).strip()


def request_verdict(snapshot: dict) -> dict:
    result = _BASE_REQUEST(snapshot)
    if not isinstance(result, dict) or not isinstance(result.get("verdict"), str):
        return result
    result = dict(result)
    has_ensemble = bool(
        (snapshot.get("policy_manager") or {}).get("derived_scenario_ensemble"))
    if result.get("model") == "deterministic-policy-fallback":
        result["verdict"] = render_policy_report(snapshot)
    elif has_ensemble and "**ПОЧЕМУ ИЗМЕНИЛОСЬ**" not in result["verdict"]:
        result["verdict"] = render_policy_report(snapshot)
        result["model"] = "deterministic-policy-fallback"
    return result


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
globals()["request_verdict"] = request_verdict
