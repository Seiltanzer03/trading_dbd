"""Policy manager v5: one risk/cost contract for base, stress and authority tests."""
from __future__ import annotations

from dataclasses import replace

from . import ai_policy_v4 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_HARD_CVAR_SELECT = _impl._BASE_SELECT


def stability_analysis(inputs: PolicyInputs, base_choice: str) -> dict:
    """Run local stresses under the same CVaR floor and net-cost model as base."""
    scenarios = [("base", inputs)]
    scenarios += [
        (f"price_{delta:+.2f}R", replace(
            inputs,
            r0=min(max(inputs.r0 + delta, -0.98), inputs.T - 0.02),
            max_r=max(
                inputs.max_r,
                min(max(inputs.r0 + delta, -0.98), inputs.T - 0.02),
            ),
        ))
        for delta in (-0.10, 0.10)
    ]
    scenarios += [
        (f"sigma_{mult:.2f}x", replace(
            inputs, sigma_R=max(0.08, inputs.sigma_R * mult)))
        for mult in (0.95, 1.05)
    ]
    scenarios += [
        (f"drift_{delta:+.2f}R", replace(
            inputs, drift_R=inputs.drift_R + delta))
        for delta in (-0.04, 0.04)
    ]
    scenarios += [
        (f"skew_{delta:+.2f}", replace(
            inputs,
            skew_R=min(max(inputs.skew_R + delta, -0.45), 0.45),
        ))
        for delta in (-0.05, 0.05)
    ]
    scenarios += [
        (f"term_{delta:+.2f}", replace(
            inputs,
            term_slope=min(max(inputs.term_slope + delta, -0.8), 0.8),
        ))
        for delta in (-0.10, 0.10)
    ]

    winners = {name: 0 for name in POLICY_FRACTIONS}
    feasible = {name: 0 for name in POLICY_FRACTIONS}
    rows = []
    for label, scenario in scenarios:
        metrics, _ = _run_once(
            scenario, n_paths=1400, n_steps=180, seed=0xB100)
        floor = _floor_for_r(scenario.r0)
        choice, rule = _raw_policy_choice(
            metrics, scenario.r0, cvar_floor=floor)
        winners[choice] += 1
        for name in rule.get("eligible", []):
            feasible[name] += 1
        rows.append({
            "variant": label,
            "winner": choice,
            "cvar_floor_r": rule.get("cvar_floor_r"),
            "eligible": list(rule.get("eligible") or []),
            "expected_r": {
                name: metrics[name].get("expected_final_r")
                for name in POLICY_FRACTIONS
            },
        })

    total = len(scenarios)
    stats = {
        name: {
            "winner_count": winners[name],
            "winner_share": round(winners[name] / total, 4),
            "feasible_count": feasible[name],
            "feasible_share": round(feasible[name] / total, 4),
        }
        for name in POLICY_FRACTIONS
    }
    return {
        "checks": total,
        "selected_policy": base_choice,
        "selected_count": winners.get(base_choice, 0),
        "selected_share": round(winners.get(base_choice, 0) / total, 4),
        "winner_counts": winners,
        "winner_shares": {
            name: row["winner_share"] for name, row in stats.items()
        },
        "policy_stats": stats,
        "rows": rows,
        "risk_rule": (
            "same active stop/BE/trailing CVaR floor as base calculation"
        ),
        "cost_rule": "same net execution-cost model as base calculation",
        "perturbations": (
            "r±0.10R; sigma±5%; drift±0.04R; skew±0.05; term±0.10"
        ),
    }


def authority_stability(inputs: PolicyInputs,
                        cvar_floor: float | None = None) -> dict:
    """Remove uncertain inputs without changing the base risk/cost contract."""
    variants = [
        ("base", inputs),
        ("drift_50pct", replace(inputs, drift_R=inputs.drift_R * 0.50)),
        ("drift_zero", replace(inputs, drift_R=0.0)),
        ("skew_zero", replace(inputs, skew_R=0.0)),
        ("term_zero", replace(inputs, term_slope=0.0)),
        ("shape_neutral", replace(
            inputs, drift_R=0.0, skew_R=0.0, term_slope=0.0)),
        ("sigma_minus_15pct", replace(
            inputs, sigma_R=max(0.08, inputs.sigma_R * 0.85))),
        ("sigma_plus_15pct", replace(
            inputs, sigma_R=max(0.08, inputs.sigma_R * 1.15))),
    ]
    winners = {name: 0 for name in POLICY_FRACTIONS}
    feasible = {name: 0 for name in POLICY_FRACTIONS}
    rows = []
    for index, (label, scenario) in enumerate(variants):
        metrics, _ = _run_once(
            scenario, n_paths=1000, n_steps=150, seed=0xC300 + index)
        floor = _floor_for_r(scenario.r0)
        if floor is None:
            floor = cvar_floor
        choice, rule = _raw_policy_choice(
            metrics, scenario.r0, cvar_floor=floor)
        winners[choice] += 1
        for name in rule.get("eligible", []):
            feasible[name] += 1
        rows.append({
            "variant": label,
            "winner": choice,
            "cvar_floor_r": rule.get("cvar_floor_r"),
            "eligible": list(rule.get("eligible") or []),
            "hold_expected_r": metrics["HOLD"].get("expected_final_r"),
            "hold_cvar10_r": metrics["HOLD"].get("cvar10_r"),
        })
    total = len(variants)
    return {
        "checks": total,
        "winner_counts": winners,
        "winner_shares": {
            name: round(count / total, 4)
            for name, count in winners.items()
        },
        "feasible_counts": feasible,
        "variants": rows,
        "risk_rule": (
            "same active stop/BE/trailing CVaR floor as base calculation"
        ),
        "cost_rule": "same net execution-cost model as base calculation",
        "description": "drift/skew/term neutralisation and sigma ±15%",
    }


def select_final_policy(raw_choice: str, stability: dict,
                        metrics: dict[str, dict], evidence: dict,
                        inputs: PolicyInputs, selection_rule: dict) -> dict:
    """Apply one hard-risk contract and forbid unsupported HOLD reductions."""
    result = _BASE_HARD_CVAR_SELECT(
        raw_choice, stability, metrics, evidence, inputs, selection_rule)
    floor = float(selection_rule.get("cvar_floor_r", -1.0))
    source_stability = authority_stability(inputs, floor)
    selected = result.get("policy") or raw_choice
    source_share = float(
        (source_stability.get("winner_shares") or {}).get(selected, 0.0))

    reliability = _at(
        evidence, "data_quality", "reliability", default={}) or {}
    level = reliability.get("level") or "не определена"
    known_reliability = level in {"высокая", "средняя", "низкая"}
    families = list(evidence.get("adverse_confirmation_families") or [])
    reasons = list(result.get("reasons") or [])
    base_status = result.get("status") or "conflict"
    status = base_status
    executable_statuses = {"confirmed", "downgraded_within_feasible_set"}
    executable = base_status in executable_statuses

    threshold = {
        "HOLD": 0.0,
        "CLOSE_10": 0.45,
        "CLOSE_25": 0.50,
        "CLOSE_50": 0.625,
        "EXIT": 0.75,
    }.get(selected, 1.0)
    if executable and source_share < threshold:
        executable = False
        status = "manual_source_conflict"
        reasons.append(
            f"устойчивость к источнику данных {source_share:.0%} "
            f"ниже {threshold:.0%}")

    if known_reliability and level == "низкая":
        executable = False
        if base_status not in {
            "conflict_stability_fallback", "manual_conflict"
        }:
            status = "manual_data_conflict"
        reasons.append("надёжность расчёта низкая")
    elif (known_reliability and selected == "EXIT"
          and not reliability.get("full_exit_authority", False)):
        executable = False
        if base_status not in {
            "conflict_stability_fallback", "manual_conflict"
        }:
            status = "manual_data_conflict"
        reasons.append(
            "EXIT требует высокой надёжности, live/direct цепочки и непрокси IV")

    if status not in executable_statuses:
        executable = False

    result.update({
        "status": status,
        "reasons": list(dict.fromkeys(reasons)),
        "authority_stability": source_stability,
        "confirmation_families": families,
        "confirmation_count": len(families),
        "mixed_confirmation_families": (
            evidence.get("mixed_confirmation_families") or []),
        "source_stability_share": source_share,
        "data_reliability": level,
        "automatic_execution_allowed": executable,
        "execution_policy": selected if executable else None,
        "provisional_policy": selected,
    })

    eligible = list(selection_rule.get("eligible") or [])
    if (raw_choice == "HOLD" and "HOLD" in eligible
            and not families and selected != "HOLD"):
        rejected = selected
        cleaned = [
            reason for reason in result.get("reasons") or []
            if "исходная политика имела устойчивость 0%" not in reason
            and "выбран устойчивый вариант" not in reason
        ]
        cleaned.append(
            "HOLD сохранён: он проходит CVaR и нет независимых "
            "подтверждений сокращения")
        result.update({
            "policy": "HOLD",
            "provisional_policy": "HOLD",
            "execution_policy": None,
            "status": "hold_no_reduction_evidence",
            "automatic_execution_allowed": False,
            "rejected_stress_fallback": rejected,
            "reasons": list(dict.fromkeys(cleaned)),
            "source_stability_share": float(
                (source_stability.get("winner_shares") or {}).get(
                    "HOLD", 0.0)
            ),
        })
    return result


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._base,
):
    module.stability_analysis = stability_analysis
    module.authority_stability = authority_stability
    module.select_final_policy = select_final_policy

globals()["stability_analysis"] = stability_analysis
globals()["authority_stability"] = authority_stability
globals()["select_final_policy"] = select_final_policy
