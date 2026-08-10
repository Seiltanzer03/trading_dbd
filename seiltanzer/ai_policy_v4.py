"""AI policy manager v4: clear action, strategy risk and net execution costs."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from . import ai_policy_v3 as _impl
from .mc_validation import seed_robustness

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_COST_CTX: ContextVar[dict | None] = ContextVar("ai_policy_costs", default=None)
_RISK_CTX: ContextVar[dict | None] = ContextVar("ai_policy_risk", default=None)

_BASE_RUN_ONCE = _impl._impl._run_once
_BASE_RAW_CHOICE = _impl._impl._raw_policy_choice
_BASE_BUILD_EVIDENCE = _impl.build_metric_evidence
_BASE_ANALYZE = _impl.analyze_policies
_BASE_SELECT = _impl._ORIGINAL_SELECT_FINAL_POLICY

_ACTIONS_RU = {
    "HOLD": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
    "CLOSE_10": "ЗАКРЫТЬ 10% ПОЗИЦИИ СЕЙЧАС",
    "CLOSE_25": "ЗАКРЫТЬ 25% ПОЗИЦИИ СЕЙЧАС",
    "CLOSE_50": "ЗАКРЫТЬ 50% ПОЗИЦИИ СЕЙЧАС",
    "EXIT": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
}


def _num(value: Any) -> float | None:
    return _impl._impl._num(value)


def _at(value: Any, *path: str, default=None):
    return _impl._impl._at(value, *path, default=default)


def _first_num(sources: list[dict], paths: tuple[tuple[str, ...], ...]):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for path in paths:
            value = _num(_at(source, *path))
            if value is not None:
                return value, ".".join(path)
    return None, None


def execution_cost_model(tick: dict, trade: dict) -> dict:
    """Use explicit R-costs, otherwise a visible 0.01R fallback."""
    sources = [trade or {}, tick or {}]
    total, total_key = _first_num(sources, (
        ("execution_cost_r",), ("costs", "total_close_r"),
        ("execution", "total_close_r"), ("market", "execution_cost_r"),
    ))
    parts = []
    keys = []
    for paths in (
        (("spread_r",), ("costs", "spread_r"), ("feeds", "price", "spread_r")),
        (("commission_r",), ("costs", "commission_r")),
        (("slippage_r",), ("costs", "slippage_r")),
        (("execution_delay_r",), ("costs", "delay_r")),
    ):
        value, key = _first_num(sources, paths)
        if value is not None:
            parts.append(max(0.0, value))
            keys.append(key)
    if total is not None:
        immediate, assumed, source = max(0.0, total), False, total_key
    elif parts:
        immediate, assumed, source = sum(parts), False, ", ".join(keys)
    else:
        immediate, assumed = 0.01, True
        source = "fallback 0.01R: broker execution costs unavailable"
    deferred, deferred_key = _first_num(sources, (
        ("deferred_execution_cost_r",), ("costs", "deferred_close_r"),
    ))
    if deferred is None:
        deferred = immediate
    return {
        "immediate_full_close_r": round(immediate, 6),
        "deferred_full_close_r": round(max(0.0, deferred), 6),
        "assumed": assumed,
        "source": source,
        "deferred_source": deferred_key or source,
    }


def risk_constraint(inputs: PolicyInputs, tick: dict, trade: dict) -> dict:
    """Tie hard CVaR to active stop/BE/trailing, never an invented 0.80R cap."""
    sources = [trade or {}, tick or {}]
    stop, stop_key = _first_num(sources, (
        ("effective_stop_r",), ("trailing_stop_r",), ("stop_r",),
        ("management", "effective_stop_r"), ("management", "stop_r"),
        ("risk", "effective_stop_r"), ("ladder", "effective_stop_r"),
        ("ladder", "stop_r"), ("prob", "stop_r"),
    ))
    if stop is None:
        be_armed = inputs.max_r >= inputs.be_after - 1e-12
        stop = 0.0 if be_armed else -1.0
        stop_source = (
            f"strategy BE floor 0R: max_r {inputs.max_r:.3f}R >= {inputs.be_after:.3f}R"
            if be_armed else "strategy initial stop -1R: BE threshold not reached"
        )
    else:
        stop_source = f"explicit {stop_key}"
    max_giveback, giveback_key = _first_num(sources, (
        ("max_giveback_r",), ("management", "max_giveback_r"),
        ("risk", "max_giveback_r"), ("ladder", "max_giveback_r"),
    ))
    giveback_floor = (
        inputs.r0 - max_giveback
        if max_giveback is not None and max_giveback >= 0.0 else None
    )
    floor = float(stop) if giveback_floor is None else max(float(stop), giveback_floor)
    return {
        "cvar_floor_r": round(floor, 4),
        "effective_stop_floor_r": round(float(stop), 4),
        "max_giveback_r": round(float(max_giveback), 4) if max_giveback is not None else None,
        "giveback_floor_r": round(float(giveback_floor), 4) if giveback_floor is not None else None,
        "source": stop_source,
        "max_giveback_source": giveback_key,
        "rule": (
            "max(active stop/BE/trailing, r0-max_giveback_r)"
            if giveback_floor is not None
            else "active stop/BE/trailing; no invented profit-giveback cap"
        ),
    }


def _floor_for_r(r_value: float) -> float | None:
    spec = _RISK_CTX.get()
    if not spec:
        return None
    floor = float(spec["effective_stop_floor_r"])
    giveback = _num(spec.get("max_giveback_r"))
    if giveback is not None:
        floor = max(floor, float(r_value) - giveback)
    return floor


def _event_geometry(sim: PathSimulation, inputs: PolicyInputs) -> dict:
    next_rung = _impl._impl._next_rung(inputs)
    stop_t = _impl._impl._strategy_risk_exit_time(sim)
    rung_t = sim.rung_times.get(next_rung) if next_rung is not None else sim.take_time
    if rung_t is None:
        rung_t = _impl._impl.np.full_like(stop_t, _impl._impl.np.nan)
    np = _impl._impl.np
    rung_first = ~np.isnan(rung_t) & (np.isnan(stop_t) | (rung_t < stop_t))
    stop_first = ~np.isnan(stop_t) & (np.isnan(rung_t) | (stop_t < rung_t))
    resolved = rung_first | stop_first
    event_t = np.where(rung_first, rung_t, np.where(stop_first, stop_t, np.nan))
    n = int(stop_t.size)
    rung_count = int(np.count_nonzero(rung_first))
    stop_count = int(np.count_nonzero(stop_first))
    unresolved_count = n - rung_count - stop_count
    windows = {}
    for minutes in (15, 30, 60, 120):
        frac = min(minutes / max(inputs.horizon_minutes, 1.0), 1.0)
        count = int(np.count_nonzero(resolved & (event_t <= frac + 1e-12)))
        windows[f"{minutes}m"] = {
            "events": count, "no_event_count": n - count, "scenarios": n,
            "event_probability": round(count / max(n, 1), 6),
            "no_event_probability": round(1.0 - count / max(n, 1), 6),
        }
    resolved_times = event_t[resolved]
    return {
        "execution_contract": sim.execution_contract or {},
        "scenario_count": n,
        "next_rung_r": _impl._impl._rnd(next_rung, 3),
        "rung_first_count": rung_count,
        "stop_first_count": stop_count,
        "unresolved_count": unresolved_count,
        "resolved_count": int(resolved_times.size),
        "p_next_rung_before_stop": round(rung_count / max(n, 1), 6),
        "p_stop_before_next_rung": round(stop_count / max(n, 1), 6),
        "p_unresolved_full_horizon": round(unresolved_count / max(n, 1), 6),
        "full_horizon_minutes": round(float(inputs.horizon_minutes), 1),
        "mean_event_minutes_given_resolved": (
            round(float(np.mean(resolved_times)) * inputs.horizon_minutes, 1)
            if resolved_times.size else None
        ),
        "no_event_windows": windows,
    }


def _run_once(inputs: PolicyInputs, *, n_paths: int, n_steps: int, seed: int):
    """Existing simulator with deterministic close costs included in outcomes."""
    costs = _COST_CTX.get()
    if not costs:
        return _BASE_RUN_ONCE(inputs, n_paths=n_paths, n_steps=n_steps, seed=seed)
    np = _impl._impl.np
    sim = _impl._impl.simulate_option_paths(inputs, n_paths=n_paths, n_steps=n_steps, seed=seed)
    baseline = _impl._impl.baseline_strategy_outcomes(sim, inputs)
    immediate = float(costs["immediate_full_close_r"])
    deferred = float(costs["deferred_full_close_r"])
    metrics = {}
    geometry = _event_geometry(sim, inputs)
    for name, fraction in POLICY_FRACTIONS.items():
        gross = (
            np.full_like(baseline, inputs.r0)
            if fraction >= 1.0
            else fraction * inputs.r0 + (1.0 - fraction) * baseline
        )
        net = (
            np.full_like(baseline, inputs.r0 - immediate)
            if fraction >= 1.0
            else fraction * (inputs.r0 - immediate) + (1.0 - fraction) * (baseline - deferred)
        )
        distribution = PolicyDistribution(name=name, close_fraction=fraction, outcomes=net)
        metric = _impl._impl.policy_metrics(distribution, sim, inputs)
        gross_expected = float(np.mean(gross))
        metric.update({
            "gross_expected_final_r": round(gross_expected, 4),
            "execution_cost_r": round(gross_expected - float(np.mean(net)), 4),
            "outcomes_include_execution_costs": True,
            "event_geometry": geometry,
            "monte_carlo": {
                "seed": int(seed), "steps": int(n_steps),
                "scenarios": int(n_paths),
                "common_random_numbers": True,
            },
        })
        metrics[name] = metric
    return metrics, sim


def _raw_policy_choice(metrics: dict[str, dict], r0: float, *, cvar_floor=None):
    if cvar_floor is None:
        cvar_floor = _floor_for_r(r0)
    return _BASE_RAW_CHOICE(metrics, r0, cvar_floor=cvar_floor)


def _context_metric(item: dict) -> bool:
    metric = str(item.get("metric") or "").lower()
    return bool(
        item.get("context_only")
        or any(token in metric for token in ("gamma", "gex", "oi_wall", "open_interest"))
    )


def _normalise_evidence(evidence: dict) -> dict:
    adverse, supportive = [], []
    context = list(evidence.get("context_observations") or [])
    for item in evidence.get("adverse_confirmations") or []:
        if _context_metric(item):
            context.append({**item, "context_only": True})
        else:
            adverse.append(item)
    for item in evidence.get("supportive_contradictions") or []:
        if _context_metric(item):
            context.append({**item, "context_only": True})
        else:
            supportive.append(item)
    for collection in (adverse, supportive, context):
        for item in collection:
            item.setdefault("family", _impl._confirmation_family(item.get("metric")))
    adverse_by, supportive_by = {}, {}
    for item in adverse:
        adverse_by.setdefault(item["family"], []).append(item)
    for item in supportive:
        supportive_by.setdefault(item["family"], []).append(item)
    decisions, adverse_families, supportive_families, mixed = {}, [], [], []
    for family in sorted(set(adverse_by) | set(supportive_by)):
        a, s = adverse_by.get(family, []), supportive_by.get(family, [])
        if a and s:
            direction = "mixed"; mixed.append(family)
        elif a:
            direction = "adverse"; adverse_families.append(family)
        else:
            direction = "supportive"; supportive_families.append(family)
        decisions[family] = {
            "direction": direction,
            "adverse_metrics": [x.get("metric") for x in a],
            "supportive_metrics": [x.get("metric") for x in s],
        }
    evidence.update({
        "adverse_confirmations": adverse,
        "supportive_contradictions": supportive,
        "context_observations": context,
        "family_decisions": decisions,
        "adverse_confirmation_families": adverse_families,
        "supportive_confirmation_families": supportive_families,
        "mixed_confirmation_families": mixed,
        "adverse_confirmation_count": len(adverse_families),
        "confirmation_independence": {
            "adverse_items": len(adverse),
            "supportive_items": len(supportive),
            "adverse_families": len(adverse_families),
            "supportive_families": len(supportive_families),
            "mixed_families": len(mixed),
            "families": decisions,
            "rule": "a family present on both sides is mixed and gives no adverse vote",
        },
    })
    return evidence


def build_metric_evidence(engine, tick: dict, ridge: dict, trade: dict,
                          inputs: PolicyInputs, sim: PathSimulation,
                          policy_metrics_map: dict[str, dict]):
    return _normalise_evidence(
        _BASE_BUILD_EVIDENCE(engine, tick, ridge, trade, inputs, sim, policy_metrics_map)
    )


def select_final_policy(raw_choice: str, stability: dict, metrics: dict[str, dict],
                        evidence: dict, inputs: PolicyInputs, selection_rule: dict):
    """Keep hard CVaR, then require unopposed evidence and source authority."""
    result = _BASE_SELECT(raw_choice, stability, metrics, evidence, inputs, selection_rule)
    floor = float(selection_rule.get("cvar_floor_r", -1.0))
    source_stability = _impl.authority_stability(inputs, floor)
    result["authority_stability"] = source_stability
    selected = result.get("policy") or raw_choice
    source_share = float((source_stability.get("winner_shares") or {}).get(selected, 0.0))
    reliability = _at(evidence, "data_quality", "reliability", default={}) or {}
    level = reliability.get("level") or "не определена"
    families = list(evidence.get("adverse_confirmation_families") or [])
    reasons = list(result.get("reasons") or [])
    base_status = result.get("status") or "conflict"
    status = base_status
    executable = base_status in {"confirmed", "downgraded_within_feasible_set"}
    threshold = {
        "HOLD": 0.0, "CLOSE_10": 0.45, "CLOSE_25": 0.50,
        "CLOSE_50": 0.625, "EXIT": 0.75,
    }.get(selected, 1.0)
    if executable and source_share < threshold:
        executable = False
        status = "manual_source_conflict"
        reasons.append(f"устойчивость к источнику данных {source_share:.0%} ниже {threshold:.0%}")
    if level == "низкая":
        executable = False
        status = "manual_data_conflict"
        reasons.append("надёжность расчёта низкая")
    elif selected == "EXIT" and not reliability.get("full_exit_authority", False):
        executable = False
        status = "manual_data_conflict"
        reasons.append("EXIT требует высокой надёжности, live/direct цепочки и непрокси IV")
    if status not in {"confirmed", "downgraded_within_feasible_set"}:
        executable = False
    result.update({
        "status": status,
        "reasons": list(dict.fromkeys(reasons)),
        "confirmation_families": families,
        "confirmation_count": len(families),
        "mixed_confirmation_families": evidence.get("mixed_confirmation_families") or [],
        "source_stability_share": source_share,
        "data_reliability": level,
        "automatic_execution_allowed": executable,
        "execution_policy": selected if executable else None,
        "provisional_policy": selected,
    })
    return result


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    inputs = _impl._impl.extract_policy_inputs(tick)
    costs = execution_cost_model(tick, trade)
    risk = risk_constraint(inputs, tick, trade)
    cost_token = _COST_CTX.set(costs)
    risk_token = _RISK_CTX.set(risk)
    try:
        result = _BASE_ANALYZE(
            engine, tick, ridge, trade,
            previous_policy_inputs=previous_policy_inputs,
            previous_evidence=previous_evidence,
        )
        numerical_validation = seed_robustness(
            inputs, run_once=_run_once, choose=_raw_policy_choice,
            n_paths=1200, n_steps=180,
        )
    finally:
        _COST_CTX.reset(cost_token)
        _RISK_CTX.reset(risk_token)

    hold = (result.get("policies") or {}).get("HOLD") or {}
    result["scenario_geometry"] = hold.get("event_geometry") or result.get("scenario_geometry") or {}
    rule = result.get("selection_rule") or {}
    rule["risk_constraint"] = risk
    rule["execution_cost_model"] = costs
    result["selection_rule"] = rule
    result["risk_constraint"] = risk
    result["execution_cost_model"] = costs
    result["monte_carlo_validation"] = numerical_validation

    rec = result.get("recommendation") or {}
    gate = result.get("gate") or {}
    selected = rec.get("policy") or gate.get("provisional_policy") or "HOLD"
    executable = bool(gate.get("automatic_execution_allowed"))
    rec.update({
        "computed_action_ru": _ACTIONS_RU[selected],
        "execution_action_ru": (
            _ACTIONS_RU[selected] if executable
            else "НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ; ПРОДОЛЖАТЬ ТЕКУЩЕЕ СОПРОВОЖДЕНИЕ"
        ),
        "working_action_code": selected if executable else "KEEP_CURRENT_MANAGEMENT",
        "automatic_execution_allowed": executable,
        "remaining_management": (
            "позиция закрыта полностью"
            if executable and selected == "EXIT"
            else "сохранить действующие стоп, БУ/trailing и лестницу фиксаций"
        ),
    })
    if executable and selected == "EXIT":
        rec["next_rung_r"] = None
    result["recommendation"] = rec

    policies = result.get("policies") or {}
    chosen = policies.get(selected) or {}
    delta = (_num(chosen.get("expected_final_r")) or 0.0) - (_num(hold.get("expected_final_r")) or 0.0)
    result["risk_tradeoff"] = {
        "expected_delta_vs_hold_r": round(delta, 4),
        "expected_delta_label": (
            "расчётное преимущество над HOLD" if delta >= 0
            else "стоимость защиты относительно HOLD"
        ),
        "cvar_improvement_vs_hold_r": round(
            (_num(chosen.get("cvar10_r")) or 0.0) - (_num(hold.get("cvar10_r")) or 0.0), 4
        ),
    }
    result["version"] = "quant-policy-v4-strategy-risk-net-evidence"
    return result


for module in (_impl, _impl._impl, _impl._impl._base):
    module._run_once = _run_once
    module._raw_policy_choice = _raw_policy_choice
    module.build_metric_evidence = build_metric_evidence
    module.select_final_policy = select_final_policy
    module.analyze_policies = analyze_policies

globals()["_run_once"] = _run_once
globals()["_raw_policy_choice"] = _raw_policy_choice
globals()["build_metric_evidence"] = build_metric_evidence
globals()["select_final_policy"] = select_final_policy
globals()["analyze_policies"] = analyze_policies
