"""Stable public facade for the quantitative AI policy manager v3.

Adds model-risk controls on top of the hard-CVaR selector:
- correlated option metrics count as one evidence family;
- anomalous or delayed IV skew is context, not confirmation;
- parameter stability is separated from source-authority stability;
- low-quality data cannot confirm an irreversible EXIT recommendation.
"""
from __future__ import annotations

from dataclasses import replace

from . import ai_policy_v2 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})

_ORIGINAL_BUILD_METRIC_EVIDENCE = _impl.build_metric_evidence
_ORIGINAL_SELECT_FINAL_POLICY = _impl.select_final_policy
_ORIGINAL_ANALYZE_POLICIES = _impl.analyze_policies


def _local24(evidence: dict) -> dict:
    rows = _impl._at(evidence, "iv_surface", "local_24h", default=[]) or []
    return next(
        (row for row in rows if _impl._num(row.get("hours")) == 24.0),
        {},
    )


def _confirmation_family(metric: str) -> str:
    """Map correlated observations to one independent evidence family."""
    name = str(metric or "unknown")
    lower = name.lower()
    if any(token in lower for token in (
        "barrier", "rnd_", "skew", "stop_vs_next_rung", "next_rung_vs_stop",
    )):
        return "option_distribution"
    if lower.startswith("live_") or "tape" in lower:
        return "live_tape"
    if any(token in lower for token in (
        "volume_delta", "accepted_value", "level", "vwap", "poc", "value_area",
    )):
        return "orderflow_levels"
    if "filter" in lower:
        return "strategy_filters"
    if any(token in lower for token in ("atr", "vrp", "regime", "sigma")):
        return "volatility_regime"
    return name


def build_metric_evidence(engine, tick: dict, ridge: dict, trade: dict,
                          inputs: _impl.PolicyInputs, sim: _impl.PathSimulation,
                          policy_metrics_map: dict[str, dict]) -> dict:
    """Enforce evidence independence and authority before the action gate."""
    evidence = _ORIGINAL_BUILD_METRIC_EVIDENCE(
        engine, tick, ridge, trade, inputs, sim, policy_metrics_map)
    adverse = list(evidence.get("adverse_confirmations") or [])
    supportive = list(evidence.get("supportive_contradictions") or [])
    context = list(evidence.get("context_observations") or [])
    flags = list(evidence.get("uncertainty_flags") or [])

    local24 = _local24(evidence)
    skew = abs(_impl._num(local24.get("put_call_skew_pp")) or 0.0)
    curvature = abs(_impl._num(local24.get("curvature_pp")) or 0.0)
    chain_status = str(inputs.chain_status or "unknown").lower()
    proxy = str(inputs.proxy_quality or "unknown").lower()
    proxy_degraded = any(token in proxy for token in (
        "proxy", "reference", "fallback", "derived", "synthetic",
    ))
    chain_trusted = chain_status in {"ok", "live", "demo"}
    anomalous_iv = skew >= 8.0 or curvature >= 15.0

    # A delayed/anomalous smile may still be displayed, but it cannot vote for an
    # action. This also prevents the same option chain from confirming itself.
    if anomalous_iv or not chain_trusted or proxy_degraded:
        removed = []
        kept_adverse = []
        for item in adverse:
            if item.get("metric") == "local24h_put_call_skew_pp":
                removed.append(item)
            else:
                kept_adverse.append(item)
        kept_supportive = []
        for item in supportive:
            if item.get("metric") == "local24h_put_call_skew_pp":
                removed.append(item)
            else:
                kept_supportive.append(item)
        adverse, supportive = kept_adverse, kept_supportive
        for item in removed:
            context.append({
                **item,
                "context_only": True,
                "authority": "unreliable_iv_context",
                "reason": (
                    "аномальный skew/curvature" if anomalous_iv
                    else "цепочка или proxy не имеют live-authority"
                ),
            })
        if removed or anomalous_iv:
            flags.append({
                "metric": "iv_skew_authority_block",
                "chain_status": chain_status,
                "proxy_quality": inputs.proxy_quality,
                "skew_pp": round(skew, 3),
                "curvature_pp": round(curvature, 3),
                "reason": "IV skew исключён из независимых подтверждений",
            })

    for collection in (adverse, supportive, context):
        for item in collection:
            item.setdefault("family", _confirmation_family(item.get("metric")))

    adverse_families = sorted({
        item["family"] for item in adverse if not item.get("context_only")
    })
    supportive_families = sorted({
        item["family"] for item in supportive if not item.get("context_only")
    })

    evidence["adverse_confirmations"] = adverse
    evidence["supportive_contradictions"] = supportive
    evidence["context_observations"] = context
    evidence["uncertainty_flags"] = flags
    evidence["adverse_confirmation_item_count"] = sum(
        1 for item in adverse if not item.get("context_only"))
    evidence["adverse_confirmation_families"] = adverse_families
    evidence["supportive_confirmation_families"] = supportive_families
    # The gate consumes independent families, not correlated metric rows.
    evidence["adverse_confirmation_count"] = len(adverse_families)
    evidence["confirmation_independence"] = {
        "adverse_items": evidence["adverse_confirmation_item_count"],
        "adverse_families": len(adverse_families),
        "families": adverse_families,
        "rule": "несколько метрик одной опционной цепочки считаются одним источником",
    }

    reliability = _impl._at(
        evidence, "data_quality", "reliability", default={}) or {}
    reliability["chain_trusted"] = chain_trusted
    reliability["proxy_degraded"] = proxy_degraded
    reliability["full_exit_authority"] = bool(
        reliability.get("level") == "высокая"
        and chain_trusted
        and not proxy_degraded
        and not anomalous_iv
    )
    evidence.setdefault("data_quality", {})["reliability"] = reliability
    return evidence


def authority_stability(inputs: _impl.PolicyInputs, cvar_floor: float) -> dict:
    """Stress whether the winner survives removal of uncertain option inputs.

    The existing 11 checks perturb parameters locally. These variants answer a
    different question: does the recommendation survive when drift/skew/term are
    shrunk or neutralised because their source is delayed or anomalous?
    """
    variants = [
        ("base", inputs),
        ("drift_50pct", replace(inputs, drift_R=inputs.drift_R * 0.50)),
        ("drift_zero", replace(inputs, drift_R=0.0)),
        ("skew_zero", replace(inputs, skew_R=0.0)),
        ("term_zero", replace(inputs, term_slope=0.0)),
        ("shape_neutral", replace(inputs, drift_R=0.0, skew_R=0.0, term_slope=0.0)),
        ("sigma_minus_15pct", replace(inputs, sigma_R=max(0.08, inputs.sigma_R * 0.85))),
        ("sigma_plus_15pct", replace(inputs, sigma_R=max(0.08, inputs.sigma_R * 1.15))),
    ]
    winners = {name: 0 for name in _impl.POLICY_FRACTIONS}
    feasible = {name: 0 for name in _impl.POLICY_FRACTIONS}
    rows = []
    for index, (label, scenario) in enumerate(variants):
        metrics, _ = _impl._run_once(
            scenario, n_paths=1000, n_steps=150, seed=0xC300 + index)
        choice, rule = _impl._raw_policy_choice(
            metrics, scenario.r0, cvar_floor=cvar_floor)
        winners[choice] += 1
        for name in rule.get("eligible", []):
            feasible[name] += 1
        rows.append({
            "variant": label,
            "winner": choice,
            "eligible": list(rule.get("eligible") or []),
            "hold_expected_r": metrics["HOLD"]["expected_final_r"],
            "hold_cvar10_r": metrics["HOLD"]["cvar10_r"],
        })
    total = len(variants)
    return {
        "checks": total,
        "winner_counts": winners,
        "winner_shares": {
            name: round(count / total, 4) for name, count in winners.items()
        },
        "feasible_counts": feasible,
        "variants": rows,
        "description": "drift/skew/term neutralisation and sigma ±15%",
    }


def select_final_policy(raw_choice: str, stability: dict,
                        metrics: dict[str, dict], evidence: dict,
                        inputs: _impl.PolicyInputs, selection_rule: dict) -> dict:
    """Apply hard CVaR, independent-evidence and model-authority gates."""
    result = _ORIGINAL_SELECT_FINAL_POLICY(
        raw_choice, stability, metrics, evidence, inputs, selection_rule)
    floor = float(selection_rule.get("cvar_floor_r", -0.60))
    source_stability = authority_stability(inputs, floor)
    result["authority_stability"] = source_stability

    reliability = _impl._at(
        evidence, "data_quality", "reliability", default={}) or {}
    level = reliability.get("level") or "не определена"
    families = list(evidence.get("adverse_confirmation_families") or [])
    selected = result.get("policy") or raw_choice
    source_share = float(
        (source_stability.get("winner_shares") or {}).get(selected, 0.0))
    result["confirmation_families"] = families
    result["confirmation_count"] = len(families)
    result["source_stability_share"] = source_share
    result["data_reliability"] = level

    reasons = list(result.get("reasons") or [])
    status = result.get("status") or "conflict"
    executable = status in {"confirmed", "downgraded_within_feasible_set"}

    source_thresholds = {
        "HOLD": 0.00,
        "CLOSE_10": 0.45,
        "CLOSE_25": 0.50,
        "CLOSE_50": 0.625,
        "EXIT": 0.75,
    }
    required_share = source_thresholds.get(selected, 1.0)
    if source_share < required_share:
        executable = False
        status = "manual_source_conflict"
        reasons.append(
            f"устойчивость к отключению ненадёжных входов {source_share:.0%} "
            f"ниже {required_share:.0%}")

    # Low reliability invalidates the word 'confirmed' for every action. Full
    # exit is additionally restricted to a trusted direct/live option source.
    if level == "низкая":
        executable = False
        status = "manual_data_conflict"
        reasons.append("надёжность расчёта низкая: действие не подтверждено")
    elif selected == "EXIT" and not reliability.get("full_exit_authority", False):
        executable = False
        status = "manual_data_conflict"
        reasons.append(
            "EXIT запрещён без высокой надёжности, live-цепочки и непрокси IV")

    if status not in {"confirmed", "downgraded_within_feasible_set"}:
        executable = False
    result["status"] = status
    result["reasons"] = list(dict.fromkeys(reasons))
    result["automatic_execution_allowed"] = executable
    result["execution_policy"] = selected if executable else None
    result["provisional_policy"] = selected
    return result


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None) -> dict:
    result = _ORIGINAL_ANALYZE_POLICIES(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    gate = result.get("gate") or {}
    rec = result.get("recommendation") or {}
    computed_action = rec.get("action_ru") or rec.get("policy")
    rec["computed_action_ru"] = computed_action
    rec["execution_action_ru"] = (
        computed_action if gate.get("automatic_execution_allowed")
        else f"НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ; расчётная политика — {rec.get('policy')}"
    )
    rec["post_execution_applicable"] = bool(
        gate.get("automatic_execution_allowed"))
    if rec.get("policy") == "EXIT":
        rec["remaining_management"] = (
            "позиция закрыта полностью; стоп, БУ/trailing и следующий рубеж не применяются"
            if gate.get("automatic_execution_allowed")
            else "полное закрытие не подтверждено; параметры сопровождения не изменять по этому отчёту"
        )
        rec["next_rung_r"] = None
    result["recommendation"] = rec
    result["version"] = "quant-policy-v3-authority-gate"
    return result


def cancellation_boundaries(inputs: _impl.PolicyInputs, selected: str) -> dict:
    """Recompute the nearest r-level where the raw optimizer switches to HOLD."""
    if selected == "HOLD":
        return {
            "available": False,
            "reason": "Для HOLD границы отмены до исполнения нет; переоценка при движении ±0.15R, новой цепочке или касании рубежа.",
        }
    grid = _impl.np.linspace(
        max(-0.95, inputs.r0 - 0.50),
        min(inputs.T - 0.02, inputs.r0 + 0.50),
        21,
    )
    rows = []
    for r_value in grid:
        scenario = replace(
            inputs,
            r0=float(r_value),
            max_r=max(inputs.max_r, float(r_value)),
        )
        metrics, sim = _impl._run_once(
            scenario, n_paths=1200, n_steps=160, seed=0xD000)
        choice, _ = _impl._raw_policy_choice(metrics, scenario.r0)
        p_take = float(_impl.np.mean(~_impl.np.isnan(sim.take_time)))
        p_stop = float(_impl.np.mean(~_impl.np.isnan(sim.stop_time)))
        rows.append({
            "r": round(float(r_value), 4),
            "choice": choice,
            "barrier_ev_r": round(inputs.T * p_take - p_stop, 4),
        })
    hold_rows = [row for row in rows if row["choice"] == "HOLD"]
    nearest = min(
        hold_rows, key=lambda row: abs(row["r"] - inputs.r0)
    ) if hold_rows else None
    return {
        "available": bool(nearest),
        "hold_switch": nearest,
        "grid_min_r": rows[0]["r"],
        "grid_max_r": rows[-1]["r"],
        "method": "пересчёт всех политик по r-сетке; остальные опционные параметры фиксированы",
        "reason": None if nearest else "На проверенной r-сетке переход к HOLD не найден.",
    }


# ai_policy_v2 functions resolve globals in that module. Rebind every overridden
# safety-critical function before build_snapshot imports the public facade.
_impl.build_metric_evidence = build_metric_evidence
_impl._base.build_metric_evidence = build_metric_evidence
_impl.select_final_policy = select_final_policy
_impl._base.select_final_policy = select_final_policy
_impl.cancellation_boundaries = cancellation_boundaries
_impl._base.cancellation_boundaries = cancellation_boundaries
_impl.analyze_policies = analyze_policies

globals()["build_metric_evidence"] = build_metric_evidence
globals()["select_final_policy"] = select_final_policy
globals()["cancellation_boundaries"] = cancellation_boundaries
globals()["analyze_policies"] = analyze_policies
