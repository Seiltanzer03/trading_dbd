"""Verdict v18: concise deterministic trade-management report for Phase E."""
from __future__ import annotations

from copy import deepcopy
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
_BASE_BUILD_SNAPSHOT = _impl.build_snapshot
SYSTEM_PROMPT = _impl.SYSTEM_PROMPT + """
Формат Phase E обязателен: отдельно ЧТО УЛУЧШИЛОСЬ и ЧТО УХУДШИЛОСЬ,
даже если заполнен только один раздел. Не используй заголовок ПОЧЕМУ ИЗМЕНИЛОСЬ,
когда production/shadow action не менялись. Показывай hard pressure только для
material scenarios. derivative_switch_thresholds — deterministic cached-scenario
sensitivity, не статистическая калибровка и не обещание смены action. Все остальные
scenario weights при threshold считаются фиксированными. promotion_allowed=false.
"""


def build_snapshot(engine) -> dict:
    """Keep the LLM/history snapshot bounded without hiding decision semantics.

    The live policy API retains the complete deterministic workspace.  The AI
    snapshot only needs published scenario attribution, not duplicated scenario
    input vectors, eligibility lists, or the raw driver workspace already present
    in option_derivative_state and state_change_attribution.
    """
    snapshot = _BASE_BUILD_SNAPSHOT(engine)
    manager = snapshot.get("policy_manager") or {}
    # ``next_attempt_ts`` is scheduler output in the future, not information
    # observed from the market at capture time.  Persisting it in the immutable
    # research snapshot both adds no explanatory value and correctly trips the
    # no-lookahead validator.  Keep the trigger/reason, but exclude the planned
    # wall-clock execution time from the decision feature set.
    triggers = manager.get("recalculation_triggers") or {}
    chain_refresh = triggers.get("chain_refresh") or {}
    chain_refresh.pop("next_attempt_ts", None)
    ensemble = manager.get("derived_scenario_ensemble") or {}
    if ensemble:
        compact = deepcopy(ensemble)
        compact.pop("drivers", None)
        compact["scenarios"] = [
            {key: row.get(key) for key in (
                "name", "weight", "winner",
                "material", "materiality_reason", "driver_confidence",
                "source_quality",
            ) if key in row}
            for row in compact.get("scenarios") or []
        ]
        # The same journal report is already present in snapshot.validation.
        compact.pop("validation_gate", None)
        manager["derived_scenario_ensemble"] = compact
    cost = manager.get("execution_cost_sensitivity") or {}
    if cost:
        manager["execution_cost_sensitivity"] = {
            key: cost.get(key) for key in (
                "type", "authority", "independent_vote", "assumed_cost",
                "baseline_full_close_cost_r", "old_policy", "candidate_policy",
                "incremental_close_fraction", "edge_robust_to_execution_cost",
                "warning", "method", "causal_pnl",
            ) if key in cost
        }
        manager["execution_cost_sensitivity"]["tested_full_close_costs_r"] = [
            row.get("full_close_cost_r") for row in cost.get("grid") or []
        ]
    mc_validation = manager.get("monte_carlo_validation") or {}
    if mc_validation:
        compact_mc = deepcopy(mc_validation)
        compact_mc["rows"] = [
            {key: row.get(key) for key in ("seed", "winner", "eligible")}
            for row in compact_mc.get("rows") or []
        ]
        manager["monte_carlo_validation"] = compact_mc
    for name, policy in (manager.get("policies") or {}).items():
        # scenario_geometry is identical for all five policies and already
        # published once at manager root.  Keep seed only on HOLD for exact
        # reproduction; policy comparison still shares that same path set.
        policy.pop("event_geometry", None)
        if name != "HOLD":
            policy.pop("monte_carlo", None)
        uncertainty = policy.get("monte_carlo_uncertainty") or {}
        if uncertainty:
            policy["monte_carlo_uncertainty"] = {
                key: uncertainty.get(key) for key in (
                    "expected_final_r", "cvar10_r", "effective_path_count")
            }
    q_calibration = engine.journal.q_calibration_report()
    manager["calibration_contract"] = {
        "version": (q_calibration.get("authority") or {}).get("version"),
        "q_probability": "risk_neutral_Q",
        "p_calibrated_shadow": None,
        "physical_probability_published": False,
        "authority": deepcopy(q_calibration.get("authority") or {}),
        "take_scorecard": {
            key: (q_calibration.get("take") or {}).get(key)
            for key in (
                "n", "event_count", "q_model_brier",
                "naive_base_rate_brier", "q_model_brier_improvement",
            )
        },
        "production_replacement_allowed": False,
    }
    evidence = manager.get("evidence") or {}
    # These are exact duplicates of canonical manager-root objects.  Retaining
    # both inflated every immutable review without adding reproducibility.
    evidence.pop("derived_scenario_ensemble", None)
    evidence.pop("lattice_revaluation", None)
    correlation = evidence.get("correlation") or {}
    # all_pairs is the authoritative full topology. The two display subsets are
    # deterministic slices of it and need not be persisted a second time.
    correlation.pop("instrument_relevant", None)
    correlation.pop("largest_changes", None)
    snapshot["policy_manager"] = manager
    return snapshot


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
    mc = manager.get("monte_carlo_validation") or {}
    mc_line = (
        f"MC robustness: winner stability {float(mc.get('winner_stability') or 0) * 100:.0f}%; "
        f"ranking agreement {float(mc.get('ranking_agreement') or 0) * 100:.0f}%; "
        + ("decision_uncertain." if mc.get("decision_uncertain") else "numerically stable on tested seeds.")
    ) if mc else "MC robustness: unavailable."
    clock = manager.get("first_touch_clock") or {}
    if clock.get("available") and clock.get("median_status") == "identified":
        clock_line = (
            "First resolution: P50 "
            f"{float(clock.get('median_resolution_minutes') or 0.0):.0f} min; "
            f"{float(clock.get('resolved_probability_horizon') or 0.0) * 100:.1f}% "
            "resolve within model horizon (TAKE vs STOP/BE)."
        )
    elif clock.get("available"):
        clock_line = (
            "First resolution: P50 beyond model horizon; "
            f"{float(clock.get('resolved_probability_horizon') or 0.0) * 100:.1f}% "
            "resolve within H."
        )
    else:
        clock_line = "First resolution: insufficient authoritative execution-MC data."
    calibration = manager.get("calibration_contract") or {}
    cal_authority = calibration.get("authority") or {}
    calibration_line = (
        "Calibration: probabilities are Q/option-implied; physical P is not "
        f"published ({cal_authority.get('status', 'insufficient_evidence')}, "
        f"N={cal_authority.get('sample_count', 0)})."
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
    cost_sensitivity = manager.get("execution_cost_sensitivity") or {}
    if cost_sensitivity.get("warning"):
        pressure.insert(0, str(cost_sensitivity["warning"]) + " — candidate edge "
                        "исчезает на опубликованной cost grid.")

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
        policy_line, mc_line, clock_line, calibration_line,
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
