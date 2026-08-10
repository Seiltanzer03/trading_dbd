"""Verdict v18: concise deterministic trade-management report for Phase E."""
from __future__ import annotations

from typing import Any

from . import ai_verdict_v17 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

# Bypass v17's old dynamic insertion while retaining the authoritative v16 base
# report.  V18 owns the complete derivative block below.
_BASE_RENDER = _impl._BASE_RENDER
_BASE_REQUEST = _impl._BASE_REQUEST
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
Формат Phase E обязателен: отдельно ЧТО УЛУЧШИЛОСЬ и ЧТО УХУДШИЛОСЬ,
даже если заполнен только один раздел. Не используй заголовок ПОЧЕМУ ИЗМЕНИЛОСЬ,
когда production/shadow action не менялись. Показывай hard pressure только для
material scenarios. derivative_switch_thresholds — deterministic cached-scenario
sensitivity, не статистическая калибровка и не обещание смены action. Все остальные
scenario weights при threshold считаются фиксированными. promotion_allowed=false.
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


def _delta_lines(rows: list[dict], limit: int = 3) -> list[str]:
    ordered = sorted(
        (row for row in rows if _number(row.get("delta")) is not None),
        key=lambda row: abs(float(row["delta"])), reverse=True,
    )
    return [
        f"{row.get('metric')}: {float(row['delta']):+.5f} vs {row.get('reference')}."
        for row in ordered[:limit]
    ]


def _dynamic_block(manager: dict) -> list[str]:
    ensemble = manager.get("derived_scenario_ensemble") or {}
    if not ensemble:
        return []
    attribution = manager.get("state_change_attribution") or {}
    shadow = manager.get("shadow_policy_contract") or {}
    production = shadow.get("old_policy") or ensemble.get("old_policy") or "—"
    candidate = shadow.get("new_candidate_policy") or ensemble.get("candidate_policy") or "—"
    candidate_changed = candidate != production
    policies = ensemble.get("policies") or {}
    candidate_row = policies.get(candidate) or {}
    scenarios = ensemble.get("scenarios") or []
    material = sorted(
        [row for row in scenarios if row.get("material") and row.get("name") != "BASE"],
        key=lambda row: float(row.get("weight") or 0.0), reverse=True,
    )
    non_material = sorted(
        [row for row in scenarios if not row.get("material") and row.get("name") != "BASE"],
        key=lambda row: float(row.get("weight") or 0.0), reverse=True,
    )

    policy_line = (
        f"Production policy не изменена: {production}. Shadow candidate: {candidate}."
        if not candidate_changed else
        f"Shadow ensemble предпочёл {candidate}, но production остаётся {production}: "
        "derived state shadow-only."
    )
    if material:
        top = material[0]
        main_reason = (
            f"{top.get('name')} — material weight "
            f"{float(top.get('weight') or 0.0) * 100:.1f}%, winner {top.get('winner')}."
        )
    elif candidate_changed:
        main_reason = (
            "Shadow candidate изменился через weighted sensitivity, но ни один "
            "derived stress не получил hard-veto materiality."
        )
    else:
        main_reason = "Material derived stress не изменил текущую policy geometry."

    improved = _delta_lines(attribution.get("what_improved") or []) or [
        "Существенного подтверждённого улучшения относительно reference нет."]
    deteriorated = _delta_lines(attribution.get("what_deteriorated") or []) or [
        "Существенного подтверждённого ухудшения относительно reference нет."]
    pressure = [
        f"{row.get('name')}: {float(row.get('weight') or 0.0) * 100:.1f}% · "
        f"confidence {float(row.get('driver_confidence') or 0.0) * 100:.0f}% · "
        f"quality {float(row.get('source_quality') or 0.0) * 100:.0f}%."
        for row in material[:4]
    ] or ["Нет derived stress, прошедшего опубликованный materiality contract."]

    ignored = [
        f"{row.get('name')}: {row.get('materiality_reason')}."
        for row in non_material[:4]
    ]
    ignored.extend(
        f"{row.get('metric')}: confidence {float(row.get('confidence') or 0) * 100:.0f}% — "
        f"{row.get('reason')}."
        for row in (attribution.get("what_did_not_influence_low_confidence") or [])[:3]
    )
    ignored.append("GEX/OI geometry: context-only; dealer inventory sign не наблюдается.")
    ignored.append("Promotion отключён: sample count сам по себе не даёт authority.")

    thresholds = []
    for row in (manager.get("derivative_switch_thresholds") or [])[:6]:
        assumptions = row.get("assumptions") or []
        equivalent = row.get("raw_metric_equivalent") or {}
        raw = _number(equivalent.get("raw_slope_threshold_per_minute"))
        raw_text = (
            f"; current-equivalent {equivalent.get('metric')} "
            f"{equivalent.get('operator')} {raw:+.6f}/min" if raw is not None else ""
        )
        thresholds.append(
            f"{row.get('driver')}: bounded weight ≥ "
            f"{float(row.get('bounded_weight_threshold') or 0):.2f} → {row.get('candidate_policy')}"
            f"{raw_text}. Sensitivity only; OOS calibrated: no. "
            f"Assumption: {assumptions[0] if assumptions else 'other weights fixed'}."
        )
    thresholds = thresholds or [
        "На текущей 0.05 sensitivity grid отдельный driver не переключает shadow candidate."]

    return [
        policy_line,
        f"Shadow metrics: Expected {_r(candidate_row.get('expected_net_r'))}; "
        f"CVaR10 {_r(candidate_row.get('cvar10_net_r'))}; "
        f"worst stress {_r(candidate_row.get('worst_stress_r'))}.", "",
        "**ГЛАВНАЯ ПРИЧИНА** —", main_reason, "",
        "**ЧТО УЛУЧШИЛОСЬ** —", *improved, "",
        "**ЧТО УХУДШИЛОСЬ** —", *deteriorated, "",
        "**ЧТО РЕАЛЬНО ДАВИТ НА РЕШЕНИЕ** —", *pressure, "",
        "**ЧТО ИГНОРИРУЕМ** —", *ignored, "",
        "**ЧТО ИЗМЕНИТ SHADOW CANDIDATE · SENSITIVITY** —", *thresholds, "",
    ]


def render_policy_report(snapshot: dict) -> str:
    text = _BASE_RENDER(snapshot)
    manager = snapshot.get("policy_manager") or {}
    block = _dynamic_block(manager)
    if not block or "**ГЛАВНАЯ ПРИЧИНА**" in text:
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
    required = (
        "**ГЛАВНАЯ ПРИЧИНА**", "**ЧТО УЛУЧШИЛОСЬ**",
        "**ЧТО УХУДШИЛОСЬ**", "**ЧТО ИГНОРИРУЕМ**",
    )
    if (result.get("model") == "deterministic-policy-fallback" or (
            has_ensemble and not all(header in result["verdict"] for header in required))):
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
